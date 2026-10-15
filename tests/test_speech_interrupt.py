"""Deterministic speech cancellation tests without a Windows audio device."""
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from assistant.local_voice import LocalVoice
from assistant.voice import VoiceRouter, WindowsVoice


class SpeechInterruptTests(unittest.TestCase):
    def test_stale_windows_job_cannot_start_or_stop_the_new_process(self):
        driver = WindowsVoice()
        old = driver.prepare("이전 답변", "여성 음성")
        driver.stop()
        new = driver.prepare("새 답변", "여성 음성")
        with patch.object(driver, "_command", return_value=["powershell.exe"]), patch("assistant.voice.subprocess.Popen") as popen:
            popen.return_value.returncode = 0
            popen.return_value.poll.return_value = 0
            new()
            old()
            popen.assert_called_once()
            self.assertIn("새 답변", popen.return_value.communicate.call_args.args[0])
            popen.return_value.terminate.assert_not_called()

    def test_windows_stop_terminates_active_speech_without_reporting_failure(self):
        driver = WindowsVoice()
        started, stopped = threading.Event(), threading.Event()
        process = MagicMock(returncode=1)
        process.poll.return_value = None
        process.terminate.side_effect = stopped.set

        def communicate(*args, **kwargs):
            started.set()
            if not stopped.wait(3):
                raise AssertionError("Speech process was not stopped")

        process.communicate.side_effect = communicate
        errors = []

        def speak():
            try:
                driver.speak("긴 답변", "여성 음성")
            except Exception as error:
                errors.append(error)

        with patch.object(driver, "_command", return_value=["powershell.exe"]), patch("assistant.voice.subprocess.Popen", return_value=process):
            worker = threading.Thread(target=speak)
            worker.start()
            try:
                self.assertTrue(started.wait(3))
                driver.stop()
                self.assertTrue(stopped.is_set())
            finally:
                stopped.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def test_router_switching_backends_invalidates_both_queued_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            router = VoiceRouter(folder)
            local = router.prepare("로컬 이전 답변", "local:F1")
            windows = router.prepare("Windows 이전 답변", "여성 음성")
            router.stop()
            with patch.object(router.local, "synthesize") as synthesize, patch("assistant.voice.subprocess.Popen") as popen:
                local()
                windows()
                synthesize.assert_not_called()
                popen.assert_not_called()

    @staticmethod
    def write_wav(filename):
        with wave.open(str(filename), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(1000)
            audio.writeframes(b"\0\0" * 30000)

    def test_local_stop_during_synthesis_is_immediate_and_discards_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            driver = LocalVoice(folder)
            work = driver.prepare("이전 답변", "local:F1")
            started, release = threading.Event(), threading.Event()
            sound = MagicMock()
            errors = []

            def synthesize(text, name, filename, **kwargs):
                started.set()
                if not release.wait(3):
                    raise AssertionError("Synthesis was not released")
                self.write_wav(filename)

            def run():
                try:
                    work()
                except Exception as error:
                    errors.append(error)

            with patch("assistant.local_voice.os", SimpleNamespace(name="nt")), patch.dict("sys.modules", {"winsound": sound}), patch.object(driver, "synthesize", side_effect=synthesize):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(started.wait(3))
                    # Must return while synthesis is still blocked.
                    driver.stop()
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                sound.PlaySound.assert_not_called()

    def test_local_playback_stops_and_only_new_reply_can_play(self):
        with tempfile.TemporaryDirectory() as folder:
            driver = LocalVoice(folder)
            started = threading.Event()
            played = []
            sound = SimpleNamespace(SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)

            def play(filename, flags):
                played.append(filename)
                if filename is not None:
                    self.assertEqual(flags, 7)
                    self.assertTrue(Path(filename).is_file())
                    started.set()

            sound.PlaySound = MagicMock(side_effect=play)
            synthesized = []

            def synthesize(text, name, filename, **kwargs):
                synthesized.append(text)
                self.write_wav(filename)

            with patch("assistant.local_voice.os", SimpleNamespace(name="nt")), patch.dict("sys.modules", {"winsound": sound}), patch.object(driver, "synthesize", side_effect=synthesize):
                old = driver.prepare("이전 답변", "local:F1")
                first = threading.Thread(target=old)
                first.start()
                try:
                    self.assertTrue(started.wait(3))
                    driver.stop()
                finally:
                    driver.stop()
                    first.join(3)
                self.assertFalse(first.is_alive())
                self.assertEqual(played[1], None)
                self.assertFalse(Path(played[0]).exists())
                stale = driver.prepare("대기 중 취소된 답변", "local:F1")
                new = driver.prepare("새 답변", "local:F2", speed=1.4)
                started.clear()
                second = threading.Thread(target=new)
                second.start()
                try:
                    self.assertTrue(started.wait(3))
                    stale()  # An old worker starting late cannot stop new audio.
                    self.assertIsNotNone(played[-1])
                finally:
                    driver.stop()
                    second.join(3)
                self.assertFalse(second.is_alive())
                self.assertEqual(synthesized, ["이전 답변", "새 답변"])
                self.assertFalse(Path(played[-2]).exists())

    def test_closing_local_voice_cancels_prepared_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            driver = LocalVoice(folder)
            work = driver.prepare("종료 전 답변", "local:F1")
            driver.close()
            with patch.object(driver, "synthesize") as synthesize:
                work()
                synthesize.assert_not_called()
