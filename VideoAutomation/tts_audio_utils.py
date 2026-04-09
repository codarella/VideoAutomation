"""
tts_audio_utils.py — Script chunking and audio stitching utilities.

Splits narration scripts into TTS-ready chunks (preferring "Number X" segment
boundaries from the listicle format) and reassembles PCM audio chunks into a
final MP3 or WAV file.
"""

from __future__ import annotations

import io
import re
import struct
import wave
import logging
from pathlib import Path
from typing import Generator

logger = logging.getLogger("tts_pipeline")

# Regex that matches "Number 10," / "Number 9." etc. at the start of a line
_NUMBER_HEADING = re.compile(r"(?m)^(Number\s+\d+[\.,]?)", re.IGNORECASE)


# ── Script splitting ─────────────────────────────────────────────────────────


def split_script(
    script_text: str,
    chunk_mode: str = "numbered_segments",
    chunk_size_fallback: int = 4500,
) -> list[str]:
    """
    Split a narration script into a list of text chunks safe for one TTS call.

    Chunk modes:
      "numbered_segments" — split on "Number X" headings (natural listicle
          boundaries). Falls back to character-count splitting if no headings
          are found.
      "characters"        — always split by character count.

    Args:
        script_text:          Full script as a plain string.
        chunk_mode:           Splitting strategy.
        chunk_size_fallback:  Max characters per chunk for character-mode or
                              fallback when no Number headings are found.

    Returns:
        List of non-empty text chunks ready to send to the TTS API.
    """
    text = script_text.strip()
    if not text:
        return []

    if chunk_mode == "numbered_segments":
        chunks = _split_by_number_headings(text)
        if chunks:
            # Further split any chunk that is still too long
            result: list[str] = []
            for chunk in chunks:
                if len(chunk) > chunk_size_fallback:
                    result.extend(_split_by_chars(chunk, chunk_size_fallback))
                else:
                    result.append(chunk)
            return [c for c in result if c.strip()]

    # Fallback: plain character-count split
    return _split_by_chars(text, chunk_size_fallback)


def _split_by_number_headings(text: str) -> list[str]:
    """
    Split text at every "Number X" heading.

    The text before the first heading (intro/hook) becomes chunk 0.
    Each "Number X" heading stays at the start of its chunk.
    Returns an empty list if no headings are found.
    """
    parts = _NUMBER_HEADING.split(text)
    # split() with a capturing group gives: [pre, heading1, body1, heading2, body2, ...]
    if len(parts) <= 1:
        return []  # no headings found

    chunks: list[str] = []
    # First element is the intro (before any Number heading)
    intro = parts[0].strip()
    if intro:
        chunks.append(intro)

    # Remaining pairs: heading + body
    it = iter(parts[1:])
    for heading in it:
        body = next(it, "")
        combined = (heading + body).strip()
        if combined:
            chunks.append(combined)

    return chunks


def _split_by_chars(text: str, max_chars: int) -> list[str]:
    """
    Split text into chunks of at most max_chars characters.

    Tries to break at sentence boundaries ('. ', '! ', '? ') to avoid
    cutting mid-sentence. Falls back to hard cut if no boundary found.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        # Find the last sentence boundary within max_chars
        window = remaining[:max_chars]
        cut = -1
        for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
            pos = window.rfind(sep)
            if pos > cut:
                cut = pos + len(sep)

        if cut <= 0:
            # No sentence boundary — hard cut at max_chars
            cut = max_chars

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        chunks.append(remaining)

    return [c for c in chunks if c]


# ── PCM → WAV ────────────────────────────────────────────────────────────────


def pcm_to_wav_bytes(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """
    Wrap raw PCM16 bytes in a RIFF/WAV container.

    Args:
        pcm_data:     Raw linear PCM bytes (16-bit, little-endian).
        sample_rate:  Samples per second (Hz).
        channels:     1 = mono, 2 = stereo.
        sample_width: Bytes per sample (2 for 16-bit).

    Returns:
        WAV file bytes (header + data).
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


# ── Chunk stitching ───────────────────────────────────────────────────────────


def stitch_audio_chunks(
    audio_chunks: list[bytes],
    output_path: str | Path,
    output_format: str = "mp3",
    sample_rate: int = 24000,
    channels: int = 1,
    gap_ms: int = 300,
    source_format: str = "pcm",
) -> float:
    """
    Stitch a list of audio byte chunks into a single output file.

    Supports two source formats:
      "pcm"  — raw PCM16 bytes (from Gemini AI Studio / Vertex AI)
      "wav"  — complete WAV file bytes (from Google Cloud TTS)

    A short silence gap is inserted between chunks to prevent words from
    running together at segment boundaries.

    Args:
        audio_chunks:  Ordered list of audio byte strings.
        output_path:   Destination file path (.mp3 or .wav).
        output_format: "mp3" or "wav".
        sample_rate:   Sample rate — used for PCM and silence generation.
        channels:      Number of audio channels (PCM only).
        gap_ms:        Silence gap in milliseconds inserted between chunks.
        source_format: "pcm" or "wav".

    Returns:
        Total duration of the output file in seconds.
    """
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise RuntimeError(
            "pydub is required for audio stitching. Run: pip install pydub"
        ) from exc

    if not audio_chunks:
        raise ValueError("No audio chunks to stitch")

    silence = AudioSegment.silent(duration=gap_ms, frame_rate=sample_rate)
    combined: AudioSegment | None = None

    for chunk in audio_chunks:
        if source_format == "wav":
            segment = AudioSegment.from_wav(io.BytesIO(chunk))
        else:
            # Raw PCM16
            segment = AudioSegment(
                data=chunk,
                sample_width=2,
                frame_rate=sample_rate,
                channels=channels,
            )
        if combined is None:
            combined = segment
        else:
            combined = combined + silence + segment

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = output_format.lower().lstrip(".")
    export_kwargs: dict = {"format": fmt}
    if fmt == "mp3":
        export_kwargs["bitrate"] = "192k"

    combined.export(str(output_path), **export_kwargs)
    duration_sec = len(combined) / 1000.0
    logger.info(
        "Stitched %d chunk(s) → %s (%.1fs, %s)",
        len(pcm_chunks),
        output_path.name,
        duration_sec,
        fmt,
    )
    return duration_sec


def estimate_audio_duration(script_text: str, words_per_minute: int = 145) -> float:
    """
    Estimate the audio duration (seconds) from word count.

    Used for cost pre-calculation before audio is generated.

    Args:
        script_text:      Plain text of the script.
        words_per_minute: Speaking pace (documentary narration ≈ 130-150 wpm).

    Returns:
        Estimated duration in seconds.
    """
    word_count = len(script_text.split())
    return (word_count / words_per_minute) * 60.0
