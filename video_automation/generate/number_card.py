"""
PIL/Pillow number card renderer.

Generates "Number X" title cards locally — NO AI image generators.
White background, large bold rounded black number centered (Fredoka Bold).
"""

from __future__ import annotations

import os
from pathlib import Path

# Bundled Fredoka font (variable, supports Bold weight)
_FREDOKA_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "FredokaOne-Regular.ttf")


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
            font_size = int(self.height * 0.6)
            font = self._load_font(font_size)

            bbox = draw.textbbox((0, 0), number_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            x = (self.width - text_width) // 2
            y = (self.height - text_height) // 2 - bbox[1]

            draw.text((x, y), number_text, fill=(0, 0, 0), font=font)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "PNG")
            return True

        except Exception as e:
            print(f"   WARNING: Error generating title card: {e}")
            return False

    def _load_font(self, font_size: int):
        """Load Fredoka Bold (rounded), with fallbacks."""
        from PIL import ImageFont

        # Primary: bundled Fredoka variable font set to Bold
        if os.path.exists(_FREDOKA_PATH):
            try:
                font = ImageFont.truetype(_FREDOKA_PATH, font_size)
                font.set_variation_by_name("Bold")
                return font
            except Exception:
                try:
                    return ImageFont.truetype(_FREDOKA_PATH, font_size)
                except Exception:
                    pass

        # Fallback: system bold fonts
        fallback_paths = [
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\impact.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]

        for fp in fallback_paths:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, font_size)
                except Exception:
                    pass

        return ImageFont.load_default()
