"""Build a public archive from an explicit, reviewed file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


PATTERNS = {
    "personal_home_path": re.compile(r"(?:/Us" + r"ers/[^/\s]+|/ho" + r"me/[^/\s]+|[A-Za-z]:[\\/]Us" + r"ers[\\/][^\\/\s]+)"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----"),
    "provider_key": re.compile(r"\b(?:gh[pousr]_" + r"[A-Za-z0-9]{30,}|github_pat_" + r"[A-Za-z0-9_]{30,}|sk-" + r"[A-Za-z0-9_-]{30,})\b"),
    "signed_share_token": re.compile(r"xsec_" + r"token=[A-Za-z0-9_%+/=-]{24,}"),
    "stored_cookie_header": re.compile(r"(?im)^Cookie:" + r"\s*\S.{20,}$"),
}


def collect(root: Path) -> dict[str, bytes]:
    entries = (root / "release-files.txt").read_text(encoding="utf-8").splitlines()
    files = {}
    for line in entries:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name or name in files:
            raise ValueError("Unsafe or duplicate allowlist entry.")
        if any(part in {".git", ".venv", "work", "jobs", "models", "browser-profile", "__pycache__"}
               or part.startswith((".env", "cookies")) for part in relative.parts):
            raise ValueError("Private runtime file listed in release allowlist.")
        path = root.joinpath(*relative.parts)
        if any(root.joinpath(*relative.parts[:i]).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError("Symlinks are not allowed in release files.")
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Missing or escaping release file.")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Release source file exceeds 2 MiB.")
        payload = path.read_bytes()
        contents = payload.decode("utf-8")
        for label, pattern in PATTERNS.items():
            if pattern.search(contents):
                # Report only file/rule, never the matched sensitive content.
                raise ValueError(f"Privacy scan rejected {name}: {label}")
        files[name] = payload
    if not files or "SKILL.md" not in files or "README.md" not in files:
        raise ValueError("Required public entrypoints are missing.")
    return files


def build(root: Path, output: Path) -> dict:
    files = collect(root)
    manifest = {"format": 1, "files": [
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ], "scan": "allowlist_and_pattern_scan_passed", "not_a_security_guarantee": True}
    files["RELEASE-MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo("video-copy-extractor/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o100644 << 16)
            archive.writestr(entry, data)
    return {"status": "packaged", "files": len(files), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.check:
        print(json.dumps({"status": "scan_passed", "files": len(collect(root))}))
    elif args.output is not None:
        print(json.dumps(build(root, args.output)))
    else:
        parser.error("Supply --check or --output. Existing archives are never overwritten.")


if __name__ == "__main__":
    main()
