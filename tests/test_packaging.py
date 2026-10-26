import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("pack_release", Path(__file__).resolve().parents[1] / "scripts/pack_release.py")
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("SKILL.md", "README.md"):
            (self.root / name).write_text("public source", encoding="utf-8")
        (self.root / "release-files.txt").write_text("SKILL.md\nREADME.md\n", encoding="utf-8")

    def test_unlisted_private_files_never_enter_archive(self):
        (self.root / ".env").write_text("private-marker")
        (self.root / "jobs").mkdir()
        (self.root / "jobs/private.txt").write_text("private-marker")
        output = self.root / "release.zip"
        pack.build(self.root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            self.assertNotIn(b"private-marker", b"".join(archive.read(n) for n in archive.namelist()))
            manifest = json.loads(archive.read("video-copy-extractor/RELEASE-MANIFEST.json"))
            self.assertEqual(len(manifest["files"]), 2)

    def test_path_traversal_and_private_files_are_rejected(self):
        for name in ("../secret", ".env", "jobs/private.txt", "C:" + "/secret", "SKILL.md\nSKILL.md"):
            with self.subTest(name=name):
                (self.root / "release-files.txt").write_text(name)
                with self.assertRaises(ValueError):
                    pack.collect(self.root)

    def test_home_path_scan_does_not_echo_secret(self):
        secret = "/Us" + "ers/PRIVATE_PERSON/input.txt"
        (self.root / "README.md").write_text(secret)
        with self.assertRaises(ValueError) as error:
            pack.collect(self.root)
        self.assertNotIn(secret, str(error.exception))

    def test_symlink_rejected(self):
        (self.root / "SKILL.md").unlink()
        try:
            (self.root / "SKILL.md").symlink_to(self.root / "README.md")
        except OSError:
            self.skipTest("Current OS account cannot create symlinks")
        with self.assertRaises(ValueError):
            pack.collect(self.root)


if __name__ == "__main__":
    unittest.main()
