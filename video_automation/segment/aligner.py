"""
Align script-derived segment structure to Whisper-derived timestamps.

This is Layer 3 of the ironclad detection system — the bridge between
script ground truth and audio timing.

Strategy:
- Structure comes from the script (cannot hallucinate)
- Timing comes from Whisper (good at timing, unreliable for structure)
- Fuzzy context matching handles word-level transcription errors
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from video_automation.models import Word
from video_automation.segment.script_parser import ScriptSegment, WORD_TO_DIGIT, DIGIT_TO_WORD


@dataclass
class AlignedSegment:
    """A script segment with its Whisper-derived timestamp."""
    number: int
    title: str
    body: str                                    # Full text from script
    start: float                                 # Timestamp in audio (from Whisper alignment)
    end: float                                   # End timestamp (= next segment's start, or audio end)
    words: list[Word]                            # All Whisper words in this segment's time range
    confidence: float = 1.0                      # Alignment confidence (0-1)
    method: str = "context"                      # How this was aligned


class ScriptAligner:
    """
    Align script segments to Whisper word timestamps using fuzzy matching.

    For each "Number X" from the script:
    1. Extract surrounding context (~20 words before and after)
    2. Fuzzy-search the Whisper word stream for that context
    3. The timestamp of the matched region = segment boundary

    Immune to Whisper hallucination because structure comes from script,
    and context windows tolerate ~30% word-level errors.
    """

    def __init__(self, context_size: int = 20, similarity_threshold: float = 0.6):
        self.context_size = context_size
        self.similarity_threshold = similarity_threshold

    def align(
        self,
        script_segments: list[ScriptSegment],
        whisper_words: list[Word],
        audio_duration: float = 0.0,
    ) -> list[AlignedSegment]:
        """
        Align each script segment to its position in the Whisper word stream.

        Returns ordered list of AlignedSegment with timestamps.
        """
        if not script_segments or not whisper_words:
            return []

        whisper_texts = [w.text.lower().strip(".,!?;:'\"") for w in whisper_words]
        aligned = []

        for seg in script_segments:
            result = self._find_segment_timestamp(seg, whisper_words, whisper_texts)
            if result:
                aligned.append(result)
            else:
                # Fallback: try phonetic/direct match on just "Number X"
                result = self._fallback_direct_match(seg, whisper_words, whisper_texts)
                if result:
                    aligned.append(result)
                else:
                    print(f"   WARNING: Could not align segment Number {seg.number} — "
                          f"will interpolate from neighbors")
                    aligned.append(AlignedSegment(
                        number=seg.number,
                        title=seg.title,
                        body=seg.body,
                        start=-1.0,  # Sentinel: needs interpolation
                        end=-1.0,
                        words=[],
                        confidence=0.0,
                        method="unaligned",
                    ))

        # Interpolate any unaligned segments from neighbors
        self._interpolate_unaligned(aligned, audio_duration)

        # Set end times (each segment ends where the next begins)
        for i in range(len(aligned)):
            if i + 1 < len(aligned):
                aligned[i].end = aligned[i + 1].start
            else:
                aligned[i].end = audio_duration if audio_duration > 0 else whisper_words[-1].end

        # Assign Whisper words to each segment's time range
        for seg in aligned:
            seg.words = [
                w for w in whisper_words
                if w.start >= seg.start and w.end <= seg.end
            ]

        return aligned

    def _find_segment_timestamp(
        self,
        seg: ScriptSegment,
        whisper_words: list[Word],
        whisper_texts: list[str],
    ) -> Optional[AlignedSegment]:
        """
        Find a segment's timestamp using fuzzy context matching.

        Uses the context AFTER "Number X" (the segment body) since that's
        more unique than the "Number X" phrase itself.
        """
        # Build the needle: "Number X" + first ~20 words of body
        body_words = seg.body.split()[:self.context_size]
        needle = [w.lower().strip(".,!?;:'\"") for w in body_words]

        if len(needle) < 3:
            return None

        best_idx, best_score = self._sliding_window_match(needle, whisper_texts)

        if best_score >= self.similarity_threshold:
            timestamp = whisper_words[best_idx].start
            print(f"   Segment {seg.number}: aligned at {timestamp:.2f}s "
                  f"(confidence={best_score:.2f}, method=context)")
            return AlignedSegment(
                number=seg.number,
                title=seg.title,
                body=seg.body,
                start=timestamp,
                end=0.0,  # Set later
                words=[],  # Set later
                confidence=round(best_score, 3),
                method="context",
            )

        return None

    def _fallback_direct_match(
        self,
        seg: ScriptSegment,
        whisper_words: list[Word],
        whisper_texts: list[str],
    ) -> Optional[AlignedSegment]:
        """
        Fallback: search for "number" followed by the digit/word directly.

        Less reliable than context matching but catches cases where context is too garbled.
        """
        target_number_text = str(seg.number)
        target_number_word = DIGIT_TO_WORD.get(seg.number, "").lower()

        for i, text in enumerate(whisper_texts):
            if text != "number":
                continue

            # Check next word
            if i + 1 < len(whisper_texts):
                next_text = whisper_texts[i + 1]
                if next_text == target_number_text or next_text == target_number_word:
                    timestamp = whisper_words[i].start
                    print(f"   Segment {seg.number}: aligned at {timestamp:.2f}s "
                          f"(method=direct_match)")
                    return AlignedSegment(
                        number=seg.number,
                        title=seg.title,
                        body=seg.body,
                        start=timestamp,
                        end=0.0,
                        words=[],
                        confidence=0.7,
                        method="direct_match",
                    )

        # Try fuzzy match on "number" (handles "lumber", "number" mishearings)
        for i, text in enumerate(whisper_texts):
            if difflib.SequenceMatcher(None, text, "number").ratio() < 0.7:
                continue

            if i + 1 < len(whisper_texts):
                next_text = whisper_texts[i + 1]
                # Check digit, word form, or fuzzy word form
                if (next_text == target_number_text or
                    next_text == target_number_word or
                    (target_number_word and
                     difflib.SequenceMatcher(None, next_text, target_number_word).ratio() > 0.7)):
                    timestamp = whisper_words[i].start
                    print(f"   Segment {seg.number}: aligned at {timestamp:.2f}s "
                          f"(method=fuzzy_direct)")
                    return AlignedSegment(
                        number=seg.number,
                        title=seg.title,
                        body=seg.body,
                        start=timestamp,
                        end=0.0,
                        words=[],
                        confidence=0.5,
                        method="fuzzy_direct",
                    )

        return None

    def _sliding_window_match(
        self,
        needle: list[str],
        haystack: list[str],
    ) -> tuple[int, float]:
        """
        Slide a window over the haystack and find the best match for the needle.

        Returns: (best_start_index, best_similarity_score)
        """
        window_size = len(needle)
        best_idx = 0
        best_score = 0.0

        # Optimization: skip positions that are too far from any plausible match
        for i in range(len(haystack) - window_size + 1):
            window = haystack[i:i + window_size]
            score = difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio()
            if score > best_score:
                best_score = score
                best_idx = i

        return best_idx, best_score

    def _interpolate_unaligned(
        self,
        aligned: list[AlignedSegment],
        audio_duration: float,
    ) -> None:
        """
        Fill in timestamps for any segments that couldn't be aligned.
        Uses linear interpolation from neighboring aligned segments.
        """
        for i, seg in enumerate(aligned):
            if seg.start >= 0:
                continue

            # Find nearest aligned neighbors
            prev_time = 0.0
            next_time = audio_duration
            prev_idx = -1
            next_idx = len(aligned)

            for j in range(i - 1, -1, -1):
                if aligned[j].start >= 0:
                    prev_time = aligned[j].start
                    prev_idx = j
                    break

            for j in range(i + 1, len(aligned)):
                if aligned[j].start >= 0:
                    next_time = aligned[j].start
                    next_idx = j
                    break

            # Interpolate evenly between neighbors
            gap_count = next_idx - prev_idx
            if gap_count > 0:
                step = (next_time - prev_time) / gap_count
                seg.start = prev_time + step * (i - prev_idx)
            else:
                seg.start = prev_time

            seg.confidence = 0.1
            seg.method = "interpolated"
            print(f"   Segment {seg.number}: interpolated at {seg.start:.2f}s "
                  f"(WARNING: manual review recommended)")
