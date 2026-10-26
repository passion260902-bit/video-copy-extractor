import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from video_copy_extractor import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.media = self.root / "sample.wav"
        self.media.write_bytes(b"local media fixture")
        self.model = self.root / "local-model"
        self.model.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json"):
            (self.model / name).write_bytes(b"synthetic model fixture")
        self.engine = Mock()
        self.engine.transcribe.return_value = (
            iter([SimpleNamespace(start=0, end=1, text="1"), SimpleNamespace(start=1, end=2, text="1")]),
            SimpleNamespace(duration=2.0, language="zh"),
        )
        self.constructor = Mock(return_value=self.engine)
        self.module = SimpleNamespace(WhisperModel=self.constructor)

    def run_transcribe(self, **kwargs):
        with patch.dict("sys.modules", {"faster_whisper": self.module}), patch.object(transcribe, "_decode_audio", return_value=[0] * 32000):
            return transcribe.transcribe_audio(self.media, self.model, **kwargs)

    def test_cpu_int8_local_files_only_and_no_auth_token(self):
        result = self.run_transcribe(language="auto")
        self.constructor.assert_called_once_with(str(self.model.resolve()), device="cpu", compute_type="int8", num_workers=1, local_files_only=True, use_auth_token=False)
        self.assertIsNone(self.engine.transcribe.call_args.kwargs["language"])
        self.assertFalse(self.engine.transcribe.call_args.kwargs["vad_filter"])
        self.assertEqual(result["text"], "1\n1")
        self.assertEqual(result["metadata"]["media_duration_seconds"], 2.0)
        self.assertTrue(result["metadata"]["audio_decoded_fully"])
        self.assertFalse(result["metadata"]["accuracy_verified"])
        self.assertFalse(result["metadata"]["completeness_verified"])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_missing_tokenizer_fails_before_engine_import_or_decode(self):
        (self.model / "tokenizer.json").unlink()
        with patch.object(transcribe, "_decode_audio") as decode, self.assertRaises(ValueError):
            self.run_transcribe()
        self.constructor.assert_not_called()
        decode.assert_not_called()

    def test_hub_name_or_missing_model_is_not_downloaded(self):
        with patch.dict("sys.modules", {"faster_whisper": self.module}), self.assertRaises(ValueError):
            transcribe.transcribe_audio(self.media, self.root / "not-downloaded-model")
        self.constructor.assert_not_called()

    def test_playlist_suffix_is_rejected_before_engine(self):
        self.media = self.media.with_suffix(".m3u8")
        self.media.write_text("#EXTM3U\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_transcribe()
        self.constructor.assert_not_called()

    def test_file_size_limit_is_enforced(self):
        with patch.object(transcribe, "MAX_MEDIA_BYTES", 1), self.assertRaises(ValueError):
            self.run_transcribe()
        self.constructor.assert_not_called()

    def test_decode_failure_does_not_load_engine_or_return_partial_result(self):
        with patch.dict("sys.modules", {"faster_whisper": self.module}), patch.object(transcribe, "_decode_audio", side_effect=ValueError("private internal details")), self.assertRaises(RuntimeError) as error:
            transcribe.transcribe_audio(self.media, self.model)
        self.constructor.assert_not_called()
        self.assertNotIn("private internal", str(error.exception))

    def test_lazy_iterator_failure_does_not_return_partial_success(self):
        def broken_iterator():
            yield SimpleNamespace(start=0, end=1, text="partial")
            raise OSError("private internal details")
        self.engine.transcribe.return_value = (broken_iterator(), SimpleNamespace(duration=2.0, language="en"))
        with self.assertRaises(RuntimeError) as error:
            self.run_transcribe()
        self.assertNotIn("private internal", str(error.exception))

    def test_empty_transcription_is_failure(self):
        self.engine.transcribe.return_value = (iter([]), SimpleNamespace(duration=2.0, language="en"))
        with self.assertRaisesRegex(RuntimeError, "no speech text"):
            self.run_transcribe()

    def test_invalid_engine_timing_is_failure(self):
        for start, end in [(float("nan"), 1), (0, float("inf")), (1, 0), (True, 2)]:
            with self.subTest(start=start, end=end):
                self.engine.transcribe.return_value = (iter([SimpleNamespace(start=start, end=end, text="text")]), SimpleNamespace(duration=2.0, language="en"))
                with self.assertRaises(RuntimeError):
                    self.run_transcribe()

    def test_engine_time_well_beyond_decoded_audio_is_rejected(self):
        self.engine.transcribe.return_value = (iter([SimpleNamespace(start=0, end=4, text="text")]), SimpleNamespace(duration=2.0, language="en"))
        with self.assertRaises(RuntimeError):
            self.run_transcribe()

    def test_small_engine_time_overrun_is_flagged(self):
        self.engine.transcribe.return_value = (iter([SimpleNamespace(start=0, end=2.2, text="text")]), SimpleNamespace(duration=2.0, language="en"))
        result = self.run_transcribe()
        self.assertIn("segment_outside_audio_duration", result["metadata"]["warnings"])
        self.assertFalse(result["metadata"]["accuracy_verified"])


@unittest.skipUnless(importlib.util.find_spec("av") and importlib.util.find_spec("numpy"), "optional audio dependencies are not installed")
class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.media = Path(self.directory.name) / "sample.wav"
        with wave.open(str(self.media), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 32000)

    def test_fully_decode_generated_audio_without_ffmpeg_executable(self):
        audio = transcribe._decode_audio(self.media)
        self.assertEqual(len(audio), 32000)
        self.assertEqual(str(audio.dtype), "float32")

    def test_duration_limit_is_measured_from_decoded_samples(self):
        with patch.object(transcribe, "MAX_AUDIO_SECONDS", 1), self.assertRaises(ValueError):
            transcribe._decode_audio(self.media)

    def test_renamed_playlist_cannot_select_playlist_demuxer(self):
        self.media.write_bytes(b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:1,\nmissing-segment.ts\n")
        with self.assertRaises(Exception):
            transcribe._decode_audio(self.media)


if __name__ == "__main__":
    unittest.main()
