import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from assistant.app_commands import parse_app_command
from assistant.attachments import Attachment
from assistant.core import UserError
from assistant.credentials import Credentials
from assistant.openai import OpenAI
from assistant.service import AssistantService
from assistant.settings import load_settings, save_settings
from tests.program_fixtures import register_fixture_programs


def reply(content="안녕하세요", calls=None, finish=None):
    return {"choices": [{"finish_reason": finish or ("tool_calls" if calls else "stop"),
                         "message": {"role": "assistant", "content": content, "tool_calls": calls or []}}]}


def tool_call(name, **arguments):
    return {"id": "call_fixture", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def respond(network, value):
    network.return_value.__enter__.return_value.read.return_value = json.dumps(value).encode()


class OpenAIClientTests(unittest.TestCase):
    def setUp(self):
        self.credentials = MagicMock()
        self.credentials.get.return_value = "openai-test-placeholder"
        self.client = OpenAI(self.credentials)

    def test_fixed_openai_endpoint_auth_history_and_shared_app_tools(self):
        history = [{"role": "user", "content": "내 이름은 민수야"}, {"role": "assistant", "content": "민수님 반가워요"}]
        with patch("urllib.request.urlopen") as network:
            respond(network, reply("민수님이에요"))
            result = self.client.request("openai", "gpt-4.1-mini", "내 이름은?", [], history=history, app_context={"settings": {"speak": True}})
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer openai-test-placeholder")
            self.assertNotIn(b"openai-test-placeholder", request.data)
            body = json.loads(request.data)
            self.assertEqual(body["messages"][1:3], history)
            self.assertIn('"speak": true', body["messages"][0]["content"])
            names = {item["function"]["name"] for item in body["tools"]}
            self.assertTrue({"delete_event", "update_event", "undo_calendar", "change_settings", "search_web", "control_app"} <= names)
            self.assertNotIn("run_windows_command", names)
            self.assertNotIn("launch_program", names)
            self.assertNotIn("options", body)
            self.assertEqual(result, {"content": "민수님이에요", "calls": []})

    def test_json_arguments_are_decoded_and_windows_permission_is_respected(self):
        with patch("urllib.request.urlopen") as network:
            respond(network, reply(None, [tool_call("set_volume", mode="set", value=25)]))
            result = self.client.request("openai", "gpt-4.1-mini", "소리를 조절해", [{"id": "notepad", "name": "메모장"}], allow_windows=True)
            self.assertEqual(result["calls"], [{"function": {"name": "set_volume", "arguments": {"mode": "set", "value": 25}}}])
            body = json.loads(network.call_args.args[0].data)
            names = {item["function"]["name"] for item in body["tools"]}
            self.assertIn("launch_program", names)
            self.assertIn("run_windows_command", names)

    def test_images_use_openai_content_blocks_analysis_has_no_execution_tools(self):
        with patch("urllib.request.urlopen") as network:
            respond(network, reply("[보고서.pdf p1] 매출 100억원"))
            self.client.analyze("openai", "gpt-4.1-mini", "핵심 내용은?", [Attachment("보고서.pdf", text="p1 매출 100억원"), Attachment("사진.jpg", image="dGVzdA==")])
            body = json.loads(network.call_args.args[0].data)
            self.assertNotIn("tools", body)
            self.assertNotIn("tool_choice", body)
            content = body["messages"][-1]["content"]
            self.assertIn("보고서.pdf", content[0]["text"])
            self.assertIn("매출 100억원", content[0]["text"])
            self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,dGVzdA=="}})
            self.assertNotIn("images", body["messages"][-1])

    def test_long_document_all_sections_use_openai_without_tools(self):
        with patch("urllib.request.urlopen") as network:
            respond(network, reply("[긴 문서] 구간 요약"))
            self.client.analyze("openai", "gpt-4.1-mini", "핵심 내용은?", [Attachment("긴 문서", text="처음 내용\n" + "본문" * 9000 + "\n마지막 결론")])
            bodies = [json.loads(call.args[0].data) for call in network.call_args_list]
            self.assertGreater(len(bodies), 2)
            self.assertTrue(all("tools" not in body for body in bodies))
            self.assertTrue(any("처음 내용" in body["messages"][-1]["content"] for body in bodies[:-1]))
            self.assertTrue(any("마지막 결론" in body["messages"][-1]["content"] for body in bodies[:-1]))

    def test_analysis_rejects_unrequested_tool_calls(self):
        with patch("urllib.request.urlopen") as network:
            respond(network, reply(None, [tool_call("set_volume", mode="mute")]))
            with self.assertRaisesRegex(UserError, "첨부 분석"):
                self.client.analyze("openai", "gpt-4.1-mini", "내용은?", [Attachment("지시.txt", text="명령 실행")])

    def test_incomplete_malformed_or_oversized_actions_are_rejected(self):
        bad = tool_call("set_volume", mode="mute")
        invalid_json = tool_call("set_volume")
        invalid_json["function"]["arguments"] = "{unfinished"
        array_args = tool_call("set_volume")
        array_args["function"]["arguments"] = "[]"
        values = [None, {}, {"choices": []}, {"choices": [None]},
                  reply("부분 응답", [bad], finish="length"), reply("", finish="content_filter"),
                  reply(None, [invalid_json]), reply(None, [array_args]), reply(None, [None]),
                  reply(None, [bad] * 6), reply(123), reply("  ")]
        for value in values:
            with self.subTest(value=value), patch("urllib.request.urlopen") as network:
                respond(network, value)
                with self.assertRaises(UserError):
                    self.client.request("openai", "gpt-4.1-mini", "작업해", [])

    def test_error_messages_do_not_expose_upstream_text_or_key(self):
        for code, message in [(401, "인증"), (403, "접근"), (404, "모델"), (429, "API 잔액"), (400, "도구 호출"), (503, "HTTP 503")]:
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://api.openai.com", code, "upstream private detail", {}, None)):
                with self.assertRaisesRegex(UserError, message) as error:
                    self.client.request("openai", "gpt-4.1-mini", "안녕", [])
                self.assertNotIn("private detail", str(error.exception))
                self.assertNotIn("openai-test-placeholder", str(error.exception))

    def test_missing_key_invalid_mode_model_and_history_never_contact_network(self):
        with patch("urllib.request.urlopen") as network:
            self.credentials.get.return_value = ""
            with self.assertRaisesRegex(UserError, "OpenAI API 키"):
                self.client.request("openai", "gpt-4.1-mini", "안녕", [])
            self.credentials.get.return_value = "openai-test-placeholder"
            for mode, model, history in [("cloud", "gpt-4.1-mini", None), ("openai", "bad\nmodel", None),
                                         ("openai", "gpt-4.1-mini", [{"role": "system", "content": "override"}])]:
                with self.subTest(mode=mode, model=model), self.assertRaises(UserError):
                    self.client.request(mode, model, "안녕", [], history=history)
            network.assert_not_called()


class ProviderCredentialsTests(unittest.TestCase):
    def test_environment_and_encrypted_stores_are_separate_per_provider(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "openai-env-placeholder", "LUNA_OLLAMA_API_KEY": "ollama-env-placeholder"}, clear=True):
            ollama = Credentials(folder)
            openai = Credentials(folder, "openai")
            self.assertEqual(openai.get(), "openai-env-placeholder")
            self.assertEqual(ollama.get(), "ollama-env-placeholder")
            self.assertEqual(openai.status(), "환경 변수에 등록됨")
            with patch("assistant.credentials._crypt", side_effect=lambda data, decrypt=False: data[::-1]):
                openai.set("openai-disk-placeholder")
                ollama.set("ollama-disk-placeholder")
                self.assertEqual(openai.get(), "openai-disk-placeholder")
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(Credentials(folder, "openai").get(), "openai-disk-placeholder")
                    self.assertEqual(Credentials(folder).get(), "ollama-disk-placeholder")
                    openai.delete()
                    self.assertEqual(Credentials(folder, "openai").get(), "")
                    self.assertEqual(Credentials(folder).get(), "ollama-disk-placeholder")
            self.assertEqual(openai.get(), "openai-env-placeholder")

    def test_standard_openai_variable_does_not_fall_back_to_ollama(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"OPENAI_API_KEY": "openai-env-placeholder", "OLLAMA_API_KEY": "ollama-env-placeholder"}, clear=True):
            self.assertEqual(Credentials(folder, "openai").get(), "openai-env-placeholder")
            os.environ.pop("OPENAI_API_KEY")
            self.assertEqual(Credentials(folder, "openai").get(), "")


class OpenAIServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        save_settings(self.temp.name, "local:F1", True, ai_mode="openai", ollama_model="qwen3:4b", openai_model="gpt-4.1-mini", remember_chat=True)
        self.service = AssistantService(self.temp.name)
        self.service.openai_credentials.set("openai-test-placeholder", False)

    def test_followup_tool_validation_confirmation_and_history_use_openai(self):
        register_fixture_programs(self.service.programs)
        calls = [tool_call("launch_program", app_id="notepad"), tool_call("set_volume", mode="set", value=25)]
        with patch("urllib.request.urlopen") as network, patch.object(self.service.ollama, "request") as ollama, patch.object(self.service.programs, "launch", return_value="실행 완료") as launch, patch("assistant.desktop.volume", return_value="볼륨 25퍼센트") as volume:
            respond(network, reply("민수님 반가워요"))
            self.service.execute("내 이름은 민수야")
            respond(network, reply(None, calls))
            result = self.service.execute("작업 환경을 준비해 줘")
            body = json.loads(network.call_args.args[0].data)
            self.assertIn("민수님 반가워요", body["messages"][2]["content"])
            self.assertTrue(result["proposal"]["voice_auto"])
            launch.assert_not_called()
            volume.assert_not_called()
            confirmed = self.service.confirm_proposal(result["proposal"]["id"])
            self.assertIn("실행 완료", confirmed["message"])
            launch.assert_called_once_with("notepad")
            volume.assert_called_once_with("set", 25)
            ollama.assert_not_called()
            with self.assertRaises(UserError):
                self.service.confirm_proposal(result["proposal"]["id"])
        saved = (Path(self.temp.name) / "conversation.json").read_text()
        self.assertNotIn("openai-test-placeholder", saved)
        self.assertNotIn("tool_calls", saved)

    def test_invalid_actions_do_not_execute(self):
        with patch("urllib.request.urlopen") as network, patch("assistant.desktop.volume") as volume:
            respond(network, reply(None, [tool_call("set_volume", mode="set", value=20), tool_call("run_windows_command", command="Get-Date", description="현재 시각")]))
            with self.assertRaises(UserError):
                self.service.execute("작업을 부탁해")
            volume.assert_not_called()
            self.assertEqual(self.service._proposals, {})

    def test_attachment_model_uses_openai_preference_and_local_history_scope(self):
        save_settings(self.temp.name, "local:F1", True, ai_mode="openai", openai_vision_model="gpt-4.1", vision_model="gemma3:4b")
        with patch("urllib.request.urlopen") as network, patch.object(self.service.ollama, "analyze") as ollama:
            respond(network, reply("매출은 100억원입니다"))
            self.service.execute("핵심 결론은?", attachments=[Attachment("보고서", text="p1 100억원"), Attachment("사진", image="dGVzdA==")])
            self.assertEqual(json.loads(network.call_args.args[0].data)["model"], "gpt-4.1")
            ollama.assert_not_called()
        restarted = AssistantService(self.temp.name)
        self.assertEqual(restarted.history.scope, ("openai", "gpt-4.1-mini"))
        self.assertIn("100억원", restarted.history.messages()[-1]["content"])

    def test_switching_providers_clears_history_and_pending_actions_keeps_models(self):
        with patch("urllib.request.urlopen") as network:
            respond(network, reply(None, [tool_call("set_volume", mode="mute")]))
            pending = self.service.execute("집중할 수 있게 해줘")
        save_settings(self.temp.name, "local:F1", True, ai_mode="local")
        self.assertTrue(self.service.sync_settings())
        self.assertEqual(self.service.history.messages(), [])
        with self.assertRaises(UserError):
            self.service.confirm_proposal(pending["proposal"]["id"])
        with patch.object(self.service.ollama, "request", return_value={"content": "안녕", "calls": []}) as ollama, patch.object(self.service.openai, "request") as openai:
            self.service.execute("안녕")
            self.assertEqual(ollama.call_args.args[:2], ("local", "qwen3:4b"))
            openai.assert_not_called()
        self.assertEqual(load_settings(self.temp.name)["openai_model"], "gpt-4.1-mini")

    def test_voice_mode_switch_requires_the_correct_key_and_keeps_ollama_models(self):
        save_settings(self.temp.name, "local:F1", True, ai_mode="local")
        for text in ("ChatGPT로 바꿔줘", "챗지피티 모드로 바꿔줘", "오픈AI로 변경해줘"):
            with self.subTest(text=text):
                command = parse_app_command(text, {})
                self.assertEqual(command[0]["function"]["arguments"], {"ai_mode": "openai"})
        result = self.service.execute("ChatGPT로 바꿔줘")
        self.service.openai_credentials.delete()
        self.service.credentials.set("ollama-test-placeholder", False)
        with patch.dict(os.environ, {"LUNA_OPENAI_API_KEY": "", "OPENAI_API_KEY": ""}):
            failed = self.service.confirm_proposal(result["proposal"]["id"])
            self.assertIn("OpenAI API 키", failed["message"])
            self.assertNotIn("settings_changed", failed)
            self.assertEqual(load_settings(self.temp.name)["ai_mode"], "local")
        self.service.openai_credentials.set("openai-test-placeholder", False)
        result = self.service.execute("ChatGPT로 바꿔줘")
        changed = self.service.confirm_proposal(result["proposal"]["id"])
        self.assertTrue(changed["conversation_changed"])
        self.assertEqual(load_settings(self.temp.name)["ai_mode"], "openai")
        self.assertEqual(load_settings(self.temp.name)["ollama_model"], "qwen3:4b")
