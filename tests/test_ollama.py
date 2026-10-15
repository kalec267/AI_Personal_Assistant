import json
import os
import tempfile
import urllib.error
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from assistant.actions import validate_calls, website_url
from tests.program_fixtures import register_fixture_programs
from assistant.core import UserError
from assistant.credentials import Credentials
from assistant.local_voice import LocalVoice, install_model
from assistant.ollama import Ollama
from assistant.programs import Programs
from assistant.service import AssistantService
from assistant.settings import save_settings, load_settings


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.credentials = MagicMock()
        self.credentials.get.return_value = "unit-test-placeholder"
        self.client = Ollama(self.credentials)

    def response(self, urlopen, payload):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()

    def test_local_request_never_sends_or_reads_cloud_key(self):
        with patch("assistant.ollama.urllib.request.urlopen") as network:
            self.response(network, {"message": {"content": "안녕하세요"}})
            self.assertEqual(self.client.request("local", "qwen3:4b", "안녕", []) ["content"], "안녕하세요")
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            self.assertIsNone(request.get_header("Authorization"))
            self.credentials.get.assert_not_called()
            payload = json.loads(request.data)
            self.assertFalse(payload["stream"])
            self.assertIn("tools", payload)

    def test_cloud_key_is_only_in_auth_header(self):
        with patch("assistant.ollama.urllib.request.urlopen") as network:
            self.response(network, {"message": {"tool_calls": [call("launch_program", app_id="notepad")]}})
            result = self.client.request("cloud", "gpt-oss:120b", "글 좀 쓰게 메모장 켜줘", [{"id": "notepad", "name": "메모장"}])
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, "https://ollama.com/api/chat")
            self.assertEqual(request.get_header("Authorization"), "Bearer unit-test-placeholder")
            self.assertNotIn(b"unit-test-placeholder", request.data)
            self.assertEqual(result["calls"][0]["function"]["name"], "launch_program")

    def test_auth_and_quota_errors_do_not_expose_server_body(self):
        for code, expected in [(401, "인증"), (403, "인증"), (429, "한도"), (404, "모델")]:
            with self.subTest(code=code), patch("assistant.ollama.urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://ollama.com/api/chat", code, "sensitive upstream detail", {}, None)):
                with self.assertRaisesRegex(UserError, expected) as result:
                    self.client.request("cloud", "gpt-oss:120b", "안녕", [])
                self.assertNotIn("sensitive", str(result.exception))

    def test_malformed_or_oversized_actions_rejected(self):
        payloads = [[], {"message": {}}, {"message": {"tool_calls": "not an array"}},
                    {"message": {"tool_calls": [call("launch_program", app_id="notepad")] * 6}}]
        for payload in payloads:
            with self.subTest(payload=payload), patch("assistant.ollama.urllib.request.urlopen") as network:
                self.response(network, payload)
                with self.assertRaises(UserError):
                    self.client.request("local", "qwen3:4b", "실행", [])

    def test_auth_error_names_the_selected_provider_and_http_status(self):
        for mode, expected in [("cloud", "Ollama 클라우드 인증 실패"), ("local", "Ollama 로컬 서버 접근 거부")]:
            with self.subTest(mode=mode), patch("assistant.ollama.urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://example.invalid", 403, "private upstream detail", {}, None)):
                with self.assertRaises(UserError) as error:
                    self.client.request(mode, "qwen3:4b", "안녕", [])
                self.assertIn(expected, str(error.exception))
                self.assertIn("HTTP 403", str(error.exception))
                self.assertNotIn("private", str(error.exception))


