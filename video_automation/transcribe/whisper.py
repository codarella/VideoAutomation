"""
Multi-pass Whisper transcriber with consensus merging.

Runs Faster-Whisper multiple times with different settings, then merges
results by taking median timestamps for words that appear across passes.
This filters out hallucinated words and stabilizes timing.

Each pass runs in a separate subprocess to isolate CTranslate2/CUDA native
code — this prevents heap corruption from crashing the main pipeline process.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import median
from typing import Optional

from video_automation.models import Word


# ── Pass configurations ───────────────────────────────────────────────────

DEFAULT_PASS_CONFIGS = [
    {"beam_size": 5, "temperature": 0.0},
    {"beam_size": 10, "temperature": 0.0},
    {"beam_size": 5, "temperature": 0.2},
]

# Audio longer than this (seconds) will be split into chunks
CHUNK_THRESHOLD = 300  # 5 minutes
CHUNK_LENGTH = 300     # 5-minute chunks
CHUNK_OVERLAP = 5      # 5-second overlap to avoid cutting mid-word


class MultiPassTranscriber:
    """Run Whisper multiple times with different settings for consensus timing."""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def unload(self):
        """No-op — each pass runs in its own subprocess now."""
        pass

    def transcribe(
        self,
        audio_path: str | Path,
        num_passes: int = 3,
    ) -> tuple[list[Word], float]:
        """
        Transcribe audio with multi-pass consensus.

        Each pass is run in a separate subprocess to isolate CTranslate2
        native code and prevent heap corruption crashes.

        Returns: (words, audio_duration)
        """
        audio_path = str(Path(audio_path).resolve())
        print(f"   Audio file: {Path(audio_path).name}")

        # Get duration (lightweight, no model needed)
        duration = self._get_duration(audio_path)
        print(f"   Duration: {duration:.1f}s ({duration / 60:.1f} min)")

        if num_passes <= 1:
            configs = [DEFAULT_PASS_CONFIGS[0]]
        else:
            configs = DEFAULT_PASS_CONFIGS[:num_passes]

        all_results: list[list[Word]] = []
        for i, cfg in enumerate(configs):
            print(f"   Whisper pass {i + 1}/{len(configs)} "
                  f"(beam={cfg['beam_size']}, temp={cfg['temperature']})...")

            words = self._run_pass_subprocess(audio_path, cfg)

            if words is None:
                if all_results:
                    print(f"   ⚠ Pass {i + 1} failed — using {len(all_results)} completed pass(es)")
                    break
                raise RuntimeError(f"Whisper pass {i + 1} failed — see subprocess output above")

            print(f"      → {len(words)} words")
            all_results.append(words)

        if len(all_results) == 1:
            return all_results[0], duration

        print(f"   Merging {len(all_results)} passes (consensus)...")
        merged = self._consensus_merge(all_results)
        print(f"   → {len(merged)} consensus words")
        return merged, duration

    def _run_pass_subprocess(self, audio_path: str, cfg: dict) -> list[Word] | None:
        """Run a single Whisper pass in an isolated subprocess."""
        # Write args to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            args_path = f.name
            output_path = f.name + ".out.json"
            json.dump({
                "audio_path": audio_path,
                "output_path": output_path,
                "model_size": self.model_size,
                "device": self.device,
                "compute_type": self.compute_type,
                "beam_size": cfg["beam_size"],
                "temperature": cfg["temperature"],
                "chunk_threshold": CHUNK_THRESHOLD,
                "chunk_length": CHUNK_LENGTH,
                "chunk_overlap": CHUNK_OVERLAP,
            }, f)

        try:
            run_kwargs = dict(
                stdin=subprocess.DEVNULL,
                timeout=1800,  # 30 min max per pass
            )
            if os.name == "nt":
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-u",
                 "-m", "video_automation.transcribe._whisper_worker", args_path],
                **run_kwargs,
            )

            if result.returncode != 0:
                print(f"      ⚠ Subprocess exited with code {result.returncode}")
                return None

            # Read results
            out_path = Path(output_path)
            if not out_path.exists():
                print("      ⚠ Subprocess produced no output file")
                return None

            data = json.loads(out_path.read_text(encoding="utf-8"))

            # Update device/compute_type if worker fell back to CPU
            if data.get("device") != self.device:
                self.device = data["device"]
                self.compute_type = data["compute_type"]

            return [
                Word(text=w["text"], start=w["start"], end=w["end"], confidence=w["confidence"])
                for w in data["words"]
            ]

        finally:
            Path(args_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def _get_duration(self, audio_path: str) -> float:
        """Get audio duration using ffprobe (no RAM usage)."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", audio_path],
                capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffprobe failed (exit {result.returncode}): {result.stderr[:200]}")
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise RuntimeError(
                f"Could not determine audio duration for '{audio_path}'. "
                f"Is ffprobe installed and is the file a valid audio file?"
            ) from e

    def _consensus_merge(self, all_results: list[list[Word]]) -> list[Word]:
        """
        Merge multiple Whisper passes into a consensus word list.

        Strategy:
        1. Use the first pass as the reference sequence
        2. Align other passes against it using difflib
        3. For each word in the reference:
           - If it appears in >= 2 passes: keep it, use median timestamps
           - If it appears in only 1 pass: keep it but mark low confidence
        4. Words that appear in other passes but NOT the reference are ignored
           (likely hallucinations)
        """
        if not all_results:
            return []

        reference = all_results[0]
        if len(all_results) == 1:
            return reference

        # Collect timestamps for each position in the reference
        ref_texts = [w.text.lower() for w in reference]
        starts_by_pos: dict[int, list[float]] = {i: [w.start] for i, w in enumerate(reference)}
        ends_by_pos: dict[int, list[float]] = {i: [w.end] for i, w in enumerate(reference)}
        match_counts: dict[int, int] = {i: 1 for i in range(len(reference))}

        for other_words in all_results[1:]:
            other_texts = [w.text.lower() for w in other_words]

            # Align this pass against the reference
            matcher = difflib.SequenceMatcher(None, ref_texts, other_texts, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for ref_idx, other_idx in zip(range(i1, i2), range(j1, j2)):
                        starts_by_pos[ref_idx].append(other_words[other_idx].start)
                        ends_by_pos[ref_idx].append(other_words[other_idx].end)
                        match_counts[ref_idx] += 1

        # Build consensus words
        num_passes = len(all_results)
        consensus = []
        for i, ref_word in enumerate(reference):
            consensus.append(Word(
                text=ref_word.text,
                start=round(median(starts_by_pos[i]), 3),
                end=round(median(ends_by_pos[i]), 3),
                confidence=round(match_counts[i] / num_passes, 2),
            ))

        return consensus
