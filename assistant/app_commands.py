"""Shared schemas and validation for native app features, without Tk objects."""
import datetime as dt
import re

from .core import Calendar, UserError, parse_command
from .programs import name_key
from .settings import check_voice_speed

UI_ACTIONS = {
    "open_settings": "설정 열기", "open_search": "검색 창 열기", "add_event": "일정 작성 창 열기",
    "attach_files": "첨부 파일 선택", "show_attachments": "첨부 목록 열기", "clear_attachments": "첨부 비우기",
    "remove_attachment": "첨부 제거", "summarize_attachments": "첨부 요약", "new_conversation": "새 대화 시작",
    "stop_speech": "음성 출력 중지", "test_voice": "목소리 미리 듣기", "install_voice": "로컬 음성 설치",
    "install_wake": "호출어 모델 설치", "restart_admin": "관리자 권한 재실행 · Windows UAC 필요",
    "test_connection": "AI 연결 테스트", "manage_api_key": "API 키 입력·삭제 설정 열기",
    "delete_api_key": "저장한 API 키 삭제", "listen": "마이크로 명령 듣기",
    "save_settings": "설정 창의 변경 저장", "close_app": "앱 종료", "show_calendar": "달력 날짜 보기",
    "export_calendar_dialog": "캘린더 저장 위치 선택", "close_settings": "설정 닫기",
}
SETTING_PROPERTIES = {
    "speak": {"type": "boolean"}, "voice_speed": {"type": "number", "minimum": 0.5, "maximum": 2.0},
    "voice": {"type": "string", "description": "local:F1~F5 또는 앱 상태에 있는 설치 음성 이름"},
    "wake_enabled": {"type": "boolean"}, "wake_during_speech": {"type": "boolean"},
    "remember_chat": {"type": "boolean"}, "history_turns": {"type": "integer", "minimum": 1, "maximum": 20},
    "volume": {"type": "integer", "minimum": 0, "maximum": 100}, "apply_volume_on_start": {"type": "boolean"},
    "search_provider": {"type": "string", "enum": ["google", "naver"]},
    "search_kind": {"type": "string", "enum": ["web", "news", "video"]},
    "ai_mode": {"type": "string", "enum": ["off", "local", "cloud", "openai"]},
    "ollama_model": {"type": "string"}, "vision_model": {"type": "string"}, "windows_control": {"type": "boolean"},
    "openai_model": {"type": "string"}, "openai_vision_model": {"type": "string"},
}
SETTING_LABELS = dict(zip(SETTING_PROPERTIES, ("답변 읽기", "음성 속도", "목소리", "호출 감지", "답변 중 호출 감지", "대화 저장", "기억할 대화 수", "기본 볼륨", "시작 시 볼륨 적용", "검색 엔진", "검색 종류", "AI 연결", "Ollama 대화 모델", "Ollama 이미지 모델", "Windows 고급 제어", "OpenAI 대화 모델", "OpenAI 이미지 모델")))


def describe_settings(values):
    labels = {"local": "로컬", "cloud": "Ollama 클라우드", "openai": "ChatGPT · OpenAI", "off": "끄기", "google": "구글", "naver": "네이버", "web": "전체", "news": "뉴스", "video": "동영상"}
    result = []
    for key, value in values.items():
        if type(value) is bool:
            value = "켜기" if value else "끄기"
        elif isinstance(value, str):
            value = labels.get(value, value)
        if key == "voice_speed":
            value = f"{value}배"
        result.append(f"{SETTING_LABELS.get(key, key)}: {value}")
    return ", ".join(result)
SELECTOR = {"event_id": {"type": "string"}, "title": {"type": "string", "description": "기존 일정 제목. 대상이 불명확하면 질문"},
            "day": {"type": "string", "description": "기존 일정 날짜 YYYY-MM-DD"},
            "start": {"type": "string", "description": "기존 일정 시작 시각. PC 현지 ISO 날짜·시간"}}
APP_ALLOWED = {
    "delete_event": set(SELECTOR), "update_event": set(SELECTOR) | {"new_title", "new_start", "new_end"},
    "undo_calendar": {"kind"}, "export_calendar": set(), "get_app_state": set(),
    "change_settings": set(SETTING_PROPERTIES),
    "manage_program": {"operation", "app_id", "name", "new_name"},
    "control_app": {"action", "tab", "index", "day", "persist"},
}


