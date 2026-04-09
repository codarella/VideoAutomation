"""
tts_cost_tracker.py — Cost calculation and persistent cost logging.

Pricing model (Gemini 2.5 Flash TTS, as of 2026):
  Input text:   $0.50 per 1M tokens
  Audio output: $10.00 per 1M audio tokens  (25 audio tokens per second)

Costs are appended to video_workspace/costs.csv and a metadata JSON is saved
alongside each output audio file.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Pricing constants ────────────────────────────────────────────────────────

INPUT_COST_PER_1M_TOKENS  = 0.50    # USD
AUDIO_COST_PER_1M_TOKENS  = 10.00   # USD
AUDIO_TOKENS_PER_SECOND   = 25


# ── Cost calculation ─────────────────────────────────────────────────────────


def estimate_input_tokens(text: str) -> int:
    """
    Estimate the number of input tokens for a piece of text.

    Uses a rough 4 chars-per-token heuristic (standard for English prose).

    Args:
        text: The full script text that will be sent to the TTS API.

    Returns:
        Estimated token count.
    """
    return max(1, len(text) // 4)


def estimate_audio_tokens(duration_seconds: float) -> int:
    """
    Convert audio duration to Gemini audio token count.

    Args:
        duration_seconds: Actual or estimated duration of the generated audio.

    Returns:
        Estimated audio token count.
    """
    return max(1, int(duration_seconds * AUDIO_TOKENS_PER_SECOND))


def calculate_cost(
    char_count: int,
    audio_duration_seconds: float,
) -> dict[str, float]:
    """
    Calculate the estimated USD cost for one TTS run.

    Args:
        char_count:              Total characters in the input script.
        audio_duration_seconds:  Actual (or estimated) audio duration.

    Returns:
        Dict with keys: input_tokens, audio_tokens, input_cost,
        audio_cost, total_cost (all floats).
    """
    # Rough token estimates
    input_tokens = max(1, char_count // 4)
    audio_tokens = estimate_audio_tokens(audio_duration_seconds)

    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_1M_TOKENS
    audio_cost = (audio_tokens / 1_000_000) * AUDIO_COST_PER_1M_TOKENS
    total_cost = input_cost + audio_cost

    return {
        "input_tokens": input_tokens,
        "audio_tokens": audio_tokens,
        "input_cost": round(input_cost, 6),
        "audio_cost": round(audio_cost, 6),
        "total_cost": round(total_cost, 6),
    }


# ── Metadata JSON ────────────────────────────────────────────────────────────


def save_metadata(
    output_audio_path: str | Path,
    project_name: str,
    script_path: str,
    voice: str,
    emotional_prompt: str,
    num_chunks: int,
    total_chars: int,
    audio_duration_seconds: float,
    skipped_chunks: list[int],
) -> Path:
    """
    Save a metadata JSON file alongside the output audio file.

    The file is named <ProjectName>_tts_meta.json and placed in
    video_workspace/scripts/ for consistency with other project files.

    Args:
        output_audio_path:    Path to the final stitched audio file.
        project_name:         Name of the project (e.g. "HistoryOne").
        script_path:          Path to the original script .txt file.
        voice:                Voice name used (e.g. "Kore").
        emotional_prompt:     Emotional prompt / system instruction used.
        num_chunks:           Total number of chunks sent to TTS.
        total_chars:          Total character count of the script.
        audio_duration_seconds: Actual duration of the generated audio.
        skipped_chunks:       1-based indices of chunks that failed and were skipped.

    Returns:
        Path to the saved metadata JSON file.
    """
    output_audio_path = Path(output_audio_path)
    timestamp = datetime.now(timezone.utc).isoformat()

    costs = calculate_cost(total_chars, audio_duration_seconds)

    meta: dict[str, Any] = {
        "project_name": project_name,
        "timestamp": timestamp,
        "script_path": str(script_path),
        "output_audio": str(output_audio_path),
        "voice": voice,
        "emotional_prompt": emotional_prompt,
        "num_chunks": num_chunks,
        "skipped_chunks": skipped_chunks,
        "total_characters": total_chars,
        "audio_duration_seconds": round(audio_duration_seconds, 2),
        "cost": costs,
    }

    # Place metadata in video_workspace/scripts/
    scripts_dir = output_audio_path.parent.parent / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    meta_path = scripts_dir / f"{project_name}_tts_meta.json"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return meta_path


# ── costs.csv ────────────────────────────────────────────────────────────────


def append_cost_row(
    workspace: str | Path,
    project_name: str,
    voice: str,
    total_chars: int,
    audio_duration_seconds: float,
    num_chunks: int,
    skipped_chunks: int,
) -> None:
    """
    Append one row to video_workspace/costs.csv.

    Creates the file with a header row if it does not exist.

    Args:
        workspace:             Path to video_workspace directory.
        project_name:          Project name.
        voice:                 Voice name used.
        total_chars:           Total characters in the script.
        audio_duration_seconds: Duration of the generated audio.
        num_chunks:            Number of chunks processed.
        skipped_chunks:        Number of chunks that were skipped due to errors.
    """
    csv_path = Path(workspace) / "costs.csv"
    costs = calculate_cost(total_chars, audio_duration_seconds)

    fieldnames = [
        "timestamp",
        "project",
        "voice",
        "chars",
        "audio_seconds",
        "chunks",
        "skipped",
        "input_tokens",
        "audio_tokens",
        "input_cost_usd",
        "audio_cost_usd",
        "total_cost_usd",
    ]

    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "project":         project_name,
            "voice":           voice,
            "chars":           total_chars,
            "audio_seconds":   round(audio_duration_seconds, 2),
            "chunks":          num_chunks,
            "skipped":         skipped_chunks,
            "input_tokens":    costs["input_tokens"],
            "audio_tokens":    costs["audio_tokens"],
            "input_cost_usd":  costs["input_cost"],
            "audio_cost_usd":  costs["audio_cost"],
            "total_cost_usd":  costs["total_cost"],
        })
