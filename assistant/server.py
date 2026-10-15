from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import conversation, desktop
from .core import Calendar, UserError, parse_command
from .programs import Programs

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data_dir):
        super().__init__(address, Handler)
        self.calendar = Calendar(Path(data_dir) / "calendar.sqlite3")
        self.programs = Programs(data_dir)
        self.token = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format, *args):
        # Do not log command bodies, credentials, or user calendar contents.
        pass

    def valid_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (not origin or origin == f"http://{host}") and self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"}

    def send(self, code, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "microphone=(self)")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_request():
            return self.send(403, {"error": "로컬 앱에서 접속해 주세요."})
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send(200, {"status": "ok", "platform": sys.platform})
        if path == "/api/config":
            return self.send(200, {"token": self.server.token, "windows": os.name == "nt", "ai": conversation.enabled(),
                                   "time": dt.datetime.now().astimezone().isoformat(),
                                   "apps": self.server.programs.catalog()})
        if path == "/api/events":
            return self.send(200, self.server.calendar.list())
        if path == "/api/calendar.ics":
            return self.send(200, self.server.calendar.export(), "text/calendar; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="luna-calendar.ics"'})
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in files:
            filename, mime = files[path]
            return self.send(200, (STATIC / filename).read_bytes(), mime)
        return self.send(404, {"error": "페이지를 찾을 수 없습니다."})

    def do_POST(self):
        if not self.valid_request() or not secrets.compare_digest(self.headers.get("X-Luna-Token", ""), self.server.token):
            return self.send(403, {"error": "앱을 새로고침한 뒤 다시 시도해 주세요."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                raise UserError("JSON 요청 크기나 형식이 올바르지 않습니다.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise UserError("올바른 JSON 객체가 필요합니다.")
            path = urlsplit(self.path).path
            if path == "/api/events":
                event = self.server.calendar.add(data.get("title", ""), data.get("start"), data.get("end"))
                return self.send(201, {"event": event, "message": "일정을 저장했어요."})
            if path == "/api/events/delete":
                if not isinstance(data.get("id"), str):
                    raise UserError("일정 ID가 필요합니다.")
                self.server.calendar.delete(data["id"])
                return self.send(200, {"message": "일정을 삭제했어요."})
            if path == "/api/command":
                if not isinstance(data.get("text"), str):
                    raise UserError("명령을 입력해 주세요.")
                program = self.server.programs.resolve(data["text"])
                if program:
                    return self.send(200, {"message": self.server.programs.launch(program["id"])})
                intent = parse_command(data["text"])
                action = intent["action"]
                if action == "create":
                    return self.send(200, {"message": "일정 초안을 만들었어요. 날짜와 시간을 확인하고 저장해 주세요. 기본 소요 시간은 1시간이에요.", "draft": intent})
                if action == "list":
                    events = self.server.calendar.list()
                    if intent["day"]:
                        day = dt.date.fromisoformat(intent["day"])
                        start = day.isoformat() + "T00:00"
                        end = (day + dt.timedelta(days=1)).isoformat() + "T00:00"
                        events = [event for event in events if event["start"] < end and event["end"] > start]
                    message = "저장된 일정이 없어요." if not events else f"일정 {len(events)}개가 있어요. " + " / ".join(
                        f"{event['start'].replace('T', ' ')} {event['title']}" for event in events[:10])
                    return self.send(200, {"message": message})
                if action == "volume":
                    return self.send(200, {"message": desktop.volume(intent["mode"], intent.get("value", 0))})
                return self.send(200, {"message": conversation.answer(data["text"])})
            return self.send(404, {"error": "요청 경로를 찾을 수 없습니다."})
        except (UserError, ValueError, TypeError) as error:
            return self.send(400, {"error": str(error) if isinstance(error, UserError) else "요청 형식이 올바르지 않습니다."})
        except Exception:
            return self.send(500, {"error": "처리 중 오류가 발생했습니다. 데이터 폴더의 쓰기 권한과 서버 상태를 확인해 주세요."})


def main():
    parser = argparse.ArgumentParser(description="Luna personal assistant (local only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "LunaAssistant" if os.name == "nt" else ROOT / ".assistant-data")
    options = parser.parse_args()
    with Server(("127.0.0.1", options.port), options.data_dir) as server:
        print(f"Luna started on local port {server.server_port}. Open the local app in Chrome or Edge.", flush=True)
        print(f"Calendar storage: {options.data_dir.resolve()}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nLuna stopped.")


if __name__ == "__main__":
    main()
