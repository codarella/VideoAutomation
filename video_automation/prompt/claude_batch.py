"""
Claude API prompt generator with visual-beat awareness.

Sends one API call per segment so Claude can deeply analyze the narration,
identify visual beats (groups of scenes sharing one subject), and write
prompts where every scene shows exactly what the narrator is saying.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from video_automation.models import Project, Scene, AlignedSegmentData
from video_automation.prompt.base import PromptGenerator, SYSTEM_PROMPT


# ── Pricing table for cost estimation ─────────────────────────────────────
PRICES = {
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

NUMBER_CARD_PROMPT = (
    "Pure white background filling the entire 16:9 frame. "
    "Single large bold black number {n} centered. "
    "No other elements, no decorative details, no gradients, completely clean. "
    "16:9 widescreen. No text or labels anywhere in the image."
)


class ClaudeBatchPromptGenerator(PromptGenerator):
    """Generate prompts per segment with visual-beat analysis."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 character_rate: float = 0.20):
        self.model = model
        self.character_rate = character_rate
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

        # Group scenes by segment number
        segments: dict[int, list[Scene]] = {}
        for scene in needs_prompt:
            seg_num = scene.metadata.get("segment_number")
            if seg_num is not None:
                segments.setdefault(seg_num, []).append(scene)

        total_assigned = 0
        total_in_tok = 0
        total_out_tok = 0
        total_scenes = sum(len(s) for s in segments.values())

        print(f"   Generating prompts for {total_scenes} scenes "
              f"across {len(segments)} segments...")

        for seg_num, seg_scenes in sorted(segments.items()):
            # Find the AlignedSegmentData for full narration text
            seg_data = next(
                (a for a in project.aligned_segments if a.number == seg_num),
                None,
            )

            try:
                assigned, in_tok, out_tok = self._generate_segment(
                    seg_num, seg_scenes, seg_data,
                )
                total_assigned += assigned
                total_in_tok += in_tok
                total_out_tok += out_tok
            except Exception as e:
                print(f"   WARNING: Segment {seg_num} failed: {e}")
                import traceback
                traceback.print_exc()

        # Print totals
        in_p, out_p = PRICES.get(self.model, (5.00, 25.00))
        total_cost = (
            (total_in_tok / 1_000_000 * in_p)
            + (total_out_tok / 1_000_000 * out_p)
        )
        print(f"\n   Claude returned {total_assigned}/{total_scenes} prompts total")
        print(f"   Tokens: {total_in_tok:,} in / {total_out_tok:,} out  "
              f"|  Est. cost: ${total_cost:.4f}")

    def _generate_segment(
        self,
        seg_num: int,
        seg_scenes: list[Scene],
        seg_data: Optional[AlignedSegmentData],
    ) -> tuple[int, int, int]:
        """
        Generate prompts for one segment via Claude API.

        Returns: (assigned_count, input_tokens, output_tokens)
        """
        n = len(seg_scenes)
        title = seg_scenes[0].metadata.get("segment_title", "")

        # Build the full narration from AlignedSegmentData (original script),
        # falling back to concatenated scene text if not available
        if seg_data and seg_data.body:
            full_narration = seg_data.body[:5000]
        else:
            full_narration = " ".join(s.text for s in seg_scenes if s.text)[:5000]

        # Build time slot list
        slot_lines = []
        for i, scene in enumerate(seg_scenes):
            slot_num = i + 1
            if scene.type == "number_card":
                slot_lines.append(
                    f"Slot {slot_num} | {scene.start:.2f}s-{scene.end:.2f}s "
                    f"({scene.duration:.1f}s) [NUMBER CARD: {seg_num}]"
                )
            else:
                char_note = " [include scientist figure]" if scene.include_character else ""
                slot_lines.append(
                    f"Slot {slot_num} | {scene.start:.2f}s-{scene.end:.2f}s "
                    f"({scene.duration:.1f}s){char_note} | \"{scene.text[:250]}\""
                )

        slots_text = "\n".join(slot_lines)

        user_message = (
            f"SEGMENT {seg_num}: \"{title}\"\n"
            f"\n"
            f"FULL NARRATION:\n"
            f"\"{full_narration}\"\n"
            f"\n"
            f"TIME SLOTS ({n} total):\n"
            f"{slots_text}\n"
            f"\n"
            f"INSTRUCTIONS:\n"
            f"\n"
            f"Step 1 — Read the FULL NARRATION above carefully. Identify visual beats: "
            f"groups of consecutive time slots that share one visual subject or concept. "
            f"A beat boundary occurs where the narration shifts to a new subject, location, "
            f"or action. Typically 3-8 beats per segment, with 2-6 slots each.\n"
            f"\n"
            f"Step 2 — For each time slot, write one image generation prompt. "
            f"The slot text fragments show you roughly WHEN each slot falls in the narration, "
            f"but read the FULL NARRATION to understand the complete idea at each moment — "
            f"do NOT treat the slot fragment as the only content to illustrate. "
            f"Show what the narrator is describing at that point in the story.\n"
            f"\n"
            f"Within a beat: same backdrop palette, same environment, same central subject. "
            f"Vary only camera angle and zoom level. First slot of a beat → establishing wide shot. "
            f"Last slot of a beat → climax or consequence framing if appropriate.\n"
            f"\n"
            f"Between beats: shift the environment, palette emphasis, and central subject.\n"
            f"\n"
            f"For slots marked [NUMBER CARD: N]: write exactly: "
            f"\"Pure white background filling the entire 16:9 frame. "
            f"Single large bold black number N centered. "
            f"No other elements, no decorative details, no gradients, completely clean. "
            f"16:9 widescreen. No text or labels anywhere in the image.\" "
            f"(replace N with the actual digit)\n"
            f"\n"
            f"For slots marked [include scientist figure]: include ONE rounded expressive "
            f"stick figure in the scene.\n"
            f"\n"
            f"Follow ALL style rules from your instructions exactly.\n"
            f"\n"
            f"Return a JSON object with this structure:\n"
            f"{{\n"
            f"  \"beats\": [\n"
            f"    {{\"beat\": 1, \"concept\": \"short description of visual subject\", \"slots\": [1, 2, 3]}},\n"
            f"    {{\"beat\": 2, \"concept\": \"...\", \"slots\": [4, 5, 6]}}\n"
            f"  ],\n"
            f"  \"prompts\": [\n"
            f"    {{\"slot\": 1, \"prompt\": \"2D Western Cartoon animation. ...\"}},\n"
            f"    {{\"slot\": 2, \"prompt\": \"2D Western Cartoon animation. ...\"}}\n"
            f"  ]\n"
            f"}}\n"
            f"\n"
            f"The \"prompts\" array must have exactly {n} entries (one per slot).\n"
            f"Output ONLY the JSON object. No preamble, no commentary, no code fences."
        )

        print(f"   Segment {seg_num} ({n} slots, \"{title[:40]}\"): sending to Claude...")

        with self.client.messages.stream(
            model=self.model,
            max_tokens=16384,
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

        # Handle both formats: dict with beats+prompts, or flat list
        if isinstance(data, dict):
            prompts_list = data.get("prompts", [])
            beats = data.get("beats", [])
            for b in beats:
                concept = b.get("concept", "")
                slots = b.get("slots", [])
                print(f"      Beat {b.get('beat')}: \"{concept}\" → slots {slots}")
        elif isinstance(data, list):
            prompts_list = data
        else:
            prompts_list = []

        assigned = 0
        for item in prompts_list:
            slot_num = item.get("slot") or item.get("scene")
            prompt = (item.get("prompt") or "").strip()
            if isinstance(slot_num, int) and 1 <= slot_num <= n and prompt:
                scene = seg_scenes[slot_num - 1]
                scene.prompt = prompt
                scene.prompt_source = "claude-api"
                scene.status = "prompted"
                assigned += 1

        # Token usage
        usage = final.usage
        in_tok = usage.input_tokens
        out_tok = usage.output_tokens
        in_p, out_p = PRICES.get(self.model, (5.00, 25.00))
        cost = (in_tok / 1_000_000 * in_p) + (out_tok / 1_000_000 * out_p)
        print(f"      → {assigned}/{n} prompts  |  "
              f"{in_tok:,} in / {out_tok:,} out  |  ${cost:.4f}")

        return assigned, in_tok, out_tok
