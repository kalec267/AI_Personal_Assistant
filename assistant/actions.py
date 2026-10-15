"""Validate every model proposal before offering it for user confirmation."""
import datetime as dt
import ipaddress
import urllib.parse
import webbrowser

from . import desktop, windows_control
from .core import UserError
from .search import search_spec, search_keywords, open_search


def website_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise UserError("웹 주소가 올바르지 않아요.")
    try:
        url = urllib.parse.urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in (None, 443):
            raise ValueError()
        hostname = url.hostname.encode("idna").decode()
        if hostname.lower() == "localhost" or "." not in hostname:
            raise ValueError()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError()
    except ValueError:
        raise UserError("공개 HTTPS 웹 주소만 열 수 있어요. 로그인 정보가 포함된 주소는 사용하지 마세요.") from None
    return value


def validate_calls(calls, programs, *, allow_windows=False):
    if not isinstance(calls, list) or not 1 <= len(calls) <= 5:
        raise UserError("동작은 한 번에 1~5개까지만 제안할 수 있어요.")
    catalog = {app["id"]: app["name"] for app in programs.catalog()}
    actions = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise UserError("AI 동작 형식이 올바르지 않아 실행하지 않았어요.")
        function = call["function"]
        name, args = function.get("name"), function.get("arguments")
        if not isinstance(args, dict):
            raise UserError("AI 동작 인자가 올바르지 않아요.")
        allowed = {"launch_program": {"app_id"}, "set_volume": {"mode", "value"}, "open_website": {"url"},
                   "media_control": {"command"}, "create_event": {"title", "start", "end"}, "list_events": {"day"},
                   "run_windows_command": {"command", "description"}, "search_web": {"query", "provider", "kind"}}
        if not isinstance(name, str) or name not in allowed or args.keys() - allowed[name]:
            raise UserError("지원하지 않는 동작이나 인자가 포함되어 실행하지 않았어요.")
        if name == "launch_program":
            identifier = args.get("app_id")
            if not isinstance(identifier, str) or identifier not in catalog:
                raise UserError("AI가 등록되지 않은 프로그램을 요청했어요. 먼저 앱에서 등록해 주세요.")
            summary = f"프로그램 실행: {catalog[identifier]}"
        elif name == "set_volume":
            mode, value = args.get("mode"), args.get("value", 0)
            if not isinstance(mode, str) or mode not in {"set", "up", "down", "mute", "unmute"} or type(value) is not int or not 0 <= value <= 100:
                raise UserError("AI 볼륨 값이 올바르지 않습니다.")
            if mode == "set" and "value" not in args:
                raise UserError("볼륨 수치가 필요합니다.")
            summary = {"set": f"볼륨 {value}%", "up": "볼륨 10%p 높이기", "down": "볼륨 10%p 낮추기", "mute": "음소거", "unmute": "음소거 해제"}[mode]
        elif name == "open_website":
            summary = "웹사이트 열기: " + website_url(args.get("url"))
        elif name == "media_control":
            command = args.get("command")
            if not isinstance(command, str) or command not in {"play_pause", "next", "previous"}:
                raise UserError("지원하지 않는 미디어 명령입니다.")
            summary = {"play_pause": "미디어 재생·일시정지", "next": "다음 곡", "previous": "이전 곡"}[command]
        elif name == "create_event":
            if len(calls) != 1:
                raise UserError("일정 추가는 다른 명령과 나누어 요청해 주세요.")
            if not all(isinstance(args.get(key), str) for key in ("title", "start", "end")):
                raise UserError("일정 제목과 시작·종료 시간이 필요합니다.")
            try:
                start, end = dt.datetime.fromisoformat(args["start"]), dt.datetime.fromisoformat(args["end"])
                if start.tzinfo or end.tzinfo or end <= start or not 1 <= len(args["title"].strip()) <= 200:
                    raise ValueError()
            except ValueError:
                raise UserError("AI 일정 날짜·시간이 올바르지 않아요. 구체적인 날짜와 시간을 다시 알려 주세요.") from None
            summary = f"일정 초안: {args['title']} · {args['start']} — {args['end']}"
        elif name == "search_web":
            spec = search_spec(args.get("query"), args.get("provider"), args.get("kind"))
            args = dict(args, query=search_keywords(spec["query"]))
            spec = search_spec(args["query"], spec["provider"], spec["kind"])
            summary = "검색 결과 열기: " + spec["label"]
        elif name == "run_windows_command":
            if not allow_windows:
                raise UserError("Windows 고급 제어가 꺼져 있어요. 설정 → Windows 제어에서 켜고 저장하세요.")
            command = windows_control.validate_command(args.get("command"))
            description = args.get("description")
            if not isinstance(description, str) or not 1 <= len(description.strip()) <= 300 or any(ord(char) < 32 for char in description):
                raise UserError("Windows 명령의 작업 설명이 올바르지 않습니다.")
            summary = f"Windows 작업: {description}\nPowerShell 명령 전체:\n{command}"
        else:
            day = args.get("day")
            if day is not None:
                try:
                    if not isinstance(day, str):
                        raise ValueError()
                    dt.date.fromisoformat(day)
                except ValueError:
                    raise UserError("AI 조회 날짜가 올바르지 않습니다.") from None
            summary = f"일정 조회: {day or '전체'}"
        actions.append({"name": name, "args": dict(args), "summary": summary})
    return actions


def perform(action, programs, *, allow_windows=False):
    name, args = action["name"], action["args"]
    if name == "launch_program":
        return programs.launch(args["app_id"])
    if name == "set_volume":
        return desktop.volume(args["mode"], args.get("value", 0))
    if name == "open_website":
        url = website_url(args["url"])
        if not webbrowser.open(url, new=2):
            raise UserError("기본 브라우저에서 사이트를 열지 못했어요.")
        return "웹사이트 열기를 요청했어요."
    if name == "media_control":
        return desktop.media(args["command"])
    if name == "search_web":
        return open_search(args["query"], args["provider"], args["kind"])
    if name == "run_windows_command":
        if not allow_windows:
            raise UserError("Windows 고급 제어가 꺼져 있어 실행하지 않았어요.")
        return windows_control.run_command(args["command"])
    raise UserError("지원하지 않는 실행 동작입니다.")
