"""
PIL/Pillow number card renderer.

Generates "Number X" title cards locally — NO AI image generators.
White background, large bold black number centered.
"""

from __future__ import annotations

import os
from pathlib import Path


class NumberCardGenerator:
    """Generate number title cards using PIL/Pillow."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height

    def generate(self, number: int, output_path: str) -> bool:
        """
        Generate a number title card.

        Args:
            number: The countdown number (10, 9, 8, etc.)
            output_path: Where to save the PNG
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            img = Image.new("RGB", (self.width, self.height), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)

            number_text = str(number)
            font_size = int(self.height * 0.5)
            font = self._load_font(font_size)

            bbox = draw.textbbox((0, 0), number_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            x = (self.width - text_width) // 2
            y = (self.height - text_height) // 2

            draw.text((x, y), number_text, fill=(0, 0, 0), font=font)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "PNG")
            return True

        except Exception as e:
            print(f"   WARNING: Error generating title card: {e}")
            return False

    def _load_font(self, font_size: int):
        """Load a bold font, with fallbacks."""
        from PIL import ImageFont

        font_paths = [
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\Arial Bold.ttf",
            "C:\\Windows\\Fonts\\impact.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]

        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, font_size)
                except Exception:
                    pass

        try:
            return ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size
            )
        except Exception:
            return ImageFont.load_default()
