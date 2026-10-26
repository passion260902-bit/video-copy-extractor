import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


from video_copy_extractor import review as MODULE

class ApplyReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw_path = self.root / "raw.json"
        self.review_path = self.root / "review.json"
        self.output = self.root / "new" / "output"
        self.raw = {"segments": [
            {"id": 0, "start": 0, "end": 1.2, "text": "  123  "},
            {"id": 1, "start": 1.2, "end": 2.4, "text": "再见"},
            {"id": 2, "start": 2.4, "end": 3, "text": "再见"},
        ]}
        self.review = {"edits": [], "uncertainties": [], "source": {"title": "测试视频"}}

    def run_review(self):
        self.raw_path.write_text(json.dumps(self.raw, ensure_ascii=False), encoding="utf-8")
        self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        return MODULE.apply_review(self.raw_path, self.review_path, self.output)

    def edit(self, **overrides):
        edit = {"segment_id": 1, "before": "再见", "after": "再见。", "reason": "核对字幕",
                "evidence": "frame_003.jpg，1.5 秒字幕"}
        return dict(edit, **overrides)

    def assert_invalid(self):
        with self.assertRaises(ValueError):
            self.run_review()
        self.assertFalse(self.output.parent.exists(), "invalid input must create no output directories")

    def test_preserves_repetition_short_numbers_and_spaces(self):
        self.run_review()
        self.assertEqual((self.output / "transcript.txt").read_text(encoding="utf-8"), "  123  \n再见\n再见\n")
        self.assertEqual(len(list(self.output.iterdir())), 4)

    def test_edit_exactly_one_segment_with_hash_and_no_accuracy_claim(self):
        self.review.update(edits=[self.edit()], accuracy_verified=True)
        self.run_review()
        report = json.loads((self.output / "review.json").read_text(encoding="utf-8"))
        self.assertFalse(report["accuracy_verified"])
        self.assertEqual(report["raw_sha256"], hashlib.sha256(self.raw_path.read_bytes()).hexdigest())
        self.assertEqual(report["raw_source"], self.raw_path.name)
        self.assertEqual((self.output / "transcript.txt").read_text(encoding="utf-8"), "  123  \n再见。\n再见\n")
        self.assertIn("[00:00:01.200 --> 00:00:02.400] 再见。", (self.output / "transcript_timed.txt").read_text(encoding="utf-8"))

    def test_before_mismatch_rejected(self):
        self.review["edits"] = [self.edit(before="再见 ")]
        self.assert_invalid()

    def test_duplicate_edit_rejected(self):
        self.review["edits"] = [self.edit(), self.edit()]
        self.assert_invalid()

    def test_invalid_id_rejected(self):
        for sid in (99, True, "1", -1):
            with self.subTest(sid=sid):
                self.review["edits"] = [self.edit(segment_id=sid)]
                self.assert_invalid()

    def test_duplicate_raw_id_rejected(self):
        self.raw["segments"][2]["id"] = 1
        self.assert_invalid()

    def test_missing_evidence_or_reason_rejected_even_for_deletion(self):
        for overrides in ({"evidence": " "}, {"evidence": []}, {"evidence": [""]},
                          {"reason": ""}, {"after": "", "evidence": None}):
            with self.subTest(overrides=overrides):
                self.review["edits"] = [self.edit(**overrides)]
                self.assert_invalid()

    def test_evidenced_deletion_removes_only_target_segment(self):
        self.review["edits"] = [self.edit(after="", reason="短片段复核确认重复解码",
                                          evidence=["tail.wav 独立转写", "字幕帧 3"])]
        self.run_review()
        self.assertEqual((self.output / "transcript.txt").read_text(encoding="utf-8"), "  123  \n再见\n")
        self.assertNotIn("[00:00:01.200 -->", (self.output / "transcript_timed.txt").read_text(encoding="utf-8"))

    def test_uncertainty_is_separate_and_timestamped(self):
        self.review["uncertainties"] = [{"segment_id": 2, "note": "读音不清，需复听"}]
        self.run_review()
        body, notes = (self.output / "transcript.md").read_text(encoding="utf-8").split("## 待核对")
        self.assertNotIn("读音不清", body)
        self.assertIn("00:00:02.400–00:00:03.000 （片段 2）：读音不清", notes)
        self.assertNotIn("读音不清", (self.output / "transcript.txt").read_text(encoding="utf-8"))

    def test_duplicate_uncertainty_rejected(self):
        self.review["uncertainties"] = [{"segment_id": 1, "note": "需复听"}] * 2
        self.assert_invalid()

    def test_existing_directory_is_never_overwritten(self):
        self.output.mkdir(parents=True)
        sentinel = self.output / "transcript.txt"
        sentinel.write_text("现有结果", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.run_review()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "现有结果")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_empty_raw_is_not_successful_transcription(self):
        for segments in ([], [{"id": 0, "start": 0, "end": 1, "text": "  "}]):
            with self.subTest(segments=segments):
                self.raw["segments"] = segments
                self.assert_invalid()

    def test_deleting_all_text_is_not_successful_transcription(self):
        self.review["edits"] = [self.edit(segment_id=s["id"], before=s["text"], after="")
                                for s in self.raw["segments"]]
        self.assert_invalid()


if __name__ == "__main__":
    unittest.main()
