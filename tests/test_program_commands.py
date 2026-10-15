import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant.actions import validate_calls
from assistant.core import UserError
from assistant.ollama import tools
from assistant.programs import Programs
from assistant.service import AssistantService
from assistant.settings import save_settings


class RegisteredProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.programs = Programs(self.temp.name)
        self.exe = Path(self.temp.name) / "My Chrome app.exe"
        self.exe.write_bytes(b"never execute this fixture")

    def test_new_catalog_has_no_preinstalled_programs_or_launch_tool(self):
        self.assertEqual(self.programs.catalog(), [])
        self.assertFalse(self.programs.path.exists())
        self.assertNotIn("launch_program", [item["function"]["name"] for item in tools([])])
        with patch("assistant.programs.subprocess.Popen") as popen:
            for identifier in ("notepad", "calculator", "explorer", "edge"):
                with self.subTest(identifier=identifier), self.assertRaises(UserError):
                    self.programs.launch(identifier)
            popen.assert_not_called()

    def test_registered_spoken_name_and_path_survive_restart(self):
        identifier = self.programs.register("크롬", self.exe)
        restarted = Programs(self.temp.name)
        self.assertEqual(restarted.catalog(), [{"id": identifier, "name": "크롬"}])
        for text in ("크롬 열어줘", "크롬을 열어 주세요", "크 롬 실행해 줘", "크롬 켜", "크롬 좀 켜줘!", "크롬 시작해줘"):
            with self.subTest(text=text):
                self.assertEqual(restarted.resolve(text)["id"], identifier)
        with patch("assistant.desktop.require_windows"), patch("assistant.programs.subprocess.Popen") as popen:
            restarted.launch(identifier)
            popen.assert_called_once_with([str(self.exe.resolve())], shell=False, cwd=str(self.exe.parent))

    def test_matching_ignores_case_and_spacing_without_fuzzy_launches(self):
        identifier = self.programs.register("My Chrome", self.exe)
        self.assertEqual(self.programs.resolve("mychrome 열어줘")["id"], identifier)
        self.assertEqual(self.programs.resolve("Ｍｙ Ｃｈｒｏｍｅ 실행해줘")["id"], identifier)
        self.assertIsNone(self.programs.resolve("My Chroma 열어줘"))

    def test_registered_name_does_not_execute_questions_negations_or_compound_requests(self):
        self.programs.register("크롬", self.exe)
        for text in ("크롬 열지 마", "크롬 실행하지 마", "크롬 실행 방법 알려줘", "크롬 열어줘 그리고 볼륨 올려",
                     "크롬 열고 계산기 켜줘", "크롬 실행 테스트 일정 추가", "크롬에 대한 뉴스 검색해줘", "크롬"):
            with self.subTest(text=text):
                self.assertIsNone(self.programs.resolve(text))

    def test_duplicate_normalized_names_are_rejected_and_old_ambiguity_is_reported(self):
        self.programs.register("크롬", self.exe)
        with self.assertRaisesRegex(UserError, "이미 등록"):
            self.programs.register("크 롬", self.exe)
        self.programs.register("Chrome", self.exe)
        with self.assertRaisesRegex(UserError, "이미 등록"):
            self.programs.register("CHROME", self.exe)
        saved = self.programs.custom()
        saved["older-duplicate"] = {"name": "크 롬", "path": str(self.exe)}
        self.programs.path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(UserError, "여러 개"):
            self.programs.resolve("크롬 열어줘")

    def test_ai_off_registered_program_executes_without_network(self):
        identifier = self.programs.register("크롬", self.exe)
        service = AssistantService(self.temp.name)
        with patch.object(service.ollama, "request") as ai, patch.object(service.programs, "launch", return_value="크롬 실행 요청") as launch:
            self.assertEqual(service.execute("크롬 열어줘")["message"], "크롬 실행 요청")
            launch.assert_called_once_with(identifier)
            ai.assert_not_called()

    def test_unregistered_program_opens_registration_settings_instead_of_a_default(self):
        service = AssistantService(self.temp.name)
        with patch.object(service.programs, "launch") as launch:
            result = service.execute("계산기 실행해줘")
            self.assertIn("등록되지", result["message"])
            self.assertEqual(result["settings_tab"], "programs")
            launch.assert_not_called()

    def test_ai_on_exact_name_uses_existing_confirmation_and_history_without_model(self):
        identifier = self.programs.register("크롬", self.exe)
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        service = AssistantService(self.temp.name)
        with patch.object(service.ollama, "request") as ai, patch.object(service.programs, "launch", return_value="크롬 실행 요청") as launch:
            result = service.execute("크롬 열어줘")
            self.assertTrue(result["proposal"]["voice_auto"])
            launch.assert_not_called()
            service.confirm_proposal(result["proposal"]["id"])
            launch.assert_called_once_with(identifier)
            ai.assert_not_called()
            self.assertIn("크롬 실행 요청", service.history.messages()[-1]["content"])

    def test_removed_registration_invalidates_an_already_prepared_proposal(self):
        identifier = self.programs.register("크롬", self.exe)
        save_settings(self.temp.name, "", False, ai_mode="local")
        service = AssistantService(self.temp.name)
        result = service.execute("크롬 열어줘")
        self.programs.remove(identifier)
        with patch("assistant.programs.subprocess.Popen") as popen, self.assertRaises(UserError):
            service.confirm_proposal(result["proposal"]["id"])
        popen.assert_not_called()
        with self.assertRaises(UserError):
            validate_calls([{"function": {"name": "launch_program", "arguments": {"app_id": identifier}}}], self.programs)

    def test_special_app_names_work_without_hijacking_other_commands(self):
        identifier = self.programs.register("소리", self.exe)
        self.assertEqual(self.programs.resolve("소리 열어줘")["id"], identifier)
        self.assertIsNone(self.programs.resolve("소리 줄여줘"))

    def test_empty_program_catalog_does_not_turn_sound_commands_into_registration_requests(self):
        service = AssistantService(self.temp.name)
        with patch("assistant.desktop.volume", return_value="음소거 해제") as volume:
            result = service.execute("소리 켜줘")
            volume.assert_called_once_with("unmute", 0)
            self.assertNotIn("open_settings", result)
