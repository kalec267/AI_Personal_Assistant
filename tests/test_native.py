import datetime as dt
import functools
import gc
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from assistant.app import LunaApp
from assistant.attachments import Attachment
from tests.program_fixtures import register_fixture_programs
from tests.test_openai import reply, respond, tool_call
from assistant.core import UserError
from assistant.programs import Programs
from assistant.service import AssistantService
from assistant.settings import load_settings, save_settings
from assistant.voice import WindowsVoice, VoiceRouter, preferred_voice, recognize_once
from assistant.wake_word import WakeListener


class ServiceTests(unittest.TestCase):
    def test_context_reset_cancels_pending_spoken_calendar_save(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AssistantService(folder)
            draft = service.execute("내일 오후 3시 팀 회의 일정 추가")
            service.clear_conversation()
            with self.assertRaisesRegex(UserError, "자동 저장을 취소"):
                service.save_draft(draft["draft"], generation=draft["history_generation"])
            self.assertEqual(service.calendar.list(), [])

    def test_native_command_flow_without_http(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AssistantService(folder)
            register_fixture_programs(service.programs)
            result = service.execute("내일 오후 3시 팀 회의 일정 추가")
            self.assertEqual(service.calendar.list(), [])
            draft = result["draft"]
            saved = service.calendar.add(draft["title"], draft["start"], draft["end"])
            self.assertIn("팀 회의", service.execute("내일 일정 보여줘")["message"])
            self.assertEqual(AssistantService(folder).calendar.list()[0]["id"], saved["id"])
            with patch("assistant.programs.Programs.launch", return_value="실행됨") as launch:
                self.assertEqual(service.execute("계산기 실행")["message"], "실행됨")
                launch.assert_called_once_with("calculator")
            with patch("assistant.desktop.volume", return_value="변경됨") as volume:
                service.execute("음소거 해제")
                volume.assert_called_once_with("unmute", 0)


class SettingsTests(unittest.TestCase):
    def test_voice_preference_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_settings(folder), {})
            save_settings(folder, "한국어 여성", False)
            self.assertEqual(load_settings(folder), {"voice": "한국어 여성", "speak": False})
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])
            (Path(folder) / "settings.json").write_text("not json")
            self.assertEqual(load_settings(folder), {})

    def test_speed_persists_and_invalid_speed_does_not_remove_other_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            save_settings(folder, "local:F2", True, voice_speed=1.35, ai_mode="local")
            self.assertEqual(load_settings(folder)["voice_speed"], 1.35)
            for invalid in (True, "1.2", 0.49, 2.01, float("nan"), float("inf")):
                with self.subTest(value=invalid), self.assertRaises(ValueError):
                    save_settings(folder, "local:F2", True, voice_speed=invalid)
            data = load_settings(folder)
            data["voice_speed"] = "invalid"
            (Path(folder) / "settings.json").write_text(json.dumps(data), encoding="utf-8")
            result = load_settings(folder)
            self.assertNotIn("voice_speed", result)
            self.assertEqual(result["ai_mode"], "local")


class VoiceTests(unittest.TestCase):
    def test_speed_reaches_both_voice_backends(self):
        with tempfile.TemporaryDirectory() as folder:
            router = VoiceRouter(folder)
            with patch.object(router.local, "speak") as local, patch.object(router.windows, "speak") as windows:
                router.speak("테스트", "local:F2", speed=0.8)
                local.assert_called_once_with("테스트", "local:F2", speed=0.8, generation=ANY)
                router.speak("테스트", "한국어 여성", speed=1.6)
                windows.assert_called_once_with("테스트", "한국어 여성", speed=1.6, generation=ANY)

    def test_real_gender_metadata_is_used_for_default(self):
        voices = [{"name": "Male Korean", "culture": "ko-KR", "gender": "Male"},
                  {"name": "Female English", "culture": "en-US", "gender": "Female"},
                  {"name": "Female Korean", "culture": "ko-KR", "gender": "Female"}]
        self.assertEqual(preferred_voice(voices), "Female Korean")
        self.assertEqual(preferred_voice(voices, "Male Korean"), "Male Korean")
        self.assertEqual(preferred_voice(voices[:2]), "")

    def test_voice_metadata_json_is_read(self):
        driver = WindowsVoice()
        with patch.object(driver, "_command", return_value=["powershell.exe", "list"]), patch("assistant.voice.subprocess.run") as run:
            run.return_value.stdout = json.dumps([{"name": "여성 음성", "culture": "ko-KR", "gender": "Female"}])
            self.assertEqual(driver.list()[0]["name"], "여성 음성")
            self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_tts_text_uses_stdin_not_executable_code(self):
        driver = WindowsVoice()
        text = '한글; "$(not-a-command)"'
        with patch.object(driver, "_command", return_value=["powershell.exe", "speak"]), patch("assistant.voice.subprocess.Popen") as popen:
            process = popen.return_value
            process.returncode = 0
            driver.speak(text, "여성 음성")
            self.assertEqual(json.loads(process.communicate.call_args.args[0]), {"text": text, "voice": "여성 음성", "speed": 1.0})
            self.assertNotIn(text, popen.call_args.args[0])
            self.assertFalse(popen.call_args.kwargs.get("shell", False))

    def test_microphone_capture_is_bounded_and_does_not_execute(self):
        sr = MagicMock()
        recognizer = sr.Recognizer.return_value
        recognizer.recognize_google.return_value = "메모장 실행"
        with patch("assistant.voice.importlib.import_module", return_value=sr):
            self.assertEqual(recognize_once(), "메모장 실행")
        self.assertEqual(recognizer.listen.call_args.kwargs, {"timeout": 5, "phrase_time_limit": 10})
        self.assertEqual(recognizer.recognize_google.call_args.kwargs["language"], "ko-KR")
        self.assertTrue(recognizer.recognize_google.call_args.kwargs["endpoint"].startswith("https://"))

    def test_missing_microphone_dependency_is_actionable(self):
        with patch("assistant.voice.importlib.import_module", side_effect=ImportError):
            with self.assertRaisesRegex(UserError, "setup.bat"):
                recognize_once()

    def test_stop_clears_current_voice_process(self):
        driver = WindowsVoice()
        process = MagicMock()
        process.poll.return_value = None
        driver._process = process
        driver.stop()
        process.terminate.assert_called_once()
        self.assertIsNone(driver._process)

    def test_closed_driver_cannot_spawn_delayed_speech(self):
        driver = WindowsVoice()
        driver.close()
        with patch("assistant.voice.subprocess.Popen") as popen:
            driver.speak("종료 후에는 말하지 않아요", "여성 음성")
            popen.assert_not_called()


class NativeUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            if os.name == "nt":
                raise
            self.skipTest(f"GUI display unavailable: {error}")
        self.temp = tempfile.TemporaryDirectory()
        register_fixture_programs(Programs(self.temp.name))
        self.voice = MagicMock()
        self.voice.prepare.side_effect = lambda *args, **kwargs: functools.partial(self.voice.speak, *args, **kwargs)
        self.app = LunaApp(self.root, self.temp.name, voice=self.voice, recognizer=lambda: "메모장 실행", load_voices=False)
        self.app.speak_var.set(False)
        self.root.update()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
            self.temp.cleanup()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Desktop callback did not complete")

    def test_new_text_command_stops_speech_before_execution_and_reads_new_reply(self):
        self.app.speak_var.set(True)
        self.app.voice_var.set("local:F1")
        calls = []
        self.voice.stop.side_effect = lambda: calls.append("stop")
        self.voice.speak.side_effect = lambda text, *args, **kwargs: calls.append(text)
        with patch.object(self.app.service, "execute", side_effect=lambda *args, **kwargs: calls.append("execute") or {"message": "새 답변"}):
            self.app.submit("새 명령")
            self.wait_for(lambda: "새 답변" in calls)
        self.assertEqual(calls, ["stop", "execute", "새 답변"])

    def attach_wake_listener(self):
        listener = WakeListener(self.temp.name, self.app.microphone_lock)
        listener.set_available(True)
        self.app.wake = listener
        return listener

    def test_wake_invocation_transcribes_and_executes_without_button_or_enter(self):
        listener = self.attach_wake_listener()
        listener.events.put((listener.epoch, "wake", None))
        listener.events.put((listener.epoch, "audio", (b"command", 16000, True)))
        with patch("assistant.app.transcribe_command", return_value="메모장 실행") as recognize, patch("assistant.programs.Programs.launch", return_value="메모장 실행 완료") as launch:
            self.app.poll_wake()
            self.wait_for(lambda: launch.called and not self.app.command_busy and not self.app.listening)
            recognize.assert_called_once_with((b"command", 16000, True))
            launch.assert_called_once_with("notepad")
            self.voice.stop.assert_called()
            self.assertIn("메모장 실행 완료", self.app.chat.get("1.0", "end"))
            self.assertTrue(listener.available.is_set())

    def test_disabling_wake_during_capture_restores_buttons_and_discards_events(self):
        listener = self.attach_wake_listener()
        listener.events.put((listener.epoch, "wake", None))
        self.app.poll_wake()
        self.assertTrue(self.app.listening)
        self.app.settings["wake_enabled"] = False
        self.app.configure_wake()
        listener.events.put((listener.epoch, "audio", (b"stale", 16000, True)))
        with patch("assistant.app.transcribe_command") as recognize:
            self.app.poll_wake()
            recognize.assert_not_called()
        self.assertIsNone(self.app.wake)
        self.assertTrue(listener.stopped.is_set())
        self.assertFalse(self.app.listening)
        self.assertEqual(str(self.app.mic_button["state"]), "normal")

    def test_disabled_wake_discards_transcription_that_finishes_late(self):
        listener = self.attach_wake_listener()
        listener.events.put((listener.epoch, "wake", None))
        listener.events.put((listener.epoch, "audio", (b"stale", 16000, True)))
        started, release = threading.Event(), threading.Event()

        def recognize(_payload):
            started.set()
            release.wait(3)
            return "메모장 실행"

        with patch("assistant.app.transcribe_command", side_effect=recognize), patch("assistant.programs.Programs.launch") as launch:
            self.app.poll_wake()
            try:
                self.assertTrue(started.wait(3))
                self.app.stop_wake()
            finally:
                release.set()
            self.wait_for(lambda: not self.app.callbacks)
            launch.assert_not_called()
            self.assertFalse(self.app.listening)

    def test_wake_pauses_during_speech_and_manual_capture_but_option_allows_interrupt(self):
        listener = self.attach_wake_listener()
        self.app.speech_active = True
        self.app.sync_wake_availability()
        self.assertFalse(listener.available.is_set())
        self.app.settings["wake_during_speech"] = True
        self.app.sync_wake_availability()
        self.assertTrue(listener.available.is_set())
        self.app.listening = True
        self.app.sync_wake_availability()
        self.assertFalse(listener.available.is_set())
        self.app.listening = False
        self.app.speech_active = False
        self.app.sync_wake_availability()
        self.assertTrue(listener.available.is_set())

    def test_old_wake_event_cannot_interrupt_a_new_typed_command(self):
        listener = self.attach_wake_listener()
        epoch = listener.epoch
        self.app.command_busy = True
        self.app.sync_wake_availability()
        listener.events.put((epoch, "wake", None))
        self.app.poll_wake()
        self.assertFalse(self.app.wake_listening)
        self.assertFalse(self.app.listening)

    def test_wake_options_restore_in_settings_after_restart(self):
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.wake_enabled.set(False)
        dialog.wake_during_speech.set(True)
        self.assertTrue(dialog.save())
        self.assertFalse(load_settings(self.temp.name)["wake_enabled"])
        self.assertTrue(load_settings(self.temp.name)["wake_during_speech"])
        dialog.close()
        self.app.open_config()
        self.assertFalse(self.app.config_dialog.wake_enabled.get())
        self.assertTrue(self.app.config_dialog.wake_during_speech.get())
        self.app.config_dialog.close()

    def test_user_registers_chrome_then_voice_and_wake_launch_the_selected_exe(self):
        for item in self.app.service.programs.catalog():
            self.app.service.programs.remove(item["id"])
        self.app.open_config()
        dialog = self.app.config_dialog
        self.assertEqual(dialog.program_list.get_children(), ())
        exe = Path(self.temp.name) / "My Chrome.exe"
        exe.write_bytes(b"never execute fixture")
        dialog.app_name.set("크롬")
        with patch("assistant.config_dialog.filedialog.askopenfilename", return_value=str(exe)):
            dialog.choose_program()
        dialog.register_program()
        self.assertEqual(len(dialog.program_list.get_children()), 1)
        self.assertIn("크롬 열어줘", dialog.program_feedback.get())
        dialog.close()
        self.app.recognizer = lambda: "크롬 열어줘"
        with patch("assistant.desktop.require_windows"), patch("assistant.programs.subprocess.Popen") as popen:
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
            popen.assert_called_once_with([str(exe)], shell=False, cwd=str(exe.parent))
            self.assertIn("크롬 실행을 요청", self.app.chat.get("1.0", "end"))
            popen.reset_mock()
            listener = self.attach_wake_listener()
            listener.events.put((listener.epoch, "wake", None))
            listener.events.put((listener.epoch, "audio", (b"wake-command", 16000, True)))
            with patch("assistant.app.transcribe_command", return_value="크롬 열어줘"):
                self.app.poll_wake()
                self.wait_for(lambda: popen.called and not self.app.listening and not self.app.command_busy)
            popen.assert_called_once_with([str(exe)], shell=False, cwd=str(exe.parent))

    def test_voice_deletes_and_restores_schedule_and_updates_calendar_without_confirmation(self):
        day = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        event = self.app.service.calendar.add("팀 회의", day + "T15:00", day + "T16:00")
        self.app.recognizer = lambda: "내일 팀 회의 일정 삭제해줘"
        with patch.object(self.app, "confirm_actions", side_effect=AssertionError("no second voice confirmation")):
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
            self.assertEqual(self.app.service.calendar.list(), [])
            self.assertEqual(self.app.tree.get_children(), ())
            self.app.recognizer = lambda: "최근 일정 변경 되돌려줘"
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(self.app.service.calendar.list(), [event])
        self.assertEqual(self.app.day_var.get(), day)
        self.assertEqual(len(self.app.tree.get_children()), 1)

    def test_ambiguous_voice_delete_speaks_a_clarification_and_keeps_both_events(self):
        day = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        for hour in (15, 18):
            self.app.service.calendar.add("팀 회의", f"{day}T{hour}:00", f"{day}T{hour+1}:00")
        self.app.speak_var.set(True)
        self.app.voice_var.set("local:F1")
        self.app.recognizer = lambda: "내일 팀 회의 일정 삭제해줘"
        self.app.listen()
        self.wait_for(lambda: self.voice.speak.called and not self.app.command_busy)
        self.assertIn("2개라", self.voice.speak.call_args.args[0])
        self.assertEqual(len(self.app.service.calendar.list()), 2)

    def test_voice_settings_update_open_dialog_without_erasing_other_unsaved_fields(self):
        self.app.open_config("sound")
        dialog = self.app.config_dialog
        dialog.remember.set(True)
        self.app.recognizer = lambda: "음성 속도 1.4배로 설정해줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(self.app.voice_speed, 1.4)
        self.assertEqual(dialog.voice_speed.get(), 1.4)
        self.assertTrue(dialog.remember.get())
        self.assertEqual(load_settings(self.temp.name)["voice_speed"], 1.4)
        self.app.recognizer = lambda: "설정 저장해줘"
        self.app.listen()
        self.wait_for(lambda: load_settings(self.temp.name).get("remember_chat") is True)
        dialog.close()

    def test_voice_program_registration_rename_and_removal_refresh_the_open_list(self):
        exe = Path(self.temp.name) / "chrome.exe"
        exe.write_bytes(b"never execute")
        self.app.recognizer = lambda: "크롬 등록해줘"
        with patch("assistant.config_dialog.filedialog.askopenfilename", return_value=str(exe)):
            self.app.listen()
            self.wait_for(lambda: any(item["name"] == "크롬" for item in self.app.service.programs.catalog()))
        dialog = self.app.config_dialog
        self.app.recognizer = lambda: "크롬 이름을 브라우저로 바꿔줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        labels = [dialog.program_list.item(key, "values")[0] for key in dialog.program_list.get_children()]
        self.assertIn("브라우저", labels)
        self.app.recognizer = lambda: "브라우저 등록 해제해줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertTrue(exe.is_file())
        self.assertFalse(any(item["name"] == "브라우저" for item in self.app.service.programs.catalog()))
        dialog.close()

    def test_voice_attachment_controls_work_without_entering_document_analysis(self):
        self.app.attachments = [Attachment("a.txt", "data"), Attachment("b.txt", "data")]
        self.app.refresh_attachments()
        self.app.recognizer = lambda: "2번 첨부 제거해줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual([item.name for item in self.app.attachments], ["a.txt"])
        self.app.recognizer = lambda: "첨부 비워줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(self.app.attachments, [])

    def test_voice_new_conversation_clears_history_and_attachments_without_an_extra_prompt(self):
        self.app.service.history.append("이전 질문", "이전 답변", self.app.service.history.generation)
        self.app.attachments = [Attachment("a.txt", "data")]
        self.app.recognizer = lambda: "새 대화 시작해줘"
        with patch("assistant.app.messagebox.askyesno", side_effect=AssertionError("no second voice prompt")):
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(self.app.service.history.messages(), [])
        self.assertEqual(self.app.attachments, [])

    def test_voice_close_app_stops_ui_polling_without_a_destroyed_tk_callback(self):
        self.app.recognizer = lambda: "앱 종료해줘"
        self.app.listen()
        self.wait_for(lambda: self.app.closed)

    def test_ai_can_change_speed_then_preview_with_only_the_final_voice_output(self):
        save_settings(self.temp.name, "local:F1", True, ai_mode="local", ollama_model="qwen3:4b")
        self.app.apply_settings()
        self.app.recognizer = lambda: "새 속도를 적용하고 한 번 들려줘"
        calls = [{"function": {"name": "change_settings", "arguments": {"voice_speed": 1.4}}},
                 {"function": {"name": "control_app", "arguments": {"action": "test_voice"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}):
            self.app.listen()
            self.wait_for(lambda: self.voice.speak.called and not self.app.command_busy)
        self.voice.speak.assert_called_once_with("안녕하세요. 당신의 개인 비서 루나예요.", "local:F1", speed=1.4)

    def test_voice_search_preference_updates_an_open_search_window(self):
        self.app.open_search()
        dialog = self.app.search_dialog
        dialog.query.set("검색어 유지")
        self.app.recognizer = lambda: "검색 엔진 네이버로 바꿔줘"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(dialog.provider.get(), "네이버")
        self.assertEqual(dialog.query.get(), "검색어 유지")
        dialog.close()

    def test_unregistered_program_request_opens_the_programs_tab(self):
        self.app.submit("등록안한브라우저 열어줘")
        self.wait_for(lambda: self.app.config_dialog is not None)
        dialog = self.app.config_dialog
        self.assertEqual(dialog.notebook.index(dialog.notebook.select()), 1)
        self.assertIn("등록되지", self.app.chat.get("1.0", "end"))
        dialog.close()

    def test_microphone_stops_speech_before_capturing_audio(self):
        calls = []
        self.voice.stop.side_effect = lambda: calls.append("stop")
        self.app.recognizer = lambda: calls.append("listen") or ""
        self.app.listen()
        self.wait_for(lambda: not self.app.listening)
        self.assertEqual(calls, ["stop", "listen"])

    def test_cancelled_speech_error_cannot_replace_current_voice_status(self):
        queued = []
        self.app.speak_var.set(True)
        self.app.voice_var.set("local:F1")
        with patch.object(self.app, "run_task", side_effect=lambda *args: queued.append(args)):
            self.app.say("이전 답변")
            self.app.stop_speech()
            self.app.voice_note.set("새 답변 준비 중")
            queued[0][2](UserError("이전 음성 오류"))
            self.assertEqual(self.app.voice_note.get(), "새 답변 준비 중")
            self.app.say("새 답변")
            queued[1][2](UserError("현재 음성 오류"))
            self.assertEqual(self.app.voice_note.get(), "현재 음성 오류")

    def test_command_draft_save_restart_delete_export(self):
        self.app.submit("내일 오후 3시 팀 회의 일정 추가")
        self.wait_for(lambda: self.app.last_dialog is not None)
        self.assertEqual(self.app.service.calendar.list(), [])
        self.app.last_dialog.save()
        self.root.update()
        events = self.app.service.calendar.list()
        self.assertEqual(events[0]["title"], "팀 회의")
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.root.update()
        self.assertTrue(self.app.tree.bbox(events[0]["id"]))
        folder = self.temp.name
        self.app.close()
        self.root = tk.Tk()
        self.app = LunaApp(self.root, folder, voice=self.voice, load_voices=False)
        self.app.day_var.set(events[0]["start"][:10])
        self.app.refresh_events()
        self.assertEqual(len(self.app.tree.get_children()), 1)
        target = Path(folder) / "calendar.ics"
        with patch("assistant.app.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_calendar()
        self.assertIn("SUMMARY:팀 회의", target.read_text(encoding="utf-8"))
        self.app.tree.selection_set(events[0]["id"])
        with patch("assistant.app.messagebox.askyesno", return_value=True):
            self.app.delete_event()
        self.assertEqual(self.app.service.calendar.list(), [])

    def test_voice_recognition_executes_once_without_enter_or_confirmation(self):
        with patch("assistant.programs.Programs.launch", return_value="메모장 실행 완료") as launch, patch("assistant.app.messagebox.askyesno", side_effect=AssertionError("no recognition confirmation")):
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
            self.assertEqual(self.app.command_var.get(), "")
            launch.assert_called_once_with("notepad")
            self.assertIn("메모장 실행 완료", self.app.chat.get("1.0", "end"))

    def test_spoken_calendar_saves_without_form_and_ambiguous_time_does_not_save(self):
        self.app.recognizer = lambda: "내일 오후 3시 팀 회의 일정 추가"
        with patch.object(self.app, "open_event", side_effect=AssertionError("no manual form for complete spoken event")):
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(len(self.app.service.calendar.list()), 1)
        self.assertEqual(self.app.service.calendar.list()[0]["title"], "팀 회의")
        self.app.recognizer = lambda: "내일 3시 다른 회의 일정 추가"
        self.app.listen()
        self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
        self.assertEqual(len(self.app.service.calendar.list()), 1)
        self.assertIn("오전인지 오후", self.app.status_var.get())

    def test_voice_ai_actions_execute_without_proposal_acknowledgement(self):
        self.app.recognizer = lambda: "메모장 켜고 볼륨을 25퍼센트로 설정해 줘"
        save_settings(self.temp.name, "여성 음성", True, ai_mode="local", ollama_model="qwen3:4b", voice_speed=1.2)
        self.app.apply_settings()
        calls = [{"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}},
                 {"function": {"name": "set_volume", "arguments": {"mode": "set", "value": 25}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.programs.Programs.launch", return_value="메모장 실행 완료") as launch, patch("assistant.desktop.volume", return_value="볼륨 25퍼센트") as volume, patch.object(self.app, "confirm_actions", side_effect=AssertionError("no extra confirmation")):
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy and self.voice.speak.called)
            launch.assert_called_once_with("notepad")
            volume.assert_called_once_with("set", 25)
            self.voice.speak.assert_called_once_with("메모장 실행 완료\n볼륨 25퍼센트", "여성 음성", speed=1.2)
            self.assertNotIn("다음 동작을 제안", self.app.chat.get("1.0", "end"))

    def test_voice_windows_command_still_requires_command_review(self):
        self.app.recognizer = lambda: "Windows의 현재 시간 확인해 줘"
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b", windows_control=True)
        calls = [{"function": {"name": "run_windows_command", "arguments": {"command": "Get-Date", "description": "현재 시간 확인"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch.object(self.app, "confirm_actions", return_value=False) as confirm, patch("assistant.windows_control.run_command") as run:
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
            confirm.assert_called_once()
            self.assertFalse(confirm.call_args.args[0]["voice_auto"])
            run.assert_not_called()
            self.assertFalse(self.app.voice_command)

    def test_empty_or_failed_recognition_never_executes_and_restores_buttons(self):
        for recognizer in (lambda: "  ", MagicMock(side_effect=UserError("인식 실패"))):
            self.app.recognizer = recognizer
            with patch.object(self.app.service, "execute") as execute:
                self.app.listen()
                self.wait_for(lambda: not self.app.listening)
                execute.assert_not_called()
                self.assertEqual(str(self.app.send_button.cget("state")), "normal")
                self.assertEqual(str(self.app.mic_button.cget("state")), "normal")

    def test_repeated_mic_and_enter_during_recognition_cannot_duplicate_commands(self):
        release = threading.Event()
        def recognize():
            release.wait(3)
            return "메모장 실행"
        self.app.recognizer = MagicMock(side_effect=recognize)
        with patch("assistant.programs.Programs.launch", return_value="실행 완료") as launch:
            self.app.listen()
            self.app.listen()
            self.app.submit("계산기 실행")
            release.set()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy)
            self.app.recognizer.assert_called_once()
            launch.assert_called_once_with("notepad")

    def test_validation_and_worker_error_recovery(self):
        self.app.open_event()
        dialog = self.app.last_dialog
        dialog.title_var.set("회의")
        dialog.start_var.set("invalid")
        dialog.save()
        self.assertTrue(dialog.error_var.get())
        self.assertEqual(self.app.service.calendar.list(), [])
        dialog.destroy()
        self.app.submit("내일 3시 회의 일정 추가")
        self.wait_for(lambda: not self.app.command_busy)
        self.assertIn("오전인지 오후", self.app.status_var.get())
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")

    def test_selected_female_voice_is_saved_and_reused(self):
        voices = [{"name": "여성 음성", "culture": "ko-KR", "gender": "Female"}]
        self.app._voices_loaded(voices)
        self.assertEqual(self.app.selected_voice(), "여성 음성")
        self.app.persist_settings()
        self.assertEqual(load_settings(self.temp.name)["voice"], "여성 음성")
        self.app.say("테스트", force=True)
        self.wait_for(lambda: self.voice.speak.called)
        self.voice.speak.assert_called_with("테스트", "여성 음성", speed=1.0)

    def test_close_with_pending_job_discards_ui_callbacks(self):
        ready, release, done = threading.Event(), threading.Event(), threading.Event()
        callback = MagicMock()
        def work():
            ready.set()
            release.wait(3)
            done.set()
            return "late result"
        self.app.run_task(work, callback)
        self.assertTrue(ready.wait(1))
        self.app.close()
        release.set()
        self.assertTrue(done.wait(1))
        self.assertEqual(self.app.callbacks, {})
        callback.assert_not_called()

    def test_closed_tk_variable_cycle_is_not_finalized_by_document_worker(self):
        import sys
        failures, collectors = [], []
        variable = tk.StringVar(master=self.root, value="닫힌 설정")
        cycle = [variable]
        cycle.append(cycle)
        del variable, cycle
        main_thread = threading.get_ident()
        def on_gc(phase, _info):
            if phase == "start":
                collectors.append(threading.get_ident())
        def allocate():
            # Parsing documents allocates many temporary cyclic containers.
            for _ in range(5000):
                item = []
                item.append(item)
            return True
        gc.callbacks.append(on_gc)
        try:
            with patch.object(sys, "unraisablehook", side_effect=failures.append):
                done = []
                self.app.run_task(allocate, done.append)
                self.wait_for(lambda: bool(done))
                gc.collect()
            self.assertFalse(failures)
            self.assertTrue(collectors)
            self.assertTrue(all(thread == main_thread for thread in collectors))
        finally:
            gc.callbacks.remove(on_gc)

    def test_main_window_has_one_settings_entry_and_no_sound_controls(self):
        self.root.geometry("960x620")
        self.root.update()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for widget in (self.app.entry, self.app.send_button, self.app.settings_button, self.app.attach_button, self.app.summary_button, self.app.search_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
        self.assertEqual(self.app.settings_button.cget("text"), "설정")
        self.assertFalse(hasattr(self.app, "voice_combo"))
        self.assertFalse(hasattr(self.app, "volume_button"))

    def test_ai_proposal_cancelled_in_native_ui_does_not_launch(self):
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        calls = [{"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.app.messagebox.askyesno", return_value=False), patch("assistant.programs.Programs.launch") as launch:
            self.app.submit("글 쓸 수 있게 메모장 좀 켜 줘")
            self.wait_for(lambda: not self.app.command_busy)
            launch.assert_not_called()
            self.assertEqual(self.app.service._proposals, {})
            self.assertIn("취소", self.app.chat.get("1.0", "end"))

    def test_ai_proposal_runs_after_native_confirmation(self):
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        calls = [{"function": {"name": "launch_program", "arguments": {"app_id": "calculator"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.app.messagebox.askyesno", return_value=True), patch("assistant.programs.Programs.launch", return_value="계산기 실행 요청") as launch:
            self.app.submit("계산할 일이 있어 계산기 부탁해")
            self.wait_for(lambda: launch.called and not self.app.command_busy)
            launch.assert_called_once_with("calculator")

    def test_native_key_dialog_masks_and_keeps_session_key_out_of_settings(self):
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.mode.set("Ollama 클라우드 · API 키 필요")
        dialog.mode_changed()
        dialog.key.set("unit-test-placeholder")
        dialog.persist.set(False)
        self.assertTrue(dialog.save())
        self.assertEqual(dialog.key.get(), "")
        self.assertEqual(self.app.service.credentials.get(), "unit-test-placeholder")
        self.assertNotIn("unit-test-placeholder", (Path(self.temp.name) / "settings.json").read_text())
        self.assertEqual(load_settings(self.temp.name)["ai_mode"], "cloud")
        dialog.close()

    def test_openai_settings_keep_keys_and_models_separate_and_restore(self):
        self.app.service.credentials.set("ollama-test-placeholder", False)
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.mode.set("Ollama 로컬 · API 키 불필요")
        dialog.model.set("qwen3:8b")
        dialog.vision_model.set("gemma3:4b")
        self.assertTrue(dialog.save())
        dialog.key.set("unsaved-ollama-placeholder")
        dialog.mode.set("ChatGPT · OpenAI API 키 필요")
        self.assertEqual(dialog.key.get(), "")
        self.assertEqual(dialog.model.get(), "gpt-4.1-mini")
        self.assertEqual(dialog.vision_model.get(), "")
        self.assertIn("OpenAI", dialog.key_label.get())
        self.assertEqual(str(dialog.key_entry.cget("show")), "●")
        dialog.key.set("openai-test-placeholder")
        dialog.persist.set(False)
        dialog.model.set("gpt-4.1")
        dialog.vision_model.set("gpt-4.1-mini")
        self.assertTrue(dialog.save())
        self.assertIn("ChatGPT", self.app.mode_var.get())
        self.assertEqual(self.app.service.openai_credentials.get(), "openai-test-placeholder")
        self.assertEqual(self.app.service.credentials.get(), "ollama-test-placeholder")
        self.assertEqual(dialog.key.get(), "")
        raw = (Path(self.temp.name) / "settings.json").read_text()
        self.assertNotIn("placeholder", raw)
        self.assertEqual(load_settings(self.temp.name)["ollama_model"], "qwen3:8b")
        dialog.mode.set("Ollama 로컬 · API 키 불필요")
        self.assertEqual(dialog.model.get(), "qwen3:8b")
        self.assertEqual(dialog.vision_model.get(), "gemma3:4b")
        dialog.mode.set("ChatGPT · OpenAI API 키 필요")
        self.assertEqual(dialog.model.get(), "gpt-4.1")
        dialog.close()
        self.app.close()
        self.root = tk.Tk()
        self.app = LunaApp(self.root, self.temp.name, voice=self.voice, load_voices=False)
        self.app.open_config()
        restored = self.app.config_dialog
        self.assertEqual(restored.mode.get(), "ChatGPT · OpenAI API 키 필요")
        self.assertEqual(restored.model.get(), "gpt-4.1")
        self.assertEqual(restored.vision_model.get(), "gpt-4.1-mini")
        self.assertEqual(restored.key.get(), "")
        restored.close()

    def test_openai_connection_test_uses_selected_client_without_executing_tools(self):
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.mode.set("ChatGPT · OpenAI API 키 필요")
        dialog.key.set("openai-test-placeholder")
        dialog.persist.set(False)
        with patch("urllib.request.urlopen") as network, patch("assistant.desktop.volume") as volume:
            respond(network, reply(None, [tool_call("set_volume", mode="mute")]))
            dialog.test_connection()
            self.wait_for(lambda: "OpenAI 연결 성공" in dialog.feedback.get())
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer openai-test-placeholder")
            volume.assert_not_called()
            self.assertEqual(self.app.service._proposals, {})
        dialog.close()

    def test_deleting_openai_key_in_settings_preserves_ollama_key(self):
        self.app.service.credentials.set("ollama-test-placeholder", False)
        self.app.service.openai_credentials.set("openai-test-placeholder", False)
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.mode.set("ChatGPT · OpenAI API 키 필요")
        dialog.key.set("unsaved-openai-placeholder")
        with patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "", "OPENAI_API_KEY": ""}):
            dialog.delete_key()
            self.assertEqual(self.app.service.openai_credentials.get(), "")
        self.assertEqual(self.app.service.credentials.get(), "ollama-test-placeholder")
        self.assertEqual(dialog.key.get(), "")
        dialog.close()

    def test_openai_voice_actions_use_existing_auto_execution_and_female_voice(self):
        self.app.recognizer = lambda: "메모장 켜고 볼륨을 25퍼센트로 설정해 줘"
        self.app.service.openai_credentials.set("openai-test-placeholder", False)
        save_settings(self.temp.name, "local:F2", True, ai_mode="openai", voice_speed=1.2)
        self.app.apply_settings()
        calls = [tool_call("launch_program", app_id="notepad"), tool_call("set_volume", mode="set", value=25)]
        with patch("urllib.request.urlopen") as network, patch("assistant.programs.Programs.launch", return_value="메모장 실행 완료") as launch, patch("assistant.desktop.volume", return_value="볼륨 25퍼센트") as volume, patch.object(self.app, "confirm_actions", side_effect=AssertionError("no extra confirmation")):
            respond(network, reply(None, calls))
            self.app.listen()
            self.wait_for(lambda: not self.app.listening and not self.app.command_busy and self.voice.speak.called)
            launch.assert_called_once_with("notepad")
            volume.assert_called_once_with("set", 25)
            self.voice.speak.assert_called_once_with("메모장 실행 완료\n볼륨 25퍼센트", "local:F2", speed=1.2)
            self.assertEqual(network.call_args.args[0].full_url, "https://api.openai.com/v1/chat/completions")

    def test_unconfigured_chat_opens_ollama_settings_without_api_request(self):
        with patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "unit-test-placeholder"}), patch("urllib.request.urlopen") as network:
            self.app.submit("안녕하세요")
            self.wait_for(lambda: self.app.config_dialog is not None)
            self.assertIn("Ollama", self.app.chat.get("1.0", "end"))
            self.assertEqual(self.app.config_dialog.mode.get(), "기본 명령 (AI 끔)")
            network.assert_not_called()
            self.app.config_dialog.close()

    def test_all_user_preferences_restore_after_app_restart(self):
        self.app._voices_loaded([{"name": "local:F4", "culture": "ko-KR", "gender": "Female"}])
        self.app.open_config()
        dialog = self.app.config_dialog
        self.assertEqual([dialog.notebook.tab(tab, "text") for tab in dialog.notebook.tabs()], ["AI · 대화", "프로그램", "소리 · 음성", "Windows 제어"])
        dialog.mode.set("Ollama 로컬 · API 키 불필요")
        dialog.model.set("qwen3:4b")
        dialog.vision_model.set("gemma3:4b")
        dialog.voice_var.set("local:F4")
        dialog.speak.set(False)
        dialog.voice_speed.set(1.4)
        dialog.volume.set(42.8)
        dialog.start_volume.set(True)
        dialog.remember.set(True)
        dialog.history_turns.set(4)
        dialog.windows_access.set(True)
        with patch("assistant.app.desktop.volume") as volume:
            self.assertTrue(dialog.save())
            volume.assert_not_called()
        dialog.close()
        self.app.close()
        self.root = tk.Tk()
        with patch("assistant.app.desktop.volume", return_value="저장 볼륨 43%") as volume:
            self.app = LunaApp(self.root, self.temp.name, voice=self.voice, load_voices=False)
            if os.name == "nt":
                self.wait_for(lambda: volume.called)
                volume.assert_called_once_with("set", 43)
            self.root.update()
        self.assertEqual(self.app.selected_voice(), "local:F4")
        self.assertFalse(self.app.speak_var.get())
        self.assertEqual(self.app.voice_speed, 1.4)
        self.app.open_config()
        restored = self.app.config_dialog
        self.assertEqual(restored.volume.get(), 43)
        self.assertTrue(restored.start_volume.get())
        self.assertTrue(restored.remember.get())
        self.assertEqual(restored.history_turns.get(), 4)
        self.assertTrue(restored.windows_access.get())
        self.assertEqual(restored.vision_model.get(), "gemma3:4b")
        self.assertEqual(restored.voice_speed.get(), 1.4)
        restored.close()

    def test_voice_preview_uses_unsaved_speed_and_save_applies_it(self):
        self.app._voices_loaded([{"name": "local:F2", "culture": "ko-KR", "gender": "Female"}])
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.voice_speed.set(1.65)
        dialog.test_voice()
        self.wait_for(lambda: self.voice.speak.called)
        self.assertEqual(self.voice.speak.call_args.kwargs["speed"], 1.65)
        self.assertNotIn("voice_speed", load_settings(self.temp.name))
        dialog.reset_speed()
        self.assertEqual(dialog.voice_speed.get(), 1.0)
        dialog.voice_speed.set(0.8)
        self.assertTrue(dialog.save())
        self.assertEqual(self.app.voice_speed, 0.8)
        self.assertEqual(load_settings(self.temp.name)["voice_speed"], 0.8)
        dialog.close()

    def test_attach_real_document_summarize_follow_up_and_clear(self):
        path = Path(self.temp.name) / "실적.txt"
        path.write_text("올해 매출은 100억원이며 전년 대비 20% 증가했습니다.", encoding="utf-8")
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        with patch("assistant.app.filedialog.askopenfilenames", return_value=(str(path),)):
            self.app.choose_attachments()
            self.wait_for(lambda: not self.app.command_busy)
        self.assertEqual(self.app.attachments[0].name, "실적.txt")
        self.assertIsNotNone(self.app.attachment_dialog)
        with patch.object(self.app.service.ollama, "request", return_value={"content": "매출 100억원, 20% 성장", "calls": []}) as request:
            self.app.summary_button.invoke()
            self.wait_for(lambda: not self.app.command_busy)
            self.assertIn("100억원", request.call_args.kwargs["context"])
            self.assertTrue(request.call_args.kwargs["analysis"])
            self.app.submit("성장률은 얼마야?")
            self.wait_for(lambda: not self.app.command_busy)
            self.assertIn("20% 성장", request.call_args.kwargs["history"][-1]["content"])
        self.assertIn("100억원", self.app.chat.get("1.0", "end"))
        self.root.geometry("960x620")
        self.root.update()
        self.assertLessEqual(self.app.send_button.winfo_rooty() + self.app.send_button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.assertTrue(self.app.privacy_note.winfo_ismapped())
        panel = self.app.privacy_note.master
        self.assertLessEqual(self.app.privacy_note.winfo_rooty() + self.app.privacy_note.winfo_height(), panel.winfo_rooty() + panel.winfo_height() - 10)
        self.app.clear_attachments()
        self.assertEqual(self.app.attachments, [])
        self.assertEqual(self.app.attachment_dialog.items.get_children(), ())
        self.app.attachment_dialog.close()

    def test_native_search_works_without_ai_and_remembers_service_and_filter(self):
        with patch("assistant.search.webbrowser.open", return_value=True) as browser, patch.object(self.app.service.ollama, "request") as ai:
            self.app.submit("네이버에서 인공지능 동영상 찾아줘")
            self.wait_for(lambda: self.app.search_dialog is not None and "열기를 요청" in self.app.search_dialog.feedback.get())
            self.assertIn("search.naver.com", browser.call_args.args[0])
            self.assertIn("where=video", browser.call_args.args[0])
            ai.assert_not_called()
        self.assertEqual(load_settings(self.temp.name)["search_provider"], "naver")
        self.assertEqual(load_settings(self.temp.name)["search_kind"], "video")
        self.app.search_dialog.close()
        self.app.open_search()
        self.assertEqual(self.app.search_dialog.provider.get(), "네이버")
        self.assertEqual(self.app.search_dialog.kind.get(), "동영상")
        self.app.search_dialog.close()

    def test_windows_command_review_shows_full_script_and_requires_click(self):
        from assistant.command_dialog import CommandDialog
        summary = "문서 폴더 만들기\n" + "Write-Output '한글 테스트'\n" * 100
        dialog = CommandDialog(self.root, summary)
        self.root.update()
        self.assertEqual(dialog.command_text.get("1.0", "end-1c"), summary)
        self.assertFalse(dialog.approved)
        dialog.destroy()
        self.assertFalse(dialog.approved)
        dialog = CommandDialog(self.root, summary)
        dialog.approve()
        self.assertTrue(dialog.approved)

    def test_admin_restart_cancel_keeps_app_running_and_restores_controls(self):
        self.app.open_config()
        dialog = self.app.config_dialog
        with patch("assistant.config_dialog.windows_control.restart_as_admin", side_effect=UserError("UAC 승인 취소")):
            dialog.restart_admin()
            self.wait_for(lambda: "취소" in dialog.windows_feedback.get())
        self.assertFalse(self.app.closed)
        self.assertFalse(self.app.command_busy)
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")
        self.assertEqual(str(self.app.mic_button.cget("state")), "normal")
        dialog.close()

    def test_windows_command_runs_only_after_actual_native_review_approval(self):
        from assistant.command_dialog import CommandDialog
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b", windows_control=True)
        calls = [{"function": {"name": "run_windows_command", "arguments": {"command": "Write-Output '한글'", "description": "한글 출력"}}}]
        for approve in (False, True):
            def show(parent, summary):
                dialog = CommandDialog(parent, summary)
                self.root.after_idle(dialog.approve if approve else dialog.destroy)
                return dialog
            with self.subTest(approve=approve), patch("assistant.command_dialog.CommandDialog", side_effect=show), patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.windows_control.run_command", return_value="PowerShell 실행 완료\n한글") as run:
                self.app.submit("한글 출력해 줘")
                self.wait_for(lambda: not self.app.command_busy)
                if approve:
                    run.assert_called_once_with("Write-Output '한글'")
                else:
                    run.assert_not_called()

    def test_volume_in_settings_does_not_need_ai_connection(self):
        self.app.open_config()
        dialog = self.app.config_dialog
        dialog.volume.set(34.8)
        with patch("assistant.config_dialog.desktop.volume", return_value="볼륨 35퍼센트") as volume, patch.object(self.app.service.ollama, "request") as ai:
            dialog.apply_volume()
            self.wait_for(lambda: "35" in dialog.sound_feedback.get())
            volume.assert_called_once_with("set", 35)
            ai.assert_not_called()
        dialog.geometry("660x600")
        self.root.update()
        self.assertLessEqual(dialog.save_button.winfo_rooty() + dialog.save_button.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
        dialog.close()

    def test_saved_conversation_restores_in_chat_and_new_chat_clears_it(self):
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b", remember_chat=True)
        with patch.object(self.app.service.ollama, "request", return_value={"content": "민수님 반가워요", "calls": []}):
            self.app.service.execute("내 이름은 민수야")
        self.app.close()
        self.root = tk.Tk()
        self.app = LunaApp(self.root, self.temp.name, voice=self.voice, load_voices=False)
        self.root.update()
        self.assertIn("내 이름은 민수야", self.app.chat.get("1.0", "end"))
        self.assertIn("민수님 반가워요", self.app.chat.get("1.0", "end"))
        with patch("assistant.app.messagebox.askyesno", return_value=True):
            self.assertTrue(self.app.new_conversation())
        self.assertEqual(self.app.service.history.messages(), [])
        self.assertNotIn("민수", self.app.chat.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
