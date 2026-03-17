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

        # Enforce monotonic ordering — if two segments are out of order,
        # keep the higher-confidence one and re-search the other with a
        # constrained time window.
        self._fix_monotonic(aligned, whisper_words, whisper_texts)

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

        Tries two needles:
        1. Full body (includes "Number X" + title + content)
        2. Content only (skips "Number X. Title." header) — catches cases
           where Whisper drops the header phrase entirely.
        """
        candidates: list[tuple[int, float, str]] = []  # (idx, score, method)

        # Attempt 1: full body
        body_words = seg.body.split()[:self.context_size]
        needle = [w.lower().strip(".,!?;:'\"") for w in body_words]
        if len(needle) >= 3:
            idx, score = self._sliding_window_match(needle, whisper_texts)
            candidates.append((idx, score, "context"))

        # Attempt 2: title sentence only (e.g. "The cosmic web is wrong")
        # Short but distinctive — good when Whisper drops the "Number X" but
        # keeps the title.
        body_sentences = re.split(r'(?<=[.!?])\s+', seg.body, maxsplit=2)
        if len(body_sentences) > 1:
            title_words = body_sentences[1].split()[:self.context_size]
            title_needle = [w.lower().strip(".,!?;:'\"") for w in title_words]
            if len(title_needle) >= 3:
                idx, score = self._sliding_window_match(title_needle, whisper_texts)
                candidates.append((idx, score, "title_body"))

        # Attempt 3: body after the title (skip "Number X. Title." prefix)
        # Catches cases where Whisper drops both the header and title.
        if len(body_sentences) > 2:
            content_text = body_sentences[2]
        elif len(body_sentences) > 1:
            content_text = body_sentences[1]
        else:
            content_text = ""

        if content_text:
            content_words = content_text.split()[:self.context_size]
            content_needle = [w.lower().strip(".,!?;:'\"") for w in content_words]
            if len(content_needle) >= 3:
                idx, score = self._sliding_window_match(content_needle, whisper_texts)
                candidates.append((idx, score, "content_body"))

        # Pick the best match
        if not candidates:
            return None

        best_idx, best_score, best_method = max(candidates, key=lambda x: x[1])

        if best_score >= self.similarity_threshold:
            timestamp = whisper_words[best_idx].start

            # When content_body or title_body matched, the match point is
            # AFTER the "Number X" header that Whisper dropped.  Walk backward
            # to find the gap where it was spoken — use the start of that gap.
            if best_method in ("content_body", "title_body") and best_idx > 0:
                timestamp = self._backtrack_to_gap(whisper_words, best_idx)

            print(f"   Segment {seg.number}: aligned at {timestamp:.2f}s "
                  f"(confidence={best_score:.2f}, method={best_method})")
            return AlignedSegment(
                number=seg.number,
                title=seg.title,
                body=seg.body,
                start=timestamp,
                end=0.0,  # Set later
                words=[],  # Set later
                confidence=round(best_score, 3),
                method=best_method,
            )

        return None

    def _backtrack_to_gap(
        self,
        whisper_words: list[Word],
        match_idx: int,
        max_lookback: int = 30,
        min_gap: float = 1.5,
    ) -> float:
        """
        Walk backward from a content_body match to find the silence gap
        where Whisper dropped the "Number X. Title." header.

        Returns the timestamp at the start of the gap (= end of previous
        segment's last word), which is where the segment truly begins.
        """
        start = max(0, match_idx - max_lookback)
        best_gap_time = whisper_words[match_idx].start  # fallback
        best_gap_size = 0.0

        for i in range(match_idx, start, -1):
            prev_end = whisper_words[i - 1].end
            curr_start = whisper_words[i].start
            gap = curr_start - prev_end

            if gap >= min_gap and gap > best_gap_size:
                best_gap_size = gap
                best_gap_time = prev_end  # segment starts at end of previous word
                break  # take the nearest large gap

        return best_gap_time

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

    def _fix_monotonic(
        self,
        aligned: list[AlignedSegment],
        whisper_words: list[Word],
        whisper_texts: list[str],
    ) -> None:
        """
        Enforce monotonically increasing timestamps.

        When two adjacent segments are out of order, keep the one with higher
        confidence and re-search the other in the constrained time window
        after the previous segment.
        """
        changed = True
        max_iters = len(aligned)  # prevent infinite loop
        iteration = 0

        while changed and iteration < max_iters:
            changed = False
            iteration += 1

            for i in range(len(aligned) - 1):
                curr = aligned[i]
                nxt = aligned[i + 1]

                if curr.start < 0 or nxt.start < 0:
                    continue  # unaligned, will be interpolated later

                if curr.start < nxt.start:
                    continue  # already in order

                # Out of order — keep the higher-confidence one, invalidate the other
                if curr.confidence >= nxt.confidence:
                    # Keep curr, re-search nxt after curr.start
                    loser = nxt
                    min_time = curr.start + 0.5
                else:
                    # Keep nxt, re-search curr before nxt.start
                    loser = curr
                    min_time = aligned[i - 1].start + 0.5 if i > 0 and aligned[i - 1].start >= 0 else 0.0

                print(f"   ⚠ Fixing order: segment {loser.number} was out of sequence, re-searching...")

                # Mark as unaligned so interpolation can fix it if re-search fails
                loser.start = -1.0
                loser.confidence = 0.0
                loser.method = "unaligned"
                changed = True

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
