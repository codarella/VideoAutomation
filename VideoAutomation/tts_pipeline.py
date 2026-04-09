"""
tts_pipeline.py — Main entry point for the Gemini 2.5 Flash TTS pipeline.

Reads a narration script, splits it into chunks, calls the Gemini TTS API
for each chunk, stitches the results, and writes the final audio file to
video_workspace/audio/<ProjectName>.mp3.

Also updates <ProjectName>_project.json audio_path if the file exists, saves
a metadata JSON, and appends a cost row to costs.csv.

Usage (CLI):
    python tts_pipeline.py \\
        --script "C:/path/to/script.txt" \\
        --project-name HistoryOne \\
        --workspace "C:/path/to/video_workspace" \\
        --gemini-key "AIza..." \\
        --voice Kore \\
        --style history

Usage (from Python):
    from tts_pipeline import run_tts_pipeline
    run_tts_pipeline(
        script_path="...",
        project_name="HistoryOne",
        workspace="...",
        gemini_api_key="AIza...",
    )
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(workspace: str | Path) -> logging.Logger:
    """Configure file + stdout logging. Returns the tts_pipeline logger."""
    logs_dir = Path(workspace) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"tts_{ts}.log"

    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    return logging.getLogger("tts_pipeline")


# ── Core pipeline ────────────────────────────────────────────────────────────


def run_tts_pipeline(
    script_path: str | None = None,
    script_text: str | None = None,
    project_name: str = "",
    workspace: str = "",
    gemini_api_key: str = "",
    gcp_project: str = "",
    gcp_location: str = "",
    backend: str = "",
    voice: str = "",
    emotional_prompt: str = "",
    style: str = "history",
) -> Path:
    """
    Run the full TTS pipeline for one project.

    Provide either script_path (path to a .txt file) or script_text (raw string).
    At least one must be given.

    Args:
        script_path:      Path to the narration script .txt file.
        script_text:      Raw script text (alternative to script_path).
        project_name:     Project name used for output file naming.
        workspace:        Path to video_workspace directory.
        gemini_api_key:   Gemini API key. Falls back to GEMINI_API_KEY env var.
        voice:            Voice name (e.g. "Kore"). Falls back to config default.
        emotional_prompt: Emotional style instruction. Falls back to style preset.
        style:            Niche style key used to look up the default prompt.

    Returns:
        Path to the final stitched audio file.

    Raises:
        ValueError:  If neither script_path nor script_text is provided, or if
                     no API key is available.
        RuntimeError: If no audio chunks were generated successfully.
    """
    # ── Import pipeline modules ─────────────────────────────────────────────
    # Import here so the GUI can import this module without loading all deps
    from tts_config import TTSConfig
    from tts_client import synthesize_chunk, synthesize_chunk_vertex, synthesize_chunk_cloud_tts, TTSError
    from tts_audio_utils import split_script, stitch_audio_chunks
    from tts_cost_tracker import append_cost_row, save_metadata

    cfg = TTSConfig.get()
    logger = logging.getLogger("tts_pipeline")

    # ── Resolve inputs ───────────────────────────────────────────────────────
    if not script_text:
        if not script_path:
            raise ValueError("Provide either script_path or script_text")
        with open(script_path, encoding="utf-8") as f:
            script_text = f.read()

    if not script_text.strip():
        raise ValueError("Script is empty")

    # Normalise backend — guard against GUI passing display labels
    _backend_raw = (backend or cfg.backend).lower()
    if "cloud" in _backend_raw or "chirp" in _backend_raw:
        resolved_backend = "cloudtts"
    elif "vertex" in _backend_raw:
        resolved_backend = "vertex"
    else:
        resolved_backend = "aistudio"
    resolved_gcp_project = gcp_project or cfg.gcp_project or os.getenv("GOOGLE_CLOUD_PROJECT", "")
    resolved_gcp_location = gcp_location or cfg.gcp_location

    api_key = gemini_api_key or cfg.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if resolved_backend == "aistudio" and not api_key:
        raise ValueError(
            "No Gemini API key. Set GEMINI_API_KEY env var or pass --gemini-key."
        )
    if resolved_backend in ("vertex", "cloudtts") and not resolved_gcp_project:
        raise ValueError(
            "No GCP project ID. Set GOOGLE_CLOUD_PROJECT env var or pass --gcp-project."
        )

    resolved_voice = voice or cfg.default_voice
    resolved_prompt = emotional_prompt or cfg.prompt_for_style(style)
    resolved_project = project_name or Path(script_path or "output").stem

    if not workspace:
        workspace = str(
            Path(__file__).parent.parent / "video_workspace"
        )

    logger.info("=== TTS Pipeline starting ===")
    logger.info("Backend : %s", resolved_backend)
    logger.info("Project : %s", resolved_project)
    logger.info("Voice   : %s", resolved_voice)
    logger.info("Style   : %s", style)
    logger.info("Model   : %s", cfg.model)
    logger.info("Prompt  : %s", resolved_prompt[:80] + "..." if len(resolved_prompt) > 80 else resolved_prompt)
    if resolved_backend in ("vertex", "cloudtts"):
        logger.info("GCP     : %s / %s", resolved_gcp_project, resolved_gcp_location)
    if resolved_backend == "cloudtts":
        logger.info("Note    : Emotional prompt is not used by Cloud TTS — voice quality comes from Chirp HD")

    # ── Split script into chunks ─────────────────────────────────────────────
    chunks = split_script(
        script_text,
        chunk_mode=cfg.chunk_mode,
        chunk_size_fallback=cfg.chunk_size_fallback,
    )
    logger.info("Split into %d chunk(s)", len(chunks))

    # ── Synthesize each chunk ────────────────────────────────────────────────
    pcm_results: list[bytes | None] = []
    skipped: list[int] = []

    for i, chunk in enumerate(chunks, start=1):
        label = chunk[:60].replace("\n", " ")
        logger.info("[%d/%d] Synthesizing: %s...", i, len(chunks), label)
        try:
            if resolved_backend == "cloudtts":
                chunk_audio = synthesize_chunk_cloud_tts(
                    text=chunk,
                    voice_name=resolved_voice,
                    language_code="en-US",
                    sample_rate=cfg.sample_rate,
                    max_retries=cfg.max_retries,
                )
            elif resolved_backend == "vertex":
                chunk_audio = synthesize_chunk_vertex(
                    text=chunk,
                    gcp_project=resolved_gcp_project,
                    gcp_location=resolved_gcp_location,
                    voice_name=resolved_voice,
                    emotional_prompt=resolved_prompt,
                    model=cfg.model,
                    sample_rate=cfg.sample_rate,
                    max_retries=cfg.max_retries,
                )
            else:
                chunk_audio = synthesize_chunk(
                    text=chunk,
                    api_key=api_key,
                    voice_name=resolved_voice,
                    emotional_prompt=resolved_prompt,
                    model=cfg.model,
                    sample_rate=cfg.sample_rate,
                    max_retries=cfg.max_retries,
                )
            pcm_results.append(chunk_audio)
            logger.info("[%d/%d] OK — %d bytes", i, len(chunks), len(chunk_audio))
        except TTSError as exc:
            logger.error("[%d/%d] SKIPPED after all retries: %s", i, len(chunks), exc)
            skipped.append(i)
            pcm_results.append(None)

    good_chunks = [p for p in pcm_results if p is not None]
    if not good_chunks:
        raise RuntimeError("All TTS chunks failed — no audio generated")

    if skipped:
        logger.warning("Skipped chunks (will be silent gaps): %s", skipped)

    # Replace failed chunks with silence so timing roughly holds
    final_chunks: list[bytes] = []
    for chunk_bytes in pcm_results:
        if chunk_bytes is not None:
            final_chunks.append(chunk_bytes)
        else:
            if resolved_backend == "cloudtts":
                # WAV silence for Cloud TTS
                from tts_audio_utils import pcm_to_wav_bytes
                silence_pcm = b"\x00" * (cfg.sample_rate * 2 * 5)
                final_chunks.append(pcm_to_wav_bytes(silence_pcm, cfg.sample_rate))
            else:
                # ~5 seconds of raw PCM16 silence
                final_chunks.append(b"\x00" * (cfg.sample_rate * 2 * 5))

    # ── Stitch and save ──────────────────────────────────────────────────────
    audio_dir = Path(workspace) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    ext = cfg.output_format.lower().lstrip(".")
    output_path = audio_dir / f"{resolved_project}.{ext}"

    source_fmt = "wav" if resolved_backend == "cloudtts" else "pcm"
    logger.info("Stitching %d chunk(s) → %s [%s]", len(final_chunks), output_path, source_fmt)
    duration = stitch_audio_chunks(
        audio_chunks=final_chunks,
        output_path=output_path,
        output_format=cfg.output_format,
        sample_rate=cfg.sample_rate,
        gap_ms=300,
        source_format=source_fmt,
    )
    logger.info("Output: %s (%.1fs)", output_path, duration)

    # ── Update project JSON ──────────────────────────────────────────────────
    project_json = (
        Path(workspace) / "scripts" / f"{resolved_project}_project.json"
    )
    if project_json.exists():
        try:
            with open(project_json, encoding="utf-8") as f:
                proj_data = json.load(f)
            proj_data["audio_path"] = str(output_path).replace("\\", "/")
            with open(project_json, "w", encoding="utf-8") as f:
                json.dump(proj_data, f, indent=2, ensure_ascii=False)
            logger.info("Updated audio_path in %s", project_json.name)
        except Exception as exc:
            logger.warning("Could not update project JSON: %s", exc)

    # ── Metadata + cost ──────────────────────────────────────────────────────
    total_chars = len(script_text)
    costs = _log_costs(
        workspace=workspace,
        project_name=resolved_project,
        voice=resolved_voice,
        total_chars=total_chars,
        duration=duration,
        num_chunks=len(chunks),
        skipped=skipped,
    )

    meta_path = save_metadata(
        output_audio_path=output_path,
        project_name=resolved_project,
        script_path=script_path or "<inline>",
        voice=resolved_voice,
        emotional_prompt=resolved_prompt,
        num_chunks=len(chunks),
        total_chars=total_chars,
        audio_duration_seconds=duration,
        skipped_chunks=skipped,
    )
    logger.info("Metadata: %s", meta_path)

    logger.info(
        "=== Done — estimated cost: $%.4f (input $%.4f + audio $%.4f) ===",
        costs["total_cost"],
        costs["input_cost"],
        costs["audio_cost"],
    )

    return output_path


def _log_costs(
    workspace: str | Path,
    project_name: str,
    voice: str,
    total_chars: int,
    duration: float,
    num_chunks: int,
    skipped: list[int],
) -> dict:
    """Append cost row to costs.csv and return cost dict."""
    from tts_cost_tracker import append_cost_row, calculate_cost
    costs = calculate_cost(total_chars, duration)
    try:
        append_cost_row(
            workspace=workspace,
            project_name=project_name,
            voice=voice,
            total_chars=total_chars,
            audio_duration_seconds=duration,
            num_chunks=num_chunks,
            skipped_chunks=len(skipped),
        )
    except Exception as exc:
        logging.getLogger("tts_pipeline").warning("Could not write costs.csv: %s", exc)
    return costs


# ── CLI entry point ──────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate narration audio with Gemini 2.5 Flash TTS"
    )
    p.add_argument("--script",           required=True,  help="Path to narration script .txt file")
    p.add_argument("--project-name",     required=True,  help="Project name (used for output filename)")
    p.add_argument("--workspace",        required=True,  help="Path to video_workspace directory")
    p.add_argument("--backend",          default="",     help="'aistudio' or 'vertex' (default: from tts_config.json)")
    p.add_argument("--gemini-key",       default="",     help="Gemini API key — AI Studio only")
    p.add_argument("--gcp-project",      default="",     help="Google Cloud project ID — Vertex AI only")
    p.add_argument("--gcp-location",     default="",     help="GCP region (default: us-central1) — Vertex AI only")
    p.add_argument("--voice",            default="",     help="Voice name (default: Kore)")
    p.add_argument("--style",            default="history", help="Niche style key for default prompt")
    p.add_argument("--emotional-prompt", default="",     help="Override emotional/tone prompt")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _setup_logging(args.workspace)
    try:
        out = run_tts_pipeline(
            script_path=args.script,
            project_name=args.project_name,
            workspace=args.workspace,
            backend=args.backend,
            gemini_api_key=args.gemini_key,
            gcp_project=args.gcp_project,
            gcp_location=args.gcp_location,
            voice=args.voice,
            emotional_prompt=args.emotional_prompt,
            style=args.style,
        )
        print(f"[TTS] Output: {out}")
        sys.exit(0)
    except Exception as exc:
        logging.getLogger("tts_pipeline").error("Pipeline failed: %s", exc)
        sys.exit(1)
