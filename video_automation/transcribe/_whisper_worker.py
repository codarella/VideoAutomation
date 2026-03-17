"""
Subprocess worker for running a single Whisper pass.

Invoked as:  python -m video_automation.transcribe._whisper_worker <json_args_file>

Reads config from a JSON file, runs one transcription pass, writes results
to an output JSON file, then exits.  This isolates CTranslate2/CUDA native
code so that heap corruption in the library cannot crash the parent process.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def _run_pass(args: dict) -> None:
    audio_path = args["audio_path"]
    output_path = args["output_path"]
    model_size = args["model_size"]
    device = args["device"]
    compute_type = args["compute_type"]
    beam_size = args["beam_size"]
    temperature = args["temperature"]
    chunk_threshold = args["chunk_threshold"]
    chunk_length = args["chunk_length"]
    chunk_overlap = args["chunk_overlap"]

    from faster_whisper import WhisperModel

    print(f"   Loading Faster-Whisper model ({model_size})...")
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except (RuntimeError, MemoryError) as e:
        err = str(e).lower()
        if "out of memory" in err or "cuda" in err or "malloc" in err or isinstance(e, MemoryError):
            print("   Out of memory — falling back to CPU with int8 quantization...")
            device = "cpu"
            compute_type = "int8"
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
        else:
            raise
    print(f"   Faster-Whisper loaded ({device}, {compute_type}).")

    # Get duration
    import subprocess as sp

    result = sp.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True, text=True, timeout=30, stdin=sp.DEVNULL,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])

    # Transcribe (chunked or single)
    if duration > chunk_threshold:
        words = _run_chunked(model, audio_path, duration, beam_size, temperature,
                             chunk_length, chunk_overlap, device)
    else:
        words = _transcribe_file(model, audio_path, beam_size, temperature)

    # Write results
    out = {
        "words": [{"text": w["text"], "start": w["start"], "end": w["end"],
                    "confidence": w["confidence"]} for w in words],
        "duration": duration,
        "device": device,
        "compute_type": compute_type,
    }
    Path(output_path).write_text(json.dumps(out), encoding="utf-8")


def _transcribe_file(model, audio_path: str, beam_size: int, temperature: float) -> list[dict]:
    segments, info = model.transcribe(
        audio_path, word_timestamps=True, beam_size=beam_size, temperature=temperature,
    )
    words = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            text = w.word.strip()
            if text:
                words.append({
                    "text": text,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "confidence": round(w.probability, 3) if hasattr(w, "probability") else 1.0,
                })
    return words


def _run_chunked(model, audio_path: str, total_duration: float,
                 beam_size: int, temperature: float,
                 chunk_length: int, chunk_overlap: int, device: str) -> list[dict]:
    import os
    import subprocess as sp

    num_chunks = int(total_duration // chunk_length) + 1
    print(f"      Splitting into {num_chunks} chunks of ~{chunk_length}s via ffmpeg...")

    all_words: list[dict] = []
    chunk_start = 0.0
    chunk_idx = 0

    while chunk_start < total_duration:
        chunk_end = min(chunk_start + chunk_length, total_duration)
        chunk_dur = chunk_end - chunk_start

        print(f"      Chunk {chunk_idx + 1}/{num_chunks}: "
              f"{chunk_start:.0f}s – {chunk_end:.0f}s ({chunk_dur:.0f}s)")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        ffmpeg_kwargs = dict(capture_output=True, stdin=sp.DEVNULL, timeout=300)
        if os.name == "nt":
            ffmpeg_kwargs["creationflags"] = sp.CREATE_NO_WINDOW

        sp.run(
            ["ffmpeg", "-y", "-nostdin", "-ss", str(chunk_start), "-t", str(chunk_dur),
             "-i", audio_path, "-ar", "16000", "-ac", "1", tmp_path],
            **ffmpeg_kwargs,
        )

        try:
            chunk_words = _transcribe_file(model, tmp_path, beam_size, temperature)
        except (RuntimeError, MemoryError):
            # OOM — retry with beam_size=1
            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"      ⚠ OOM on chunk {chunk_idx + 1}, retrying with beam_size=1...")
            chunk_words = _transcribe_file(model, tmp_path, 1, temperature)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # Free CUDA cache between chunks
        if device == "cuda":
            try:
                import gc
                gc.collect()
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

        for w in chunk_words:
            absolute_start = w["start"] + chunk_start
            if all_words and absolute_start < all_words[-1]["end"]:
                continue
            all_words.append({
                "text": w["text"],
                "start": round(absolute_start, 3),
                "end": round(w["end"] + chunk_start, 3),
                "confidence": w["confidence"],
            })

        if chunk_end >= total_duration:
            break
        chunk_start = chunk_end - chunk_overlap
        chunk_idx += 1

    return all_words


if __name__ == "__main__":
    args_file = sys.argv[1]
    args = json.loads(Path(args_file).read_text(encoding="utf-8"))
    _run_pass(args)
