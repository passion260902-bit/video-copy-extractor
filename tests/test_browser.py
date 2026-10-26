"""No browser or network is opened by these tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_copy_extractor.acquire import SafeError
from video_copy_extractor.browser import _profile, _save_snapshot


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = {"platform": "xiaohongshu", "id": "a" * 24,
                       "source_url": "https://www.xiaohongshu.com/explore/" + "a" * 24}

    def test_existing_unowned_profile_is_rejected(self):
        profile = self.root / "profile"
        profile.mkdir()
        (profile / "Cookies").write_text("synthetic private data")
        with self.assertRaises(SafeError):
            _profile(profile)
        self.assertEqual((profile / "Cookies").read_text(), "synthetic private data")

    def test_new_owned_profile_can_be_reused(self):
        profile = _profile(self.root / "profile")
        (profile / "browser-state").write_text("synthetic")
        self.assertEqual(_profile(profile), profile)

    def test_exact_note_identity_required(self):
        with patch("video_copy_extractor.browser.download_asset") as download:
            with self.assertRaises(SafeError) as error:
                _save_snapshot({"id": "b" * 24, "caption": "https://cdn.xhscdn.com/caption.srt"}, self.target, self.root)
        self.assertEqual(error.exception.code, "video_mismatch")
        download.assert_not_called()

    def test_caption_precedes_media_and_signed_url_is_not_returned(self):
        data = {"id": self.target["id"], "caption": "https://cdn.xhscdn.com/sub.srt?token=PRIVATE",
                "media": "https://cdn.xhscdn.com/media.mp4?token=PRIVATE", "duration": 20}
        with patch("video_copy_extractor.browser.download_asset") as download:
            result = _save_snapshot(data, self.target, self.root)
        self.assertEqual(result["caption_format"], "srt")
        self.assertIsNone(result["media_path"])
        self.assertNotIn("PRIVATE", repr(result))
        download.assert_called_once()

    def test_duration_limit_before_any_download(self):
        with patch("video_copy_extractor.browser.download_asset") as download:
            with self.assertRaises(SafeError) as error:
                _save_snapshot({"id": self.target["id"], "duration": 3601}, self.target, self.root)
        self.assertEqual(error.exception.code, "duration_limit")
        download.assert_not_called()

    def test_browser_media_uses_supported_container_suffix(self):
        data = {"id": self.target["id"], "media": "https://cdn.xhscdn.com/media", "duration": 2}
        def write_asset(url, output, platform, limit, headers):
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"synthetic")
        with patch("video_copy_extractor.browser.download_asset", side_effect=write_asset):
            result = _save_snapshot(data, self.target, self.root)
        from video_copy_extractor.transcribe import _FORMATS
        self.assertIn(result["media_path"].suffix, _FORMATS)
        self.assertEqual(result["media_path"].name, "media.mp4")


if __name__ == "__main__":
    unittest.main()