def app_tools(tool):
    return [
        tool("delete_event", "저장된 일정 한 개 삭제. 제목·날짜·시각 또는 ID로 지정. 여러 후보면 질문하며 삭제는 되돌릴 수 있음", SELECTOR, []),
        tool("update_event", "기존 일정의 제목·시작·종료 변경. 기존 대상 선택과 새 값을 구분. 시작만 변경하면 기존 길이 유지", {
            **SELECTOR, **{key: {"type": "string"} for key in ("new_title", "new_start", "new_end")}}, []),
        tool("undo_calendar", "최근 일정 변경 한 건 되돌리기. 삭제 복구면 kind=delete, 추가 취소면 add, 수정 취소면 update로 지정. 최근 변경 종류가 다르면 덮어쓰지 않고 질문. 최대 최근 20건 순차 지원", {"kind": {"type": "string", "enum": ["add", "update", "delete"]}}, []),
        tool("export_calendar", "전체 일정을 앱 데이터 폴더의 exports에 새 .ics 파일로 저장. 기존 파일 덮어쓰기 없음", {}, []),
        tool("get_app_state", "비밀 키를 제외한 현재 앱 설정·등록 프로그램·일정 목록 조회", {}, []),
        tool("change_settings", "사용자 지정 설정을 PC에 저장. 목소리·음성 속도·읽기·호출·대화·검색·모델·권한. 현재 볼륨 변경은 set_volume 사용", SETTING_PROPERTIES, []),
        tool("manage_program", "등록 프로그램 목록·추가·이름 변경·등록 해제. register는 사용자가 .exe 파일 선택. remove는 설치 파일 삭제가 아님", {
            "operation": {"type": "string", "enum": ["list", "register", "rename", "remove"]},
            "app_id": {"type": "string"}, "name": {"type": "string"}, "new_name": {"type": "string"}}, ["operation"]),
        tool("control_app", "앱 화면·첨부·음성·설치·연결·대화·종료 제어. API 키는 직접 발화하거나 도구에 넣지 않고 manage_api_key로 가려진 설정 창에서 입력. 파일 선택·UAC는 Windows 창 사용", {
            "action": {"type": "string", "enum": list(UI_ACTIONS)},
            "tab": {"type": "string", "enum": ["ai", "programs", "sound", "windows"]},
            "index": {"type": "integer", "minimum": 1, "maximum": 5},
            "persist": {"type": "boolean", "description": "manage_api_key에서 새 키를 Windows 사용자에 암호화 저장할지 선택"},
            "day": {"type": "string", "description": "YYYY-MM-DD"}}, ["action"]),
    ]


def checked_text(value, limit=200):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or any(ord(c) < 32 for c in value):
        raise UserError("이름·대상 값이 올바르지 않아요.")
    return value.strip()


