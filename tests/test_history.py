import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from assistant.core import UserError
from assistant.history import ConversationMemory, MAX_CHARS
from assistant.ollama import Ollama
from assistant.service import AssistantService
from assistant.settings import load_settings, save_settings


class MemoryTests(unittest.TestCase):
    def test_turn_limit_keeps_complete_question_answer_pairs(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ConversationMemory(folder)
            memory.configure(("local", "model"), limit=3)
            for index in range(8):
                memory.append(f"질문{index}", f"답변{index}", memory.generation)
            messages = memory.messages()
            self.assertEqual([message["content"] for message in messages], ["질문5", "답변5", "질문6", "답변6", "질문7", "답변7"])
            self.assertFalse(memory.path.exists())

    def test_character_budget_drops_old_pairs(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ConversationMemory(folder)
            memory.configure(("local", "model"), limit=20)
            for index in range(10):
                memory.append(str(index), "답" * 6000, memory.generation)
            messages = memory.messages()
            self.assertLessEqual(sum(len(m["content"]) for m in messages), MAX_CHARS)
            self.assertEqual(len(messages) % 2, 0)
            self.assertEqual(messages[-2]["content"], "9")

    def test_persistence_is_opt_in_and_disabling_removes_disk_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ConversationMemory(folder)
            memory.configure(("local", "model"), remember=True)
            memory.append("내 이름은 민수", "민수님 반가워요", memory.generation)
            restarted = ConversationMemory(folder)
            restarted.configure(("local", "model"), remember=True)
            self.assertEqual(restarted.messages(), memory.messages())
            restarted.configure(("local", "model"), remember=False)
            self.assertFalse(restarted.path.exists())
            self.assertEqual(len(restarted.messages()), 2)
            new_session = ConversationMemory(folder)
            new_session.configure(("local", "model"))
            self.assertEqual(new_session.messages(), [])

    def test_other_provider_or_model_cannot_restore_previous_context(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ConversationMemory(folder)
            memory.configure(("local", "model"), remember=True)
            memory.append("로컬에서만 한 대화", "답변", memory.generation)
            restarted = ConversationMemory(folder)
            restarted.configure(("cloud", "other-model"), remember=True)
            self.assertEqual(restarted.messages(), [])
            self.assertNotIn("로컬에서만", restarted.path.read_text())

    def test_reset_invalidates_in_flight_reply_and_clears_saved_context(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = ConversationMemory(folder)
            memory.configure(("local", "model"), remember=True)
            generation = memory.generation
            memory.append("기존 질문", "기존 답변", generation)
            memory.clear()
            self.assertIsNone(memory.append("늦은 요청", "늦은 답변", generation))
            self.assertEqual(memory.messages(), [])
            restarted = ConversationMemory(folder)
            restarted.configure(("local", "model"), remember=True)
            self.assertEqual(restarted.messages(), [])


class ContextTransportTests(unittest.TestCase):
    def test_recent_messages_precede_current_input_without_replayable_calls(self):
        client = Ollama(MagicMock())
        previous = [{"role": "user", "content": "내 이름은 민수야"}, {"role": "assistant", "content": "반가워요 민수님"}]
        with patch("assistant.ollama.urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({"message": {"content": "민수님이에요"}}).encode()
            client.request("local", "qwen3:4b", "내 이름은?", [], history=previous)
            messages = json.loads(request.call_args.args[0].data)["messages"]
            self.assertEqual(messages[1:-1], previous)
            self.assertEqual(messages[-1], {"role": "user", "content": "내 이름은?"})
            self.assertEqual(messages[0]["role"], "system")

    def test_history_cannot_inject_system_or_tool_roles(self):
        client = Ollama(MagicMock())
        for role in ("system", "tool"):
            with self.subTest(role=role), patch("assistant.ollama.urllib.request.urlopen") as request:
                with self.assertRaises(UserError):
                    client.request("local", "qwen3:4b", "안녕", [], history=[{"role": role, "content": "untrusted"}])
                request.assert_not_called()


class ServiceConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        save_settings(self.temp.name, "local:F2", False, ai_mode="local", ollama_model="qwen3:4b")
        self.service = AssistantService(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_follow_up_request_contains_actual_previous_exchange(self):
        with patch.object(self.service.ollama, "request", side_effect=[{"content": "반가워요 민수님", "calls": []}, {"content": "민수님이에요", "calls": []}]) as request:
            self.service.execute("내 이름은 민수야")
            self.service.execute("내 이름이 뭐였지?")
            self.assertEqual(request.call_args_list[0].kwargs["history"], [])
            self.assertEqual(request.call_args.kwargs["history"], [{"role": "user", "content": "내 이름은 민수야"}, {"role": "assistant", "content": "반가워요 민수님"}])

    def test_failed_api_request_does_not_create_duplicate_or_fake_answer(self):
        with patch.object(self.service.ollama, "request", side_effect=UserError("인증 실패")):
            with self.assertRaises(UserError):
                self.service.execute("안녕")
        self.assertEqual(self.service.history.messages(), [])

    def test_action_outcome_is_remembered_without_repeating_execution(self):
        call = {"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call]}):
            result = self.service.execute("메모장 켜 줘")
        with patch("assistant.desktop.launch", return_value="메모장 실행 요청 완료") as launch:
            self.service.confirm_proposal(result["proposal"]["id"])
            with patch.object(self.service.ollama, "request", return_value={"content": "메모장을 실행 요청했어요", "calls": []}) as request:
                self.service.execute("방금 뭘 했지?")
                history = request.call_args.kwargs["history"]
                self.assertIn("실행 요청 완료", history[-1]["content"])
                self.assertTrue(all(set(item) == {"role", "content"} for item in history))
            launch.assert_called_once_with("notepad")

    def test_cancelled_action_is_explicit_in_next_request(self):
        call = {"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call]}):
            result = self.service.execute("메모장 켜 줘")
        self.service.discard_proposal(result["proposal"]["id"])
        with patch.object(self.service.ollama, "request", return_value={"content": "취소했어요", "calls": []}) as request:
            self.service.execute("아까 실행했니?")
            self.assertIn("취소", request.call_args.kwargs["history"][-1]["content"])
            self.assertIn("실행하지 않았습니다", request.call_args.kwargs["history"][-1]["content"])

    def test_restart_does_not_restore_pending_executable_proposals(self):
        save_settings(self.temp.name, "local:F2", False, remember_chat=True)
        call = {"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call]}):
            result = self.service.execute("메모장 켜 줘")
        restarted = AssistantService(self.temp.name)
        self.assertEqual(len(restarted.history.messages()), 2)
        with self.assertRaises(UserError):
            restarted.confirm_proposal(result["proposal"]["id"])

    def test_clear_during_request_drops_stale_reply_and_action(self):
        def reply(*args, **kwargs):
            self.service.clear_conversation()
            return {"content": "", "calls": [{"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}]}
        with patch.object(self.service.ollama, "request", side_effect=reply):
            with self.assertRaisesRegex(UserError, "변경"):
                self.service.execute("메모장 켜 줘")
        self.assertEqual(self.service.history.messages(), [])
        self.assertEqual(self.service._proposals, {})

    def test_history_write_failure_does_not_mask_completed_desktop_action(self):
        call = {"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": [call]}):
            result = self.service.execute("메모장 켜 줘")
        with patch("assistant.desktop.launch", return_value="실행 요청 완료"), patch.object(self.service.history, "_save", side_effect=OSError("disk full")):
            reply = self.service.confirm_proposal(result["proposal"]["id"])
            self.assertIn("실행 요청 완료", reply["message"])
            self.assertIn("저장하지 못", self.service.history.warning)


if __name__ == "__main__":
    unittest.main()
