import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from assistant.core import UserError
from assistant.settings import load_settings, save_settings
from assistant.wake_word import (MODEL_FILES, MODEL_NAME, WakeCapture, WakeListener,
                                 install_wake_model, model_ready, recognize_locked,
                                 transcribe_command, wake_command)


class WakeTests(unittest.TestCase):
    def test_invocation_must_start_with_the_name_and_have_a_word_boundary(self):
        for text, expected in (("루나", ""), ("루나야", ""), ("루 나, 계산기 실행", "계산기 실행"),
                               ("루나 삼성에 대한 기사 찾아줘", "삼성에 대한 기사 찾아줘"), ("Luna, 계산기 실행", "계산기 실행")):
            with self.subTest(text=text):
                self.assertEqual(wake_command(text), expected)
        for text in ("코로나 뉴스", "루나틱", "안녕 루나", "누나", "루나라는 이름"):
            with self.subTest(text=text):
                self.assertIsNone(wake_command(text))

    def test_inline_invocation_emits_once_and_retains_the_start_of_the_command(self):
        capture = WakeCapture()
        self.assertEqual(capture.feed(b"first", "루나", False, 1), [("wake", None)])
        self.assertEqual(capture.feed(b"second", "루나 계산기", False, 2), [])
        events = capture.feed(b"third", "루나 계산기 실행", True, 3)
        self.assertEqual(events, [("audio", (b"firstsecondthird", 16000, True))])

    def test_name_alone_then_next_utterance_uses_only_the_command_audio(self):
        capture = WakeCapture()
        self.assertEqual(capture.feed(b"name", "루나", True, 1), [("wake", None)])
        self.assertEqual(capture.feed(b"next", "볼륨", False, 2), [])
        self.assertEqual(capture.feed(b"final", "볼륨 30퍼센트", True, 3), [("audio", (b"nextfinal", 16000, False))])

    def test_unrelated_speech_is_not_sent_and_waiting_audio_is_bounded(self):
        capture = WakeCapture(sample_rate=100)
        for index in range(100):
            self.assertEqual(capture.feed(b"x" * 100, "일상 대화", False, index), [])
        self.assertLessEqual(capture.size, 2400)
        self.assertEqual(capture.feed(b"end", "일상 대화", True, 101), [])
        self.assertEqual(capture.size, 0)

    def test_silence_after_invocation_times_out_without_an_execution(self):
        capture = WakeCapture()
        capture.feed(b"name", "루나", True, 1)
        events = capture.feed(b"silence", "", False, 13)
        self.assertEqual([kind for kind, _ in events], ["timeout"])

    def test_google_recognizes_only_requested_audio_and_strips_invocation(self):
        sr = MagicMock()
        sr.Recognizer.return_value.recognize_google.return_value = "루나 계산기 실행"
        with patch("assistant.wake_word.importlib.import_module", return_value=sr):
            self.assertEqual(transcribe_command((b"command", 16000, True)), "계산기 실행")
        sr.AudioData.assert_called_once_with(b"command", 16000, 2)
        recognizer = sr.Recognizer.return_value
        self.assertEqual(recognizer.operation_timeout, 12)
        self.assertEqual(recognizer.recognize_google.call_args.kwargs["language"], "ko-KR")

    def test_inline_transcription_without_confirmed_name_does_not_execute(self):
        sr = SimpleNamespace(Recognizer=MagicMock(), AudioData=MagicMock(), RequestError=ConnectionError, UnknownValueError=ValueError)
        sr.Recognizer.return_value.recognize_google.return_value = "다른 대화"
        with patch("assistant.wake_word.importlib.import_module", return_value=sr), self.assertRaisesRegex(UserError, "호출어"):
            transcribe_command((b"audio", 16000, True))

    def test_wake_settings_are_validated_and_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            save_settings(folder, "local:F1", True, wake_enabled=False, wake_during_speech=True)
            options = load_settings(folder)
            self.assertFalse(options["wake_enabled"])
            self.assertTrue(options["wake_during_speech"])
            with self.assertRaises(ValueError):
                save_settings(folder, "", False, wake_enabled="true")

    def test_model_install_uses_local_archive_and_preserves_existing_install(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "model.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for name in MODEL_FILES:
                    package.writestr(f"{MODEL_NAME}/{name}", "test model data")
            with patch("urllib.request.urlopen") as network:
                result = install_wake_model(folder, archive)
                self.assertTrue(model_ready(folder))
                self.assertEqual(install_wake_model(folder), result)
                network.assert_not_called()

    def test_model_install_rejects_zip_traversal_and_missing_files(self):
        for name in ("../outside", "C:/outside", "..\\outside", f"{MODEL_NAME}/only.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / "bad.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr(name, "bad")
                with self.assertRaises(UserError):
                    install_wake_model(folder, archive)
                self.assertFalse(model_ready(folder))

    def test_listener_is_local_and_releases_microphone_after_one_command(self):
        sr, vosk = MagicMock(), MagicMock()
        source = sr.Microphone.return_value.__enter__.return_value
        source.SAMPLE_RATE = 48000
        source.CHUNK = 1024
        source.stream.read.side_effect = [b"start", b"end"]
        decoder = vosk.KaldiRecognizer.return_value
        decoder.AcceptWaveform.side_effect = [False, True]
        decoder.PartialResult.return_value = json.dumps({"partial": "루나"})
        decoder.Result.return_value = json.dumps({"text": "루나 계산기 실행"})
        listener = WakeListener("test", threading.Lock())
        modules = {"speech_recognition": sr, "vosk": vosk}
        with patch("urllib.request.urlopen") as network, patch("assistant.wake_word.importlib.import_module", side_effect=modules.__getitem__):
            listener.set_available(True)
            listener.start()
            try:
                events = [listener.events.get(timeout=3) for _ in range(3)]
                self.assertEqual([event[1] for event in events], ["ready", "wake", "audio"])
                self.assertEqual(events[-1][2], (b"startend", 48000, True))
                sr.Microphone.assert_called_once_with()
                vosk.KaldiRecognizer.assert_called_once_with(vosk.Model.return_value, 48000)
                network.assert_not_called()
                sr.Recognizer.assert_not_called()
            finally:
                listener.close()
                listener.thread.join(3)
            self.assertFalse(listener.thread.is_alive())
            sr.Microphone.return_value.__exit__.assert_called_once()

    def test_pause_invalidates_events_and_serializes_manual_microphone_capture(self):
        listener = WakeListener("test", threading.Lock())
        listener.set_available(True)
        epoch = listener.epoch
        self.assertTrue(listener.valid(epoch))
        listener.set_available(False)
        self.assertFalse(listener.valid(epoch))
        listener.set_available(True)
        self.assertFalse(listener.valid(epoch))
        called = threading.Event()
        with listener.microphone_lock:
            manual = threading.Thread(target=recognize_locked, args=(called.set, listener.microphone_lock))
            manual.start()
            self.assertFalse(called.wait(0.02))
        manual.join(3)
        self.assertTrue(called.is_set())
        listener.close()

    def test_pause_and_resume_during_a_result_cannot_stall_the_new_listener(self):
        sr, vosk = MagicMock(), MagicMock()
        source = sr.Microphone.return_value.__enter__.return_value
        source.SAMPLE_RATE, source.CHUNK = 16000, 1024
        source.stream.read.side_effect = [b"stale", b"fresh"]
        decoder = vosk.KaldiRecognizer.return_value
        decoder.AcceptWaveform.return_value = True
        decoder.Result.return_value = json.dumps({"text": "루나 계산기 실행"})
        listener = WakeListener("test", threading.Lock())
        stale = MagicMock()

        def interrupted_result(*args):
            listener.set_available(False)
            listener.set_available(True)
            return [("audio", (b"stale", 16000, True))]

        stale.feed.side_effect = interrupted_result
        modules = {"speech_recognition": sr, "vosk": vosk}
        with patch("assistant.wake_word.WakeCapture", side_effect=[stale, WakeCapture()]), patch("assistant.wake_word.importlib.import_module", side_effect=modules.__getitem__):
            listener.set_available(True)
            listener.start()
            try:
                events = [listener.events.get(timeout=3) for _ in range(4)]
                self.assertEqual([item[1] for item in events], ["ready", "ready", "wake", "audio"])
                self.assertEqual(events[-1][2], (b"fresh", 16000, True))
            finally:
                listener.close()
                listener.thread.join(3)
            self.assertFalse(listener.thread.is_alive())

    def test_missing_model_or_dependency_reports_one_error_without_retry_loop(self):
        listener = WakeListener("test", threading.Lock())
        with patch("assistant.wake_word.importlib.import_module", side_effect=ImportError):
            listener.start()
            listener.thread.join(3)
        self.assertTrue(listener.failed)
        self.assertEqual(listener.events.get_nowait()[1], "error")
        self.assertTrue(listener.events.empty())
