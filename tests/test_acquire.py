"""Offline boundary tests. All identities and URLs below are synthetic."""
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_copy_extractor.acquire import (
    MAX_CAPTION, MAX_MEDIA, SafeError, _Redirect, _caption, _duration,
    _options, acquire, download_asset, name_media, normalize_asset, normalize_url, validate_url,
)

YT_ID = "Example_123"
YT_URL = "https://www.youtube.com/watch?v=" + YT_ID
NOTE_ID = "a" * 24
DY_ID = "1" * 19


class FakeYDL:
    info = {}
    last_options = None
    requests = []
    error = None

    def __init__(self, options):
        type(self).last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, **kwargs):
        type(self).requests.append((url, kwargs))
        if self.error:
            raise self.error
        return dict(self.info)

    def process_ie_result(self, info, download):
        assert download is False
        return {**info, "url": info.get("formats", [{}])[0].get("url", "https://media.googlevideo.com/synthetic-audio")}


class Response(io.BytesIO):
    def geturl(self):
        return "https://media.googlevideo.com/synthetic-audio"


class AcquireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "output"
        FakeYDL.requests, FakeYDL.error = [], None
        FakeYDL.info = {"id": YT_ID, "title": "Synthetic video", "duration": 12,
                        "subtitles": {"en": [{"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:01,000\n123\n"}]}}
        self.fake = patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=FakeYDL)})
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def test_links_strip_tracking_and_keep_identity(self):
        target = normalize_url("https://youtu.be/" + YT_ID + "?si=private-value&list=another")
        self.assertEqual(target["source_url"], YT_URL)
        self.assertEqual(target["fetch_url"], YT_URL)

    def test_xhs_token_only_in_fetch_url(self):
        target = normalize_url("https://www.xiaohongshu.com/explore/" + NOTE_ID + "?xsec_token=PRIVATE&xsec_source=pc&other=secret")
        self.assertIn("PRIVATE", target["fetch_url"])
        self.assertNotIn("other", target["fetch_url"])
        self.assertNotIn("?", target["source_url"])

    def test_douyin_modal_wins_over_other_video_ids(self):
        target = normalize_url("https://www.douyin.com/user/synthetic?modal_id=" + DY_ID + "&vid=" + "2" * 19)
        self.assertEqual(target["id"], DY_ID)
        self.assertIn("modal_id", target["selection_note"])
        self.assertEqual(target["fetch_url"], "https://www.douyin.com/video/" + DY_ID)

    def test_rejects_unsafe_urls(self):
        for url in ("http://www.youtube.com/watch?v=" + YT_ID,
                    "https://www.youtube.com.evil.example/watch?v=" + YT_ID,
                    "https://password@www.youtube.com/watch?v=" + YT_ID,
                    "https://www.youtube.com:8443/watch?v=" + YT_ID,
                    "https://127.0.0.1/watch?v=" + YT_ID,
                    "https://www.youtube.com/watch?v=" + YT_ID + "\n",
                    "https://www.youtube.com\\@evil.example/watch?v=" + YT_ID):
            with self.subTest(url=url), self.assertRaises(SafeError):
                normalize_url(url)

    def test_rejects_channel_and_playlist(self):
        for url in ("https://www.youtube.com/playlist?list=synthetic",
                    "https://www.douyin.com/user/synthetic", "https://www.xiaohongshu.com/explore"):
            with self.assertRaises(SafeError):
                normalize_url(url)

    def test_redirect_checks_host_before_following(self):
        with self.assertRaises(SafeError):
            _Redirect("douyin").redirect_request(None, None, 302, "", {}, "https://evil.example/")
        with self.assertRaises(SafeError):
            _Redirect("youtube", asset=True).redirect_request(None, None, 302, "", {}, "http://media.googlevideo.com/file")

    def test_asset_subdomain_suffix_is_exact(self):
        validate_url("https://media.googlevideo.com/file", "youtube", asset=True)
        for url in ("https://googlevideo.com.evil.example/file", "https://evilgooglevideo.com/file"):
            with self.assertRaises(SafeError):
                validate_url(url, "youtube", asset=True)

    def test_known_xhs_http_cdn_upgrades_without_changing_resource(self):
        resource = "/a%2Fb/video?signature=a%2B+b%2F&empty=&v=1&v=2"
        self.assertEqual(normalize_asset("http://media.xhscdn.com" + resource, "xiaohongshu"),
                         "https://media.xhscdn.com" + resource)
        self.assertEqual(normalize_asset("http://media.xhscdn.com:80/file", "xiaohongshu"),
                         "https://media.xhscdn.com/file")

    def test_http_upgrade_rejects_credentials_bad_hosts_and_ports(self):
        for url in ("http://xhscdn.com.evil.example/file", "http://evilxhscdn.com/file",
                    "http://user:secret@media.xhscdn.com/file", "http://media.xhscdn.com:8080/file",
                    "http://media.xhscdn.com:443/file", "http://media.xhscdn.net/file",
                    "http://media.xhscdn.com\\@evil.example/file", "http://media.xhscdn.com/file\n"):
            with self.subTest(url=url), self.assertRaises(SafeError):
                normalize_asset(url, "xiaohongshu")
        with self.assertRaises(SafeError):
            normalize_asset("http://media.googlevideo.com/file", "youtube")

    def test_xhs_selection_receives_only_upgraded_https(self):
        FakeYDL.info = {"id": NOTE_ID, "duration": 2, "formats": [{
            "url": "http://media.xhscdn.com/synthetic?signature=unchanged%2Bvalue", "protocol": "http"}]}
        def save(url, output, platform, limit, headers):
            self.assertEqual(url, "https://media.xhscdn.com/synthetic?signature=unchanged%2Bvalue")
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"synthetic")
        with patch("video_copy_extractor.acquire.download_asset", side_effect=save):
            result = acquire("https://www.xiaohongshu.com/explore/" + NOTE_ID, self.out)
        self.assertEqual(result["media_path"].suffix, ".mp4")

    def test_caption_success_preserves_text_and_avoids_media(self):
        result = acquire(YT_URL, self.out)
        self.assertEqual(result["caption_format"], "srt")
        self.assertIn("123", result["caption_path"].read_text())
        self.assertIsNone(result["media_path"])
        self.assertEqual([p.name for p in self.out.iterdir()], ["captions.srt"])
        self.assertEqual(FakeYDL.requests[0][1], {"download": False, "process": False})

    def test_no_ambient_credentials_or_command_hooks(self):
        acquire(YT_URL, self.out)
        options = FakeYDL.last_options
        self.assertEqual(os.environ["YTDLP_NO_PLUGINS"], "1")
        self.assertIsNone(options["cookiesfrombrowser"])
        self.assertIsNone(options["cookiefile"])
        self.assertFalse(options["usenetrc"])
        self.assertFalse(options["writeinfojson"])
        self.assertTrue(options["ignoreconfig"])
        self.assertEqual(options["postprocessors"], [])
        self.assertEqual(options["exec_cmd"], {})
        self.assertEqual(options["remote_components"], [])
        self.assertEqual(options["max_filesize"], MAX_MEDIA)

    def test_metadata_identity_mismatch_never_falls_back(self):
        FakeYDL.info["id"] = "OtherID_123"
        with patch("video_copy_extractor.browser.acquire_browser") as fallback:
            with self.assertRaises(SafeError) as error:
                acquire(YT_URL, self.out, browser=True, profile_dir=Path(self.temp.name) / "profile")
            self.assertEqual(error.exception.code, "video_mismatch")
            fallback.assert_not_called()

    def test_playlist_result_rejected(self):
        FakeYDL.info["_type"] = "playlist"
        with self.assertRaises(SafeError) as error:
            acquire(YT_URL, self.out)
        self.assertEqual(error.exception.code, "not_single_video")

    def test_remote_exception_is_not_exposed(self):
        FakeYDL.error = ValueError("https://secret.example/?token=PRIVATE Cookie: sensitive")
        with self.assertRaises(SafeError) as error:
            acquire(YT_URL, self.out)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn("secret.example", str(error.exception))

    def test_duration_limit_fails_before_download(self):
        FakeYDL.info["duration"] = 3601
        with self.assertRaises(SafeError) as error:
            acquire(YT_URL, self.out)
        self.assertEqual(error.exception.code, "duration_limit")
        self.assertFalse(any(self.out.iterdir()))

    def test_inline_caption_limit(self):
        FakeYDL.info["subtitles"]["en"][0]["data"] = "x" * (MAX_CAPTION + 1)
        with self.assertRaises(SafeError) as error:
            acquire(YT_URL, self.out)
        self.assertEqual(error.exception.code, "caption_limit")

    def test_auto_translation_is_not_chosen(self):
        info = {"automatic_captions": {"zh-Hans": [{"ext": "srt", "url": "https://www.youtube.com/api?tlang=zh"}]}}
        self.assertIsNone(_caption(info))
        info["automatic_captions"]["en-orig"] = [{"ext": "json3", "data": "{}"}]
        self.assertEqual(_caption(info)[2], "automatic_original")

    def test_original_language_precedes_other_subtitles(self):
        info = {"language": "fr", "subtitles": {"zh": [{"ext": "srt", "data": "translated"}],
                                                   "fr": [{"ext": "srt", "data": "original"}]}}
        self.assertEqual(_caption(info)[0]["data"], "original")

    def test_sole_translated_manual_track_does_not_override_original_auto(self):
        info = {"subtitles": {"zh": [{"ext": "srt", "data": "translated"}]},
                "automatic_captions": {"en-orig": [{"ext": "json3", "data": "original"}]}}
        self.assertEqual(_caption(info)[0]["data"], "original")
        info["language"] = "en"
        info["automatic_captions"] = {}
        self.assertIsNone(_caption(info))

    def test_nonempty_output_is_not_modified(self):
        self.out.mkdir()
        saved = self.out / "keep.txt"
        saved.write_text("keep")
        with self.assertRaises(SafeError):
            acquire(YT_URL, self.out)
        self.assertEqual(saved.read_text(), "keep")

    def test_stream_limit_deletes_partial_output(self):
        output = Path(self.temp.name) / "asset"
        opener = SimpleNamespace(open=lambda *a, **k: Response(b"12345"))
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(SafeError):
                download_asset("https://media.googlevideo.com/file", output, "youtube", 4)
        self.assertFalse(output.exists())

    def test_empty_download_is_failure(self):
        output = Path(self.temp.name) / "asset"
        with patch("urllib.request.build_opener", return_value=SimpleNamespace(open=lambda *a, **k: Response(b""))):
            with self.assertRaises(SafeError):
                download_asset("https://media.googlevideo.com/file", output, "youtube", 4)
        self.assertFalse(output.exists())

    def test_existing_asset_is_not_deleted(self):
        output = Path(self.temp.name) / "asset"
        output.write_text("keep")
        with patch("urllib.request.build_opener", return_value=SimpleNamespace(open=lambda *a, **k: Response(b"new"))):
            with self.assertRaises(FileExistsError):
                download_asset("https://media.googlevideo.com/file", output, "youtube", 4)
        self.assertEqual(output.read_text(), "keep")

    def test_media_uses_bounded_reader_not_external_downloader(self):
        FakeYDL.info["subtitles"] = {}
        FakeYDL.info["formats"] = [{"url": "https://media.googlevideo.com/file", "protocol": "https"}]
        payload = b"\x00\x00\x00\x18ftypisom" + b"synthetic"
        with patch("urllib.request.build_opener", return_value=SimpleNamespace(open=lambda *a, **k: Response(payload))):
            result = acquire(YT_URL, self.out)
        self.assertEqual(result["media_path"].read_bytes(), payload)
        self.assertEqual(result["media_path"].suffix, ".mp4")

    def test_renamed_playlist_is_rejected(self):
        download = Path(self.temp.name) / "media.download"
        download.write_bytes(b"#EXTM3U\n#EXTINF:1\nhttps://evil.example/segment\n")
        with self.assertRaises(SafeError) as error:
            name_media(download)
        self.assertEqual(error.exception.code, "unsupported_container")
        self.assertFalse(download.exists())

    def test_each_platform_media_result_reaches_asr(self):
        from video_copy_extractor import transcribe
        model = Path(self.temp.name) / "model"
        model.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (model / name).write_bytes(b"synthetic")
        payload = b"\x00\x00\x00\x18ftypisom" + b"synthetic"
        def write_asset(url, output, platform, limit, headers):
            output.write_bytes(payload)
        examples = [(YT_URL, YT_ID, "googlevideo.com"),
                    ("https://www.douyin.com/video/" + DY_ID, DY_ID, "douyinvod.com"),
                    ("https://www.xiaohongshu.com/explore/" + NOTE_ID, NOTE_ID, "xhscdn.com")]
        for number, (url, ident, host) in enumerate(examples):
            with self.subTest(platform=host):
                FakeYDL.info = {"id": ident, "duration": 2, "formats": [{"url": "https://media." + host + "/synthetic"}]}
                with patch("video_copy_extractor.acquire.download_asset", side_effect=write_asset):
                    result = acquire(url, Path(self.temp.name) / str(number))
                engine = SimpleNamespace(transcribe=lambda *a, **k: (
                    iter([SimpleNamespace(start=0, end=1, text="synthetic")]), SimpleNamespace(language="en")))
                module = SimpleNamespace(WhisperModel=lambda *a, **k: engine)
                with patch.dict(sys.modules, {"faster_whisper": module}), patch.object(transcribe, "_decode_audio", return_value=[0] * 32000) as decode:
                    transcript = transcribe.transcribe_audio(result["media_path"], model)
                self.assertEqual(transcript["text"], "synthetic")
                decode.assert_called_once_with(result["media_path"])

    def test_unapproved_media_host_is_rejected(self):
        FakeYDL.info["subtitles"] = {}
        FakeYDL.info["formats"] = [{"url": "https://127.0.0.1/file", "protocol": "https"}]
        with self.assertRaises(SafeError) as error:
            acquire(YT_URL, self.out)
        self.assertEqual(error.exception.code, "no_direct_media")

    def test_explicit_xhs_browser_checks_platform_captions_first(self):
        with patch("video_copy_extractor.browser.acquire_browser", return_value={"caption_kind": "provided_original"}) as fallback:
            result = acquire("https://www.xiaohongshu.com/explore/" + NOTE_ID, self.out,
                             browser=True, profile_dir=Path(self.temp.name) / "profile")
        self.assertEqual(result["caption_kind"], "provided_original")
        self.assertEqual(FakeYDL.requests, [])
        fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