def checked_day(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise UserError("날짜는 YYYY-MM-DD 형식으로 지정하세요.") from None


def select_event(calendar, args):
    if calendar is None:
        raise UserError("일정 저장소가 연결되지 않았어요.")
    if not args or not any(key in args for key in SELECTOR):
        raise UserError("삭제·수정할 일정의 제목과 날짜를 알려 주세요.")
    events = calendar.list()
    for key in SELECTOR:
        if key not in args:
            continue
        value = checked_day(args[key]) if key == "day" else checked_text(args[key])
        if key == "event_id":
            events = [event for event in events if event["id"] == value]
        elif key == "day":
            events = [event for event in events if event["start"][:10] == value]
        elif key == "title":
            exact = [event for event in events if name_key(event["title"]) == name_key(value)]
            events = exact or [event for event in events if name_key(value) in name_key(event["title"])]
        else:
            try:
                parsed = dt.datetime.fromisoformat(value)
                if parsed.tzinfo:
                    raise ValueError()
                value = parsed.isoformat(timespec="minutes")
            except ValueError:
                raise UserError("대상 일정의 시작 시각을 정확히 알려 주세요.") from None
            events = [event for event in events if event["start"] == value]
    if not events:
        raise UserError("조건에 맞는 일정이 없어요. 일정 제목과 날짜를 확인해 주세요.")
    if len(events) != 1:
        choices = " / ".join(f"{event['start'].replace('T', ' ')} {event['title']}" for event in events[:8])
        raise UserError(f"일정이 {len(events)}개라 아직 변경하지 않았어요. 날짜·시간·제목으로 하나를 지정해 주세요. {choices}")
    return events[0]


def settings_values(args):
    if not args or args.keys() - SETTING_PROPERTIES.keys():
        raise UserError("변경할 지원 설정을 지정해 주세요.")
    values = dict(args)
    for key, value in args.items():
        spec = SETTING_PROPERTIES[key]
        if spec["type"] == "boolean" and type(value) is not bool:
            raise UserError("설정의 켜기·끄기 값이 올바르지 않아요.")
        if "enum" in spec and (not isinstance(value, str) or value not in spec["enum"]):
            raise UserError("지원하지 않는 설정 값입니다.")
        if spec["type"] == "integer" and (type(value) is not int or not spec["minimum"] <= value <= spec["maximum"]):
            raise UserError("설정 수치가 허용 범위를 벗어났어요.")
        if key == "voice_speed":
            try:
                values[key] = check_voice_speed(value)
            except ValueError as error:
                raise UserError(str(error)) from None
        if key in {"ollama_model", "vision_model", "openai_model", "openai_vision_model"}:
            if key in {"vision_model", "openai_vision_model"} and value == "":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", value):
                raise UserError("모델 이름이 올바르지 않아요.")
        if key == "voice":
            values[key] = checked_text(value, 150)
            if value.startswith("local:") and value not in {f"local:F{i}" for i in range(1, 6)}:
                raise UserError("로컬 목소리는 local:F1~F5 중 선택하세요.")
    return values


def validate_app_action(name, args, programs, calendar):
    action = {"name": name, "args": dict(args)}
    if name in {"delete_event", "update_event"}:
        event = select_event(calendar, args)
        action["expected_event"] = event
        action["args"] = {"event_id": event["id"]}
        summary = f"{event['start'].replace('T', ' ')} {event['title']}"
        if name == "delete_event":
            summary = "일정 삭제: " + summary
        else:
            changes = {key: value for key, value in args.items() if key.startswith("new_")}
            if not changes:
                raise UserError("일정의 어떤 내용을 바꿀지 알려 주세요.")
            title, start, end = (changes.get("new_title", event["title"]), changes.get("new_start", event["start"]), changes.get("new_end", event["end"]))
            if "new_start" in changes and "new_end" not in changes:
                try:
                    end = (dt.datetime.fromisoformat(start) + (dt.datetime.fromisoformat(event["end"]) - dt.datetime.fromisoformat(event["start"]))).isoformat(timespec="minutes")
                except (ValueError, TypeError):
                    raise UserError("변경할 시작 시각을 정확히 지정하세요.") from None
            if not all(isinstance(value, str) for value in (title, start, end)):
                raise UserError("일정의 변경 값이 올바르지 않아요.")
            values = Calendar.event_values(title, start, end)
            action["args"].update({"new_" + key: value for key, value in values.items()})
            summary = f"일정 수정: {summary} → {values['start']} — {values['end']} {values['title']}"
    elif name == "undo_calendar":
        change = calendar.last_change() if calendar else None
        if change is None:
            raise UserError("되돌릴 일정 변경이 없어요.")
        kind = "add" if change["before"] is None else "delete" if change["after"] is None else "update"
        if "kind" in args and (not isinstance(args["kind"], str) or args["kind"] not in {"add", "update", "delete"} or args["kind"] != kind):
            raise UserError("최근 일정 변경의 종류가 요청과 달라 되돌리지 않았어요. 최근 변경부터 순서대로 되돌려 주세요.")
        action["expected_change"] = change["id"]
        summary = "최근 일정 변경 되돌리기: " + (change["after"] or change["before"])["title"]
    elif name == "change_settings":
        action["args"] = settings_values(args)
        summary = "사용자 설정 저장: " + describe_settings(action["args"])
    elif name == "manage_program":
        operation = args.get("operation")
        if not isinstance(operation, str) or operation not in {"list", "register", "rename", "remove"}:
            raise UserError("프로그램 목록·등록·이름 변경·등록 해제 중 지정하세요.")
        allowed = {"list": {"operation"}, "register": {"operation", "name"},
                   "remove": {"operation", "name", "app_id"}, "rename": {"operation", "name", "app_id", "new_name"}}[operation]
        if args.keys() - allowed:
            raise UserError("프로그램 작업에 맞지 않는 인자입니다.")
        if operation in {"remove", "rename"}:
            candidates = [{"id": key, **item} for key, item in programs.custom().items()]
            if "app_id" in args:
                candidates = [item for item in candidates if item["id"] == checked_text(args["app_id"])]
            if "name" in args:
                candidates = [item for item in candidates if name_key(item["name"]) == name_key(checked_text(args["name"], 60))]
            if "app_id" not in args and "name" not in args:
                candidates = []
            if len(candidates) != 1:
                raise UserError("프로그램 등록 이름을 하나로 정확히 지정해 주세요.")
            item = candidates[0]
            action["expected_program"] = item
            action["args"] = {"operation": operation, "app_id": item["id"]}
            if operation == "rename":
                new_name = checked_text(args.get("new_name"), 60)
                if any(key != item["id"] and name_key(value["name"]) == name_key(new_name) for key, value in programs.custom().items()):
                    raise UserError("이미 등록된 이름입니다.")
                action["args"]["new_name"] = new_name
            summary = f"프로그램 등록 {'해제' if operation == 'remove' else '이름 변경'}: {item['name']}" + (" → " + new_name if operation == "rename" else " · 설치 파일은 유지")
        elif operation == "register":
            if "name" in args:
                action["args"]["name"] = checked_text(args["name"], 60)
                if any(name_key(item["name"]) == name_key(args["name"]) for item in programs.custom().values()):
                    raise UserError("이미 등록된 이름입니다.")
            summary = "프로그램 등록 · .exe 파일 직접 선택: " + args.get("name", "이름 입력")
        else:
            summary = "등록 프로그램 목록"
    elif name == "control_app":
        kind = args.get("action")
        if not isinstance(kind, str) or kind not in UI_ACTIONS:
            raise UserError("지원하지 않는 앱 기능입니다.")
        allowed = {"action"} | {"open_settings": {"tab"}, "show_calendar": {"day"}, "remove_attachment": {"index"}, "manage_api_key": {"persist"}}.get(kind, set())
        if args.keys() - allowed:
            raise UserError("화면 기능에 맞지 않는 인자입니다.")
        if "tab" in args and (not isinstance(args["tab"], str) or args["tab"] not in {"ai", "programs", "sound", "windows"}):
            raise UserError("설정 탭이 올바르지 않아요.")
        if kind == "remove_attachment" and (type(args.get("index")) is not int or not 1 <= args["index"] <= 5):
            raise UserError("제거할 첨부 번호를 1~5로 지정하세요.")
        if "persist" in args and type(args["persist"]) is not bool:
            raise UserError("키 저장 여부는 켜기·끄기로 지정하세요.")
        if "day" in args:
            action["args"]["day"] = checked_day(args["day"])
        summary = UI_ACTIONS[kind]
    else:
        summary = "캘린더 파일 내보내기" if name == "export_calendar" else "앱 상태·설정 조회"
    action["summary"] = summary
    return action


def call(tool_name, **args):
    return [{"function": {"name": tool_name, "arguments": args}}]


def selector_words(text, view=None):
    match = re.fullmatch(r"\s*(?:(오늘|내일|모레|\d{4}-\d{2}-\d{2})\s*)?(.*?)\s*", text)
    day, title = match.groups()
    args = {}
    if day:
        args["day"] = (dt.date.today() + dt.timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[day])).isoformat() if day in {"오늘", "내일", "모레"} else checked_day(day)
    clock = re.match(r"(?:(?:오전|오후)\s*)?\d{1,2}(?:시(?:\s*(?:반|\d{1,2}\s*분))?|:\d{2})(?:에)?\s*", title)
    if clock:
        if "day" not in args:
            raise UserError("대상 일정의 날짜도 알려 주세요.")
        args["start"] = parse_command(args["day"] + " " + clock[0] + " 임시 일정 추가")["start"]
        title = title[clock.end():]
    if title.strip() in {"선택한", "선택된"}:
        if not view or not view.get("selected_event"):
            raise UserError("선택된 일정이 없어요. 제목과 날짜를 말씀해 주세요.")
        args["event_id"] = view["selected_event"]["id"]
    elif title.strip():
        args["title"] = title.strip()
    return args


