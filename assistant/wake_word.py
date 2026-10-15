"""Local Korean wake detection. Only a requested command goes to Google STT."""
import argparse
from collections import deque
import importlib
import json
from pathlib import Path
import queue
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.request
import zipfile

from .core import UserError
from .settings import default_data_dir
from .voice import recognize_audio

MODEL_NAME = "vosk-model-small-ko-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_FILES = ("am/final.mdl", "conf/mfcc.conf", "conf/model.conf", "graph/phones/word_boundary.int")
WAKE_PREFIX = re.compile(r"^\s*(?:루\s*나(?:야)?|luna)(?=$|[\s,，.!?…:])\s*[,，.!?…:]*\s*", re.IGNORECASE)


def wake_command(text):
    """None means no invocation; an empty string means the name alone."""
    match = WAKE_PREFIX.match(text)
    return text[match.end():].strip() if match else None


def model_ready(folder):
    path = Path(folder) / "models" / MODEL_NAME
    return all((path / name).is_file() for name in MODEL_FILES)


def install_wake_model(folder, archive_path=None):
    if model_ready(folder):
        return Path(folder) / "models" / MODEL_NAME
    models = Path(folder) / "models"
    models.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wake-install-", dir=models) as temporary:
        temporary = Path(temporary)
        archive = temporary / "model.zip"
        if archive_path is None:
            try:
                with urllib.request.urlopen(MODEL_URL, timeout=45) as response, archive.open("wb") as output:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 150 * 1024 * 1024:
                            raise UserError("호출어 모델 다운로드 크기가 예상보다 큽니다.")
                        output.write(chunk)
            except OSError:
                raise UserError("호출어 모델 다운로드에 실패했어요. 인터넷 연결을 확인하거나 setup_wake.bat을 실행하세요.") from None
        else:
            shutil.copyfile(archive_path, archive)
        unpacked = temporary / "unpacked"
        unpacked.mkdir()
        try:
            with zipfile.ZipFile(archive) as package:
                total = 0
                for member in package.infolist():
                    target = (unpacked / member.filename).resolve()
                    mode = member.external_attr >> 16
                    if ("\\" in member.filename or ":" in member.filename or not target.is_relative_to(unpacked.resolve())
                            or stat.S_ISLNK(mode)):
                        raise UserError("호출어 모델 압축 파일 경로가 올바르지 않습니다.")
                    total += member.file_size
                    if total > 500 * 1024 * 1024:
                        raise UserError("호출어 모델 압축 해제 크기가 예상보다 큽니다.")
                package.extractall(unpacked)
        except zipfile.BadZipFile:
            raise UserError("호출어 모델 ZIP 파일이 올바르지 않습니다.") from None
        source = unpacked / MODEL_NAME
        if not all((source / name).is_file() for name in MODEL_FILES):
            raise UserError("호출어 모델 필수 파일이 누락되었습니다.")
        destination = models / MODEL_NAME
        if destination.exists():
            raise UserError("불완전한 호출어 모델 폴더가 있어요. 폴더 이름을 바꾼 뒤 다시 설치하세요.")
        source.rename(destination)
    return destination


class WakeCapture:
    """Bounded PCM capture, shared by the real stream and deterministic tests."""
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.chunks = deque()
        self.size = 0
        self.activated = None
        self.follow_up = False

    def feed(self, audio, text, final, now):
        self.chunks.append(audio)
        self.size += len(audio)
        # Keep at most 12 seconds, including the start of an inline command.
        while self.size > self.sample_rate * 2 * 12:
            self.size -= len(self.chunks.popleft())
        events = []
        tail = wake_command(text)
        if self.activated is None and tail is not None:
            self.activated = now
            events.append(("wake", None))
        if self.activated is not None:
            if final and text.strip():
                if self.follow_up or tail:
                    events.append(("audio", (b"".join(self.chunks), self.sample_rate, not self.follow_up)))
                    return events
                if tail == "":
                    self.follow_up = True
                    self.chunks.clear()
                    self.size = 0
            if now - self.activated >= 12:
                events.append(("timeout", "명령이 감지되지 않았어요. 다시 ‘루나’라고 불러 주세요."))
        elif final:
            self.chunks.clear()
            self.size = 0
        return events


