"""
Claude API batch prompt generator.

Sends ALL scenes in one streaming API call so Claude sees full video structure
for consistent visual style and narrative coherence.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from video_automation.models import Project, Scene
from video_automation.prompt.base import PromptGenerator, SYSTEM_PROMPT


class ClaudeBatchPromptGenerator(PromptGenerator):
    """Generate all prompts in a single Claude API call."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.model = model
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
            self.enabled = True
            print(f"   Claude API ready ({model})")
        except ImportError:
            print("   WARNING: anthropic package not installed — pip install anthropic")
            self.enabled = False
            self.client = None

    def generate(self, project: Project, workspace: Path) -> None:
        if not self.enabled:
            return

        needs_prompt = [s for s in project.scenes if s.needs_prompt()]
        if not needs_prompt:
            return

        # Build scene list for Claude
        scene_lines = []
        current_seg = None
        for i, scene in enumerate(needs_prompt):
            seg_num = scene.metadata.get("segment_number")
            if seg_num != current_seg:
                current_seg = seg_num
                title = scene.metadata.get("segment_title", "")
                scene_lines.append(f"\n-- SEGMENT {seg_num}: \"{title}\" --")
                # Inject segment body text for context
                seg_scenes = [s for s in project.scenes
                              if s.metadata.get("segment_number") == seg_num]
                full_text = " ".join(s.text for s in seg_scenes if s.text)
                if full_text:
                    scene_lines.append(f"FULL NARRATION: \"{full_text[:2000]}\"")
                scene_lines.append("SCENES:")

            if scene.type == "number_card":
                scene_lines.append(
                    f"Scene {i+1} | {scene.start:.2f}s-{scene.end:.2f}s "
                    f"({scene.duration:.1f}s) [NUMBER CARD: {seg_num}]"
                )
            else:
                char_note = " [include scientist figure]" if scene.include_character else ""
                scene_lines.append(
                    f"Scene {i+1} | {scene.start:.2f}s-{scene.end:.2f}s "
                    f"({scene.duration:.1f}s){char_note} | {scene.text[:250]}"
                )

        n = len(needs_prompt)
        scenes_text = "\n".join(scene_lines)
        user_message = (
            f"Here is the complete scene list for this video ({n} scenes total).\n"
            f"Each segment begins with its FULL NARRATION — read it entirely before writing "
            f"prompts for that segment. Use it to understand the story arc, key concepts, "
            f"and emotional beats, then write prompts that build a coherent visual narrative "
            f"across the segment's scenes.\n"
            f"\n{scenes_text}\n\n"
            f"For scenes marked [NUMBER CARD: N]: write exactly this prompt, replacing N with the actual digit: "
            f"\"Pure white background filling the entire 16:9 frame. "
            f"Single large bold black number N centered. "
            f"No other elements, no decorative details, no gradients, completely clean. "
            f"16:9 widescreen. No text or labels anywhere in the image.\"\n"
            f"\n"
            f"For all other scenes, apply this thinking before writing the prompt:\n"
            f"1. Re-read the FULL NARRATION of this segment — what is the story arc?\n"
            f"2. Which scene type fits this scene's role in that arc? (ESTABLISH/DETAIL/CLIMAX/REACTION/CHANGE)\n"
            f"3. LITERAL READ: What objects, events, and actions are literally described in this scene text?\n"
            f"4. CONTEXT & TONE: What is the broader emotional context?\n"
            f"5. ENVIRONMENT: What does the illustrated space backdrop look and feel like?\n"
            f"6. FREEZE-FRAME: Show the literal content as an action inside that environment.\n"
            f"7. What was the previous scene — vary the composition and zoom level.\n"
            f"8. Is this the first scene of a segment? Use a simple establishing/wide shot.\n"
            f"9. Is this the last scene of a segment? Use peak-drama or consequence framing.\n"
            f"\n"
            f"Write ONE image generation prompt per scene. Follow ALL style rules exactly.\n"
            f"Scenes marked [include scientist figure] must include ONE rounded expressive figure.\n"
            f"Return a JSON array with exactly {n} objects:\n"
            f'[{{"scene": 1, "prompt": "..."}}, {{"scene": 2, "prompt": "..."}}, '
            f'..., {{"scene": {n}, "prompt": "..."}}]\n'
            f"Output ONLY the JSON array. No preamble, no commentary, no code fences."
        )

        try:
            print(f"   Sending {n} scenes to Claude API ({self.model})...")
            with self.client.messages.stream(
                model=self.model,
                max_tokens=65536,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                final = stream.get_final_message()

            text = next(
                (b.text for b in final.content if b.type == "text"), ""
            ).strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```", 1)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()

            data = json.loads(text)
            assigned = 0
            for item in data:
                scene_num = item.get("scene")
                prompt = (item.get("prompt") or "").strip()
                if isinstance(scene_num, int) and 1 <= scene_num <= n and prompt:
                    scene = needs_prompt[scene_num - 1]
                    scene.prompt = prompt
                    scene.prompt_source = "claude-api"
                    scene.status = "prompted"
                    assigned += 1

            # Token usage + cost
            usage = final.usage
            in_tok = usage.input_tokens
            out_tok = usage.output_tokens
            PRICES = {
                "claude-opus-4-6": (5.00, 25.00),
                "claude-sonnet-4-6": (3.00, 15.00),
                "claude-haiku-4-5": (1.00, 5.00),
            }
            in_p, out_p = PRICES.get(self.model, (5.00, 25.00))
            cost = (in_tok / 1_000_000 * in_p) + (out_tok / 1_000_000 * out_p)
            print(f"   Claude returned {assigned}/{n} prompts")
            print(f"   Tokens: {in_tok:,} in / {out_tok:,} out  |  Est. cost: ${cost:.4f}")

        except Exception as e:
            print(f"   WARNING: Claude API error: {e}")
            import traceback
            traceback.print_exc()
