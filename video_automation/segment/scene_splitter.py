"""
Split aligned segments into timed scenes for image generation.

Primary: LLM-based splitting via Gemini 2.5 Pro (semantic visual beats).
Fallback: Time-based splitting at punctuation boundaries.
"""

from __future__ import annotations

import json
import random
import re
import time
from video_automation.config import PacingProfile
from video_automation.models import Scene, Word
from video_automation.segment.aligner import AlignedSegment

# Guardrail constants
MIN_SCENE_DURATION = 3.0   # Merge scenes shorter than this
MAX_SCENE_DURATION = 8.0   # Split scenes longer than this

GEMINI_MODEL = "gemini-2.5-flash"

SCENE_SPLIT_PROMPT = """You are a video scene splitter for a top-N listicle video. Given narration text for one numbered segment, split it into scenes where each scene represents a distinct visual concept that would warrant a different image on screen.

Split at points where the VISUAL SUBJECT changes — a new entity, location, action, or idea that a viewer would expect to see a different image for.

Rules:
- Each scene should be roughly 3-8 seconds of spoken narration
- Split at natural visual transition points, not mid-sentence unless the sentence describes two clearly different visuals
- Return the EXACT input text, partitioned into ordered chunks — every word must appear in exactly one chunk, in the original order
- Do NOT rephrase, reorder, or drop any words

Return a JSON array of strings. Each string is one scene's narration text.
Example: ["The African elephant is the largest land animal on Earth.", "It roams the savannas and forests of sub-Saharan Africa, traveling in herds led by a matriarch.", "Their massive ears help regulate body temperature in the scorching heat."]

Narration text:
{segment_text}"""


