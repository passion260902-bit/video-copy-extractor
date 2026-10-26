"""Read local subtitle files without silently dropping or deduplicating cues."""

import html
import json
import math
from pathlib import Path
import re


MAX_CAPTION_BYTES = 4 * 1024 * 1024
MAX_CAPTION_SECONDS = 3600
MAX_CUE_CHARACTERS = 4000
_SRT_TIME = re.compile(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})")
_VTT_TIME = re.compile(r"(?:(\d{2,}):)?([0-5]\d):([0-5]\d)\.(\d{3})")
_VTT_TAG = re.compile(
    r"</?(?:b|i|u|ruby|rt|c(?:\.[^\s<>]+)?|v(?:\s+[^<>]*)?|lang(?:\s+[^<>]*)?)>"
    r"|<(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}>"
)


def _time(value, subtitle_format):
    match = (_SRT_TIME if subtitle_format == "srt" else _VTT_TIME).fullmatch(value)
    if match is None:
        raise ValueError("Invalid subtitle timestamp.")
    hours, minutes, seconds, milliseconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Subtitle timing must be a finite number.")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Subtitle timing must be finite and nonnegative.")
    return float(value)


def _text_cues(contents, subtitle_format):
    blocks = re.split(r"\n[\t ]*\n", contents.replace("\r\n", "\n").replace("\r", "\n").strip())
    if subtitle_format == "vtt":
        if not blocks or not re.match(r"^WEBVTT(?:[\t ].*)?(?:\n|$)", blocks[0]):
            raise ValueError("WebVTT header is missing or invalid.")
        if "-->" in blocks[0]:
            raise ValueError("WebVTT header must be separated from cues by a blank line.")
        blocks = blocks[1:]
    segments = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        if subtitle_format == "vtt" and (
            re.match(r"^NOTE(?:[\t ]|$)", lines[0]) or lines[0] in {"STYLE", "REGION"}
        ):
            continue
        timing_index = 0 if "-->" in lines[0] else 1
        if timing_index >= len(lines) or "-->" not in lines[timing_index]:
            raise ValueError("Subtitle cue has no valid timing line.")
        timing = lines[timing_index].split("-->")
        if len(timing) != 2:
            raise ValueError("Subtitle cue has an invalid timing line.")
        start = _time(timing[0].strip(), subtitle_format)
        end_and_settings = timing[1].strip().split()
        if not end_and_settings:
            raise ValueError("Subtitle cue has no end timestamp.")
        if subtitle_format == "srt" and len(end_and_settings) != 1:
            raise ValueError("Unsupported SRT timing suffix.")
        end = _time(end_and_settings[0], subtitle_format)
        raw_text = "\n".join(lines[timing_index + 1:])
        text = html.unescape(_VTT_TAG.sub("", raw_text)) if subtitle_format == "vtt" else raw_text
        if not text.strip():
            raise ValueError("Subtitle cue has no text.")
        cue = {"id": len(segments), "start": start, "end": end, "text": text}
        if text != raw_text:
            cue["raw_text"] = raw_text
        segments.append(cue)
    return segments, []


def _json3_cues(contents):
    try:
        document = json.loads(contents)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("Invalid JSON3 subtitle document.") from None
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise ValueError("JSON3 subtitles require an events array.")
    segments, warnings = [], []
    for event in document["events"]:
        if not isinstance(event, dict):
            raise ValueError("Invalid JSON3 event.")
        if event.get("aAppend") and "rolling_caption_events" not in warnings:
            warnings.append("rolling_caption_events")
        if "segs" not in event:
            continue  # Window definition and other non-text events.
        if not isinstance(event["segs"], list):
            raise ValueError("Invalid JSON3 text segments.")
        pieces = []
        for piece in event["segs"]:
            if not isinstance(piece, dict):
                raise ValueError("Invalid JSON3 text segment.")
            if "utf8" in piece:
                if not isinstance(piece["utf8"], str):
                    raise ValueError("JSON3 subtitle text must be a string.")
                pieces.append(piece["utf8"])
        if not pieces:
            continue
        if "tStartMs" not in event or "dDurationMs" not in event:
            raise ValueError("JSON3 text events require explicit start and duration; timing was not inferred.")
        start = _number(event["tStartMs"]) / 1000
        duration = _number(event["dDurationMs"]) / 1000
        segments.append({"id": len(segments), "start": start, "end": start + duration, "text": "".join(pieces)})
    return segments, warnings


def read_captions(path: Path, format: str | None = None) -> dict:
    """Return cues and verbatim text; rolling captions remain explicitly unreviewed.

    Known WebVTT presentation tags are removed from ``text`` and retained in
    ``raw_text``. Repeated, numeric, short, overlapping, and out-of-order cues
    are never silently discarded. Text-bearing JSON3 events without explicit
    duration fail instead of receiving invented timings.
    """
    path = Path(path)
    subtitle_format = (format or path.suffix.lstrip(".")).lower()
    if subtitle_format == "json":
        subtitle_format = "json3"
    if subtitle_format not in {"srt", "vtt", "json3"}:
        raise ValueError("Supported subtitle formats are srt, vtt, and json3.")
    if not path.is_file():
        raise ValueError("Caption input must be a local regular file.")
    try:
        with path.open("rb") as source:
            encoded = source.read(MAX_CAPTION_BYTES + 1)
        if len(encoded) > MAX_CAPTION_BYTES:
            raise ValueError("Caption file exceeds the 4 MiB limit; it was not truncated.")
        contents = encoded.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("Caption file could not be read as UTF-8 text.") from None
    if subtitle_format == "json3":
        segments, warnings = _json3_cues(contents)
    else:
        segments, warnings = _text_cues(contents, subtitle_format)
    if not segments or not any(segment["text"].strip() for segment in segments):
        raise ValueError("Subtitle transcript is empty.")
    previous_start, previous_end = -1.0, -1.0
    for segment in segments:
        start, end = _number(segment["start"]), _number(segment["end"])
        if end <= start:
            raise ValueError("Subtitle end timestamp must be later than its start.")
        if end > MAX_CAPTION_SECONDS:
            raise ValueError("Subtitle timestamps exceed the one-hour limit.")
        if len(segment["text"]) > MAX_CUE_CHARACTERS or len(segment.get("raw_text", "")) > MAX_CUE_CHARACTERS:
            raise ValueError("Subtitle cue exceeds the 4000-character limit; it was not truncated.")
        if start < previous_start and "out_of_order_cues" not in warnings:
            warnings.append("out_of_order_cues")
        if start < previous_end and "overlapping_cues" not in warnings:
            warnings.append("overlapping_cues")
        previous_start, previous_end = start, max(previous_end, end)
    return {
        "segments": segments,
        "text": "\n".join(segment["text"] for segment in segments),
        "metadata": {
            "source_type": "captions",
            "format": subtitle_format,
            "segment_count": len(segments),
            "accuracy_verified": False,
            "completeness_verified": False,
            "requires_review": True,
            "warnings": warnings,
        },
    }
