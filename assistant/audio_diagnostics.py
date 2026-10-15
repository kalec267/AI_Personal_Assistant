"""Local device/model checks. Diagnostic audio is discarded, never uploaded."""
import array
import importlib
import math
import sys

from .core import UserError
from .local_voice import LocalVoice
from .voice import WindowsVoice
from .wake_word import model_ready


def diagnose_audio(folder, voice_name, speak_enabled):
    report = ["[음성 출력]", "답변 읽기: " + ("켜짐" if speak_enabled else "꺼짐 · ‘답변을 음성으로 읽기’를 켜고 설정 저장")]
    local = LocalVoice(folder)
    report.append("선택한 목소리: " + (voice_name or "없음 · 목소리를 선택하고 설정 저장"))
    report.append("로컬 음성 모델: " + ("설치됨" if local.ready() else "미설치 · ‘로컬 음성 설치’를 누르세요"))
    try:
        importlib.import_module("sherpa_onnx")
        report.append("로컬 음성 패키지: 정상")
    except (ImportError, OSError):
        report.append("로컬 음성 패키지: 불러오기 실패 · setup_voice.bat 실행 후 다시 시작")
    if voice_name and not voice_name.startswith("local:"):
        try:
            installed = {item["name"] for item in WindowsVoice().list()}
            report.append("선택한 Windows 음성: " + ("설치됨" if voice_name in installed else "찾지 못함 · 목소리 다시 선택"))
        except UserError as error:
            report.append(str(error))
    report.extend(["소리가 안 들리면 Windows 설정 → 시스템 → 소리에서 기본 출력 장치·앱별 볼륨·음소거를 확인하세요.",
                   "", "[음성 입력]"])
    try:
        sr = importlib.import_module("speech_recognition")
        with sr.Microphone() as source:
            if source.stream is None:
                raise OSError("Microphone did not open")
            # Opening and reading also detects denied permission and stale
            # devices. A short level sample stays entirely in this function.
            samples = array.array("h")
            frames = 0
            while frames < source.SAMPLE_RATE:
                chunk = source.stream.read(source.CHUNK)
                if not chunk:
                    raise OSError("Microphone stream ended")
                samples.frombytes(chunk)
                frames += len(chunk) // source.SAMPLE_WIDTH
            if sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768 * 100
            name = source.audio.get_default_input_device_info().get("name", "Windows 기본 입력 장치")
            report.append(f"기본 마이크: {name} · 열기·입력 정상 · {source.SAMPLE_RATE} Hz · 입력 레벨 {rms:.1f}%")
            if rms < 0.1:
                report.append("입력이 매우 작습니다. 점검 중 말했는데도 낮다면 기본 입력 장치·입력 볼륨·마이크 음소거를 확인하세요.")
    except (ImportError, AttributeError, AssertionError, OSError, ValueError):
        report.append("마이크 점검 실패 · setup.bat 실행, Windows 기본 입력 장치·마이크 권한·다른 앱의 독점 사용을 확인하세요.")
    try:
        sr = importlib.import_module("speech_recognition")
        # Verify the executable actually converts PCM, without uploading audio.
        encoded = sr.AudioData(b"\0\0" * 1600, 16000, 2).get_flac_data()
        if not encoded.startswith(b"fLaC"):
            raise ValueError("Invalid FLAC")
        report.append("음성 변환 도구: 정상")
    except (ImportError, AttributeError, OSError, ValueError):
        report.append("음성 변환 도구 점검 실패 · setup.bat 실행 또는 FLAC 도구를 포함해 실행 파일 다시 빌드")
    report.append("Windows 설정 → 개인 정보 및 보안 → 마이크에서 ‘마이크 액세스’와 ‘데스크톱 앱의 마이크 액세스’를 켜세요.")
    report.append("호출어 모델: " + ("설치됨" if model_ready(folder) else "미설치 · ‘호출어 모델 설치 / 다시 시작’을 누르세요"))
    try:
        importlib.import_module("vosk")
        report.append("호출어 인식 패키지: 정상")
    except (ImportError, OSError):
        report.append("호출어 인식 패키지: 불러오기 실패 · setup_wake.bat 실행 후 다시 시작")
    report.append("명령 음성 인식은 Google 온라인 인식이며 인터넷이 필요합니다. 이 점검은 외부 전송·명령 실행을 하지 않습니다.")
    return "\n".join(report)
