"""Install hash-locked dependencies into this distribution's private virtualenv."""
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise SystemExit("Please install Python 3.11, 3.12 or 3.13 (64-bit), then retry.")
    root = Path(__file__).resolve().parents[1]
    target = root / ".venv"
    if target.is_symlink():
        raise SystemExit("Refusing a linked virtual environment directory.")
    python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    print("Installing locked Python dependencies from PyPI. No speech model or browser will be downloaded.", flush=True)
    subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                    "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--require-hashes",
                    "-r", str(root / "requirements-lock.txt")], check=True, cwd=root)
    subprocess.run([str(python), str(root / "scripts/vce.py"), "doctor"], check=True, cwd=root)
    print("Setup finished. Follow README.md to connect your agent or MCP client.")


if __name__ == "__main__":
    main()
