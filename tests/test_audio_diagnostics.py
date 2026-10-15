import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from assistant.audio_diagnostics import diagnose_audio
from assistant.core import UserError
from assistant.local_voice import LocalVoice
from assistant.voice import preferred_voice, recognize_audio, recognize_once


class AudioTests(unittest.TestCase):
    def test_uninstalled_local_voice_is_not_chosen_over_an_installed_female_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            voices = LocalVoice(folder).list()
            self.assertEqual(preferred_voice(voices), "")
            windows = {"name": "설치된 여성", "culture": "ko-KR", "gender": "Female"}
            self.assertEqual(preferred_voice(voices + [windows]), "설치된 여성")
            # Keep an explicit choice so its missing-model instructions remain
            # visible instead of silently switching to another voice.
            self.assertEqual(preferred_voice(voices, "local:F3"), "local:F3")
            with patch.object(LocalVoice, "ready", return_value=True):
                self.assertEqual(preferred_voice(LocalVoice(folder).list()), "local:F1")

    def test_manual_capture_announces_readiness_after_calibration_and_before_listening(self):
        sr = MagicMock()
        events = []
        recognizer = sr.Recognizer.return_value
        recognizer.adjust_for_ambient_noise.side_effect = lambda *a, **k: events.append("calibrated")
        recognizer.listen.side_effect = lambda *a, **k: events.append("listen") or "captured"
        recognizer.recognize_google.return_value = "오늘 일정 보여줘"
        with patch("assistant.voice.importlib.import_module", return_value=sr):
            result = recognize_once(progress=events.append)
        self.assertEqual(result, "오늘 일정 보여줘")
        self.assertEqual(events[1], "calibrated")
        self.assertIn("지금 말씀하세요", events[2])
        self.assertEqual(events[3], "listen")
        self.assertIn("변환", events[4])

    def test_device_open_failure_is_actionable_before_listening_or_upload(self):
        sr = SimpleNamespace(Microphone=MagicMock(), Recognizer=MagicMock(), WaitTimeoutError=TimeoutError)
        sr.Microphone.return_value.__enter__.return_value.stream = None
        with patch("assistant.voice.importlib.import_module", return_value=sr), self.assertRaisesRegex(UserError, "마이크 입력을 열지"):
            recognize_once()
        sr.Recognizer.return_value.listen.assert_not_called()
        sr.Recognizer.return_value.recognize_google.assert_not_called()

    def test_capture_and_online_failures_are_distinct(self):
        sr = SimpleNamespace(Microphone=MagicMock(side_effect=OSError), Recognizer=MagicMock(),
                             WaitTimeoutError=TimeoutError, UnknownValueError=ValueError, RequestError=ConnectionError)
        with patch("assistant.voice.importlib.import_module", return_value=sr), self.assertRaisesRegex(UserError, "마이크"):
            recognize_once()
        for failure, expected in [(ConnectionError, "Google"), (ValueError, "이해하지"), (OSError, "음성 변환")]:
            with self.subTest(failure=failure), self.assertRaisesRegex(UserError, expected):
                sr.Recognizer.return_value.recognize_google.side_effect = failure
                recognize_audio("audio", sr)

    def test_model_engine_failure_reports_voice_installation_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = LocalVoice(folder)
            sherpa = MagicMock()
            sherpa.OfflineTts.side_effect = RuntimeError("backend unavailable")
            with patch.object(voice, "ready", return_value=True), patch("assistant.local_voice.importlib.import_module", return_value=sherpa):
                with self.assertRaisesRegex(UserError, "setup_voice.bat"):
                    voice._engine()


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sr = MagicMock()
        self.source = self.sr.Microphone.return_value.__enter__.return_value
        self.source.SAMPLE_RATE = 48000
        self.source.SAMPLE_WIDTH = 2
        self.source.CHUNK = 4800
        self.source.stream.read.return_value = b"\0\x01" * 4800
        self.source.audio.get_default_input_device_info.return_value = {"name": "USB 마이크"}
        self.sr.AudioData.return_value.get_flac_data.return_value = b"fLaCfixture"
        self.modules = {"speech_recognition": self.sr, "sherpa_onnx": object(), "vosk": object()}

    def diagnose(self, name="local:F1", speak=True):
        with patch("urllib.request.urlopen", side_effect=AssertionError("No upload during diagnostics")), patch("assistant.audio_diagnostics.importlib.import_module", side_effect=self.modules.__getitem__):
            result = diagnose_audio(self.temp.name, name, speak)
        self.sr.Recognizer.assert_not_called()
        return result

    def test_reports_selected_state_and_real_input_level_without_network_or_commands(self):
        result = self.diagnose(speak=False)
        self.assertIn("답변 읽기: 꺼짐", result)
        self.assertIn("로컬 음성 모델: 미설치", result)
        self.assertIn("호출어 모델: 미설치", result)
        self.assertIn("USB 마이크", result)
        self.assertIn("48000 Hz", result)
        self.assertIn("입력 레벨 0.8%", result)
        self.assertIn("음성 변환 도구: 정상", result)
        self.assertNotIn("점검 실패", result)
        self.sr.Microphone.assert_called_once_with()
        self.sr.Microphone.return_value.__exit__.assert_called_once()

    def test_silent_input_is_distinguished_from_a_missing_device(self):
        self.source.stream.read.return_value = b"\0\0" * 4800
        self.assertIn("입력이 매우 작습니다", self.diagnose())

    def test_microphone_failure_does_not_hide_model_and_converter_checks(self):
        self.sr.Microphone.side_effect = OSError("No default input")
        result = self.diagnose(name="")
        self.assertIn("마이크 점검 실패", result)
        self.assertIn("선택한 목소리: 없음", result)
        self.assertIn("로컬 음성 모델: 미설치", result)
        self.assertIn("음성 변환 도구: 정상", result)

    def test_converter_failure_is_distinguished_from_successful_capture(self):
        self.sr.AudioData.return_value.get_flac_data.side_effect = FileNotFoundError()
        result = self.diagnose()
        self.assertIn("입력 정상", result)
        self.assertNotIn("마이크 점검 실패", result)
        self.assertIn("음성 변환 도구 점검 실패", result)

    def test_missing_packages_report_each_independent_problem(self):
        with patch("assistant.audio_diagnostics.importlib.import_module", side_effect=ImportError):
            result = diagnose_audio(self.temp.name, "local:F1", True)
        self.assertIn("로컬 음성 패키지: 불러오기 실패", result)
        self.assertIn("호출어 인식 패키지: 불러오기 실패", result)
        self.assertIn("마이크 점검 실패", result)
        self.assertIn("음성 변환 도구 점검 실패", result)
