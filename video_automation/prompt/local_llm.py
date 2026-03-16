"""
Local LLM prompt generator (Ollama / LM Studio).

Generates prompts one scene at a time via local LLM API.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from video_automation.models import Project, Scene
from video_automation.prompt.base import PromptGenerator, SYSTEM_PROMPT


class LocalLLMPromptGenerator(PromptGenerator):
    """Generate prompts via local LLM (Ollama or LM Studio)."""

    def __init__(self, provider: str = "ollama", model: str = "qwen2.5:7b", url: str = ""):
        self.provider = provider.lower()
        self.model = model

        if url:
            self.base_url = url
        elif self.provider == "ollama":
            self.base_url = "http://localhost:11434"
        elif self.provider == "lmstudio":
            self.base_url = "http://localhost:1234"
        else:
            self.base_url = "http://localhost:11434"

        self.enabled = self._test_connection()

    def _test_connection(self) -> bool:
        try:
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            else:
                r = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if r.status_code == 200:
                print(f"   Local LLM ready ({self.provider}: {self.model})")
                return True
        except Exception:
            pass
        print(f"   WARNING: Could not connect to {self.provider} at {self.base_url}")
        return False

    def generate(self, project: Project, workspace: Path) -> None:
        if not self.enabled:
            print("   Local LLM not available, skipping.")
            return

        needs_prompt = [s for s in project.scenes if s.needs_prompt()]
        if not needs_prompt:
            return

        print(f"   Generating {len(needs_prompt)} prompts via {self.provider}...")
        done = 0

        for scene in needs_prompt:
            if scene.type == "number_card":
                num = scene.metadata.get("segment_number", 0)
                scene.prompt = (
                    f"Pure white background filling the entire 16:9 frame. "
                    f"Single large bold black number {num} centered. "
                    f"No other elements, no decorative details, no gradients, completely clean. "
                    f"16:9 widescreen. No text or labels anywhere in the image."
                )
                scene.prompt_source = "template"
                scene.status = "prompted"
                done += 1
                continue

            # Build per-scene user message
            seg_title = scene.metadata.get("segment_title", "")
            char_note = "Include ONE stick figure scientist." if scene.include_character else ""
            user_msg = (
                f"Segment: #{scene.metadata.get('segment_number', '?')}: {seg_title}\n"
                f"Scene text: \"{scene.text}\"\n"
                f"Duration: {scene.duration:.1f}s\n"
                f"{char_note}\n"
                f"Write ONE image generation prompt following all style rules."
            )

            prompt = self._call_llm(user_msg)
            if prompt:
                scene.prompt = prompt
                scene.prompt_source = self.provider
                scene.status = "prompted"
                done += 1

            if done % 10 == 0:
                print(f"      {done}/{len(needs_prompt)} done")

        print(f"   Generated {done}/{len(needs_prompt)} prompts via {self.provider}")

    def _call_llm(self, user_message: str) -> str:
        try:
            if self.provider == "ollama":
                r = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "system": SYSTEM_PROMPT,
                        "prompt": user_message,
                        "stream": False,
                    },
                    timeout=120,
                )
                if r.status_code == 200:
                    return r.json().get("response", "").strip()
            else:
                # OpenAI-compatible API (LM Studio)
                r = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                    timeout=120,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"      LLM error: {e}")
        return ""
