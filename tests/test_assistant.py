import datetime as dt
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import conversation, desktop
from tests.program_fixtures import register_fixture_programs
from assistant.core import Calendar, UserError, parse_command
from assistant.programs import Programs
from assistant.server import Server


class CommandTests(unittest.TestCase):
    def test_korean_schedule_and_year_rollover(self):
        command = parse_command("내일 오후 3시 30분 팀 회의 일정 추가해줘", dt.datetime(2026, 12, 31))
        self.assertEqual(command, dict(action="create", title="팀 회의", start="2027-01-01T15:30", end="2027-01-01T16:30"))

    def test_midnight_and_half_hour(self):
        command = parse_command("2026-10-17 오전 12시 반 운동 일정 등록")
        self.assertEqual(command["start"], "2026-10-17T00:30")
        self.assertEqual(command["title"], "운동")

    def test_24_hour_format(self):
        command = parse_command("10월 20일 15:45 병원 일정 추가", dt.datetime(2026, 10, 15))
        self.assertEqual(command["start"], "2026-10-20T15:45")

    def test_missing_or_ambiguous_details_require_clarification(self):
        for text in ["회의 일정 추가", "내일 회의 일정 추가", "내일 3시 회의 일정 추가", "내일 오후 3시 일정 추가",
                     "2월 30일 오후 3시 회의 일정 추가", "내일 25:00 회의 일정 추가", "내일 오후 14시 회의 일정 추가"]:
            with self.subTest(text=text), self.assertRaises(UserError):
                parse_command(text)

    def test_sound_and_app_commands(self):
        self.assertEqual(parse_command("볼륨 30퍼센트"), dict(action="volume", mode="set", value=30))
        self.assertEqual(parse_command("볼륨 올려"), dict(action="volume", mode="up"))
        self.assertEqual(parse_command("소리 줄여"), dict(action="volume", mode="down"))
        self.assertEqual(parse_command("음소거 해제"), dict(action="volume", mode="unmute"))
        self.assertEqual(parse_command("메모장 실행"), dict(action="help"))
        with self.assertRaises(UserError):
            parse_command("볼륨 200퍼센트")

    def test_do_not_execute_negative_or_explanatory_commands(self):
        with self.assertRaises(UserError):
            parse_command("메모장 실행하지 마")
        self.assertEqual(parse_command("메모장 실행 방법 알려줘"), dict(action="help"))
        self.assertEqual(parse_command("powershell.exe 실행"), dict(action="help"))

    def test_relative_schedule_lookup(self):
        self.assertEqual(parse_command("내일 일정 보여줘", dt.datetime(2026, 10, 15)), dict(action="list", day="2026-10-16"))

    def test_desktop_words_in_event_titles_do_not_execute(self):
        for title in ["메모장 실행 테스트", "소리 연구", "음소거 테스트"]:
            with self.subTest(title=title):
                command = parse_command(f"내일 오후 3시 {title} 일정 추가")
                self.assertEqual(command["action"], "create")
                self.assertEqual(command["title"], title)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "calendar.sqlite3"
        self.calendar = Calendar(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_and_delete(self):
        title = "팀 회의 ' ; DROP TABLE events; --"
        event = self.calendar.add(title, "2026-10-16T15:00", "2026-10-16T16:00")
        self.assertEqual(Calendar(self.path).list()[0]["title"], title)
        self.calendar.delete(event["id"])
        self.assertEqual(self.calendar.list(), [])
        with self.assertRaises(UserError):
            self.calendar.delete(event["id"])

    def test_validate_before_inserting(self):
        for title, start, end in [("", "2026-10-16T15:00", "2026-10-16T16:00"),
                                  ("회의", "2026-10-16T15:00", "2026-10-16T14:00"),
                                  ("회의", "bad date", "2026-10-16T16:00"),
                                  ("회의", "2026-10-16T15:00+09:00", "2026-10-16T16:00+09:00")]:
            with self.subTest(title=title, start=start), self.assertRaises(UserError):
                self.calendar.add(title, start, end)
        self.assertEqual(self.calendar.list(), [])

    def test_ics_escaping_folding_and_event_times(self):
        title = "한국어 회의," * 20 + "\n설명;끝\\"
        self.calendar.add(title, "2026-10-16T15:00", "2026-10-16T16:00")
        output = self.calendar.export()
        self.assertIn("DTSTART:20261016T150000", output)
        self.assertIn("DTEND:20261016T160000", output)
        self.assertIn("SUMMARY:" + title.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,"), output.replace("\r\n ", ""))
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in output.split("\r\n")))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(("127.0.0.1", 0), self.temp.name)
        register_fixture_programs(self.server.programs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        combined = {"Content-Type": "application/json", "X-Luna-Token": self.server.token}
        combined.update(headers or {})
        connection.request(method, path, json.dumps(data) if data is not None else None, combined)
        response = connection.getresponse()
        body = response.read()
        result = json.loads(body) if response.getheader("Content-Type", "").startswith("application/json") else body.decode()
        connection.close()
        return response.status, result

    def test_end_to_end_command_confirm_list_export_delete(self):
        status, command = self.request("POST", "/api/command", {"text": "내일 오후 3시 팀 회의 일정 추가"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/events")[1], [])  # No write before user confirmation.
        status, created = self.request("POST", "/api/events", command["draft"])
        self.assertEqual(status, 201)
        self.assertEqual(created["event"]["title"], "팀 회의")
        status, listed = self.request("POST", "/api/command", {"text": "내일 일정 보여줘"})
        self.assertIn("팀 회의", listed["message"])
        self.assertIn("SUMMARY:팀 회의", self.request("GET", "/api/calendar.ics")[1])
        self.assertEqual(self.request("POST", "/api/events/delete", {"id": created["event"]["id"]})[0], 200)
        self.assertEqual(self.request("GET", "/api/events")[1], [])

    def test_csrf_host_origin_and_json_validation(self):
        self.assertEqual(self.request("POST", "/api/command", {"text": "메모장 실행"}, {"X-Luna-Token": "wrong"})[0], 403)
        for headers in [{"Origin": "https://evil.example"}, {"Host": f"evil.example:{self.port}"}, {"Sec-Fetch-Site": "cross-site"}]:
            self.assertEqual(self.request("GET", "/api/config", headers=headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/command", {"text": 123})[0], 400)
        self.assertEqual(self.request("POST", "/api/events", {"title": "회의"})[0], 400)
        self.assertEqual(self.request("GET", "/../../assistant/core.py")[0], 404)

    def test_static_and_health(self):
        self.assertEqual(self.request("GET", "/api/health")[1]["status"], "ok")
        self.assertIn("LUNA", self.request("GET", "/")[1])
        self.assertEqual(self.request("GET", "/app.js")[0], 200)

    def test_day_lookup_includes_overnight_events(self):
        day = dt.date.today()
        previous = day - dt.timedelta(days=1)
        self.server.calendar.add("밤샘 작업", f"{previous}T23:00", f"{day}T02:00")
        self.server.calendar.add("어제만", f"{previous}T21:00", f"{day}T00:00")
        status, result = self.request("POST", "/api/command", {"text": "오늘 일정 보여줘"})
        self.assertEqual(status, 200)
        self.assertIn("밤샘 작업", result["message"])
        self.assertNotIn("어제만", result["message"])

    def test_desktop_dispatch_is_allowlisted(self):
        with patch("assistant.programs.Programs.launch", return_value="메모장 실행 요청") as launch:
            self.assertEqual(self.request("POST", "/api/command", {"text": "메모장 실행"})[0], 200)
            launch.assert_called_once_with("notepad")
        with patch("assistant.desktop.volume", return_value="볼륨 변경") as volume:
            self.assertEqual(self.request("POST", "/api/command", {"text": "볼륨 35퍼센트"})[0], 200)
            volume.assert_called_once_with("set", 35)


class DesktopTests(unittest.TestCase):
    def test_invalid_launch_and_volume_cannot_reach_subprocess(self):
        with tempfile.TemporaryDirectory() as folder, patch("assistant.desktop.require_windows"), patch("assistant.desktop.subprocess.Popen") as popen:
            with self.assertRaises(UserError):
                Programs(folder).launch("cmd /c echo anything")
            popen.assert_not_called()
        with patch("assistant.desktop.require_windows"), patch("assistant.desktop.subprocess.run") as run:
            for mode, value in [("injection", 0), ("set", 101), ("set", "30")]:
                with self.assertRaises(UserError):
                    desktop.volume(mode, value)
            run.assert_not_called()

    def test_driver_parameters_and_response_translation(self):
        with patch("assistant.desktop.require_windows"), patch("assistant.desktop.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), patch("assistant.desktop.subprocess.run") as run:
            run.return_value.stdout = "Volume: 35% / unmuted\n"
            self.assertIn("35퍼센트", desktop.volume("set", 35))
            args = run.call_args.args[0]
            self.assertEqual(args[-4:], ["-Action", "set", "-Value", "35"])
            self.assertNotIn("-ExecutionPolicy", args)


class ConversationTests(unittest.TestCase):
    def test_key_is_optional(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(conversation.enabled())
            self.assertIn("일정", conversation.answer("안녕"))

    def test_optional_ai_response_extraction(self):
        with patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "unit-test-placeholder"}), patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({"output": [{"content": [{"type": "output_text", "text": "안녕하세요"}]}]}).encode()
            self.assertEqual(conversation.answer("안녕"), "안녕하세요")
            self.assertEqual(request.call_args.args[0].full_url, "https://api.openai.com/v1/responses")


if __name__ == "__main__":
    unittest.main()
