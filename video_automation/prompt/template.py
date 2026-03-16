"""
Template-based fallback prompt generator.

Generates prompts from scene text using keyword extraction — no external API needed.
"""

from __future__ import annotations

import re
from pathlib import Path

from video_automation.models import Project, Scene
from video_automation.prompt.base import PromptGenerator


class TemplatePromptGenerator(PromptGenerator):
    """Generate prompts from templates when no LLM is available."""

    def generate(self, project: Project, workspace: Path) -> None:
        needs_prompt = [s for s in project.scenes if s.needs_prompt()]
        if not needs_prompt:
            return

        print(f"   Generating {len(needs_prompt)} prompts via template...")

        for scene in needs_prompt:
            if scene.type == "number_card":
                num = scene.metadata.get("segment_number", 0)
                scene.prompt = (
                    f"Pure white background filling the entire 16:9 frame. "
                    f"Single large bold black number {num} centered. "
                    f"No other elements, no decorative details, no gradients. "
                    f"16:9 widescreen."
                )
            else:
                scene.prompt = self._build_template(scene)

            scene.prompt_source = "template"
            scene.status = "prompted"

        print(f"   Generated {len(needs_prompt)} template prompts")

    def _build_template(self, scene: Scene) -> str:
        """Build a prompt from scene text using keyword extraction."""
        text = scene.text.lower()

        # Extract key subjects
        keywords = self._extract_keywords(text)
        subject = ", ".join(keywords[:3]) if keywords else "abstract science concept"

        # Detect scene tone
        if any(w in text for w in ("discover", "found", "first", "proved", "confirm")):
            tone = "dramatic discovery moment, neon highlights, maximum visual energy"
        elif any(w in text for w in ("because", "means", "therefore", "consequence")):
            tone = "aftermath and realization, muted background with one bright focal point"
        elif any(w in text for w in ("but", "however", "instead", "actually")):
            tone = "contrast and surprise, split composition showing before and after"
        else:
            tone = "educational illustration, busy maximalist alien sci-fi backdrop"

        char_note = (
            "ONE stick figure scientist (round head, dot eyes, line body) reacting with expression. "
            if scene.include_character else ""
        )

        return (
            f"2D Western Cartoon animation. Vivid alien sci-fi environment with "
            f"neon greens, electric blues, and deep purples. {tone}. "
            f"Central subject: {subject}. {char_note}"
            f"Bold wobbly black outlines, flat cel-shading, no gradients, "
            f"no text anywhere. 16:9 widescreen."
        )

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from scene text."""
        # Remove common words
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "that", "this", "these", "those", "it", "its", "not", "no",
            "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
            "each", "every", "all", "any", "few", "more", "most", "other", "some",
            "such", "than", "too", "very", "just", "about", "up", "down",
        }
        words = re.findall(r'\b[a-z]{3,}\b', text)
        return [w for w in words if w not in stopwords][:10]
