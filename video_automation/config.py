"""
Configuration model for the VideoAutomation pipeline.

Replaces the old boolean mode flags (compile_only, prompts_only, etc.)
with a clean start_from/stop_after pipeline model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PacingProfile:
    """Controls how scene durations vary across the video."""
    fast_cut_window: float = 60.0         # Seconds from start to use fast cuts
    fast_cut_min: float = 6.0             # Min scene duration in fast-cut window
    fast_cut_max: float = 8.0             # Max scene duration in fast-cut window
    standard_min: float = 7.0             # Min scene duration after fast-cut window
    standard_max: float = 8.0             # Max scene duration after fast-cut window
    number_card_duration: float = 1.0     # Duration for "Number X" title cards
    content_min_duration: float = 4.0     # Absolute minimum for any content scene

    def target_duration(self, time_in_video: float) -> tuple[float, float]:
        """Return (min, max) target duration based on position in video."""
        if time_in_video < self.fast_cut_window:
            return (self.fast_cut_min, self.fast_cut_max)
        return (self.standard_min, self.standard_max)


@dataclass
class Config:
    """Pipeline configuration settings."""

    # ── API ───────────────────────────────────────────────────────
    ai33_base_url: str = "https://api.ai33.pro"
    ai33_model: str = "flux-2-pro"
    ai33_api_key: str = ""

    # ── Anthropic / Claude ────────────────────────────────────────
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # ── Local LLM ────────────────────────────────────────────────
    llm_provider: str = ""                  # "ollama", "lmstudio", or ""
    llm_model: str = ""
    llm_url: str = ""

    # ── Image settings ────────────────────────────────────────────
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    image_width: int = 1920
    image_height: int = 1080

    # ── Video ─────────────────────────────────────────────────────
    fps: int = 30
    ken_burns: bool = False
    crossfade: bool = False
    crossfade_duration: float = 0.4

    # ── Character ─────────────────────────────────────────────────
    character_rate: float = 0.20            # ~20% of scenes include MC character

    # ── Style reference ───────────────────────────────────────────
    use_style_reference: bool = True

    # ── Pacing ────────────────────────────────────────────────────
    pacing: PacingProfile = field(default_factory=PacingProfile)

    # ── Parallel workers ──────────────────────────────────────────
    max_workers: int = 10                   # Image generation threads
    compile_workers: int = 3                # FFmpeg clip encoding threads

    # ── API polling ───────────────────────────────────────────────
    poll_interval: float = 4.0
    max_poll_time: float = 300.0
    max_retries: int = 3

    # ── Whisper ───────────────────────────────────────────────────
    whisper_model: str = "medium"
    whisper_passes: int = 3                 # Multi-pass consensus (1 = single pass)
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    # ── Duplicate detection ───────────────────────────────────────
    find_dupes: bool = False
    dupe_threshold: int = 10                # Hamming distance (0-64, lower = stricter)

    # ── Transcript fixer ──────────────────────────────────────────
    fix_transcript: bool = False

    # ── Scenes to regenerate (1-based indices) ────────────────────
    regen_scenes: list = field(default_factory=list)

    # ── Intro ─────────────────────────────────────────────────────
    intro_duration: float = 0.0             # Seconds of intro clip to use (0 = no intro)
