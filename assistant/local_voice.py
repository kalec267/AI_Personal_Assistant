"""Offline Korean Supertonic speech. Networking is restricted to explicit install."""
import argparse
import array
import hashlib
import importlib
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path

from .core import UserError
from .settings import default_data_dir, check_voice_speed

MODEL_NAME = "sherpa-onnx-supertonic-tts-int8-2026-03-06"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_NAME}.tar.bz2"
MODEL_SHA256 = "8c74359f63edd5045d47747f65331f0f6dbcbc91d7e898dd756d631295fe3259"
MODEL_FILES = ("duration_predictor.int8.onnx", "text_encoder.int8.onnx", "vector_estimator.int8.onnx",
               "vocoder.int8.onnx", "tts.json", "unicode_indexer.bin", "voice.bin", "LICENSE")


def install_model(folder, archive_path=None):
    models = Path(folder) / "models"
    destination = models / MODEL_NAME
    if all((destination / filename).is_file() for filename in MODEL_FILES):
        return destination
    models.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voice-install-", dir=models) as temporary:
        temporary = Path(temporary)
        archive = temporary / "model.tar.bz2"
        if archive_path is None:
            try:
                with urllib.request.urlopen(MODEL_URL, timeout=60) as response, archive.open("wb") as output:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 150 * 1024 * 1024:
                            raise UserError("음성 모델 다운로드 크기가 예상보다 큽니다.")
                        output.write(chunk)
            except OSError:
                raise UserError("음성 모델을 다운로드하지 못했어요. 인터넷 연결을 확인해 주세요.") from None
        else:
            shutil.copyfile(archive_path, archive)
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise UserError("음성 모델 SHA-256 검증에 실패했어요. 해당 파일은 설치하지 않았습니다.")
        unpacked = temporary / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive, "r:bz2") as package:
            members = package.getmembers()
            total = 0
            for member in members:
                target = (unpacked / member.name).resolve()
                if not target.is_relative_to(unpacked.resolve()) or not (member.isfile() or member.isdir()):
                    raise UserError("음성 모델 압축 파일의 경로가 올바르지 않습니다.")
                total += member.size
                if total > 500 * 1024 * 1024:
                    raise UserError("음성 모델 압축 해제 크기가 예상보다 큽니다.")
            # Paths, types, total size and the full archive digest were checked above.
            package.extractall(unpacked, members=members, filter="data")
        source = unpacked / MODEL_NAME
        if not all((source / filename).is_file() for filename in MODEL_FILES):
            raise UserError("음성 모델 필수 파일이 누락되었습니다.")
        if destination.exists():
            # Preserve incomplete/user-supplied installations instead of deleting them.
            raise UserError("불완전한 음성 모델 폴더가 있어요. 해당 폴더의 이름을 바꾼 뒤 다시 설치하세요.")
        source.rename(destination)
    return destination


class LocalVoice:
    def __init__(self, folder):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False
        self._tts = None

    @property
    def model_path(self):
        return self.folder / "models" / MODEL_NAME

    def ready(self):
        return all((self.model_path / filename).is_file() for filename in MODEL_FILES)

    def list(self):
        return [{"name": f"local:F{i}", "culture": "ko-KR", "gender": "Female"} for i in range(1, 6)]

    def _engine(self):
        if self._tts is not None:
            return self._tts
        if not self.ready():
            raise UserError("로컬 음성 모델이 없어요. setup_voice.bat을 한 번 실행하세요.")
        try:
            sherpa = importlib.import_module("sherpa_onnx")
        except ImportError:
            raise UserError("로컬 음성 패키지가 없어요. setup_voice.bat을 실행하세요.") from None
        folder = self.model_path
        options = {name: str(folder / filename) for name, filename in [
            ("duration_predictor", "duration_predictor.int8.onnx"), ("text_encoder", "text_encoder.int8.onnx"),
            ("vector_estimator", "vector_estimator.int8.onnx"), ("vocoder", "vocoder.int8.onnx"),
            ("tts_json", "tts.json"), ("unicode_indexer", "unicode_indexer.bin"), ("voice_style", "voice.bin")]}
        config = sherpa.OfflineTtsConfig(model=sherpa.OfflineTtsModelConfig(
            supertonic=sherpa.OfflineTtsSupertonicModelConfig(**options), num_threads=2, provider="cpu", debug=False))
        if not config.validate():
            raise UserError("로컬 음성 모델 설정을 확인할 수 없습니다.")
        self._tts = sherpa.OfflineTts(config)
        return self._tts

    def synthesize(self, text, name="local:F1", filename=None, *, speed=1.0):
        try:
            speed = check_voice_speed(speed)
        except ValueError as error:
            raise UserError(str(error)) from None
        if name not in {f"local:F{i}" for i in range(1, 6)}:
            raise UserError("지원하지 않는 로컬 음성입니다.")
        engine = self._engine()
        sherpa = importlib.import_module("sherpa_onnx")
        config = sherpa.GenerationConfig()
        config.sid = int(name[-1]) - 1
        config.num_steps = 5
        config.speed = speed
        config.extra["lang"] = "ko"
        audio = engine.generate(str(text)[:1000], config)
        if not len(audio.samples):
            raise UserError("로컬 음성이 생성되지 않았어요.")
        if filename:
            values = array.array("h", (max(-32768, min(32767, round(float(sample) * 32767))) for sample in audio.samples))
            if sys.byteorder != "little":
                values.byteswap()
            with wave.open(str(filename), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(audio.sample_rate)
                output.writeframes(values.tobytes())
        return audio

    def stop(self):
        self._generation += 1
        if os.name == "nt":
            import winsound
            winsound.PlaySound(None, 0)

    def close(self):
        self._closed = True
        self.stop()

    def speak(self, text, name, *, speed=1.0):
        if os.name != "nt":
            raise UserError("앱 음성 재생은 Windows에서 지원합니다.")
        generation = self._generation
        with self._lock:
            if self._closed or generation != self._generation:
                return
            with tempfile.TemporaryDirectory(prefix="luna-speech-") as folder:
                filename = Path(folder) / "speech.wav"
                self.synthesize(text, name, filename, speed=speed)
                if not self._closed and generation == self._generation:
                    import winsound
                    winsound.PlaySound(str(filename), winsound.SND_FILENAME)


def main():
    parser = argparse.ArgumentParser(description="Install free offline Korean voice")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--archive", type=Path, help="Already downloaded official archive (SHA-256 checked)")
    parser.add_argument("--sample", type=Path, help="Generate a local Korean sample WAV after installation")
    options = parser.parse_args()
    print("Installing verified local Korean voice model (about 81 MB download)...", flush=True)
    path = install_model(options.data_dir, options.archive)
    print(f"Voice model installed: {path}", flush=True)
    if options.sample:
        LocalVoice(options.data_dir).synthesize("안녕하세요. 당신의 개인 비서 루나예요. 오늘도 함께할게요.", filename=options.sample)
        print(f"Offline Korean sample generated: {options.sample}", flush=True)


if __name__ == "__main__":
    main()
