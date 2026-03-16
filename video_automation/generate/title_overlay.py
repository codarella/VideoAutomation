"""
Title bar overlay — burns a white banner with segment title onto each image.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class TitleBarOverlay:
    """Adds a white title bar with centered black text to the top of an image."""

    def __init__(self, bar_fraction: float = 0.08):
        self.bar_fraction = bar_fraction

    def apply(self, image_path: str | Path, title: str) -> bool:
        """Burn title bar onto image in-place. Returns True on success."""
        image_path = str(image_path)
        if not os.path.exists(image_path):
            return False
        if not title:
            return False

        try:
            img = Image.open(image_path).convert("RGB")
            w, h = img.size
            bar_h = int(h * self.bar_fraction)

            draw = ImageDraw.Draw(img)

            # White bar + black border line
            draw.rectangle([0, 0, w, bar_h], fill=(255, 255, 255))
            draw.line([(0, bar_h), (w, bar_h)], fill=(0, 0, 0), width=2)

            # Font
            font_size = int(bar_h * 0.6)
            font = None
            for fp in [
                "C:\\Windows\\Fonts\\arialbd.ttf",
                "C:\\Windows\\Fonts\\arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]:
                if os.path.exists(fp):
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except Exception:
                        pass

            if not font:
                font = ImageFont.load_default()

            # Center text in bar
            bbox = draw.textbbox((0, 0), title, font=font)
            x = (w - (bbox[2] - bbox[0])) // 2
            y = (bar_h - (bbox[3] - bbox[1])) // 2 - 2

            draw.text((x, y), title, fill=(0, 0, 0), font=font)
            img.save(image_path, "PNG")
            return True

        except Exception as e:
            print(f"      Title overlay error: {e}")
            return False
