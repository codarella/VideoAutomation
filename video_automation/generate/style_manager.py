"""
Style reference manager.

Rotates through style reference images with cooldown to prevent repetition.
"""

from __future__ import annotations

import random
import threading
from pathlib import Path


class StyleReferenceManager:
    """Rotate through style reference images with cooldown."""

    def __init__(self, style_dir: Path, cooldown: int = 5):
        self.style_dir = Path(style_dir)
        self.cooldown = cooldown
        self.lock = threading.Lock()
        self.recent: list[str] = []

        self.images: list[str] = []
        if self.style_dir.exists():
            self.images = [
                str(f) for f in self.style_dir.iterdir()
                if f.suffix.lower() in (".png", ".jpg", ".jpeg")
            ]

        if self.images:
            print(f"   Style references: {len(self.images)} images loaded")
        else:
            print(f"   No style references found in {self.style_dir}")

    def get_next(self) -> str | None:
        """Get next style reference image, avoiding recent ones."""
        if not self.images:
            return None

        with self.lock:
            available = [img for img in self.images if img not in self.recent]
            if not available:
                available = self.images

            choice = random.choice(available)

            self.recent.append(choice)
            if len(self.recent) > self.cooldown:
                self.recent.pop(0)

            return choice
