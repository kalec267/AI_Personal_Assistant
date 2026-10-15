import datetime as dt
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from assistant.app import LunaApp
from assistant.core import UserError
from assistant.service import AssistantService
from assistant.settings import load_settings, save_settings
from assistant.voice import WindowsVoice, preferred_voice, recognize_once


class ServiceTests(unittest.TestCase):
    def test_native_command_flow_without_http(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AssistantService(folder)
            result = service.execute("내일 오후 3시 팀 회의 일정 추가")
            self.assertEqual(service.calendar.list(), [])
            draft = result["draft"]
            saved = service.calendar.add(draft["title"], draft["start"], draft["end"])
            self.assertIn("팀 회의", service.execute("내일 일정 보여줘")["message"])
            self.assertEqual(AssistantService(folder).calendar.list()[0]["id"], saved["id"])
            with patch("assistant.desktop.launch", return_value="실행됨") as launch:
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


class VoiceTests(unittest.TestCase):
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
            self.assertEqual(json.loads(process.communicate.call_args.args[0]), {"text": text, "voice": "여성 음성"})
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
        self.voice = MagicMock()
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

    def test_voice_recognition_fills_entry_without_launching(self):
        with patch("assistant.desktop.launch") as launch:
            self.app.listen()
            self.wait_for(lambda: not self.app.listening)
            self.assertEqual(self.app.command_var.get(), "메모장 실행")
            launch.assert_not_called()

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
        self.voice.speak.assert_called_with("테스트", "여성 음성")

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

    def test_main_window_has_one_settings_entry_and_no_sound_controls(self):
        self.root.geometry("960x620")
        self.root.update()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for widget in (self.app.entry, self.app.send_button, self.app.settings_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
        self.assertEqual(self.app.settings_button.cget("text"), "설정")
        self.assertFalse(hasattr(self.app, "voice_combo"))
        self.assertFalse(hasattr(self.app, "volume_button"))

    def test_ai_proposal_cancelled_in_native_ui_does_not_launch(self):
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        calls = [{"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.app.messagebox.askyesno", return_value=False), patch("assistant.desktop.launch") as launch:
            self.app.submit("글 쓸 수 있게 메모장 좀 켜 줘")
            self.wait_for(lambda: not self.app.command_busy)
            launch.assert_not_called()
            self.assertEqual(self.app.service._proposals, {})
            self.assertIn("취소", self.app.chat.get("1.0", "end"))

    def test_ai_proposal_runs_after_native_confirmation(self):
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        calls = [{"function": {"name": "launch_program", "arguments": {"app_id": "calculator"}}}]
        with patch.object(self.app.service.ollama, "request", return_value={"content": "", "calls": calls}), patch("assistant.app.messagebox.askyesno", return_value=True), patch("assistant.desktop.launch", return_value="계산기 실행 요청") as launch:
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
        dialog.voice_var.set("local:F4")
        dialog.speak.set(False)
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
        self.app.open_config()
        restored = self.app.config_dialog
        self.assertEqual(restored.volume.get(), 43)
        self.assertTrue(restored.start_volume.get())
        self.assertTrue(restored.remember.get())
        self.assertEqual(restored.history_turns.get(), 4)
        self.assertTrue(restored.windows_access.get())
        restored.close()

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
