"""
Load existing transcripts from various JSON formats into the unified Word model.

Supports: word_timestamps JSON (our format), AI33, Nexlev/YouTube, segments array,
SRT subtitles, and wrapper formats.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from video_automation.models import Word


def load_word_timestamps(path: str | Path) -> tuple[list[Word], float]:
    """
    Load our own word_timestamps.json format.
    Returns: (words, total_duration)
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries", [])
    words = []
    for e in entries:
        text = e.get("text", "").strip()
        if text:
            words.append(Word(
                text=text,
                start=float(e.get("start", 0)),
                end=float(e.get("end", 0)),
            ))

    duration = words[-1].end if words else 0.0
    print(f"   Loaded {len(words)} words from word_timestamps ({duration:.1f}s)")
    return words, duration


def load_transcript_json(path: str | Path) -> tuple[list[Word], float]:
    """
    Auto-detect JSON format and load as Word list.
    Returns: (words, total_duration)
    """
    path = Path(path)
    print(f"   Loading: {path.name}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ── Our word_timestamps format: {text, entries: [{text, start, end}]}
    if isinstance(data, dict) and "entries" in data and isinstance(data["entries"], list):
        first = data["entries"][0] if data["entries"] else {}
        if "start" in first and "end" in first and "text" in first:
            return load_word_timestamps(path)

    # ── AI33 word-level: [{text, words: [{text, start, end, type}]}]
    if isinstance(data, list) and data and isinstance(data[0], dict) and "words" in data[0]:
        print("   Format: AI33 Word-Level")
        actual_words = [w for w in data[0].get("words", []) if w.get("type") == "word"]
        words = [
            Word(text=w["text"], start=float(w["start"]), end=float(w["end"]))
            for w in actual_words if w.get("text", "").strip()
        ]
        duration = words[-1].end if words else 0.0
        print(f"   Loaded {len(words)} words ({duration:.1f}s)")
        return words, duration

    # ── Segments array: {segments: [{start, end, text}]}
    segments_list = None
    if isinstance(data, dict):
        if "segments" in data:
            segments_list = data["segments"]
        elif "transcription" in data and isinstance(data["transcription"], dict):
            segments_list = data["transcription"].get("segments", [])
        elif "result" in data and isinstance(data["result"], dict):
            segments_list = data["result"].get("segments", [])

    if segments_list:
        print(f"   Format: Segments array ({len(segments_list)} segments)")
        words = _segments_to_words(segments_list)
        duration = words[-1].end if words else 0.0
        print(f"   Loaded {len(words)} words ({duration:.1f}s)")
        return words, duration

    # ── Nexlev/YouTube: {transcript: [{time: "M:SS", script: "..."}]}
    if isinstance(data, dict) and "transcript" in data and isinstance(data["transcript"], list):
        transcript = data["transcript"]
        if transcript and "time" in transcript[0] and "script" in transcript[0]:
            print(f"   Format: Nexlev/YouTube ({len(transcript)} entries)")
            words = _nexlev_to_words(transcript)
            duration = words[-1].end if words else 0.0
            print(f"   Loaded {len(words)} words ({duration:.1f}s)")
            return words, duration

    print(f"   WARNING: Unknown transcript format in {path.name}")
    return [], 0.0


def _segments_to_words(segments: list[dict]) -> list[Word]:
    """Convert segment-level entries to approximate word-level entries."""
    words = []
    for seg in segments:
        text = seg.get("text", "").strip()
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        if not text or end <= start:
            continue

        seg_words = text.split()
        if not seg_words:
            continue

        word_dur = (end - start) / len(seg_words)
        for i, w in enumerate(seg_words):
            words.append(Word(
                text=w,
                start=round(start + i * word_dur, 3),
                end=round(start + (i + 1) * word_dur, 3),
                confidence=0.5,  # estimated, not real word-level
            ))
    return words


def _nexlev_to_words(transcript: list[dict]) -> list[Word]:
    """Convert Nexlev/YouTube format to approximate word-level entries."""
    words = []
    for i, entry in enumerate(transcript):
        text = entry.get("script", "").strip()
        start = _time_to_seconds(entry.get("time", "0:00"))
        if i + 1 < len(transcript):
            end = _time_to_seconds(transcript[i + 1]["time"])
        else:
            end = start + 3.0

        if not text or end <= start:
            continue

        seg_words = text.split()
        if not seg_words:
            continue

        word_dur = (end - start) / len(seg_words)
        for j, w in enumerate(seg_words):
            words.append(Word(
                text=w,
                start=round(start + j * word_dur, 3),
                end=round(start + (j + 1) * word_dur, 3),
                confidence=0.5,
            ))
    return words


def _time_to_seconds(time_str: str) -> float:
    """Convert "M:SS" or "H:MM:SS" to seconds."""
    parts = time_str.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0
