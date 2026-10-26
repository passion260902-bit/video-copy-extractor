"""Local CPU transcription using an explicitly supplied CTranslate2 model."""

import io
import itertools
import math
from pathlib import Path


MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_AUDIO_SECONDS = 3600
SAMPLE_RATE = 16000
_FORMATS = {
    ".mp4": "mov", ".m4a": "mov", ".mov": "mov",
    ".mkv": "matroska", ".webm": "matroska", ".mp3": "mp3",
    ".wav": "wav", ".flac": "flac", ".ogg": "ogg", ".opus": "ogg",
    ".aac": "aac", ".aiff": "aiff", ".aif": "aiff",
}


def _decode_audio(media):
    import av
    import numpy as np

    # Force a non-playlist demuxer: a renamed manifest cannot select HLS/concat.
    # External MP4 references are disabled; no network protocol is permitted.
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    raw = io.BytesIO()
    with media.open("rb") as source, av.open(
        source, mode="r", format=_FORMATS[media.suffix.lower()],
        options={"protocol_whitelist": "file,pipe", "enable_drefs": "0", "use_absolute_path": "0"},
    ) as container:
        if not container.streams.audio:
            raise ValueError("Media has no audio stream.")
        for frame in itertools.chain(container.decode(audio=0), [None]):
            if frame is not None:
                frame.pts = None
            for decoded in resampler.resample(frame):
                pcm = decoded.to_ndarray().tobytes()
                if raw.tell() + len(pcm) > MAX_AUDIO_SECONDS * SAMPLE_RATE * 2:
                    raise ValueError("Audio exceeds the one-hour limit.")
                raw.write(pcm)
    if not raw.tell():
        raise ValueError("Decoded audio is empty.")
    pcm = np.frombuffer(raw.getbuffer(), dtype=np.int16)
    audio = pcm.astype(np.float32)
    del pcm
    raw.close()
    audio /= 32768.0
    return audio


def _timing(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("Speech engine returned invalid timing.")
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("Speech engine returned invalid timing.")
    return float(value)


def transcribe_audio(media: Path, model: Path, language: str | None = None) -> dict:
    """Decode local audio via PyAV and consume the full ASR iterator before success.

    No model name, Hub identifier, token, remote audio URL, or automatic download
    is accepted. A local tokenizer is mandatory because faster-whisper otherwise
    has a tokenizer download fallback even with ``local_files_only=True``.
    """
    media, model = Path(media), Path(model)
    if not media.is_file() or media.stat().st_size == 0:
        raise ValueError("Supply a nonempty local audio or video file.")
    if media.suffix.lower() not in _FORMATS:
        raise ValueError("Supply a supported media file; playlists and manifests are not accepted.")
    if media.stat().st_size > MAX_MEDIA_BYTES:
        raise ValueError("Media exceeds the 512 MiB limit.")
    if not model.is_dir() or any(
        not (model / name).is_file() or (model / name).stat().st_size == 0
        for name in ("config.json", "model.bin", "tokenizer.json")
    ):
        raise ValueError("Supply a local CTranslate2 model containing config.json, model.bin, and tokenizer.json.")
    if language == "auto":
        language = None
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError("Language must be a Whisper language code or auto.")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("Local speech recognition requires the optional faster-whisper dependency.") from None
    try:
        audio = _decode_audio(media)
        duration = len(audio) / SAMPLE_RATE
        engine = WhisperModel(
            str(model.resolve()), device="cpu", compute_type="int8", num_workers=1,
            local_files_only=True, use_auth_token=False,
        )
        iterator, info = engine.transcribe(
            audio, language=language, task="transcribe", beam_size=5,
            vad_filter=False, log_progress=False,
        )
        segments, warnings = [], []
        for segment in iterator:
            start, end = _timing(segment.start), _timing(segment.end)
            if end <= start or not isinstance(segment.text, str):
                raise RuntimeError("Speech engine returned an invalid segment.")
            if end > duration + 1.0:
                raise RuntimeError("Speech engine timing exceeds the decoded audio duration.")
            if end > duration + 0.05 and "segment_outside_audio_duration" not in warnings:
                warnings.append("segment_outside_audio_duration")
            segments.append({"id": len(segments), "start": start, "end": end, "text": segment.text})
        detected_language = info.language
        if not isinstance(detected_language, str):
            raise RuntimeError("Speech engine returned invalid language metadata.")
    except Exception:
        raise RuntimeError("Local speech recognition failed; check the model format and whether the file has decodable audio.") from None
    if not segments or not any(segment["text"].strip() for segment in segments):
        raise RuntimeError("Local speech recognition produced no speech text.")
    return {
        "segments": segments,
        "text": "\n".join(segment["text"] for segment in segments),
        "language": detected_language,
        "metadata": {
            "source_type": "local_asr",
            "engine": "faster-whisper",
            "device": "cpu",
            "compute_type": "int8",
            "audio_decoded_fully": True,
            "media_duration_seconds": duration,
            "language_detected": detected_language,
            "segment_count": len(segments),
            "accuracy_verified": False,
            "completeness_verified": False,
            "requires_review": True,
            "warnings": warnings,
        },
    }
