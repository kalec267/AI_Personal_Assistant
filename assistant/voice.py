"""Native microphone capture and Windows installed speech voices."""
import importlib
import json
import os
import subprocess
import threading
from pathlib import Path

from .core import UserError
from .local_voice import LocalVoice


def preferred_voice(voices, saved=""):
    if any(voice["name"] == saved for voice in voices):
        return saved
    # Never silently select a male or non-Korean voice as the requested default.
    for voice in voices:
        if voice.get("culture", "").lower().startswith("ko") and voice.get("gender") == "Female":
            return voice["name"]
    return ""


class WindowsVoice:
    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self._closed = False

    def _command(self, action):
        if os.name != "nt":
            raise UserError("Windows 음성 출력은 Windows PC에서 사용할 수 있어요.")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("speech.ps1")), "-Action", action]

    def list(self):
        try:
            result = subprocess.run(self._command("list"), capture_output=True, text=True, encoding="utf-8",
                                    timeout=15, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            voices = json.loads(result.stdout)
            if isinstance(voices, dict):
                voices = [voices]
            if not isinstance(voices, list) or any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("name", "culture", "gender")) for item in voices):
                raise ValueError("Invalid voice metadata")
            return voices
        except (OSError, ValueError, subprocess.SubprocessError):
            raise UserError("설치된 음성을 읽지 못했어요. Windows 음성 설정과 PowerShell 실행 정책을 확인해 주세요.") from None

    def stop(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None

    def close(self):
        with self._lock:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None

    def speak(self, text, name):
        if not name:
            raise UserError("한국어 여성 음성을 설치하거나 사용할 음성을 직접 선택해 주세요.")
        # Both speech and voice names go through JSON stdin, never into PowerShell source.
        payload = json.dumps({"text": str(text)[:2000], "voice": name}, ensure_ascii=False)
        try:
            with self._lock:
                if self._closed:
                    return
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
                process = subprocess.Popen(self._command("speak"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._process = process
            try:
                process.communicate(payload, timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise UserError("음성 출력 시간이 초과되었어요. 다시 시도해 주세요.") from None
            # terminate() is an intentional cancellation, not a voice failure.
            with self._lock:
                if process is not self._process:
                    return
                if process.returncode:
                    raise UserError("음성 출력에 실패했어요. 음성을 다시 선택하거나 PowerShell 실행 정책을 확인해 주세요.")
        except (OSError, subprocess.SubprocessError):
            raise UserError("Windows 음성 출력을 시작하지 못했어요.") from None


class VoiceRouter:
    def __init__(self, folder):
        self.local = LocalVoice(folder)
        self.windows = WindowsVoice()

    def list(self):
        voices = self.local.list()
        if os.name == "nt":
            try:
                voices += self.windows.list()
            except UserError:
                pass
        return voices

    def speak(self, text, name):
        self.stop()
        return (self.local if name.startswith("local:") else self.windows).speak(text, name)

    def stop(self):
        self.local.stop()
        self.windows.stop()

    def close(self):
        self.local.close()
        self.windows.close()


def recognize_once():
    try:
        sr = importlib.import_module("speech_recognition")
    except ImportError:
        raise UserError("마이크 기능 설치가 필요해요. setup.bat을 실행한 뒤 앱을 다시 시작하세요.") from None
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 12
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
        # Capture is bounded; no background or always-on listener is started.
        return recognizer.recognize_google(audio, language="ko-KR", endpoint="https://www.google.com/speech-api/v2/recognize")
    except sr.WaitTimeoutError:
        raise UserError("음성이 감지되지 않았어요. 마이크 버튼을 누르고 다시 말씀해 주세요.") from None
    except sr.UnknownValueError:
        raise UserError("말씀을 이해하지 못했어요. 다시 말하거나 텍스트를 입력해 주세요.") from None
    except sr.RequestError:
        raise UserError("온라인 음성 인식에 연결하지 못했어요. 네트워크를 확인하거나 텍스트를 입력하세요.") from None
    except (AttributeError, ImportError, OSError):
        raise UserError("마이크를 사용할 수 없어요. setup.bat 설치, 기본 입력 장치와 Windows 마이크 권한을 확인해 주세요.") from None