def transcribe_command(payload):
    data, sample_rate, inline = payload
    try:
        sr = importlib.import_module("speech_recognition")
    except ImportError:
        raise UserError("setup.bat으로 음성 인식 패키지를 설치하세요.") from None
    text = recognize_audio(sr.AudioData(data, sample_rate, 2), sr)
    tail = wake_command(text)
    if inline and tail is None:
        raise UserError("호출어와 명령을 확인하지 못했어요. ‘루나’라고 부른 뒤 명령을 말씀해 주세요.")
    text = tail if tail is not None else text
    if not text.strip():
        raise UserError("명령이 비어 있어요. 다시 ‘루나’라고 불러 주세요.")
    return text.strip()


def recognize_locked(recognizer, microphone_lock):
    with microphone_lock:
        return recognizer()


class WakeListener:
    """No Tk references: one microphone owner, cancellable events, no recordings."""
    def __init__(self, folder, microphone_lock):
        self.folder = Path(folder)
        self.microphone_lock = microphone_lock
        self.events = queue.Queue()
        self.available = threading.Event()
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self.epoch = 0
        self.delivering = False
        self.failed = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="luna-wake")

    def start(self):
        self.thread.start()

    def set_available(self, value):
        with self._lock:
            if value:
                if not self.available.is_set():
                    self.delivering = False
                self.available.set()
            elif self.available.is_set():
                self.epoch += 1
                self.available.clear()

    def close(self):
        self.stopped.set()
        self.set_available(False)

    def valid(self, epoch):
        return not self.stopped.is_set() and self.available.is_set() and epoch == self.epoch

    def _run(self):
        try:
            sr = importlib.import_module("speech_recognition")
            vosk = importlib.import_module("vosk")
            vosk.SetLogLevel(-1)
            model = vosk.Model(str(self.folder / "models" / MODEL_NAME))
            while not self.stopped.is_set():
                if not self.available.wait(0.1) or self.delivering:
                    self.stopped.wait(0.05)
                    continue
                epoch = self.epoch
                with self.microphone_lock:
                    if not self.valid(epoch):
                        continue
                    # Use the device's native rate; some Windows devices reject
                    # a forced 16 kHz stream. Vosk resamples from SAMPLE_RATE.
                    with sr.Microphone() as source:
                        decoder = vosk.KaldiRecognizer(model, source.SAMPLE_RATE)
                        capture = WakeCapture(source.SAMPLE_RATE)
                        if self.valid(epoch):
                            self.events.put((epoch, "ready", None))
                        while self.valid(epoch):
                            chunk = source.stream.read(source.CHUNK)
                            if not chunk:
                                raise OSError("Microphone stream ended")
                            final = decoder.AcceptWaveform(chunk)
                            result = json.loads(decoder.Result() if final else decoder.PartialResult())
                            events = capture.feed(chunk, result.get("text" if final else "partial", ""), final, time.monotonic())
                            for kind, value in events:
                                with self._lock:
                                    if not self.valid(epoch):
                                        break
                                    if kind in {"audio", "timeout"}:
                                        self.delivering = True
                                    self.events.put((epoch, kind, value))
                            if self.delivering:
                                break
        except Exception:
            # A failed device/model should not retry in a tight background loop.
            if not self.stopped.is_set():
                self.failed = True
                self.events.put((self.epoch, "error", "호출어 감지를 시작하지 못했어요. setup_wake.bat, 기본 마이크와 Windows 마이크 권한을 확인하세요."))


def main():
    parser = argparse.ArgumentParser(description="Install the free local Korean wake model")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--archive", type=Path)
    options = parser.parse_args()
    print("Installing Korean wake model from the official Vosk HTTPS download...", flush=True)
    print(install_wake_model(options.data_dir, options.archive))


if __name__ == "__main__":
    main()
