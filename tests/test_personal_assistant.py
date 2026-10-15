import datetime as dt
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from assistant.briefing import daily_briefing
from assistant.core import UserError
from assistant.service import AssistantService
from assistant.settings import save_settings
from assistant.speech_text import speech_chunks, spoken_text
from tests.program_fixtures import register_fixture_programs


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = AssistantService(self.temp.name)
        self.calendar = self.service.calendar
        self.now = dt.datetime(2026, 10, 16, 9)

    def event(self, title, start, end):
        return self.calendar.add(title, "2026-10-16T" + start, "2026-10-16T" + end)

    def test_briefing_distinguishes_completed_ongoing_upcoming_and_conflicts(self):
        self.event("아침 회의", "07:00", "08:00")
        self.event("개발 작업", "08:30", "09:30")
        self.event("팀 회의", "09:15", "10:00")
        self.event("점심 약속", "12:00", "13:00")
        result = daily_briefing(self.calendar.list(), self.now)
        for expected in ("현재 09:00", "오늘 일정은 4건", "지난 일정 1건", "진행 중 1건", "남은 일정 2건", "팀 회의", "겹치는 일정"):
            self.assertIn(expected, result)
        self.assertNotIn("아침 회의", result)

    def test_empty_day_mentions_next_saved_event_without_inventing_weather_or_news(self):
        self.calendar.add("다음 회의", "2026-10-17T10:00", "2026-10-17T11:00")
        text = daily_briefing(self.calendar.list(), self.now)
        self.assertIn("오늘 등록된 일정은 없습니다", text)
        self.assertIn("10월 17일 10:00", text)
        self.assertNotIn("날씨", text)

    def test_overnight_and_end_boundary_are_handled(self):
        self.calendar.add("야간 작업", "2026-10-15T23:00", "2026-10-16T09:00")
        text = daily_briefing(self.calendar.list(), self.now)
        self.assertIn("지난 일정 1건", text)
        self.assertIn("진행 중 0건", text)

    def test_overnight_end_displays_the_actual_date(self):
        self.calendar.add("긴 작업", "2026-10-16T08:00", "2026-10-17T01:00")
        self.assertIn("10월 17일 01:00 종료", daily_briefing(self.calendar.list(), self.now))

    def test_direct_briefing_is_read_only_and_available_without_an_ai_api(self):
        with patch("urllib.request.urlopen") as network:
            result = self.service.execute("오늘 브리핑해줘")
        network.assert_not_called()
        self.assertNotIn("proposal", result)
        self.assertIn("오늘", result["message"])
        self.assertEqual(self.calendar.list(), [])

    def test_ai_tool_can_request_briefing_without_action_confirmation(self):
        save_settings(self.temp.name, "", False, ai_mode="local")
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [
                {"function": {"name": "daily_briefing", "arguments": {}}}]}):
            result = self.service.execute("오늘 하루를 정리해 줄래?")
        self.assertNotIn("proposal", result)
        self.assertIn("오늘", result["message"])

    def test_long_schedule_stays_speakable_and_indicates_hidden_entries(self):
        for index in range(8):
            self.event("회의" * 90, f"{10 + index:02}:00", f"{11 + index:02}:00")
        text = daily_briefing(self.calendar.list(), self.now)
        self.assertLess(len(text), 1000)
        self.assertIn("그 외 남은 일정 5건", text)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = AssistantService(self.temp.name)
        self.calendar = self.service.calendar
        self.now = dt.datetime(2026, 10, 16, 9)
        self.event = self.calendar.add("팀 회의", "2026-10-16T09:10", "2026-10-16T10:00")

    def test_window_boundary_and_persistent_deduplication(self):
        self.calendar.add("지난 회의", "2026-10-16T08:59", "2026-10-16T09:30")
        self.calendar.add("나중 회의", "2026-10-16T09:11", "2026-10-16T10:00")
        due = self.service.due_reminders(self.now)
        self.assertEqual(due, [self.event])
        self.assertIn("약 10분 뒤", self.service.consume_reminders(due, self.now))
        restarted = AssistantService(self.temp.name)
        self.assertEqual(restarted.due_reminders(self.now), [])
        self.assertEqual(restarted.consume_reminders(due, self.now), "")

    def test_deleted_rescheduled_and_renamed_events_are_rechecked_before_display(self):
        due = self.service.due_reminders(self.now)
        self.calendar.update(self.event["id"], "새 제목", self.event["start"], self.event["end"])
        self.assertIn("새 제목", self.service.consume_reminders(due, self.now))
        self.calendar.update(self.event["id"], "새 제목", "2026-10-16T09:09", self.event["end"])
        due = self.service.due_reminders(self.now)
        self.assertEqual(len(due), 1)
        self.calendar.delete(self.event["id"])
        self.assertEqual(self.service.consume_reminders(due, self.now), "")

    def test_off_or_shorter_lead_time_does_not_consume_a_pending_notice(self):
        due = self.service.due_reminders(self.now)
        save_settings(self.temp.name, "", False, reminders_enabled=False)
        self.assertEqual(self.service.consume_reminders(due, self.now), "")
        self.assertEqual(self.service.due_reminders(self.now), [])
        save_settings(self.temp.name, "", False, reminders_enabled=True, reminder_minutes=5)
        self.assertEqual(self.service.consume_reminders(due, self.now), "")
        now = self.now + dt.timedelta(minutes=5)
        self.assertIn("약 5분 뒤", self.service.consume_reminders(self.service.due_reminders(now), now))

    def test_recheck_avoids_announcing_an_event_that_started_while_user_was_busy(self):
        due = self.service.due_reminders(self.now)
        self.assertEqual(self.service.consume_reminders(due, self.now + dt.timedelta(minutes=11)), "")

    def test_more_than_five_due_events_can_be_delivered_in_later_batches(self):
        for index in range(7):
            self.calendar.add(str(index), "2026-10-16T09:05", "2026-10-16T09:30")
        first = self.service.due_reminders(self.now)
        self.assertEqual(len(first), 5)
        self.service.consume_reminders(first, self.now)
        self.assertEqual(len(self.service.due_reminders(self.now)), 3)

    def test_busy_database_does_not_block_the_ui_for_the_default_five_seconds(self):
        due = self.service.due_reminders(self.now)
        with self.calendar.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError):
                self.service.consume_reminders(due, self.now)
            self.assertLess(time.monotonic() - started, 1)


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = AssistantService(self.temp.name)
        register_fixture_programs(self.service.programs)
        self.routines = self.service.routines

    def test_named_sequence_reuses_confirmation_and_executes_once_in_order_without_ai(self):
        self.routines.save("작업 준비", ["notepad"], 25, "https://www.example.com")
        actions = []
        with patch.object(self.service.programs, "launch", side_effect=lambda app: actions.append(app) or "실행"), patch("assistant.desktop.volume", side_effect=lambda *a: actions.append("volume") or "볼륨"), patch("assistant.actions.webbrowser.open", side_effect=lambda *a, **k: actions.append("web") or True), patch("urllib.request.urlopen") as network:
            result = self.service.execute("작업 준비해줘")
            self.assertEqual(actions, [])
            self.assertTrue(result["proposal"]["voice_auto"])
            self.service.confirm_proposal(result["proposal"]["id"])
            self.assertEqual(actions, ["notepad", "volume", "web"])
            network.assert_not_called()
            with self.assertRaises(UserError):
                self.service.confirm_proposal(result["proposal"]["id"])

    def test_registration_edit_restart_delete_and_normalized_duplicate_names(self):
        identifier = self.routines.save("작업 준비", [], 20)
        with self.assertRaises(UserError):
            self.routines.save("작업준비", [], 30)
        self.routines.save("회의 모드", ["notepad"], identifier=identifier)
        restarted = AssistantService(self.temp.name)
        self.assertEqual(restarted.routines.catalog()[0]["name"], "회의 모드")
        restarted.routines.remove(identifier)
        self.assertEqual(restarted.routines.catalog(), [])
        self.assertEqual(len(restarted.programs.catalog()), 2)

    def test_empty_oversized_or_unknown_program_recipes_are_rejected(self):
        for ids, volume, url in [([], None, ""), (["not-registered"], 30, ""), (["notepad"] * 6, 30, ""), ([], 101, ""), ([], 30, "file:///C:/Windows")]:
            with self.subTest(ids=ids, volume=volume, url=url), self.assertRaises(UserError):
                self.routines.save("테스트", ids, volume, url)
        self.assertFalse(self.routines.path.exists())

    def test_missing_program_is_detected_before_any_step_executes(self):
        self.routines.save("작업 준비", ["notepad"], 30)
        self.service.programs.remove("notepad")
        with patch("assistant.desktop.volume") as volume, self.assertRaises(UserError):
            self.service.execute("작업 준비 실행해줘")
        volume.assert_not_called()

    def test_ambiguous_mentions_negation_and_partial_names_are_not_run(self):
        self.routines.save("작업 준비", [], 30)
        for text in ("작업 준비하지 마", "작업 준비 방법", "작업 준 실행해줘", "작업 준비라는 말은 무슨 뜻이야"):
            with self.subTest(text=text):
                self.assertIsNone(self.routines.resolve(text))

    def test_program_and_routine_name_collision_never_picks_an_arbitrary_action(self):
        with self.assertRaises(UserError):
            self.routines.save("메모장", [], 30)
        self.routines.save("작업 준비", [], 30)
        self.service.programs.rename("notepad", "작업 준비")
        with patch.object(self.service.programs, "launch") as launch, patch("assistant.desktop.volume") as volume:
            with self.assertRaisesRegex(UserError, "같은 이름"):
                self.service.execute("작업 준비 실행해줘")
            launch.assert_not_called()
            volume.assert_not_called()

    def test_ai_can_use_saved_routine_but_cannot_expand_over_five_actions(self):
        self.routines.save("작업 준비", ["notepad", "calculator"], 25, "https://example.com")
        save_settings(self.temp.name, "", False, ai_mode="local")
        call = {"function": {"name": "run_routine", "arguments": {"name": "작업 준비"}}}
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call]}) as ai:
            result = self.service.execute("집중할 수 있게 준비해 줄래?")
            self.assertIn("메모장", result["proposal"]["summary"])
            self.assertEqual(ai.call_args.kwargs["app_context"]["routines"][0]["name"], "작업 준비")
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call, call]}), self.assertRaises(UserError):
            self.service.execute("두 번 준비해줘")

    def test_disk_recipe_cannot_smuggle_shell_or_recursive_routine_actions(self):
        for tool in ("run_windows_command", "run_routine", "change_settings"):
            self.routines.path.write_text(json.dumps({"fixture": {"name": "위조 루틴", "calls": [{"function": {"name": tool, "arguments": {}}}]}}))
            with self.subTest(tool=tool), self.assertRaises(UserError):
                self.service.execute("위조 루틴 실행해줘")


class SpeechTextTests(unittest.TestCase):
    def test_markdown_is_read_naturally_without_spelling_code_or_link_syntax(self):
        text = "## 안내\n- **회의**는 10:30입니다.\n[참고](https://example.com)\n```python\nprint('hello')\n```"
        spoken = spoken_text(text)
        self.assertIn("회의는 10:30입니다", spoken)
        self.assertIn("참고", spoken)
        self.assertIn("코드 내용은 화면", spoken)
        for fragment in ("##", "**", "https://", "print("):
            self.assertNotIn(fragment, spoken)

    def test_chunks_are_bounded_and_preserve_all_text_in_order(self):
        text = "회의는 오전 10시입니다. 점심은 오후 12시입니다.\n" * 15
        chunks = speech_chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 240 for chunk in chunks))
        self.assertEqual("".join("".join(chunks).split()), "".join(spoken_text(text).split()))

    def test_very_long_speech_has_an_explicit_end_and_does_not_truncate_silently(self):
        self.assertIn("나머지 내용은 화면", spoken_text("긴 답변 " * 1000))
        self.assertLessEqual(len(spoken_text("긴 답변 " * 1000)), 2000)
