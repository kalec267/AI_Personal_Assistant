"""Native application operations, independent of UI and HTTP."""
import datetime as dt
import secrets
import threading
import time
import uuid
from pathlib import Path

from . import desktop
from .core import Calendar, parse_command
from .core import UserError
from .actions import validate_calls, perform
from .credentials import Credentials
from .ollama import Ollama
from .programs import Programs
from .settings import load_settings, save_settings
from .history import ConversationMemory
from .attachments import validate_attachments
from .search import parse_search
from .app_commands import APP_ALLOWED, describe_settings, parse_app_command


class AssistantService:
    def __init__(self, data_dir):
        self.calendar = Calendar(Path(data_dir) / "calendar.sqlite3")
        self.data_dir = Path(data_dir)
        self.credentials = Credentials(data_dir)
        self.programs = Programs(data_dir)
        self.ollama = Ollama(self.credentials)
        self._proposals = {}
        self._lock = threading.Lock()
        self.history = ConversationMemory(data_dir)
        self.sync_settings()

    def sync_settings(self, options=None):
        options = load_settings(self.data_dir) if options is None else options
        mode = options.get("ai_mode", "off")
        model = options.get("ollama_model") or ("gpt-oss:120b" if mode == "cloud" else "qwen3:4b")
        with self._lock:
            changed = self.history.configure((mode, model), options.get("remember_chat", False), options.get("history_turns", 10))
            if changed:
                self._proposals.clear()
        return changed

    def clear_conversation(self):
        with self._lock:
            self._proposals.clear()
            self.history.clear()

    def events_on(self, day):
        day = dt.date.fromisoformat(day)
        start = day.isoformat() + "T00:00"
        end = (day + dt.timedelta(days=1)).isoformat() + "T00:00"
        return [event for event in self.calendar.list() if event["start"] < end and event["end"] > start]

    def execute(self, text, *, attachments=(), progress=None, view=None):
        options = load_settings(self.data_dir)
        self.sync_settings(options)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise UserError("명령을 1~500자로 입력하세요.")
        explicit = parse_app_command(text, options, view)
        if explicit:
            if options.get("ai_mode", "off") == "off" and len(explicit) == 1 and explicit[0]["function"]["name"] in {"set_volume", "media_control"}:
                action = validate_calls(explicit, self.programs, calendar=self.calendar)[0]
                return self._run_action(action)
            with self._lock:
                return self._interpret_result(text, {"content": "", "calls": explicit}, self.history.generation)
        search = parse_search(text, options.get("search_provider", "google"), options.get("search_kind", "web"))
        if search:
            result = {"message": search["label"] + " 결과 페이지를 준비했어요.", "search": search}
            if options.get("ai_mode", "off") != "off":
                with self._lock:
                    return self._remember(text, result, self.history.generation,
                                          search["label"] + " 결과 페이지 열기를 준비했습니다. 기사·영상 내용은 조회하지 않았습니다.")
            return result
        program = self.programs.resolve(text)
        if program:
            if options.get("ai_mode", "off") != "off":
                with self._lock:
                    return self._interpret_result(text, {"content": "", "calls": [{"function": {
                        "name": "launch_program", "arguments": {"app_id": program["id"]}}}]}, self.history.generation)
            return {"message": self.programs.launch(program["id"])}
        if attachments:
            validate_attachments(attachments)
            if options.get("ai_mode", "off") == "off":
                raise UserError("첨부 분석에는 Ollama가 필요해요. 설정 → AI · 대화에서 로컬 또는 클라우드를 선택하세요.")
            return self._interpret(text, options, attachments=attachments, progress=progress)
        if options.get("ai_mode", "off") != "off":
            # Use the model for the entire utterance, so multi-step requests are
            # not partially executed by the old keyword parser.
            return self._interpret(text, options, view=view)
        intent = parse_command(text)
        action = intent["action"]
        if action == "create":
            return {"message": "일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요. 기본 길이는 1시간이에요.", "draft": intent,
                    "history_generation": self.history.generation}
        if action == "list":
            events = self.events_on(intent["day"]) if intent["day"] else self.calendar.list()
            message = "저장된 일정이 없어요." if not events else f"일정 {len(events)}개가 있어요. " + " / ".join(
                f"{event['start'].replace('T', ' ')} {event['title']}" for event in events[:10])
        elif action == "volume":
            message = desktop.volume(intent["mode"], intent.get("value", 0))
        else:
            target = self.programs.launch_target(text)
            if target:
                return {"message": f"‘{target}’에 해당하는 프로그램이 등록되지 않았어요. 설정 → 프로그램에서 부를 이름과 .exe 파일을 등록해 주세요.",
                        "open_settings": True, "settings_tab": "programs"}
            # AI off must not contact an unrelated provider because a legacy
            # environment variable happens to exist on the user's machine.
            return {"message": "현재는 기본 명령 모드예요. 자연어 대화와 명령을 사용하려면 "
                    "상단 ‘설정’에서 Ollama 로컬 또는 Ollama 클라우드를 선택하고 설정을 저장해 주세요. "
                    "클라우드 키는 그 창의 ‘Ollama API 키’에 입력하세요.", "open_settings": True}
        return {"message": message}

    def _interpret(self, text, options, *, attachments=(), progress=None, view=None):
        mode = options.get("ai_mode", "local")
        model = options.get("ollama_model") or ("gpt-oss:120b" if mode == "cloud" else "qwen3:4b")
        with self._lock:
            if self.history.scope != (mode, model):
                raise UserError("대화 설정이 변경되었어요. 새 설정에서 다시 요청해 주세요.")
            generation, turns = self.history.snapshot()
        messages = [message for turn in turns for message in (
            {"role": "user", "content": turn["user"]}, {"role": "assistant", "content": turn["assistant"]})]
        if attachments:
            analysis_model = (options.get("vision_model") or model) if any(item.image for item in attachments) else model
            result = self.ollama.analyze(mode, analysis_model, text, attachments, history=messages, progress=progress,
                                         cancelled=lambda: generation != self.history.snapshot()[0])
        else:
            result = self.ollama.request(mode, model, text, self.programs.catalog(), history=messages,
                                         allow_windows=options.get("windows_control", False), app_context=view)
        with self._lock:
            if generation != self.history.generation:
                raise UserError("대화나 연결 설정이 변경되어 이전 응답을 취소했어요. 다시 요청해 주세요.")
            if attachments:
                if result["calls"]:
                    raise UserError("첨부 분석 중 PC 동작을 실행하지 않았습니다.")
                names = ", ".join(item.name for item in attachments)
                limitations = "\n".join(dict.fromkeys(item.note for item in attachments if item.note))
                message = result["content"] + ("\n\n[첨부 읽기 안내]\n" + limitations if limitations else "")
                context = ("[첨부 읽기 안내] " + limitations + "\n" if limitations else "") + result["content"]
                return self._remember(f"{text} [첨부: {names[:160]}]", {"message": message}, generation, context)
            return self._interpret_result(text, result, generation)

    def _remember(self, text, result, generation, context=None):
        identifier = self.history.append(text, context or result["message"], generation)
        result["history_turn"] = identifier
        result["history_generation"] = generation
        return result

    def _interpret_result(self, text, result, generation):
        if not result["calls"]:
            return self._remember(text, {"message": result["content"]}, generation)
        actions = validate_calls(result["calls"], self.programs,
                                 allow_windows=load_settings(self.data_dir).get("windows_control", False), calendar=self.calendar)
        if len(actions) == 1 and actions[0]["name"] == "create_event":
            return self._remember(text, {"message": "일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요.", "draft": actions[0]["args"]},
                                  generation, actions[0]["summary"] + " · 아직 저장하지 않은 초안입니다.")
        if all(action["name"] in {"list_events", "get_app_state"} or action["name"] == "manage_program" and action["args"]["operation"] == "list" for action in actions):
            return self._remember(text, {"message": "\n".join(self._run_action(action)["message"] for action in actions)}, generation)
        identifier = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._proposals = {key: value for key, value in self._proposals.items() if value[0] > now}
        if len(self._proposals) >= 20:
            self._proposals.clear()
        summary = "\n".join(f"{i+1}. {action['summary']}" for i, action in enumerate(actions))
        reply = self._remember(text, {"message": "다음 동작을 제안했어요. 내용을 확인하고 실행해 주세요.",
                              "proposal": {"id": identifier, "summary": summary,
                                           "windows_command": any(action["name"] == "run_windows_command" for action in actions),
                                           "voice_auto": all(self._voice_auto(action) for action in actions)}}, generation,
                               "동작 제안 (확인 대기, 아직 실행하지 않음): " + summary)
        self._proposals[identifier] = (now + 300, actions, reply["history_turn"])
        return reply

    @staticmethod
    def _voice_auto(action):
        if action["name"] == "run_windows_command":
            return False
        if action["name"] == "change_settings" and action["args"].get("windows_control") is True:
            return False
        if action["name"] == "control_app" and action["args"]["action"] == "restart_admin":
            return False
        return action["name"] in APP_ALLOWED or action["name"] in {"launch_program", "set_volume", "open_website", "media_control", "search_web", "list_events"}

    def _run_action(self, action):
        name, args = action["name"], action["args"]
        if name == "list_events":
            return {"message": self._list_action(action)}
        if name == "delete_event":
            self.calendar.delete(args["event_id"], expected=action["expected_event"])
            return {"message": f"‘{action['expected_event']['title']}’ 일정을 삭제했어요. ‘최근 일정 변경 되돌려줘’로 복구할 수 있어요.", "calendar_changed": True, "calendar_day": action["expected_event"]["start"][:10]}
        if name == "update_event":
            event = self.calendar.update(args["event_id"], args["new_title"], args["new_start"], args["new_end"], expected=action["expected_event"])
            return {"message": f"일정을 수정했어요. {event['start'].replace('T', ' ')} {event['title']}", "calendar_changed": True, "calendar_day": event["start"][:10]}
        if name == "undo_calendar":
            event = self.calendar.undo(expected_change=action["expected_change"])
            return {"message": "최근 일정 변경을 되돌렸어요.", "calendar_changed": True, **({"calendar_day": event["start"][:10]} if event else {})}
        if name == "export_calendar":
            folder = self.data_dir / "exports"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"luna-calendar-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.ics"
            with path.open("xb") as output:
                output.write(self.calendar.export().encode("utf-8"))
            return {"message": f"캘린더를 저장했어요: {path}", "export_path": str(path)}
        if name == "get_app_state":
            options = load_settings(self.data_dir)
            return {"message": "현재 사용자 설정: " + describe_settings(options) + "\n등록 프로그램: " + (", ".join(item["name"] for item in self.programs.catalog()) or "없음") + "\n" + self._list_action({"args": {}})}
        if name == "change_settings":
            current = load_settings(self.data_dir)
            if args.get("ai_mode") == "cloud" and not self.credentials.get():
                raise UserError("클라우드 API 키를 먼저 설정 창에 등록해 주세요.")
            if "voice" in args and not args["voice"].startswith("local:") and args["voice"] != current.get("voice"):
                from .voice import WindowsVoice
                if args["voice"] not in {item["name"] for item in WindowsVoice().list()}:
                    raise UserError("설치된 음성 이름을 확인하세요.")
            values = {**current, **args}
            if "ai_mode" in args and "ollama_model" not in args and current.get("ollama_model", "qwen3:4b") in {"qwen3:4b", "gpt-oss:120b"}:
                values["ollama_model"] = "gpt-oss:120b" if args["ai_mode"] == "cloud" else "qwen3:4b"
            voice, speak = values.pop("voice", ""), values.pop("speak", True)
            save_settings(self.data_dir, voice, speak, **values)
            changed = self.sync_settings()
            return {"message": "사용자 설정을 저장했어요. " + action["summary"], "settings_changed": list(args),
                    **({"conversation_changed": True} if changed else {})}
        if name == "manage_program":
            operation = args["operation"]
            if operation == "list":
                items = self.programs.catalog()
                return {"message": "등록된 프로그램이 없어요." if not items else "등록 프로그램: " + ", ".join(item["name"] for item in items)}
            if operation == "register":
                return {"message": "등록할 실행 파일을 선택해 주세요.", "ui_actions": [{"action": "register_program", "name": args.get("name", "")}]}
            if operation == "remove":
                self.programs.remove(args["app_id"], expected=action["expected_program"])
                message = "프로그램 등록을 해제했어요. 설치 파일은 유지합니다."
            else:
                self.programs.rename(args["app_id"], args["new_name"], expected=action["expected_program"])
                message = f"프로그램을 부를 이름을 ‘{args['new_name']}’으로 변경했어요."
            return {"message": message, "programs_changed": True}
        if name == "control_app":
            return {"message": action["summary"] + " 요청을 준비했어요.", "ui_actions": [dict(args)]}
        return {"message": perform(action, self.programs, allow_windows=load_settings(self.data_dir).get("windows_control", False))}

    def _list_action(self, action):
        day = action["args"].get("day")
        events = self.events_on(day) if day else self.calendar.list()
        return "저장된 일정이 없어요." if not events else f"일정 {len(events)}개: " + " / ".join(f"{event['start']} {event['title']}" for event in events[:10])

    def discard_proposal(self, identifier):
        with self._lock:
            proposal = self._proposals.pop(identifier, None)
            if proposal:
                self.history.update(proposal[2], "사용자가 동작 제안을 취소했습니다. 실행하지 않았습니다. 제안: " +
                                    " / ".join(action["summary"] for action in proposal[1]))

    def draft_saved(self, turn_id, event):
        if turn_id:
            self.history.update(turn_id, f"일정 저장 완료: {event['title']} · {event['start']} — {event['end']}")

    def save_draft(self, draft, turn_id=None, *, generation=None):
        with self._lock:
            if generation is not None and generation != self.history.generation:
                raise UserError("대화 설정이 변경되어 이전 음성 일정의 자동 저장을 취소했어요.")
            event = self.calendar.add(draft["title"], draft["start"], draft["end"])
            self.draft_saved(turn_id, event)
        return {"message": f"일정을 저장했어요. {event['start'].replace('T', ' ')} · {event['title']}", "saved_event": event}

    def confirm_proposal(self, identifier):
        with self._lock:
            proposal = self._proposals.pop(identifier, None)
        if proposal is None or proposal[0] < time.monotonic():
            if proposal:
                self.history.update(proposal[2], "실행 제안이 만료되어 아무 동작도 실행하지 않았습니다.")
            raise UserError("실행 제안이 만료되었거나 이미 처리되었어요. 다시 요청하세요.")
        # Validate the complete plan again before any side effect.
        allow_windows = load_settings(self.data_dir).get("windows_control", False)
        try:
            actions = validate_calls([{"function": {"name": action["name"], "arguments": action["args"]}} for action in proposal[1]], self.programs,
                                     allow_windows=allow_windows, calendar=self.calendar)
            for before, current in zip(proposal[1], actions):
                for key in ("expected_event", "expected_change", "expected_program"):
                    if before.get(key) != current.get(key):
                        raise UserError("대상 일정·프로그램이 변경되어 실행하지 않았어요. 다시 요청하세요.")
        except UserError:
            self.history.update(proposal[2], "실행 직전 권한·등록 정보 확인에 실패하여 동작을 실행하지 않았습니다.")
            raise
        completed = []
        effects = {}
        for action in actions:
            try:
                # Recheck the saved permission immediately before each command.
                outcome = self._run_action(action)
                completed.append(outcome["message"])
                for key, value in outcome.items():
                    if key == "message":
                        continue
                    if key in {"settings_changed", "ui_actions"}:
                        effects.setdefault(key, []).extend(value)
                    else:
                        effects[key] = value
            except (UserError, OSError) as error:
                # Never claim atomicity: already completed actions cannot be undone.
                detail = ("\n" + str(error)) if isinstance(error, UserError) else ""
                result = {"message": "\n".join(completed + [f"중단: {action['summary']} 실행에 실패했어요.{detail}\n이후 동작은 실행하지 않았습니다."]), **effects}
                self.history.update(proposal[2], result["message"])
                return result
        result = {"message": "\n".join(completed), **effects}
        self.history.update(proposal[2], result["message"])
        return result
