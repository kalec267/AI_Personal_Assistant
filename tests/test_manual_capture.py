"""Exercise the real speech detector with PCM, without a microphone or network."""
import io
import math
import struct
import unittest
from unittest.mock import patch

import speech_recognition as sr

from assistant.core import UserError
from assistant.voice import recognize_once


class PCMSource(sr.AudioSource):
    def __init__(self, pcm):
        self.SAMPLE_RATE = 16000
        self.SAMPLE_WIDTH = 2
        self.CHUNK = 1024
        self.stream = io.BytesIO(pcm)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.stream.close()


def phrase(frames, peak):
    return b"".join(struct.pack("<h", int(peak * math.sin(2 * math.pi * 220 * n / 16000))) for n in range(frames))


class ManualCaptureTests(unittest.TestCase):
    def test_short_speech_at_stream_start_is_preserved_and_transcribed(self):
        pcm = phrase(4800, 500)  # 0.3 seconds: previously consumed as calibration.
        with patch.object(sr, "Microphone", return_value=PCMSource(pcm + b"\0\0" * 96000)), patch.object(sr.Recognizer, "recognize_google", return_value="넌 뭐야") as google:
            self.assertEqual(recognize_once(), "넌 뭐야")
        google.assert_called_once()
        self.assertIn(pcm, google.call_args.args[0].frame_data)

    def test_quiet_short_phrase_is_not_rejected_as_noise(self):
        pcm = phrase(3072, 260)  # RMS ~184, below the previous initial threshold.
        with patch.object(sr, "Microphone", return_value=PCMSource(pcm + b"\0\0" * 96000)), patch.object(sr.Recognizer, "recognize_google", return_value="넌 뭐야") as google:
            self.assertEqual(recognize_once(), "넌 뭐야")
        self.assertIn(pcm, google.call_args.args[0].frame_data)

    def test_speech_after_preparation_silence_still_works(self):
        pcm = phrase(4800, 500)
        with patch.object(sr, "Microphone", return_value=PCMSource(b"\0\0" * 12800 + pcm + b"\0\0" * 96000)), patch.object(sr.Recognizer, "recognize_google", return_value="오늘 일정") as google:
            self.assertEqual(recognize_once(), "오늘 일정")
        self.assertIn(pcm, google.call_args.args[0].frame_data)

    def test_silent_microphone_times_out_with_device_guidance_and_no_upload(self):
        with patch.object(sr, "Microphone", return_value=PCMSource(b"\0\0" * 96000)), patch.object(sr.Recognizer, "recognize_google") as google:
            with self.assertRaisesRegex(UserError, "마이크 이름과 입력 레벨"):
                recognize_once()
        google.assert_not_called()
