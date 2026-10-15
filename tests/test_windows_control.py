import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from assistant import windows_control as windows
from assistant.actions import validate_calls, perform
from assistant.core import UserError
from assistant.ollama import Ollama
from assistant.service import AssistantService
from assistant.settings import load_settings, save_settings


def command_call(command="Write-Output '한글'", description="한글 출력"):
    return {"function": {"name": "run_windows_command", "arguments": {"command": command, "description": description}}}


class WindowsPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        save_settings(self.temp.name, "", False, ai_mode="local", ollama_model="qwen3:4b")
        self.service = AssistantService(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def enable(self, value=True):
        save_settings(self.temp.name, "", False, windows_control=value)

    def propose(self, calls=None):
        with patch.object(self.service.ollama, "request", return_value={"content": "", "calls": calls or [command_call()]}):
            return self.service.execute("한글 출력해 줘")

    def test_permission_is_explicit_and_preserved_by_other_settings(self):
        with patch("assistant.windows_control.run_command") as run:
            with self.assertRaisesRegex(UserError, "꺼져"):
                self.propose()
            run.assert_not_called()
        self.enable()
        save_settings(self.temp.name, "local:F3", True)
        self.assertTrue(load_settings(self.temp.name)["windows_control"])
        self.enable(False)
        self.assertFalse(load_settings(self.temp.name)["windows_control"])
        with self.assertRaises(ValueError):
            save_settings(self.temp.name, "", False, windows_control="yes")

    def test_command_is_only_advertised_when_enabled(self):
        client = Ollama(MagicMock())
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch("assistant.ollama.urllib.request.urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = json.dumps({"message": {"content": "안녕"}}).encode()
                client.request("local", "qwen3:4b", "안녕", [], allow_windows=enabled)
                payload = json.loads(request.call_args.args[0].data)
                self.assertEqual("run_windows_command" in [item["function"]["name"] for item in payload["tools"]], enabled)

    def test_entire_exact_command_requires_confirmation_then_runs_once(self):
        self.enable()
        command = "Write-Output '한글'\nGet-Date"
        with patch("assistant.windows_control.run_command", return_value="PowerShell 실행 완료\n한글") as run:
            result = self.propose([command_call(command)])
            self.assertTrue(result["proposal"]["windows_command"])
            self.assertIn(command, result["proposal"]["summary"])
            run.assert_not_called()
            self.service.confirm_proposal(result["proposal"]["id"])
            run.assert_called_once_with(command)
            with self.assertRaises(UserError):
                self.service.confirm_proposal(result["proposal"]["id"])
        with patch.object(self.service.ollama, "request", return_value={"content": "출력했어요", "calls": []}) as request:
            self.service.execute("결과가 뭐야?")
            self.assertTrue(request.call_args.kwargs["allow_windows"])
            self.assertIn("실행 완료", request.call_args.kwargs["history"][-1]["content"])

    def test_revoked_permission_blocks_entire_plan_before_any_side_effect(self):
        self.enable()
        result = self.propose([{"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}, command_call()])
        self.enable(False)
        with patch("assistant.desktop.launch") as launch, patch("assistant.windows_control.run_command") as run:
            with self.assertRaisesRegex(UserError, "꺼져"):
                self.service.confirm_proposal(result["proposal"]["id"])
            launch.assert_not_called()
            run.assert_not_called()
        self.assertIn("실행하지 않았습니다", self.service.history.messages()[-1]["content"])

    def test_revocation_while_model_is_replying_rejects_command(self):
        self.enable()
        def reply(*args, **kwargs):
            self.enable(False)
            return {"content": "", "calls": [command_call()]}
        with patch.object(self.service.ollama, "request", side_effect=reply):
            with self.assertRaisesRegex(UserError, "꺼져"):
                self.service.execute("한글 출력해 줘")
        self.assertEqual(self.service._proposals, {})

    def test_cancel_never_executes_command(self):
        self.enable()
        result = self.propose()
        with patch("assistant.windows_control.run_command") as run:
            self.service.discard_proposal(result["proposal"]["id"])
            with self.assertRaises(UserError):
                self.service.confirm_proposal(result["proposal"]["id"])
            run.assert_not_called()

    def test_failure_keeps_output_and_stops_following_actions(self):
        self.enable()
        result = self.propose([command_call(), {"function": {"name": "launch_program", "arguments": {"app_id": "notepad"}}}])
        with patch("assistant.windows_control.run_command", side_effect=UserError("PowerShell 명령 실패: 접근 거부")), patch("assistant.desktop.launch") as launch:
            completed = self.service.confirm_proposal(result["proposal"]["id"])
            self.assertIn("접근 거부", completed["message"])
            self.assertIn("이후 동작은 실행하지 않았습니다", completed["message"])
            launch.assert_not_called()

    def test_invalid_command_and_unapproved_direct_perform_are_rejected(self):
        for command in (None, "", "   ", "a" * 4001, "Get-Date\x00"):
            with self.subTest(command=str(command)[:20]), self.assertRaises(UserError):
                validate_calls([command_call(command)], self.service.programs, allow_windows=True)
        action = validate_calls([command_call()], self.service.programs, allow_windows=True)[0]
        with patch("assistant.windows_control.run_command") as run, self.assertRaises(UserError):
            perform(action, self.service.programs)
        run.assert_not_called()


class WindowsProcessTests(unittest.TestCase):
    def run_mock(self, content=b"", returncode=0, timeout=False):
        process = MagicMock()
        process.returncode = returncode
        process.pid = 12345
        process.wait.side_effect = [subprocess.TimeoutExpired("powershell", 30), 0] if timeout else None
        process.poll.return_value = 0
        def start(*args, **kwargs):
            kwargs["stdout"].write(content)
            kwargs["stdout"].flush()
            return process
        return process, patch("assistant.windows_control.subprocess.Popen", side_effect=start)

    def test_unicode_command_uses_absolute_windows_powershell_and_no_shell(self):
        process, mock = self.run_mock("한글 출력\n".encode())
        with patch("assistant.windows_control.require_windows"), mock as start:
            result = windows.run_command("Write-Output '한글'")
            args, kwargs = start.call_args
            self.assertIn("System32", args[0][0])
            self.assertTrue(args[0][0].endswith("powershell.exe"))
            self.assertIn("Write-Output '한글'", args[0][-1])
            self.assertNotIn("-ExecutionPolicy", args[0])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            process.wait.assert_called_once_with(timeout=30)
            self.assertIn("한글 출력", result)

    def test_output_is_bounded_and_failed_native_exit_is_reported(self):
        _, mock = self.run_mock(b"x" * (windows.MAX_OUTPUT + 1000))
        with patch("assistant.windows_control.require_windows"), mock:
            self.assertIn("나머지는 생략", windows.run_command("Get-Date"))
        _, mock = self.run_mock(b"access denied", returncode=5)
        with patch("assistant.windows_control.require_windows"), mock:
            with self.assertRaisesRegex(UserError, "종료 코드 5") as error:
                windows.run_command("Get-Date")
            self.assertIn("access denied", str(error.exception))

    def test_timeout_requests_tree_termination_and_preserves_partial_result(self):
        _, mock = self.run_mock(b"partial output", timeout=True)
        with patch("assistant.windows_control.require_windows"), mock, patch("assistant.windows_control.subprocess.run") as terminate:
            with self.assertRaisesRegex(UserError, "30초") as error:
                windows.run_command("Get-Date")
            self.assertIn("partial output", str(error.exception))
            self.assertIn("이미 적용된 변경", str(error.exception))
            args = terminate.call_args.args[0]
            self.assertEqual(Path(args[0]).parent.name, "System32")
            self.assertEqual(args[1:], ["/PID", "12345", "/T", "/F"])

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell requires Windows")
    def test_real_windows_unicode_command_and_native_error(self):
        self.assertIn("한글 테스트", windows.run_command("Write-Output '한글 테스트'"))
        with self.assertRaisesRegex(UserError, "종료 코드 7"):
            windows.run_command("cmd.exe /c exit 7")


class ElevationTests(unittest.TestCase):
    def test_source_restart_preserves_data_directory_and_quoted_arguments(self):
        with tempfile.TemporaryDirectory(prefix="luna data ") as folder, patch("assistant.windows_control.sys.frozen", False, create=True):
            executable, arguments = windows.elevation_target(folder)
            self.assertIn("launch.py", arguments)
            self.assertIn('--data-dir "', arguments)
            self.assertIn(str(Path(folder).resolve()), arguments)
            self.assertTrue(Path(executable).is_absolute())

    def test_frozen_restart_targets_executable_without_python_source(self):
        with patch("assistant.windows_control.sys.frozen", True, create=True), patch("assistant.windows_control.sys.executable", "/tmp/Luna App/Luna.exe"):
            executable, arguments = windows.elevation_target("/tmp/Luna Data")
            self.assertEqual(executable, "/tmp/Luna App/Luna.exe")
            self.assertEqual(arguments, '--data-dir "/tmp/Luna Data"')

    def test_elevation_uses_uac_and_reports_cancellation(self):
        shell = MagicMock()
        shell.CoInitializeEx.return_value = 0
        with patch("assistant.windows_control.require_windows"), patch("assistant.windows_control.is_admin", return_value=False), patch.object(ctypes, "WinDLL", return_value=shell, create=True), patch.object(ctypes, "get_last_error", return_value=1223, create=True):
            shell.ShellExecuteW.return_value = 5
            with self.assertRaisesRegex(UserError, "취소"):
                windows.restart_as_admin("/tmp/luna data")
            self.assertEqual(shell.ShellExecuteW.call_args.args[1], "runas")
            shell.ShellExecuteW.return_value = 42
            self.assertIn("실행했습니다", windows.restart_as_admin("/tmp/luna data"))
            self.assertEqual(shell.CoUninitialize.call_count, 2)


if __name__ == "__main__":
    unittest.main()