class SceneSplitter:
    """
    Split each aligned segment into timed scenes.

    - Number card scene prepended to each numbered segment
    - Content scenes split by LLM (Gemini) or time-based fallback
    - All scenes are gapless and word-boundary aligned
    """

    def __init__(self, pacing: PacingProfile, character_rate: float = 0.20,
                 gemini_api_key: str = ""):
        self.pacing = pacing
        self.character_rate = character_rate
        self.gemini_api_key = gemini_api_key
        self._gemini_model = None

    def split_all(
        self,
        segments: list[AlignedSegment],
        intro_end: float = 0.0,
        audio_duration: float = 0.0,
    ) -> list[Scene]:
        """Split all segments into a flat list of scenes."""
        all_scenes: list[Scene] = []
        scene_idx = 0

        # Intro scene (before first segment)
        if intro_end > 0.001 and segments and segments[0].start > 0.001:
            intro_scene = Scene(
                id="intro",
                type="intro",
                start=0.0,
                end=min(intro_end, segments[0].start),
                text="",
                words=[],
                status="planned",
            )
            all_scenes.append(intro_scene)
            scene_idx += 1

        for seg in segments:
            scenes = self._split_segment(seg, scene_idx)
            all_scenes.extend(scenes)
            scene_idx += len(scenes)

        # Ensure last scene reaches audio_duration
        if audio_duration > 0 and all_scenes:
            last = all_scenes[-1]
            if last.end < audio_duration - 0.01:
                last.end = audio_duration

        return all_scenes

    def _compute_card_end(self, seg: AlignedSegment) -> float:
        """Compute the end time for the number card scene."""
        if seg.number_word_end > 0:
            title_end = seg.number_word_end
            for w in seg.words:
                if w.start < seg.number_word_end:
                    continue
                title_end = w.end
                if w.text.rstrip().endswith((".", "!", "?")):
                    break

            card_dur = (title_end - seg.start) + 0.1
            card_dur = max(0.8, min(card_dur, 1.2))
            return min(seg.start + card_dur, seg.end)

        return min(seg.start + self.pacing.number_card_duration, seg.end)

    def _split_segment(self, seg: AlignedSegment, start_idx: int) -> list[Scene]:
        """Split a single segment into number card + content scenes."""
        scenes: list[Scene] = []
        idx = start_idx

        # 1. Number card scene
        card_end = self._compute_card_end(seg)
        card_words = [w for w in seg.words if w.start >= seg.start and w.end <= card_end]

        card = Scene(
            id=f"seg{seg.number:02d}_card",
            type="number_card",
            start=seg.start,
            end=card_end,
            text=f"Number {seg.number}.",
            words=card_words,
            status="planned",
            metadata={
                "segment_number": seg.number,
                "segment_title": seg.title,
            },
        )
        scenes.append(card)
        idx += 1

        # 2. Content scenes from remaining words
        remaining_words = [w for w in seg.words if w.start >= card_end]
        if not remaining_words:
            return scenes

        # Try LLM splitting, fall back to time-based
        if self.gemini_api_key:
            content_scenes = self._split_content_llm(seg, remaining_words)
            if content_scenes is None:
                content_scenes = self._split_content_time(seg, remaining_words)
        else:
            content_scenes = self._split_content_time(seg, remaining_words)

        # Build Scene objects from word groups
        scene_num = 0
        for group in content_scenes:
            scene_text = " ".join(w.text for w in group)
            is_last = (scene_num == len(content_scenes) - 1)
            scene_end = seg.end if is_last else group[-1].end

            include_char = random.random() < self.character_rate

            scene = Scene(
                id=f"seg{seg.number:02d}_scene{scene_num:02d}",
                type="content",
                start=group[0].start,
                end=scene_end,
                text=scene_text,
                words=list(group),
                status="planned",
                include_character=include_char,
                metadata={
                    "segment_number": seg.number,
                    "segment_title": seg.title,
                },
            )
            scenes.append(scene)
            idx += 1
            scene_num += 1

        # Handle edge case: gap between card_end and first remaining word
        if scenes and len(scenes) > 1:
            first_content = scenes[1]
            if first_content.start > card_end + 0.001:
                first_content.start = card_end

        # Ensure gapless: each scene starts where previous ends
        for i in range(1, len(scenes)):
            if abs(scenes[i].start - scenes[i - 1].end) > 0.001:
                scenes[i].start = scenes[i - 1].end

        return scenes

    # ── LLM-based splitting ──────────────────────────────────────────────

    def _get_gemini_client(self):
        """Lazy-init the Gemini client."""
        if self._gemini_model is not None:
            return self._gemini_model

        try:
            from google import genai
        except ImportError:
            print("   WARNING: google-genai not installed. "
                  "Run: pip install google-genai")
            return None

        self._gemini_model = genai.Client(api_key=self.gemini_api_key)
        return self._gemini_model

    def _split_content_llm(
        self, seg: AlignedSegment, remaining_words: list[Word]
    ) -> list[list[Word]] | None:
        """
        Use Gemini to split the segment text into semantic scenes.
        Returns list of word groups, or None on failure (triggers fallback).
        """
        client = self._get_gemini_client()
        if client is None:
            return None

        from google.genai import types

        segment_text = " ".join(w.text for w in remaining_words)

        prompt = SCENE_SPLIT_PROMPT.format(segment_text=segment_text)

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw = response.text.strip()
        except Exception as e:
            print(f"   WARNING: Gemini API error for segment {seg.number}: {e}")
            return None

        # Parse JSON response
        try:
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            chunks = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"   WARNING: Gemini returned invalid JSON for segment {seg.number}: {e}")
            return None

        if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
            print(f"   WARNING: Gemini returned unexpected format for segment {seg.number}")
            return None

        if len(chunks) < 1:
            print(f"   WARNING: Gemini returned empty scene list for segment {seg.number}")
            return None

        # Map text chunks back to word groups
        word_groups = self._map_chunks_to_words(chunks, remaining_words)
        if word_groups is None:
            print(f"   WARNING: Could not map Gemini chunks to words for segment {seg.number}")
            return None

        # Enforce time guardrails
        word_groups = self._enforce_guardrails(word_groups)

        print(f"   Segment {seg.number}: Gemini split into {len(word_groups)} scenes")
        return word_groups

    def _map_chunks_to_words(
        self, chunks: list[str], words: list[Word]
    ) -> list[list[Word]] | None:
        """
        Map LLM text chunks back to the actual Word objects.

        Strategy: count words in each chunk and assign that many words
        sequentially. The LLM receives the exact text, so word counts
        should match closely.
        """
        # Count words in each chunk
        chunk_word_counts = []
        for chunk in chunks:
            # Split on whitespace to count words, matching how we built the text
            count = len(chunk.split())
            chunk_word_counts.append(count)

        total_chunk_words = sum(chunk_word_counts)
        total_actual_words = len(words)

        # Allow some tolerance for minor discrepancies
        if abs(total_chunk_words - total_actual_words) > max(3, total_actual_words * 0.15):
            print(f"   WARNING: Word count mismatch — LLM: {total_chunk_words}, "
                  f"actual: {total_actual_words}")
            return None

        # Assign words to groups sequentially
        word_groups: list[list[Word]] = []
        word_idx = 0

        for i, count in enumerate(chunk_word_counts):
            is_last_chunk = (i == len(chunk_word_counts) - 1)

            if is_last_chunk:
                # Last chunk gets all remaining words
                group = words[word_idx:]
            else:
                end_idx = min(word_idx + count, total_actual_words)
                group = words[word_idx:end_idx]
                word_idx = end_idx

            if group:
                word_groups.append(group)

        return word_groups if word_groups else None

    def _enforce_guardrails(self, word_groups: list[list[Word]]) -> list[list[Word]]:
        """Merge short scenes and split long ones."""
        # Pass 1: merge scenes shorter than MIN_SCENE_DURATION
        merged = list(word_groups)
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(merged):
                group = merged[i]
                duration = group[-1].end - group[0].start
                if duration < MIN_SCENE_DURATION and len(merged) > 1:
                    # Merge with the shorter neighbor
                    if i == 0:
                        merged[1] = group + merged[1]
                        merged.pop(0)
                    elif i == len(merged) - 1:
                        merged[-2] = merged[-2] + group
                        merged.pop()
                    else:
                        prev_dur = merged[i - 1][-1].end - merged[i - 1][0].start
                        next_dur = merged[i + 1][-1].end - merged[i + 1][0].start
                        if prev_dur <= next_dur:
                            merged[i - 1] = merged[i - 1] + group
                            merged.pop(i)
                        else:
                            merged[i + 1] = group + merged[i + 1]
                            merged.pop(i)
                    changed = True
                else:
                    i += 1

        # Pass 2: split scenes longer than MAX_SCENE_DURATION
        final: list[list[Word]] = []
        for group in merged:
            duration = group[-1].end - group[0].start
            if duration > MAX_SCENE_DURATION and len(group) > 1:
                halves = self._split_long_scene(group)
                final.extend(halves)
            else:
                final.append(group)

        return final

    def _split_long_scene(self, group: list[Word]) -> list[list[Word]]:
        """Split an over-long scene at the best punctuation boundary near the midpoint."""
        mid_time = (group[0].start + group[-1].end) / 2.0

        # Find best split point: strong punctuation closest to midpoint
        best_idx = None
        best_score = float("inf")

        for i, w in enumerate(group[:-1]):  # Don't split after last word
            strength = self._boundary_strength(w)
            if strength < 2:
                continue
            dist = abs(w.end - mid_time)
            # Prefer stronger boundaries, then closer to midpoint
            score = dist - (strength * 2.0)
            if score < best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            # No punctuation — split at word closest to midpoint
            best_idx = len(group) // 2

        left = group[:best_idx + 1]
        right = group[best_idx + 1:]

        result = []
        if left:
            result.append(left)
        if right:
            result.append(right)

        # Recursively split if still too long
        final = []
        for part in result:
            dur = part[-1].end - part[0].start
            if dur > MAX_SCENE_DURATION and len(part) > 1:
                final.extend(self._split_long_scene(part))
            else:
                final.append(part)

        return final

    # ── Time-based splitting (fallback) ──────────────────────────────────

    def _split_content_time(
        self, seg: AlignedSegment, remaining_words: list[Word]
    ) -> list[list[Word]]:
        """Original time-based splitting at punctuation boundaries."""
        word_groups: list[list[Word]] = []
        current_words: list[Word] = []

        for wi, word in enumerate(remaining_words):
            current_words.append(word)
            elapsed = word.end - current_words[0].start

            min_dur, max_dur = self.pacing.target_duration(word.start)
            target = (min_dur + max_dur) / 2.0
            min_content = self.pacing.content_min_duration

            is_last_word = (wi == len(remaining_words) - 1)
            strength = self._boundary_strength(word)
            past_target = elapsed >= target
            past_hard_max = elapsed >= max_dur
            past_minimum = elapsed >= min_content

            should_break = (
                is_last_word
                or (past_target and strength >= 2 and past_minimum)
                or (past_hard_max and past_minimum)
            )

            if should_break:
                word_groups.append(list(current_words))
                current_words = []

        if not word_groups and current_words:
            word_groups.append(current_words)

        print(f"   Segment {seg.number}: time-based split into {len(word_groups)} scenes")
        return word_groups

    def _boundary_strength(self, word: Word) -> int:
        """Return boundary strength: 0=none, 1=weak (comma), 2=medium (;:—), 3=strong (.?!)."""
        text = word.text.rstrip()
        if text.endswith((".", "?", "!")):
            return 3
        if text.endswith((";", ":", "—")):
            return 2
        if text.endswith(","):
            return 1
        return 0
