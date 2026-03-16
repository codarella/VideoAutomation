"""
Multi-pass Whisper transcriber with consensus merging.

Runs Faster-Whisper multiple times with different settings, then merges
results by taking median timestamps for words that appear across passes.
This filters out hallucinated words and stabilizes timing.

Long audio files are automatically split into chunks to avoid memory issues.
"""

from __future__ import annotations

import difflib
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
CHUNK_THRESHOLD = 600  # 10 minutes
CHUNK_LENGTH = 600     # 10-minute chunks
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
        self._model = None

    def unload(self):
        """Explicitly release the Whisper model and free CUDA memory."""
        if self._model is not None:
            del self._model
            self._model = None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            print(f"   Loading Faster-Whisper model ({self.model_size})...")
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except (RuntimeError, MemoryError) as e:
                err = str(e).lower()
                if "out of memory" in err or "cuda" in err or "malloc" in err or isinstance(e, MemoryError):
                    print(f"   Out of memory — falling back to CPU with int8 quantization...")
                    self.device = "cpu"
                    self.compute_type = "int8"
                    self._model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                    )
                else:
                    raise
            except Exception as e:
                err = str(e).lower()
                err_type = type(e).__name__
                if "timed out" in err or "connection" in err or "LocalEntryNotFound" in err_type:
                    raise RuntimeError(
                        f"Cannot load Whisper model '{self.model_size}': "
                        f"HuggingFace Hub is unreachable and the model is not fully cached. "
                        f"Please check your internet connection and try again, or "
                        f"download the model manually first."
                    ) from e
                raise
            print(f"   Faster-Whisper loaded ({self.device}, {self.compute_type}).")
        return self._model

    def transcribe(
        self,
        audio_path: str | Path,
        num_passes: int = 3,
    ) -> tuple[list[Word], float]:
        """
        Transcribe audio with multi-pass consensus.

        Returns: (words, audio_duration)
        """
        audio_path = str(audio_path)
        print(f"   Audio file: {Path(audio_path).name}")

        # Get duration to decide if we need chunking
        duration = self._get_duration(audio_path)
        print(f"   Duration: {duration:.1f}s ({duration / 60:.1f} min)")

        if num_passes <= 1:
            configs = [DEFAULT_PASS_CONFIGS[0]]
        else:
            configs = DEFAULT_PASS_CONFIGS[:num_passes]

        all_results: list[list[Word]] = []
        for i, cfg in enumerate(configs):
            # Free CUDA cache between passes to avoid OOM buildup
            if i > 0 and self.device == "cuda":
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass

            print(f"   Whisper pass {i + 1}/{len(configs)} "
                  f"(beam={cfg['beam_size']}, temp={cfg['temperature']})...")

            try:
                if duration > CHUNK_THRESHOLD:
                    words = self._run_chunked_pass(audio_path, duration, **cfg)
                else:
                    words = self._run_single_pass(audio_path, **cfg)
            except (RuntimeError, MemoryError) as e:
                if ("out of memory" in str(e).lower() or isinstance(e, MemoryError)) and all_results:
                    print(f"   ⚠ OOM on pass {i + 1} — skipping remaining passes, "
                          f"using {len(all_results)} completed pass(es)")
                    try:
                        import gc
                        gc.collect()
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    break
                raise

            print(f"      → {len(words)} words")
            all_results.append(words)

        # Unload model NOW — all passes done, free CUDA before any more work.
        # CTranslate2 can corrupt the heap if the model lingers while Python
        # allocates heavily (e.g. during consensus merge or stage transition).
        self.unload()

        if len(all_results) == 1:
            return all_results[0], duration

        print(f"   Merging {len(all_results)} passes (consensus)...")
        merged = self._consensus_merge(all_results)
        print(f"   → {len(merged)} consensus words")
        return merged, duration

    def _get_duration(self, audio_path: str) -> float:
        """Get audio duration using ffprobe (no RAM usage)."""
        import subprocess, json
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

    def _run_chunked_pass(
        self,
        audio_path: str,
        total_duration: float,
        beam_size: int = 5,
        temperature: float = 0.0,
    ) -> list[Word]:
        """Split audio into chunks via ffmpeg, transcribe each, merge with offset timestamps."""
        import os, subprocess

        num_chunks = int(total_duration // CHUNK_LENGTH) + 1
        print(f"      Splitting into {num_chunks} chunks of ~{CHUNK_LENGTH}s via ffmpeg...")

        all_words: list[Word] = []
        chunk_start = 0.0
        chunk_idx = 0

        while chunk_start < total_duration:
            chunk_end = min(chunk_start + CHUNK_LENGTH, total_duration)
            chunk_dur = chunk_end - chunk_start

            print(f"      Chunk {chunk_idx + 1}/{num_chunks}: "
                  f"{chunk_start:.0f}s – {chunk_end:.0f}s ({chunk_dur:.0f}s)")

            # Extract chunk with ffmpeg (streams audio, no full-file RAM load)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            ffmpeg_kwargs = dict(capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
            if os.name == "nt":
                ffmpeg_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            subprocess.run(
                ["ffmpeg", "-y", "-nostdin", "-ss", str(chunk_start), "-t", str(chunk_dur),
                 "-i", audio_path, "-ar", "16000", "-ac", "1", tmp_path],
                **ffmpeg_kwargs,
            )

            try:
                chunk_words = self._run_single_pass(tmp_path, beam_size=beam_size, temperature=temperature)
            except (RuntimeError, MemoryError) as e:
                if "out of memory" in str(e).lower() or isinstance(e, MemoryError):
                    # Clear cache and retry once with smaller beam
                    try:
                        import gc
                        gc.collect()
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    print(f"      ⚠ OOM on chunk {chunk_idx + 1}, retrying with beam_size=1...")
                    chunk_words = self._run_single_pass(tmp_path, beam_size=1, temperature=temperature)
                else:
                    raise
            finally:
                Path(tmp_path).unlink(missing_ok=True)

            # Free CUDA cache between chunks
            if self.device == "cuda":
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass

            # Offset timestamps and add to results
            for w in chunk_words:
                absolute_start = w.start + chunk_start
                # Skip words from the overlap region of previous chunk
                if all_words and absolute_start < all_words[-1].end:
                    continue
                all_words.append(Word(
                    text=w.text,
                    start=round(absolute_start, 3),
                    end=round(w.end + chunk_start, 3),
                    confidence=w.confidence,
                ))

            # Advance, minus overlap to catch words at boundaries
            # But if we already reached the end, stop
            if chunk_end >= total_duration:
                break
            chunk_start = chunk_end - CHUNK_OVERLAP
            chunk_idx += 1

        return all_words

    def _run_single_pass(
        self,
        audio_path: str,
        beam_size: int = 5,
        temperature: float = 0.0,
    ) -> list[Word]:
        """Run one Whisper pass on a single audio file, return words."""
        model = self._get_model()
        segments, info = model.transcribe(
            audio_path,
            word_timestamps=True,
            beam_size=beam_size,
            temperature=temperature,
        )

        words = []
        for segment in segments:
            if not segment.words:
                continue
            for w in segment.words:
                text = w.word.strip()
                if text:
                    words.append(Word(
                        text=text,
                        start=round(w.start, 3),
                        end=round(w.end, 3),
                        confidence=round(w.probability, 3) if hasattr(w, 'probability') else 1.0,
                    ))
        return words

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