class ProviderRoutingTests(unittest.TestCase):
    def test_ai_off_never_contacts_openai_even_with_legacy_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "unit-test-placeholder"}), patch("urllib.request.urlopen") as network:
            service = AssistantService(folder)
            result = service.execute("안녕하세요")
            self.assertTrue(result["open_settings"])
            self.assertIn("Ollama", result["message"])
            self.assertNotIn("LUNA_OPENAI_API_KEY", result["message"])
            network.assert_not_called()

    def test_selected_ollama_route_ignores_legacy_openai_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "unit-test-placeholder"}), patch("urllib.request.urlopen") as network:
            save_settings(folder, "", False, ai_mode="cloud", ollama_model="gpt-oss:120b")
            service = AssistantService(folder)
            with patch.object(service.ollama, "request", return_value={"content": "안녕하세요", "calls": []}) as ollama:
                self.assertEqual(service.execute("안녕")["message"], "안녕하세요")
                self.assertEqual(ollama.call_args.args[:2], ("cloud", "gpt-oss:120b"))
            network.assert_not_called()


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AssistantService(self.temp.name)
        register_fixture_programs(self.service.programs)
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")

    def tearDown(self):
        self.temp.cleanup()

    def propose(self, calls):
        with patch.object(self.service.ollama, "request", return_value={"content": "실행 완료라는 잘못된 AI 문구", "calls": calls}):
            return self.service.execute("글 쓰게 메모장을 켜고 소리를 25퍼센트로 맞춰줘")

    def test_multiple_actions_wait_for_confirmation_and_only_run_once(self):
        with patch("assistant.programs.Programs.launch", return_value="메모장 요청") as launch, patch("assistant.desktop.volume", return_value="볼륨 변경") as volume:
            result = self.propose([call("launch_program", app_id="notepad"), call("set_volume", mode="set", value=25)])
            self.assertNotIn("실행 완료", result["message"])
            launch.assert_not_called()
            volume.assert_not_called()
            identifier = result["proposal"]["id"]
            self.assertIn("25%", result["proposal"]["summary"])
            completed = self.service.confirm_proposal(identifier)
            launch.assert_called_once_with("notepad")
            volume.assert_called_once_with("set", 25)
            self.assertIn("볼륨 변경", completed["message"])
            with self.assertRaises(UserError):
                self.service.confirm_proposal(identifier)

    def test_cancel_and_expiration_have_no_side_effects(self):
        with patch("assistant.programs.Programs.launch") as launch:
            result = self.propose([call("launch_program", app_id="notepad")])
            identifier = result["proposal"]["id"]
            self.service.discard_proposal(identifier)
            with self.assertRaises(UserError):
                self.service.confirm_proposal(identifier)
            result = self.propose([call("launch_program", app_id="notepad")])
            identifier = result["proposal"]["id"]
            _, actions, turn = self.service._proposals[identifier]
            self.service._proposals[identifier] = (0, actions, turn)
            with self.assertRaises(UserError):
                self.service.confirm_proposal(identifier)
            launch.assert_not_called()

    def test_entire_plan_validated_before_any_action(self):
        plans = [[call("launch_program", app_id="notepad"), call("shell", command="anything")],
                 [call("set_volume", mode="set", value=True)],
                 [call("set_volume", mode="set", value=101)],
                 [call("launch_program", app_id="notepad", args=["unexpected"])],
                 [call("launch_program", app_id="not registered")]]
        with patch("assistant.programs.Programs.launch") as launch:
            for plan in plans:
                with self.subTest(plan=plan), self.assertRaises(UserError):
                    self.propose(plan)
            launch.assert_not_called()
            self.assertEqual(self.service._proposals, {})

    def test_failure_stops_later_actions_and_reports_partial_completion(self):
        result = self.propose([call("launch_program", app_id="notepad"), call("set_volume", mode="set", value=30), call("media_control", command="next")])
        with patch("assistant.programs.Programs.launch", return_value="메모장 실행 요청 완료"), patch("assistant.desktop.volume", side_effect=UserError("장치 없음")), patch("assistant.desktop.media") as media:
            completed = self.service.confirm_proposal(result["proposal"]["id"])
            self.assertIn("메모장 실행 요청 완료", completed["message"])
            self.assertIn("중단", completed["message"])
            media.assert_not_called()

    def test_calendar_model_result_still_needs_form_confirmation(self):
        result = self.propose([call("create_event", title="회의", start="2026-10-16T15:00", end="2026-10-16T16:00")])
        self.assertEqual(result["draft"]["title"], "회의")
        self.assertEqual(self.service.calendar.list(), [])
        self.assertNotIn("proposal", result)

    def test_voice_preferences_do_not_clear_ai_settings(self):
        save_settings(self.temp.name, "local:F2", True)
        result = load_settings(self.temp.name)
        self.assertEqual(result["ai_mode"], "local")
        self.assertEqual(result["ollama_model"], "qwen3:4b")


