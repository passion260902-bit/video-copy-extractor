#!/usr/bin/env python3
"""Apply evidenced, exact-segment edits without normalizing transcript text."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def apply_review(raw_path, review_path, output_dir):
    raw_path, review_path, output_dir = map(Path, (raw_path, review_path, output_dir))
    raw_bytes = raw_path.read_bytes()
    raw = json.loads(raw_bytes)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    require(isinstance(raw, dict) and isinstance(raw.get("segments"), list),
            "raw must contain a segments list")
    require(isinstance(review, dict), "review must be an object")
    segments, indexed = raw["segments"], {}
    for segment in segments:
        require(isinstance(segment, dict), "segment must be an object")
        sid = segment.get("id")
        require(type(sid) is int and sid >= 0 and sid not in indexed,
                f"invalid or duplicate raw segment id: {sid!r}")
        require(isinstance(segment.get("text"), str), f"segment {sid}: text must be a string")
        times = [segment.get("start"), segment.get("end")]
        require(all(type(t) in (int, float) and math.isfinite(t) and t >= 0 for t in times),
                f"segment {sid}: invalid timestamps")
        require(times[0] <= times[1], f"segment {sid}: end precedes start")
        indexed[sid] = segment
    require(any(s["text"].strip() for s in segments), "raw contains no transcript text")

    edits = review.get("edits", [])
    uncertainties = review.get("uncertainties", [])
    source = review.get("source", {})
    require(isinstance(edits, list) and isinstance(uncertainties, list),
            "edits and uncertainties must be lists")
    require(isinstance(source, dict), "source must be an object")
    require(all(isinstance(source.get(key, ""), str) for key in ("title", "url", "author")),
            "source title, url and author must be strings")
    replacements, pending = {}, {}
    for edit in edits:
        require(isinstance(edit, dict), "edit must be an object")
        sid = edit.get("segment_id")
        require(type(sid) is int and sid in indexed and sid not in replacements,
                f"invalid or duplicate edit segment_id: {sid!r}")
        require(edit.get("before") == indexed[sid]["text"], f"segment {sid}: before mismatch")
        require(isinstance(edit.get("after"), str), f"segment {sid}: after must be a string")
        evidence = edit.get("evidence")
        require(nonempty(edit.get("reason")), f"segment {sid}: reason is required")
        require(nonempty(evidence) or (isinstance(evidence, list) and bool(evidence)
                                      and all(nonempty(item) for item in evidence)),
                f"segment {sid}: evidence is required, including for deletions")
        replacements[sid] = edit["after"]
    for uncertainty in uncertainties:
        require(isinstance(uncertainty, dict), "uncertainty must be an object")
        sid = uncertainty.get("segment_id")
        require(type(sid) is int and sid in indexed and sid not in pending,
                f"invalid or duplicate uncertainty segment_id: {sid!r}")
        require(nonempty(uncertainty.get("note")), f"segment {sid}: uncertainty note is required")
        pending[sid] = uncertainty["note"]

    texts, timed = [], []
    for segment in segments:
        sid = segment["id"]
        text = replacements.get(sid, segment["text"])
        if sid in replacements and text == "":
            continue  # Only explicit, evidenced deletion removes a segment.
        texts.append(text)
        timed.append(f'[{timestamp(segment["start"])} --> {timestamp(segment["end"])}] {text}')
    require(any(text.strip() for text in texts), "review removes all transcript text")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    report = dict(review)
    report.update(raw_source=raw_path.name, raw_sha256=digest,
                  accuracy_verified=False, source=source, edits=edits, uncertainties=uncertainties)
    status = "字幕或其他证据辅助校对稿" if edits else "原始转写整理稿"
    header = [f'# {source.get("title") or "视频逐字稿"}',
              f"{status}；未经完整准确性验证。"]
    for key, label in (("author", "作者"), ("url", "来源")):
        if source.get(key):
            header.append(f"{label}：{source[key]}")
    notes = []
    for sid, note in pending.items():
        segment = indexed[sid]
        notes.append(f'- {timestamp(segment["start"])}–{timestamp(segment["end"])} '
                     f'（片段 {sid}）：{note}')
    markdown = "\n\n".join(header + ["## 逐字稿", "\n\n".join(texts), "## 待核对",
                                      "\n".join(notes) or "未列出疑点；这不代表准确率为 100%。"])
    outputs = {
        "transcript.md": markdown + "\n",
        "transcript.txt": "\n".join(texts) + "\n",
        "transcript_timed.txt": "\n".join(timed) + "\n",
        "review.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    }
    # Validate and render everything before creating any output directory.
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in outputs.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        apply_review(args.raw, args.review, args.output_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Wrote reviewed transcript to {args.output_dir}")


if __name__ == "__main__":
    main()
