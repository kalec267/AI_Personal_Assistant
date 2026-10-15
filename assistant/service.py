"""Native application operations, independent of UI and HTTP."""
import datetime as dt
import secrets
import threading
import time
from pathlib import Path

from . import desktop
from .core import Calendar, parse_command
from .core import UserError
from .actions import validate_calls, perform
from .credentials import Credentials
from .ollama import Ollama
from .programs import Programs
from .settings import load_settings
from .history import ConversationMemory


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

    def execute(self, text):
        options = load_settings(self.data_dir)
        self.sync_settings(options)
        if options.get("ai_mode", "off") != "off":
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                raise UserError("명령을 1~500자로 입력하세요.")
            # Use the model for the entire utterance, so multi-step requests are
            # not partially executed by the old keyword parser.
            return self._interpret(text, options)
        intent = parse_command(text)
        action = intent["action"]
        if action == "create":
            return {"message": "일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요. 기본 길이는 1시간이에요.", "draft": intent}
        if action == "list":
            events = self.events_on(intent["day"]) if intent["day"] else self.calendar.list()
            message = "저장된 일정이 없어요." if not events else f"일정 {len(events)}개가 있어요. " + " / ".join(
                f"{event['start'].replace('T', ' ')} {event['title']}" for event in events[:10])
        elif action == "launch":
            message = desktop.launch(intent["app"])
        elif action == "volume":
            message = desktop.volume(intent["mode"], intent.get("value", 0))
        else:
            # AI off must not contact an unrelated provider because a legacy
            # environment variable happens to exist on the user's machine.
            return {"message": "현재는 기본 명령 모드예요. 자연어 대화와 명령을 사용하려면 "
                    "상단 ‘설정’에서 Ollama 로컬 또는 Ollama 클라우드를 선택하고 설정을 저장해 주세요. "
                    "클라우드 키는 그 창의 ‘Ollama API 키’에 입력하세요.", "open_settings": True}
        return {"message": message}

    def _interpret(self, text, options):
        mode = options.get("ai_mode", "local")
        model = options.get("ollama_model") or ("gpt-oss:120b" if mode == "cloud" else "qwen3:4b")
        with self._lock:
            if self.history.scope != (mode, model):
                raise UserError("대화 설정이 변경되었어요. 새 설정에서 다시 요청해 주세요.")
            generation, turns = self.history.snapshot()
        messages = [message for turn in turns for message in (
            {"role": "user", "content": turn["user"]}, {"role": "assistant", "content": turn["assistant"]})]
        result = self.ollama.request(mode, model, text, self.programs.catalog(), history=messages)
        with self._lock:
            if generation != self.history.generation:
                raise UserError("대화나 연결 설정이 변경되어 이전 응답을 취소했어요. 다시 요청해 주세요.")
            return self._interpret_result(text, result, generation)

    def _remember(self, text, result, generation, context=None):
        identifier = self.history.append(text, context or result["message"], generation)
        result["history_turn"] = identifier
        return result

    def _interpret_result(self, text, result, generation):
        if not result["calls"]:
            return self._remember(text, {"message": result["content"]}, generation)
        actions = validate_calls(result["calls"], self.programs)
        if len(actions) == 1 and actions[0]["name"] == "create_event":
            return self._remember(text, {"message": "AI가 일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요.", "draft": actions[0]["args"]},
                                  generation, actions[0]["summary"] + " · 아직 저장하지 않은 초안입니다.")
        if all(action["name"] == "list_events" for action in actions):
            return self._remember(text, {"message": "\n".join(self._list_action(action) for action in actions)}, generation)
        identifier = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._proposals = {key: value for key, value in self._proposals.items() if value[0] > now}
        if len(self._proposals) >= 20:
            self._proposals.clear()
        summary = "\n".join(f"{i+1}. {action['summary']}" for i, action in enumerate(actions))
        reply = self._remember(text, {"message": "다음 동작을 제안했어요. 내용을 확인하고 실행해 주세요.",
                              "proposal": {"id": identifier, "summary": summary}}, generation,
                               "동작 제안 (확인 대기, 아직 실행하지 않음): " + summary)
        self._proposals[identifier] = (now + 300, actions, reply["history_turn"])
        return reply

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

    def confirm_proposal(self, identifier):
        with self._lock:
            proposal = self._proposals.pop(identifier, None)
        if proposal is None or proposal[0] < time.monotonic():
            if proposal:
                self.history.update(proposal[2], "실행 제안이 만료되어 아무 동작도 실행하지 않았습니다.")
            raise UserError("실행 제안이 만료되었거나 이미 처리되었어요. 다시 요청하세요.")
        # Validate the complete plan again before any side effect.
        actions = validate_calls([{"function": {"name": action["name"], "arguments": action["args"]}} for action in proposal[1]], self.programs)
        completed = []
        for action in actions:
            try:
                completed.append(self._list_action(action) if action["name"] == "list_events" else perform(action, self.programs))
            except (UserError, OSError):
                # Never claim atomicity: already completed actions cannot be undone.
                result = {"message": "\n".join(completed + [f"중단: {action['summary']} 실행에 실패했어요. 이후 동작은 실행하지 않았습니다."])}
                self.history.update(proposal[2], result["message"])
                return result
        result = {"message": "\n".join(completed)}
        self.history.update(proposal[2], result["message"])
        return result
