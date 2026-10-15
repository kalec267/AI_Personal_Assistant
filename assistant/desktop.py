import os
import re
import subprocess
from pathlib import Path

from .core import UserError

APPS = {"notepad": ("메모장", ["notepad.exe"]), "calculator": ("계산기", ["calc.exe"]),
        "explorer": ("탐색기", ["explorer.exe"]), "edge": ("Microsoft Edge", None)}


def media(command):
    require_windows()
    keys = {"play_pause": (0xB3, "미디어 재생·일시정지를 요청했어요."),
            "next": (0xB0, "다음 곡 재생을 요청했어요."), "previous": (0xB1, "이전 곡 재생을 요청했어요.")}
    if command not in keys:
        raise UserError("지원하지 않는 미디어 명령입니다.")
    import ctypes
    key, message = keys[command]
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    return message


def require_windows():
    if os.name != "nt":
        raise UserError("프로그램 실행과 볼륨 제어는 Windows PC에서 사용할 수 있습니다.")


def launch(app):
    require_windows()
    if app not in APPS:
        raise UserError("허용되지 않은 프로그램입니다.")
    label, command = APPS[app]
    try:
        if app == "edge":
            os.startfile("microsoft-edge:")
        else:
            subprocess.Popen(command, shell=False)
    except OSError:
        raise UserError(f"{label} 실행에 실패했습니다. 설치 여부를 확인해 주세요.") from None
    return f"{label} 실행을 요청했습니다."


def volume(mode, value=0):
    require_windows()
    if mode not in {"set", "up", "down", "mute", "unmute"} or type(value) is not int or not 0 <= value <= 100:
        raise UserError("올바르지 않은 볼륨 명령입니다.")
    script = Path(__file__).with_name("volume.ps1")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                                 "-Action", mode, "-Value", str(value)], capture_output=True, text=True,
                                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    except (OSError, subprocess.SubprocessError):
        raise UserError("볼륨 제어에 실패했습니다. Windows 기본 오디오 장치와 PowerShell 실행 정책을 확인해 주세요.") from None
    state = result.stdout.strip().splitlines()
    if state:
        match = re.fullmatch(r"Volume: (\d+)% / (muted|unmuted)", state[-1])
        if match:
            return f"볼륨 {match[1]}퍼센트, " + ("음소거 상태예요." if match[2] == "muted" else "음소거가 해제된 상태예요.")
    return "볼륨 제어를 완료했습니다."
