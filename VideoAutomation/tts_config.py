"""
tts_config.py — Load and expose TTS pipeline configuration.

Reads from tts_config.json (in the same directory) and .env (GEMINI_API_KEY,
DEFAULT_VOICE, DEFAULT_EMOTIONAL_PROMPT). All callers import TTSConfig.get().
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # dotenv optional — rely on env vars being set externally

_CONFIG_PATH = Path(__file__).parent / "tts_config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "chunk_mode": "numbered_segments",
    "chunk_size_fallback": 4500,
    "max_retries": 3,
    "output_format": "mp3",
    "sample_rate": 24000,
    "model": "gemini-2.5-flash-preview-tts",
    "voices": ["Kore", "Charon", "Fenrir", "Puck", "Aoede", "Leda", "Orus", "Zephyr"],
    "style_prompts": {
        "history": (
            "Speak like a professional documentary narrator. Deep, authoritative, "
            "measured pace, serious gravitas. Build tension naturally through the script."
        ),
    },
}


class TTSConfig:
    """
    Singleton config object. Call TTSConfig.get() to retrieve the shared instance.

    Priority order for each setting:
      1. Runtime override (passed to TTSConfig.get())
      2. Environment variable (GEMINI_API_KEY, TTS_DEFAULT_VOICE, etc.)
      3. tts_config.json
      4. Hard-coded defaults above
    """

    _instance: TTSConfig | None = None

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @classmethod
    def get(cls) -> "TTSConfig":
        """Return (or build) the shared config instance."""
        if cls._instance is None:
            cls._instance = cls._load()
        return cls._instance

    @classmethod
    def reload(cls) -> "TTSConfig":
        """Force a re-read of tts_config.json (useful after GUI saves changes)."""
        cls._instance = cls._load()
        return cls._instance

    @classmethod
    def _load(cls) -> "TTSConfig":
        data = dict(_DEFAULT_CONFIG)

        # Merge tts_config.json
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH, encoding="utf-8") as f:
                    file_data = json.load(f)
                # Deep-merge style_prompts so partial overrides don't wipe defaults
                if "style_prompts" in file_data and "style_prompts" in data:
                    data["style_prompts"].update(file_data.pop("style_prompts"))
                data.update(file_data)
            except Exception as exc:
                print(f"[TTS Config] Warning: could not read tts_config.json — {exc}")

        # Env-var overrides
        if os.getenv("GEMINI_API_KEY"):
            data["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
        if os.getenv("TTS_DEFAULT_VOICE"):
            data["default_voice"] = os.environ["TTS_DEFAULT_VOICE"]
        if os.getenv("TTS_DEFAULT_EMOTIONAL_PROMPT"):
            data["default_emotional_prompt"] = os.environ["TTS_DEFAULT_EMOTIONAL_PROMPT"]
        if os.getenv("GOOGLE_CLOUD_PROJECT"):
            data["gcp_project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
        if os.getenv("TTS_BACKEND"):
            data["backend"] = os.environ["TTS_BACKEND"]

        return cls(data)

    # ── Accessors ────────────────────────────────────────────────────────────

    @property
    def backend(self) -> str:
        """Active TTS backend: 'aistudio' or 'vertex'."""
        return self._data.get("backend", "aistudio")

    @property
    def gcp_project(self) -> str:
        """Google Cloud project ID (Vertex AI only)."""
        return self._data.get("gcp_project", "")

    @property
    def gcp_location(self) -> str:
        """Google Cloud region for Vertex AI (default: us-central1)."""
        return self._data.get("gcp_location", "us-central1")

    @property
    def gemini_api_key(self) -> str:
        """Gemini API key (from env or gui_state). Empty string if not set."""
        return self._data.get("gemini_api_key", "")

    @property
    def model(self) -> str:
        return self._data.get("model", "gemini-2.5-flash-preview-tts")

    @property
    def default_voice(self) -> str:
        return self._data.get("default_voice", "Kore")

    @property
    def voices(self) -> list[str]:
        return self._data.get("voices", ["Kore"])

    @property
    def chunk_mode(self) -> str:
        return self._data.get("chunk_mode", "numbered_segments")

    @property
    def chunk_size_fallback(self) -> int:
        return int(self._data.get("chunk_size_fallback", 4500))

    @property
    def max_retries(self) -> int:
        return int(self._data.get("max_retries", 3))

    @property
    def output_format(self) -> str:
        return self._data.get("output_format", "mp3")

    @property
    def sample_rate(self) -> int:
        return int(self._data.get("sample_rate", 24000))

    @property
    def style_prompts(self) -> dict[str, str]:
        return self._data.get("style_prompts", {})

    def prompt_for_style(self, style_key: str) -> str:
        """
        Return the emotional prompt for a given niche style key.

        Falls back to the history prompt, then to a generic default.
        """
        prompts = self.style_prompts
        if style_key in prompts:
            return prompts[style_key]
        if "history" in prompts:
            return prompts["history"]
        return (
            "Speak like a professional documentary narrator. Deep, authoritative, "
            "measured pace, serious gravitas."
        )

    def save_style_prompts(self, prompts: dict[str, str]) -> None:
        """
        Persist updated style prompts to tts_config.json.

        Called by the GUI Settings tab when the user edits a prompt.
        """
        self._data["style_prompts"] = prompts
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        else:
            raw = {}
        raw["style_prompts"] = prompts
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