class ProgramsTests(unittest.TestCase):
    def test_custom_launch_accepts_no_model_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test app.exe"
            path.write_bytes(b"unit-test-fixture")
            programs = Programs(folder)
            identifier = programs.register("내 편집기", path)
            actions = validate_calls([call("launch_program", app_id=identifier)], programs)
            self.assertIn("내 편집기", actions[0]["summary"])
            with patch("assistant.desktop.require_windows"), patch("assistant.programs.subprocess.Popen") as process:
                programs.launch(identifier)
                self.assertEqual(process.call_args.args[0], [str(path)])
                self.assertFalse(process.call_args.kwargs["shell"])
            programs.remove(identifier)
            self.assertTrue(path.exists())
            with self.assertRaises(UserError):
                validate_calls([call("launch_program", app_id=identifier)], programs)

    def test_website_scheme_and_credentials_are_restricted(self):
        self.assertEqual(website_url("https://www.youtube.com/"), "https://www.youtube.com/")
        for url in ["file:///C:/Windows", "javascript:alert(1)", "http://example.com", "https://user:password@example.com", "https://127.0.0.1/", "https://example.com:8443", "https://example.com/\n"]:
            with self.subTest(url=url), self.assertRaises(UserError):
                website_url(url)


class CredentialsTests(unittest.TestCase):
    def test_encrypted_storage_contains_no_cleartext_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"OLLAMA_API_KEY": "", "LUNA_OLLAMA_API_KEY": ""}):
            store = Credentials(folder)
            with patch("assistant.credentials._crypt", side_effect=lambda data, decrypt=False: data[::-1]):
                store.set("unit-test-placeholder", persist=True)
                self.assertNotIn(b"unit-test-placeholder", store.path.read_bytes())
                self.assertEqual(Credentials(folder).get(), "unit-test-placeholder")
                self.assertNotIn("placeholder", store.status())
                store.delete()
                self.assertFalse(store.path.exists())

    def test_session_only_key_is_never_written(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Credentials(folder)
            store.set("unit-test-placeholder", persist=False)
            self.assertEqual(store.get(), "unit-test-placeholder")
            self.assertEqual(list(Path(folder).iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI requires Windows")
    def test_real_windows_dpapi_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"OLLAMA_API_KEY": "", "LUNA_OLLAMA_API_KEY": ""}):
            store = Credentials(folder)
            store.set("unit-test-placeholder", persist=True)
            self.assertNotIn(b"unit-test-placeholder", store.path.read_bytes())
            self.assertEqual(Credentials(folder).get(), "unit-test-placeholder")


class LocalVoiceTests(unittest.TestCase):
    def test_wrong_digest_never_installs_model(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "wrong-model.tar.bz2"
            archive.write_bytes(b"untrusted model")
            with self.assertRaisesRegex(UserError, "SHA-256"):
                install_model(folder, archive)
            self.assertEqual(list((Path(folder) / "models").iterdir()), [])

    def test_synthesis_uses_korean_female_style_and_no_network(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = LocalVoice(folder)
            engine = MagicMock()
            engine.generate.return_value = SimpleNamespace(samples=[0.1, -0.2, 0.3], sample_rate=44100)
            config = SimpleNamespace(extra={})
            module = SimpleNamespace(GenerationConfig=lambda: config)
            output = Path(folder) / "sample.wav"
            with patch("urllib.request.urlopen", side_effect=AssertionError("must stay offline")), patch.object(voice, "_engine", return_value=engine), patch("assistant.local_voice.importlib.import_module", return_value=module):
                voice.synthesize("안녕하세요", "local:F3", output, speed=1.35)
            self.assertEqual(config.sid, 2)
            self.assertEqual(config.speed, 1.35)
            self.assertEqual(config.extra["lang"], "ko")
            with wave.open(str(output)) as reader:
                self.assertEqual(reader.getframerate(), 44100)
                self.assertEqual(reader.getnframes(), 3)


if __name__ == "__main__":
    unittest.main()
