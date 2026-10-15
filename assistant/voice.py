"""Native microphone capture and Windows installed speech voices."""
import importlib
import functools
import json
import os
import subprocess
import threading
from pathlib import Path

from .core import UserError
from .local_voice import LocalVoice
from .settings import check_voice_speed
from .speech_text import spoken_text


def preferred_voice(voices, saved=""):
    if any(voice["name"] == saved for voice in voices):
        return saved
    # Never silently select a male or non-Korean voice as the requested default.
    for voice in voices:
        if voice.get("culture", "").lower().startswith("ko") and voice.get("gender") == "Female" and voice.get("available", True):
            return voice["name"]
    return ""


class WindowsVoice:
    def __init__(self):
        self._lock = threading.RLock()
        self._generation = 0
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
            self._generation += 1
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None

    def close(self):
        with self._lock:
            self._closed = True
            self.stop()

    def prepare(self, text, name, *, speed=1.0):
        """Reserve speech before queuing a worker, so stop cancels queued work too."""
        with self._lock:
            self.stop()
            return functools.partial(self.speak, text, name, speed=speed, generation=self._generation)

    def speak(self, text, name, *, speed=1.0, generation=None):
        if generation is None:
            return self.prepare(text, name, speed=speed)()
        try:
            speed = check_voice_speed(speed)
        except ValueError as error:
            raise UserError(str(error)) from None
        if not name:
            raise UserError("한국어 여성 음성을 설치하거나 사용할 음성을 직접 선택해 주세요.")
        # Both speech and voice names go through JSON stdin, never into PowerShell source.
        payload = json.dumps({"text": str(text)[:2000], "voice": name, "speed": speed}, ensure_ascii=False)
        try:
            with self._lock:
                if self._closed or generation != self._generation:
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
        self._lock = threading.RLock()

    def list(self):
        voices = self.local.list()
        if os.name == "nt":
            try:
                voices += self.windows.list()
            except UserError:
                pass
        return voices

    def speak(self, text, name, *, speed=1.0):
        return self.prepare(text, name, speed=speed)()

    def prepare(self, text, name, *, speed=1.0):
        with self._lock:
            selected, other = (self.local, self.windows) if name.startswith("local:") else (self.windows, self.local)
            other.stop()
            return selected.prepare(spoken_text(text), name, speed=speed)

    def stop(self):
        with self._lock:
            self.local.stop()
            self.windows.stop()

    def close(self):
        with self._lock:
            self.local.close()
            self.windows.close()


def recognize_audio(audio, sr):
    """Shared online STT for the manual button and locally detected wake calls."""
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 12
    try:
        return recognizer.recognize_google(audio, language="ko-KR", endpoint="https://www.google.com/speech-api/v2/recognize")
    except sr.UnknownValueError:
        raise UserError("말씀을 이해하지 못했어요. 마이크 입력 볼륨·주변 소음을 확인한 뒤 다시 말씀해 주세요.") from None
    except sr.RequestError:
        raise UserError("Google 음성 인식에 연결하지 못했어요. 인터넷·방화벽·VPN을 확인하세요. AI API 키와는 별도 기능입니다.") from None
    except (OSError, AttributeError):
        raise UserError("음성 변환에 실패했어요. setup.bat을 다시 실행하세요. 실행 파일을 쓰면 FLAC 변환 도구가 포함되도록 다시 빌드하세요.") from None


def recognize_once(*, progress=None):
    try:
        sr = importlib.import_module("speech_recognition")
    except ImportError:
        raise UserError("마이크 기능 설치가 필요해요. setup.bat을 실행한 뒤 앱을 다시 시작하세요.") from None
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 12
    recognizer.pause_threshold = 1.0
    recognizer.energy_threshold = 150
    recognizer.phrase_threshold = 0.1
    try:
        if progress:
            progress("마이크 연결 중… ‘지금 말씀하세요’가 표시되면 말해 주세요.")
        with sr.Microphone() as source:
            if source.stream is None:
                raise UserError("마이크 입력을 열지 못했어요. Windows 기본 입력 장치·마이크 권한·다른 앱의 독점 사용을 확인하세요.")
            # Do not discard the first half second as ambient noise: it can
            # contain the entire short command spoken just after a click.
            # Listen's dynamic threshold still adapts to background noise.
            if progress:
                progress("지금 말씀하세요. 말이 끝나면 자동으로 명령을 수행합니다.")
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
    except sr.WaitTimeoutError:
        raise UserError("5초 동안 말소리를 감지하지 못했어요. 음성을 글자로 바꾸기 전의 입력 문제입니다. "
                        "설정 → 소리 · 음성 → 음성 입력·출력 점검에서 마이크 이름과 입력 레벨을 확인하세요. "
                        "말해도 입력 레벨이 낮으면 Windows 기본 입력 장치·입력 볼륨·음소거를 확인하세요.") from None
    except (AttributeError, AssertionError, ImportError, OSError):
        raise UserError("마이크를 사용할 수 없어요. setup.bat 설치, 기본 입력 장치와 Windows 마이크 권한을 확인해 주세요.") from None
    if progress:
        progress("말씀하신 명령을 글자로 변환하고 있어요…")
    return recognize_audio(audio, sr)
