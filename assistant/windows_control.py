"""Explicitly enabled Windows commands and normal UAC elevation."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .core import UserError
from .desktop import require_windows

MAX_COMMAND = 4000
MAX_OUTPUT = 12000
COMMAND_TIMEOUT = 30


def is_admin():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def validate_command(command):
    if not isinstance(command, str) or not 1 <= len(command.strip()) <= MAX_COMMAND or len(command) > MAX_COMMAND:
        raise UserError(f"PowerShell 명령은 1~{MAX_COMMAND}자로 작성해야 합니다.")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in command):
        raise UserError("PowerShell 명령에 표시할 수 없는 문자가 있습니다.")
    return command


def run_command(command):
    require_windows()
    validate_command(command)
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    # The command is deliberately executable PowerShell, as shown in the native
    # confirmation window. No cmd.exe, execution-policy override or UAC bypass.
    script = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
              "$OutputEncoding = [Console]::OutputEncoding\n"
              "$ErrorActionPreference = 'Stop'\n"
              "$global:LASTEXITCODE = 0\n"
              "try {\n& {\n" + command + "\n}\n"
              "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
              "} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }\n")
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       cwd=str(Path.home()), shell=False,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timed_out = False
            try:
                process.wait(timeout=COMMAND_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                # End the command's process tree, including any console children.
                taskkill = executable.parents[2] / "taskkill.exe"
                try:
                    subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            output.seek(0)
            data = output.read(MAX_OUTPUT + 1)
        result = data[:MAX_OUTPUT].decode("utf-8", errors="replace").strip()
        if len(data) > MAX_OUTPUT:
            result += "\n(긴 출력의 나머지는 생략했습니다.)"
        if timed_out:
            raise UserError(f"PowerShell 실행이 {COMMAND_TIMEOUT}초를 초과하여 중단을 요청했습니다. 이미 적용된 변경은 유지됩니다.\n" + result)
        if process.returncode:
            raise UserError(f"PowerShell 명령 실패 (종료 코드 {process.returncode}). 이미 적용된 변경은 유지됩니다.\n" + result)
        return "PowerShell 실행 완료" + ("\n" + result if result else " · 출력 없음")
    except UserError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise UserError("PowerShell 프로세스를 실행하거나 종료 상태를 확인하지 못했어요. Windows 권한과 실행 정책을 확인하세요. 실행 중이던 변경은 적용되었을 수 있습니다.") from None


def elevation_target(data_dir):
    executable = Path(sys.executable)
    arguments = ["--data-dir", str(Path(data_dir).resolve())]
    if not getattr(sys, "frozen", False):
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
        arguments.insert(0, str(Path(__file__).resolve().parent.parent / "launch.py"))
    return str(executable), subprocess.list2cmdline(arguments)


def restart_as_admin(data_dir):
    require_windows()
    if is_admin():
        raise UserError("이미 관리자 권한으로 실행 중입니다.")
    from ctypes import wintypes
    executable, arguments = elevation_target(data_dir)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    initialize = ole32.CoInitializeEx
    initialize.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    initialize.restype = ctypes.c_int32
    # ShellExecute may invoke COM shell extensions; this function runs on a new
    # worker thread so initialize a single-threaded apartment there explicitly.
    if initialize(None, 0x2 | 0x4) not in (0, 1):
        raise UserError("Windows 관리자 실행을 준비하지 못했어요. 앱을 다시 실행해 주세요.")
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    launch = shell32.ShellExecuteW
    launch.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    launch.restype = ctypes.c_void_p
    try:
        result = launch(None, "runas", executable, arguments, str(Path(executable).parent), 1)
        error = ctypes.get_last_error()
    finally:
        ole32.CoUninitialize()
    if not result or result <= 32:
        if error == 1223:
            raise UserError("관리자 권한 요청을 취소했어요. 현재 앱은 그대로 사용할 수 있습니다.")
        raise UserError("관리자 권한으로 실행하지 못했어요. UAC 승인과 Windows 계정 권한을 확인하세요.")
    return "관리자 권한 앱을 실행했습니다."