def parse_app_command(text, options, view=None):
    """Explicit user commands only; document contents never reach this parser."""
    text = text.strip().rstrip(".!?。！？")
    key = re.sub(r"\s+", "", text)
    if re.fullmatch(r"(?:방금|최근)?(?:일정(?:변경|삭제|수정|추가)?|삭제한일정)(?:을|를)?(?:되돌려(?:줘|주세요)?|(?:복구|복원)(?:해줘|해주세요)?|취소해줘)", key):
        kind = "delete" if "삭제" in key else "add" if "추가" in key else "update" if "수정" in key else None
        return call("undo_calendar", **({"kind": kind} if kind else {}))
    if re.search(r"하지\s*마|하지\s*말|말고|방법|어떻게|사용법|예시", text):
        return None
    suffix = r"(?:해(?:줘|주세요)?|줘|주세요)?"
    simple = {
        r"(?:음소거해제|소리켜)(?:줘|주세요)?": ("set_volume", {"mode": "unmute"}),
        r"(?:음소거|소리꺼)(?:해줘|해주세요|줘|주세요)?": ("set_volume", {"mode": "mute"}),
        r"(?:볼륨|음량|소리)(?:을|를)?(?:올려|높여|크게)(?:줘|주세요|해줘)?": ("set_volume", {"mode": "up"}),
        r"(?:볼륨|음량|소리)(?:을|를)?(?:내려|낮춰|줄여|작게)(?:줘|주세요|해줘)?": ("set_volume", {"mode": "down"}),
        r"(?:전체)?(?:일정|캘린더)(?:을|를)?내보내(?:기|줘|주세요)?": ("export_calendar", {}),
        r"(?:파일|문서|이미지)(?:을|를)?첨부" + suffix: ("control_app", {"action": "attach_files"}),
        r"첨부(?:파일|자료)?(?:를|을)?(?:비워줘|비워주세요|초기화해줘)": ("control_app", {"action": "clear_attachments"}),
        r"첨부(?:파일|자료)?(?:목록)?(?:을|를)?(?:보여줘|열어줘)": ("control_app", {"action": "show_attachments"}),
        r"첨부(?:파일|자료)?(?:를|을)?요약" + suffix: ("control_app", {"action": "summarize_attachments"}),
        r"일정(?:작성|추가)(?:창)?(?:을|를)?열어(?:줘|주세요)?": ("control_app", {"action": "add_event"}),
        r"(?:검색|뉴스검색)(?:창)?(?:을|를)?열어(?:줘|주세요)?": ("control_app", {"action": "open_search"}),
        r"(?:API|에이피아이)키(?:을|를)?(?:설정|등록|입력)" + suffix: ("control_app", {"action": "manage_api_key"}),
        r"(?:API|에이피아이)키(?:을|를)?삭제" + suffix: ("control_app", {"action": "delete_api_key"}),
        r"(?:마이크켜|음성인식시작)(?:줘|주세요)?": ("control_app", {"action": "listen"}),
        r"(?:새대화(?:시작)?|대화초기화)" + suffix: ("control_app", {"action": "new_conversation"}),
        r"(?:음성|목소리)(?:출력)?(?:을|를)?(?:중지|멈춰|그만말해)" + suffix: ("control_app", {"action": "stop_speech"}),
        r"(?:목소리|음성)(?:미리)?(?:들어보기|들려줘|테스트해줘)": ("control_app", {"action": "test_voice"}),
        r"로컬(?:목소리|음성)(?:모델)?설치" + suffix: ("control_app", {"action": "install_voice"}),
        r"호출어(?:모델)?설치" + suffix: ("control_app", {"action": "install_wake"}),
        r"(?:AI|에이아이|올라마|Ollama|ChatGPT|챗지피티|챗GPT|OpenAI|오픈AI|오픈에이아이)연결테스트" + suffix: ("control_app", {"action": "test_connection"}),
        r"관리자권한으로(?:다시)?(?:실행|재실행)" + suffix: ("control_app", {"action": "restart_admin"}),
        r"(?:앱|루나)(?:을|를)?종료" + suffix: ("control_app", {"action": "close_app"}),
        r"설정(?:을|를)?저장" + suffix: ("control_app", {"action": "save_settings"}),
        r"설정(?:창)?(?:을|를)?닫아(?:줘|주세요)?": ("control_app", {"action": "close_settings"}),
        r"(?:프로그램|등록프로그램)(?:목록)?(?:을|를)?보여(?:줘|주세요)?": ("manage_program", {"operation": "list"}),
        r"프로그램등록" + suffix: ("manage_program", {"operation": "register"}),
        r"(?:현재)?(?:앱)?설정(?:을|를)?(?:알려줘|보여줘)": ("get_app_state", {}),
        r"(?:다음곡|다음노래)(?:틀어줘|재생해줘)?": ("media_control", {"command": "next"}),
        r"(?:이전곡|이전노래)(?:틀어줘|재생해줘)?": ("media_control", {"command": "previous"}),
        r"(?:음악|미디어)(?:을|를)?(?:일시정지|재생)" + suffix: ("media_control", {"command": "play_pause"}),
    }
    for pattern, (name, args) in simple.items():
        if re.fullmatch(pattern, key, re.IGNORECASE):
            return call(name, **args)
    match = re.fullmatch(r"(?:볼륨|음량|소리)(?:을|를)?(\d+)(?:퍼센트|%)?(?:로|으로)?(?:설정|맞춰)?(?:해줘|해주세요|줘|주세요)?", key)
    if match:
        return call("set_volume", mode="set", value=int(match[1]))
    if re.fullmatch(r"(?:(?:오늘|내일|모레)\s*)?(?:일정|스케줄|캘린더)(?:을|를)?\s*(?:보여줘|알려줘|확인해줘|목록)", text):
        intent = parse_command(text)
        return call("list_events", **({"day": intent["day"]} if intent["day"] else {}))
    if re.search(r"(?:일정|스케줄|캘린더)(?:을|를)?\s*(?:추가|등록|작성|만들어|잡아)(?:해)?\s*(?:줘|주세요)?$", text):
        intent = parse_command(text)
        if intent["action"] == "create":
            return call("create_event", **{key: intent[key] for key in ("title", "start", "end")})
    match = re.fullmatch(r"(?:(AI|프로그램|소리|음성|Windows|윈도우)\s*)?설정(?:\s*창)?(?:을|를)?\s*열어\s*(?:줘|주세요)?", text, re.IGNORECASE)
    if match:
        return call("control_app", action="open_settings", tab={"프로그램": "programs", "소리": "sound", "음성": "sound", "windows": "windows", "윈도우": "windows"}.get((match[1] or "").lower(), "ai"))
    match = re.fullmatch(r"(?:음성|목소리)(?:출력)?속도(?:를|는)?(\d+(?:\.\d+)?)배?(?:로)?(?:설정|변경|조절)?" + suffix, key)
    if match:
        return call("change_settings", voice_speed=float(match[1]))
    match = re.fullmatch(r"(?:음성|목소리)(?:출력)?속도(?:를|는)?(?:조금|더)?(느리게|빠르게)(?:해|바꿔)?(?:줘|주세요)?", key)
    if match:
        return call("change_settings", voice_speed=round(options.get("voice_speed", 1.0) + (-0.1 if match[1] == "느리게" else 0.1), 2))
    match = re.fullmatch(r"(?:목소리|음성)(?:을|를)?(?:여성|여자)?([1-5])번(?:으로)?(?:설정|변경|바꿔)?" + suffix, key)
    if match:
        return call("change_settings", voice=f"local:F{match[1]}")
    match = re.fullmatch(r"(답변읽기|음성출력|호출감지|호출어감지|대화저장)(?:를|을)?(켜|꺼)(?:줘|주세요)?", key)
    if match:
        setting = {"답변읽기": "speak", "음성출력": "speak", "호출감지": "wake_enabled", "호출어감지": "wake_enabled", "대화저장": "remember_chat"}[match[1]]
        return call("change_settings", **{setting: match[2] == "켜"})
    match = re.fullmatch(r"(?:기본)?검색(?:엔진|서비스)(?:을|를)?(구글|네이버)(?:로|으로)(?:변경|설정|바꿔)" + suffix, key)
    if match:
        return call("change_settings", search_provider="google" if match[1] == "구글" else "naver")
    match = re.fullmatch(r"(로컬|클라우드)(?:모드)?(?:로|으로)(?:변경|설정|바꿔)" + suffix, key)
    if match:
        return call("change_settings", ai_mode="local" if match[1] == "로컬" else "cloud")
    if re.fullmatch(r"(?:챗지피티|챗gpt|chatgpt|오픈ai|openai|오픈에이아이)(?:모드)?(?:로|으로)(?:변경|설정|바꿔)" + suffix, key, re.IGNORECASE):
        return call("change_settings", ai_mode="openai")
    match = re.fullmatch(r"(?:기본|저장할)볼륨(?:을|를)?(\d+)(?:퍼센트|%)?(?:로|으로)?(?:저장|설정)" + suffix, key)
    if match:
        return call("change_settings", volume=int(match[1]))
    match = re.fullmatch(r"(.+?)\s*일정(?:의)?\s*(?:이름|제목)(?:을|를)?\s*(.+?)(?:으로|로)\s*(?:바꿔|변경)(?:해)?\s*(?:줘|주세요)?", text)
    if match:
        return call("update_event", **selector_words(match[1], view), new_title=match[2].strip())
    match = re.fullmatch(r"(.+?)\s*일정(?:을|를)?\s*(?:시간(?:을|를)?\s*)?(.+?)(?:으로|로)\s*(?:바꿔|변경|옮겨)(?:해)?\s*(?:줘|주세요)?", text)
    if match:
        args = selector_words(match[1], view)
        when = match[2].strip()
        if not re.match(r"오늘|내일|모레|\d{4}-\d{2}-\d{2}", when):
            when = args.get("day", dt.date.today().isoformat()) + " " + when
        parsed = parse_command(when + " 임시 일정 추가")
        return call("update_event", **args, new_start=parsed["start"])
    match = re.fullmatch(r"(.+?)\s*(?:프로그램\s*)?등록(?:을|를)?\s*(해제|삭제)(?:\s*해)?\s*(?:줘|주세요)?", text)
    if match and not re.search(r"일정|스케줄|캘린더", match[1]):
        return call("manage_program", operation="remove", name=match[1].strip())
    match = re.fullmatch(r"(.+?)\s*(?:프로그램\s*)?등록(?:\s*해)?\s*(?:줘|주세요)?", text)
    if match and not re.search(r"일정|스케줄|캘린더", match[1]):
        return call("manage_program", operation="register", name=match[1].strip())
    match = re.fullmatch(r"(.+?)\s*(?:등록\s*)?이름(?:을|를)?\s*(.+?)(?:으로|로)\s*(?:바꿔|변경)(?:\s*해)?\s*(?:줘|주세요)?", text)
    if match and not re.search(r"일정|스케줄|캘린더", match[1]):
        return call("manage_program", operation="rename", name=match[1].strip(), new_name=match[2].strip())
    match = re.fullmatch(r"([1-5])번첨부(?:를|을)?(?:제거|삭제)" + suffix, key)
    if match:
        return call("control_app", action="remove_attachment", index=int(match[1]))
    match = re.fullmatch(r"(?:(오늘|내일|모레|\d{4}-\d{2}-\d{2})\s*)?(.*?)\s*(?:일정|스케줄)(?:을|를)?\s*(?:삭제|지워)(?:\s*해)?\s*(?:줘|주세요)?", text)
    if match:
        day, title = match.groups()
        if options.get("ai_mode", "off") != "off" and re.fullmatch(r"그|이|방금\s*말한|아까\s*말한|첫\s*번째|두\s*번째", title.strip()):
            return None
        return call("delete_event", **selector_words((day or "") + " " + title, view))
    return None
