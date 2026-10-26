"""Shared workflow for CLI and MCP; private state never lives in the repository."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import uuid

from . import __version__


class ExtractionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "video-copy-extractor"


def model_path() -> Path | None:
    configured = os.environ.get("VCE_MODEL_PATH")
    if configured:
        return Path(configured).expanduser()
    default = data_dir() / "models" / "large-v3-turbo"
    return default if default.is_dir() else None


def doctor() -> dict:
    versions = {}
    for name in ("yt-dlp", "faster-whisper", "mcp", "playwright"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    model = model_path()
    return {
        "version": __version__, "python": platform.python_version(),
        "system": platform.system(), "architecture": platform.machine(),
        "dependencies": versions,
        "model_files_present": bool(model and all((model / n).is_file()
            for n in ("config.json", "model.bin", "tokenizer.json"))),
        "browser_enabled": os.environ.get("VCE_ENABLE_BROWSER") == "1",
        "network_tested": False, "extraction_tested": False,
        "note": "依赖存在不代表平台可访问；模型目录检查不代表模型加载成功。",
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _new_job(output_root: Path | None):
    root = Path(output_root) if output_root is not None else data_dir() / "jobs"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    job_id = uuid.uuid4().hex
    pending, final = root / (job_id + ".partial"), root / job_id
    pending.mkdir(mode=0o700)
    return job_id, pending, final


def _finish(source: dict, raw: dict, job_id: str, pending: Path, final: Path) -> dict:
    from .review import apply_review

    raw["accuracy_verified"] = False
    raw["completeness_verified"] = False
    _write_json(pending / "raw.json", raw)
    review = {"source": {key: source.get(key) or "" for key in ("title", "author")},
              "edits": [], "uncertainties": []}
    review["source"]["url"] = source.get("source_url", "")
    _write_json(pending / "review-input.json", review)
    apply_review(pending / "raw.json", pending / "review-input.json", pending / "text")
    transcript = (pending / "text" / "transcript.txt").read_text(encoding="utf-8")
    result = {
        "job_id": job_id, "status": "extracted_unverified", "source": source,
        "method": raw.get("method", "captions"),
        "accuracy_verified": False, "completeness_verified": False,
        "requires_review": True,
        "warnings": raw.get("metadata", {}).get("warnings", []) + ["提取完成，尚未逐句核对原声；不能视为百分之百准确或全文完整。"],
        "transcript": transcript, "segments": raw["segments"],
    }
    _write_json(pending / "result.json", result)
    pending.rename(final)
    return {**result, "output_dir": str(final.resolve())}


def _run(acquire_source, model, language, output_root):
    from .captions import read_captions
    from .transcribe import transcribe_audio

    job_id, pending, final = _new_job(output_root)
    try:
        obtained = acquire_source(pending)
        source = {k: obtained.get(k) for k in
                  ("platform", "id", "title", "author", "duration", "source_url", "caption_kind", "selection_note")}
        if obtained.get("caption_path"):
            raw = read_captions(Path(obtained["caption_path"]), obtained.get("caption_format"))
            raw["method"] = "captions"
        elif obtained.get("media_path"):
            selected_model = Path(model) if model is not None else model_path()
            if selected_model is None:
                raise ExtractionError("model_required", "没有可用字幕，需先配置本地语音模型；请阅读 README 的模型准备说明。")
            raw = transcribe_audio(Path(obtained["media_path"]), selected_model, language)
            raw["method"] = "asr"
        else:
            raise ExtractionError("no_content", "没有取得目标视频的字幕或可解码音频。")
        return _finish(source, raw, job_id, pending, final)
    except BaseException:
        # Includes cancellation: no apparently successful directory or leftover downloads.
        shutil.rmtree(pending, ignore_errors=True)
        raise


def extract_video(url: str, *, model: Path | None = None, browser: bool = False,
                  channel: str = "chrome", language: str | None = None,
                  output_root: Path | None = None) -> dict:
    from .acquire import acquire

    return _run(lambda pending: acquire(url, pending, browser=browser,
        profile_dir=data_dir() / "browser-profile", channel=channel), model, language, output_root)


def extract_local(path: Path, *, model: Path | None = None, language: str | None = None,
                  output_root: Path | None = None) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ExtractionError("file_missing", "输入文件不存在或不是普通文件。")
    is_caption = path.suffix.lower() in (".srt", ".vtt", ".json3")
    obtained = {"platform": "local", "id": "local", "title": "本地文件提取稿",
                "author": "", "duration": None, "source_url": "", "caption_kind": "local_unknown",
                "caption_path": path if is_caption else None,
                "caption_format": path.suffix[1:].lower() if is_caption else None,
                "media_path": None if is_caption else path}
    return _run(lambda pending: obtained, model, language, output_root)
