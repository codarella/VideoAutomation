"""
Split aligned segments into timed scenes for image generation.

This is the micro-segmenter: given a segment with start/end times and words,
produce 3-8 second scenes that cover the segment gaplessly.
"""

from __future__ import annotations

import re
import random
from video_automation.config import PacingProfile
from video_automation.models import Scene, Word
from video_automation.segment.aligner import AlignedSegment

# Patterns that signal the start of a new list item
_LIST_ITEM_PATTERNS = re.compile(
    r"^("
    r"first(ly)?|second(ly)?|third(ly)?|fourth(ly)?|fifth(ly)?|"
    r"sixth(ly)?|seventh(ly)?|eighth(ly)?|ninth(ly)?|tenth(ly)?|"
    r"next|finally|lastly|additionally|furthermore|moreover|"
    r"also|another|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"\d+[\.\):]"
    r")[,\.\s:;]?$",
    re.IGNORECASE,
)


class SceneSplitter:
    """
    Split each aligned segment into timed scenes.

    - Number card scene prepended to each numbered segment
    - Content scenes split at sentence/clause boundaries
    - Pacing varies based on position in video (fast cuts early)
    - All scenes are gapless and word-boundary aligned
    """

    def __init__(self, pacing: PacingProfile, character_rate: float = 0.20):
        self.pacing = pacing
        self.character_rate = character_rate

    def split_all(
        self,
        segments: list[AlignedSegment],
        intro_end: float = 0.0,
        audio_duration: float = 0.0,
    ) -> list[Scene]:
        """
        Split all segments into a flat list of scenes.
        Optionally prepend an intro scene if intro_end > 0.
        """
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
        """
        Compute the end time for the number card scene.

        When Whisper captured "Number X" (number_word_end > 0), find where
        the title sentence ends and use that. Otherwise fall back to the
        fixed pacing duration.
        """
        if seg.number_word_end > 0:
            # Find end of title sentence in the segment's words
            title_end = seg.number_word_end
            for w in seg.words:
                if w.start < seg.number_word_end:
                    continue
                title_end = w.end
                if w.text.rstrip().endswith((".", "!", "?")):
                    break

            # Add small padding so the card doesn't cut off abruptly
            card_dur = (title_end - seg.start) + 0.3
            # Clamp to reasonable range
            card_dur = max(1.5, min(card_dur, 5.0))
            return min(seg.start + card_dur, seg.end)

        # Fallback: fixed duration from pacing config
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

        content_start = card_end
        current_words: list[Word] = []
        scene_num = 0

        for wi, word in enumerate(remaining_words):
            # Check if this word starts a new list item — force a scene break
            # before it (but only if we have accumulated some words already)
            if current_words and self._is_list_item_start(word):
                prev_word = current_words[-1]
                scene_text = " ".join(w.text for w in current_words)
                include_char = random.random() < self.character_rate
                scene = Scene(
                    id=f"seg{seg.number:02d}_scene{scene_num:02d}",
                    type="content",
                    start=current_words[0].start,
                    end=prev_word.end,
                    text=scene_text,
                    words=list(current_words),
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
                current_words = []

            current_words.append(word)
            elapsed = word.end - current_words[0].start

            # Get target duration range for this position in the video
            min_dur, max_dur = self.pacing.target_duration(word.start)
            target = (min_dur + max_dur) / 2.0

            is_last_word = (wi == len(remaining_words) - 1)
            at_boundary = self._is_sentence_boundary(word)
            past_target = elapsed >= target
            past_hard_max = elapsed >= max_dur

            if is_last_word or (past_target and at_boundary) or past_hard_max:
                scene_text = " ".join(w.text for w in current_words)
                scene_end = word.end if is_last_word else word.end

                # Ensure scene ends at segment boundary if last scene
                if is_last_word:
                    scene_end = seg.end

                include_char = random.random() < self.character_rate

                scene = Scene(
                    id=f"seg{seg.number:02d}_scene{scene_num:02d}",
                    type="content",
                    start=current_words[0].start,
                    end=scene_end,
                    text=scene_text,
                    words=list(current_words),
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
                current_words = []

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

    def _is_sentence_boundary(self, word: Word) -> bool:
        """Check if a word ends at a sentence/clause boundary."""
        text = word.text.rstrip()
        return text.endswith((".", ",", ";", "?", "!", ":", "—"))

    def _is_list_item_start(self, word: Word) -> bool:
        """Check if a word signals the start of a new list item."""
        return bool(_LIST_ITEM_PATTERNS.match(word.text.strip()))
