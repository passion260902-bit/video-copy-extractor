import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_copy_extractor import core
from video_copy_extractor.__main__ import main


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.caption = self.root / "input.srt"
        self.caption.write_text("1\n00:00:00,000 --> 00:00:02,000\n123\n\n2\n00:00:01,000 --> 00:00:03,000\n123\n", encoding="utf-8")

    def test_caption_workflow_retains_raw_and_warnings(self):
        result = core.extract_local(self.caption, output_root=self.root / "jobs")
        out = Path(result["output_dir"])
        self.assertEqual(result["transcript"], "123\n123\n")
        self.assertEqual(result["status"], "extracted_unverified")
        self.assertFalse(result["accuracy_verified"])
        self.assertFalse(result["completeness_verified"])
        self.assertIn("overlapping_cues", result["warnings"])
        self.assertEqual(len(json.loads((out / "raw.json").read_text())["segments"]), 2)
        self.assertEqual(json.loads((out / "text/review.json").read_text())["raw_source"], "raw.json")

    def test_failure_cleans_partial_results_and_preserves_prior_job(self):
        first = core.extract_local(self.caption, output_root=self.root / "jobs")
        before = (Path(first["output_dir"]) / "raw.json").read_bytes()
        with patch("video_copy_extractor.captions.read_captions", side_effect=ValueError("bad caption")):
            with self.assertRaises(ValueError):
                core.extract_local(self.caption, output_root=self.root / "jobs")
        self.assertEqual(len(list((self.root / "jobs").iterdir())), 1)
        self.assertEqual((Path(first["output_dir"]) / "raw.json").read_bytes(), before)

    def test_without_model_no_caption_does_not_succeed(self):
        media = self.root / "sample.wav"
        media.write_bytes(b"invalid")
        with patch.object(core, "model_path", return_value=None):
            with self.assertRaises(core.ExtractionError) as error:
                core.extract_local(media, output_root=self.root / "jobs")
        self.assertEqual(error.exception.code, "model_required")
        self.assertEqual(list((self.root / "jobs").iterdir()), [])

    def test_cli_does_not_echo_secret_in_unexpected_error(self):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["vce", "doctor"]), patch.object(core, "doctor", side_effect=RuntimeError("private-marker")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main()
        self.assertEqual(code, 1)
        self.assertNotIn("private-marker", out.getvalue() + err.getvalue())

    def test_download_model_does_not_use_ambient_auth(self):
        try:
            import huggingface_hub
        except ImportError:
            self.skipTest("optional ASR dependency is not installed")
        with patch("sys.argv", ["vce", "download-model", "--name", "base", "--output", str(self.root / "model")]):
            with patch.object(huggingface_hub, "snapshot_download") as download:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 0)
        self.assertIs(download.call_args.kwargs["token"], False)
        self.assertNotIn("local_dir_use_symlinks", download.call_args.kwargs)
        self.assertIn("model.bin", download.call_args.kwargs["allow_patterns"])


if __name__ == "__main__":
    unittest.main()
