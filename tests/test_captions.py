import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_copy_extractor import captions
from video_copy_extractor.captions import read_captions


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def read(self, text, suffix="srt"):
        path = Path(self.directory.name) / ("sample." + suffix)
        path.write_text(text, encoding="utf-8")
        return read_captions(path)

    def test_srt_preserves_numbers_short_lines_and_repeated_cues(self):
        result = self.read("1\n00:00:00,000 --> 00:00:01,000\n123\n\n2\n00:00:01,000 --> 00:00:02,000\n是\n\n3\n00:00:02,000 --> 00:00:03,000\n是\n第二行\n")
        self.assertEqual(result["text"], "123\n是\n是\n第二行")
        self.assertEqual([cue["id"] for cue in result["segments"]], [0, 1, 2])
        self.assertFalse(result["metadata"]["accuracy_verified"])
        self.assertFalse(result["metadata"]["completeness_verified"])

    def test_vtt_preserves_text_and_retains_removed_markup_as_evidence(self):
        result = self.read("WEBVTT\n\nNOTE metadata\nnot spoken\n\ncue-a\n00:00.000 --> 00:01.500 align:start\n<v Speaker><c.green>2 &amp; 3</c></v>\n", "vtt")
        self.assertEqual(result["text"], "2 & 3")
        self.assertIn("<v Speaker>", result["segments"][0]["raw_text"])

    def test_json3_joins_parts_without_inserting_or_removing_words(self):
        result = self.read(json.dumps({"events": [
            {"id": 1},
            {"tStartMs": 100, "dDurationMs": 900, "segs": [{"utf8": "12"}, {"utf8": " 34"}]},
            {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "12 34"}]},
        ]}), "json3")
        self.assertEqual(result["text"], "12 34\n12 34")
        self.assertEqual(result["segments"][0]["start"], 0.1)

    def test_rolling_and_overlapping_cues_are_flagged_and_not_deduplicated(self):
        result = self.read(json.dumps({"events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "A"}]},
            {"tStartMs": 1000, "dDurationMs": 2000, "aAppend": 1, "segs": [{"utf8": "A B"}]},
        ]}), "json3")
        self.assertEqual(result["text"], "A\nA B")
        self.assertEqual(set(result["metadata"]["warnings"]), {"rolling_caption_events", "overlapping_cues"})
        self.assertTrue(result["metadata"]["requires_review"])

    def test_out_of_order_cues_are_preserved_and_flagged(self):
        result = self.read("1\n00:00:02,000 --> 00:00:03,000\nA\n\n2\n00:00:00,000 --> 00:00:01,000\nB\n")
        self.assertEqual(result["text"], "A\nB")
        self.assertIn("out_of_order_cues", result["metadata"]["warnings"])

    def test_invalid_text_timestamps_fail(self):
        for start, end in [("00:61:00,000", "01:00:00,000"), ("00:00:01,000", "00:00:00,000"), ("00:00:00,000", "00:00:00,000")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.read(f"1\n{start} --> {end}\ntext\n")

    def test_invalid_json_timestamps_and_missing_duration_fail(self):
        for timing in [{"tStartMs": 0}, {"tStartMs": -1, "dDurationMs": 100}, {"tStartMs": True, "dDurationMs": 100}, {"tStartMs": 0, "dDurationMs": float("inf")}, {"tStartMs": 0, "dDurationMs": 0}]:
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                self.read(json.dumps({"events": [{**timing, "segs": [{"utf8": "text"}]}]}), "json3")

    def test_empty_and_malformed_transcripts_fail(self):
        for text, suffix in [("", "srt"), ("{", "json3"), ('{"events": []}', "json3"), ("00:00.000 --> 00:01.000\ntext", "vtt"), ("unstructured prose", "srt")]:
            with self.subTest(text=text, suffix=suffix), self.assertRaises(ValueError):
                self.read(text, suffix)

    def test_explicit_format_and_utf8_bom(self):
        path = Path(self.directory.name) / "captions.txt"
        path.write_text("\ufeff1\n00:00:00,000 --> 00:00:01,000\n原文\n", encoding="utf-8")
        self.assertEqual(read_captions(path, format="SRT")["text"], "原文")

    def test_read_failure_does_not_disclose_local_path(self):
        missing = Path(self.directory.name) / "private-caption.srt"
        with self.assertRaises(ValueError) as error:
            read_captions(missing)
        self.assertNotIn(str(missing), str(error.exception))

    def test_oversize_input_is_rejected_without_truncation(self):
        with patch.object(captions, "MAX_CAPTION_BYTES", 10), self.assertRaisesRegex(ValueError, "not truncated"):
            self.read("1\n00:00:00,000 --> 00:00:01,000\ntext\n")

    def test_cue_past_one_hour_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one-hour"):
            self.read("1\n00:59:59,000 --> 01:00:00,001\ntext\n")

    def test_cue_ending_at_exactly_one_hour_is_allowed(self):
        result = self.read("1\n00:59:59,000 --> 01:00:00,000\ntext\n")
        self.assertEqual(result["segments"][0]["end"], 3600)

    def test_abnormally_large_single_cue_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not truncated"):
            self.read("1\n00:00:00,000 --> 00:00:01,000\n" + "字" * 4001 + "\n")


if __name__ == "__main__":
    unittest.main()
