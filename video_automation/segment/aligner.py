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
    number_word_end: float = 0.0                 # End time of "Number X" phrase (for card duration)


def _fmt_time(seconds: float) -> str:
    """Format seconds as 'Xs (M:SS)'."""
    m, s = divmod(seconds, 60)
    return f"{seconds:.2f}s ({int(m)}:{s:05.2f})"


class ScriptAligner:
    """
    Align script segments to Whisper word timestamps.

    Priority order:
    1. Direct "Number X" match in Whisper stream (most accurate)
    2. Fuzzy context matching (fallback when Whisper drops "Number X")

    Structure comes from the script (cannot hallucinate).
    Timing comes from Whisper (good at timing, unreliable for structure).
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

        # Phase 1: Build a map of all "Number X" anchors in the Whisper stream.
        # This pre-scan avoids repeated linear searches per segment.
        number_anchors = self._find_all_number_anchors(whisper_words, whisper_texts)

        aligned = []

        for seg in script_segments:
            # Priority 1: Direct "Number X" match (exact timestamps)
            result = self._direct_number_match(
                seg, whisper_words, number_anchors,
            )
            if not result:
                # Priority 2: Fuzzy context match (Whisper dropped "Number X")
                result = self._fuzzy_context_match(
                    seg, whisper_words, whisper_texts,
                )
            if not result:
                print(f"   WARNING: Could not align segment Number {seg.number} — "
                      f"will interpolate from neighbors")
                result = AlignedSegment(
                    number=seg.number,
                    title=seg.title,
                    body=seg.body,
                    start=-1.0,  # Sentinel: needs interpolation
                    end=-1.0,
                    words=[],
                    confidence=0.0,
                    method="unaligned",
                )
            aligned.append(result)

        # Enforce monotonic ordering
        self._fix_monotonic(aligned, whisper_words, whisper_texts)

        # Fix impossibly short segments (e.g. context match landing near next segment)
        self._fix_short_segments(aligned)

        # Detect fuzzy matches that created abnormal duration distributions
        # (long segment + tiny neighbors = fuzzy match landed in wrong region)
        self._fix_duration_imbalance(aligned)

        # Re-attempt fuzzy matching for unaligned segments in constrained windows
        # (between their aligned neighbors, not the whole transcript)
        self._rescue_unaligned(aligned, whisper_words, whisper_texts, audio_duration)

        # Interpolate any remaining unaligned segments from neighbors
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

    # ── Phase 1: Direct "Number X" matching ──────────────────────────────

    def _find_all_number_anchors(
        self,
        whisper_words: list[Word],
        whisper_texts: list[str],
    ) -> dict[int, list[tuple[int, float, float]]]:
        """
        Pre-scan Whisper stream for all "Number X" patterns.

        Returns: {segment_number: [(word_idx, start_time, number_phrase_end), ...]}
        Multiple entries per number handles cases where the narrator says
        "number eight" casually later in the video.
        """
        anchors: dict[int, list[tuple[int, float, float]]] = {}

        for i, text in enumerate(whisper_texts):
            # Exact match on "number"
            is_number = (text == "number")
            # Fuzzy match (handles "lumber", "humber" mishearings)
            if not is_number:
                is_number = (
                    len(text) >= 4 and
                    difflib.SequenceMatcher(None, text, "number").ratio() >= 0.7
                )
            # Check for fused token like "number8", "numbereight"
            if not is_number and text.startswith("number"):
                suffix = text[6:]
                if suffix:
                    num_val = self._parse_number_token(suffix)
                    if num_val is not None:
                        anchors.setdefault(num_val, []).append(
                            (i, whisper_words[i].start, whisper_words[i].end)
                        )
                continue

            if not is_number:
                continue

            # Check next word for the digit/word
            if i + 1 >= len(whisper_texts):
                continue

            next_text = whisper_texts[i + 1]
            num_val = self._parse_number_token(next_text)

            if num_val is not None:
                phrase_end = whisper_words[i + 1].end
                anchors.setdefault(num_val, []).append(
                    (i, whisper_words[i].start, phrase_end)
                )

        return anchors

    def _parse_number_token(self, text: str) -> Optional[int]:
        """Parse a token as a number (digit or word form). Returns None if not a number."""
        clean = text.strip(".,!?;:'\"")
        if clean.isdigit():
            return int(clean)
        if clean in WORD_TO_DIGIT:
            return WORD_TO_DIGIT[clean]
        # Fuzzy match against number words (min length 4 to avoid "the" → "three")
        if len(clean) >= 4:
            for word, digit in WORD_TO_DIGIT.items():
                if difflib.SequenceMatcher(None, clean, word).ratio() > 0.7:
                    return digit
        return None

    def _direct_number_match(
        self,
        seg: ScriptSegment,
        whisper_words: list[Word],
        number_anchors: dict[int, list[tuple[int, float, float]]],
    ) -> Optional[AlignedSegment]:
        """
        Match a segment using pre-scanned "Number X" anchors.

        When multiple anchors exist for the same number, picks the one
        that makes chronological sense (not a casual mid-sentence mention).
        """
        candidates = number_anchors.get(seg.number, [])
        if not candidates:
            return None

        if len(candidates) == 1:
            idx, start, phrase_end = candidates[0]
        else:
            # Multiple matches — filter out casual mentions by checking if
            # the word before the anchor ends a sentence (gap or punctuation)
            best = None
            best_score = -1.0

            for idx, start, phrase_end in candidates:
                score = 0.0
                # Bonus: preceded by sentence-ending punctuation or silence gap
                if idx > 0:
                    prev_word = whisper_words[idx - 1]
                    gap = start - prev_word.end
                    if gap >= 0.3:
                        score += 2.0  # Silence before = likely a real segment start
                    if prev_word.text.rstrip().endswith((".", "!", "?")):
                        score += 1.0
                else:
                    score += 2.0  # First word in stream = definitely real

                if score > best_score:
                    best_score = score
                    best = (idx, start, phrase_end)

            idx, start, phrase_end = best

        print(f"   Segment {seg.number}: aligned at {_fmt_time(start)} "
              f"(confidence=0.95, method=direct_match)")
        return AlignedSegment(
            number=seg.number,
            title=seg.title,
            body=seg.body,
            start=start,
            end=0.0,
            words=[],
            confidence=0.95,
            method="direct_match",
            number_word_end=phrase_end,
        )

    # ── Phase 2: Fuzzy context matching (fallback) ───────────────────────

    def _fuzzy_context_match(
        self,
        seg: ScriptSegment,
        whisper_words: list[Word],
        whisper_texts: list[str],
    ) -> Optional[AlignedSegment]:
        """
        Find a segment's timestamp using fuzzy context matching.

        Fallback for when Whisper dropped "Number X" entirely.
        Tries three needles and picks the best, then backtracks to find
        the true segment boundary.
        """
        candidates: list[tuple[int, float, str]] = []  # (idx, score, method)

        # Attempt 1: full body, but strip "Number X." prefix since Whisper dropped it
        body_words = seg.body.split()[:self.context_size + 3]
        clean_body = [w.lower().strip(".,!?;:'\"") for w in body_words]
        # Skip leading "number X" if present (we're here because Whisper missed it)
        skip = 0
        if len(clean_body) >= 2 and clean_body[0] == "number":
            skip = 2  # skip "number" and the digit/word
        needle = clean_body[skip:skip + self.context_size]
        if len(needle) >= 3:
            idx, score = self._sliding_window_match(needle, whisper_texts)
            candidates.append((idx, score, "context"))

        # Attempt 2: title sentence only
        body_sentences = re.split(r'(?<=[.!?])\s+', seg.body, maxsplit=2)
        if len(body_sentences) > 1:
            title_words = body_sentences[1].split()[:self.context_size]
            title_needle = [w.lower().strip(".,!?;:'\"") for w in title_words]
            if len(title_needle) >= 3:
                idx, score = self._sliding_window_match(title_needle, whisper_texts)
                candidates.append((idx, score, "title_body"))

        # Attempt 3: content after the title
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

        if not candidates:
            return None

        best_idx, best_score, best_method = max(candidates, key=lambda x: x[1])

        if best_score < self.similarity_threshold:
            return None

        timestamp = whisper_words[best_idx].start
        number_word_end = 0.0

        # Walk backward to find the true segment boundary
        if best_idx > 0:
            timestamp, number_word_end = self._backtrack_to_boundary(
                whisper_words, whisper_texts, best_idx, seg.number,
            )

        print(f"   Segment {seg.number}: aligned at {_fmt_time(timestamp)} "
              f"(confidence={best_score:.2f}, method={best_method})")
        return AlignedSegment(
            number=seg.number,
            title=seg.title,
            body=seg.body,
            start=timestamp,
            end=0.0,
            words=[],
            confidence=round(best_score, 3),
            method=best_method,
            number_word_end=number_word_end,
        )

    def _backtrack_to_boundary(
        self,
        whisper_words: list[Word],
        whisper_texts: list[str],
        match_idx: int,
        expected_number: int,
        max_lookback: int = 15,
        max_lookahead: int = 10,
    ) -> tuple[float, float]:
        """
        Search around a fuzzy match to find the true segment boundary.

        Searches both backward and forward from the match point, because
        the sliding window can land before the actual boundary when the
        needle includes words Whisper dropped.

        Uses two signals (checked in priority order):
        1. "Number X" words — if found, use their timestamp directly
        2. Sentence boundary + silence gap — scored by gap size, sentence
           ending bonus, and proximity to the match point

        Returns: (segment_start, number_phrase_end)
        number_phrase_end is 0.0 if "Number X" was not found during search.
        """
        search_start = max(0, match_idx - max_lookback)
        search_end = min(len(whisper_words), match_idx + max_lookahead)
        target_text = str(expected_number)
        target_word = DIGIT_TO_WORD.get(expected_number, "").lower()

        # Signal 1: Look for "Number X" words in search range
        for i in range(match_idx, search_start, -1):
            text = whisper_texts[i]
            is_number = (text == "number") or (
                len(text) >= 4 and
                difflib.SequenceMatcher(None, text, "number").ratio() >= 0.7
            )
            if is_number and i + 1 < len(whisper_texts):
                next_text = whisper_texts[i + 1]
                clean_next = next_text.strip(".,!?;:'\"")
                if (clean_next == target_text or clean_next == target_word or
                    (target_word and
                     difflib.SequenceMatcher(None, clean_next, target_word).ratio() > 0.7)):
                    # Found "Number X" — use its start time
                    return whisper_words[i].start, whisper_words[i + 1].end

        # Signal 2: Find the best gap in both directions from match point.
        # Score = gap_size + sentence_bonus - distance_penalty
        best_boundary = whisper_words[match_idx].start  # fallback
        best_score = 0.0

        for i in range(search_start + 1, search_end):
            prev_word = whisper_words[i - 1]
            curr_word = whisper_words[i]
            gap = curr_word.start - prev_word.end

            if gap < 0.15:
                continue  # Too small to be a boundary

            # Score: larger gaps + sentence-enders score higher
            score = gap
            if prev_word.text.rstrip().endswith((".", "!", "?")):
                score += 0.5  # Sentence boundary bonus

            # Penalize boundaries far from the match point so we prefer
            # the nearest strong boundary over a distant one
            distance = abs(i - match_idx)
            score -= distance * 0.03

            if score > best_score:
                best_score = score
                best_boundary = prev_word.end

        return best_boundary, 0.0

    def _sliding_window_match(
        self,
        needle: list[str],
        haystack: list[str],
        start_idx: int = 0,
        end_idx: Optional[int] = None,
    ) -> tuple[int, float]:
        """
        Slide a window over the haystack and find the best match for the needle.

        Args:
            start_idx: Start searching from this index (inclusive).
            end_idx: Stop searching at this index (exclusive). None = end of haystack.

        Returns: (best_start_index, best_similarity_score)
        """
        if end_idx is None:
            end_idx = len(haystack)
        window_size = len(needle)
        best_idx = start_idx
        best_score = 0.0

        for i in range(start_idx, min(end_idx, len(haystack)) - window_size + 1):
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

    def _fix_short_segments(
        self,
        aligned: list[AlignedSegment],
        min_duration: float = 10.0,
    ) -> None:
        """
        Detect segments that would be impossibly short and mark them for
        interpolation.  A context-matched segment landing right next to
        a direct-matched neighbor is almost certainly wrong.
        """
        for i in range(len(aligned) - 1):
            curr = aligned[i]
            nxt = aligned[i + 1]

            if curr.start < 0 or nxt.start < 0:
                continue  # already marked for interpolation

            duration = nxt.start - curr.start
            if duration >= min_duration:
                continue

            # Pick the lower-confidence segment to invalidate.
            # direct_match (found the literal "Number X" phrase) always wins
            # over a fuzzy context match, regardless of confidence score.
            curr_is_direct = curr.method == "direct_match"
            nxt_is_direct = nxt.method == "direct_match"
            if curr_is_direct and not nxt_is_direct:
                loser = nxt
            elif nxt_is_direct and not curr_is_direct:
                loser = curr
            elif curr.confidence <= nxt.confidence:
                loser = curr
            else:
                loser = nxt

            print(f"   ⚠ Segment {loser.number}: duration too short "
                  f"({duration:.1f}s), will interpolate")
            loser.start = -1.0
            loser.confidence = 0.0
            loser.method = "unaligned"

    def _fix_duration_imbalance(
        self,
        aligned: list[AlignedSegment],
    ) -> None:
        """
        Detect abnormally long segments adjacent to short fuzzy-matched neighbors.

        When a fuzzy match lands in the wrong region, it creates a pattern:
        one segment balloons (because the real boundary is inside it) while
        the misplaced fuzzy match and its neighbors get squeezed into a tiny
        gap. This method detects that pattern and invalidates the suspicious
        fuzzy match so _rescue_unaligned can re-search in the correct window.
        """
        # Build list of consecutive aligned-segment pairs
        aligned_indices = [i for i, s in enumerate(aligned) if s.start >= 0]
        if len(aligned_indices) < 3:
            return

        durations = []
        for k in range(len(aligned_indices) - 1):
            i, j = aligned_indices[k], aligned_indices[k + 1]
            dur = aligned[j].start - aligned[i].start
            if dur > 0:
                durations.append(dur)

        if len(durations) < 3:
            return

        median_dur = sorted(durations)[len(durations) // 2]
        if median_dur <= 0:
            return

        # Re-scan with the median to find imbalances
        for k in range(len(aligned_indices) - 1):
            i, j = aligned_indices[k], aligned_indices[k + 1]
            dur = aligned[j].start - aligned[i].start

            if dur <= median_dur * 2.0:
                continue

            # Long gap between aligned[i] and aligned[j].
            # If aligned[j] is fuzzy-matched, check the gap AFTER it.
            seg_j = aligned[j]
            if seg_j.method == "direct_match":
                continue

            # Find the next aligned segment after j
            if k + 2 > len(aligned_indices) - 1:
                continue
            next_j = aligned_indices[k + 2]
            gap_after = aligned[next_j].start - seg_j.start
            # How many segments (including unaligned) share the gap after seg_j
            segs_in_gap = next_j - j
            avg_per_seg = gap_after / segs_in_gap if segs_in_gap > 0 else gap_after

            if avg_per_seg < median_dur * 0.35:
                print(f"   ⚠ Segment {seg_j.number}: likely misaligned "
                      f"({dur:.0f}s gap before, {gap_after:.0f}s for {segs_in_gap} "
                      f"segment(s) after, median={median_dur:.0f}s), will re-search")
                seg_j.start = -1.0
                seg_j.confidence = 0.0
                seg_j.method = "unaligned"

    def _rescue_unaligned(
        self,
        aligned: list[AlignedSegment],
        whisper_words: list[Word],
        whisper_texts: list[str],
        audio_duration: float,
    ) -> None:
        """
        Re-attempt fuzzy matching for unaligned segments, constrained to the
        time window between their aligned neighbors.

        This catches segments that initially fuzzy-matched to the wrong region
        but have a valid match in their expected position. Processes in order
        so that rescued segments narrow the window for subsequent ones.
        """
        end_time = audio_duration if audio_duration > 0 else whisper_words[-1].end

        for i, seg in enumerate(aligned):
            if seg.start >= 0:
                continue

            # Find time bounds from nearest aligned neighbors
            min_time = 0.0
            max_time = end_time

            for j in range(i - 1, -1, -1):
                if aligned[j].start >= 0:
                    min_time = aligned[j].start
                    break

            for j in range(i + 1, len(aligned)):
                if aligned[j].start >= 0:
                    max_time = aligned[j].start
                    break

            # Convert time bounds to word indices
            min_idx = 0
            max_idx = len(whisper_words)
            for wi, w in enumerate(whisper_words):
                if w.start >= min_time:
                    min_idx = wi
                    break
            for wi in range(len(whisper_words) - 1, -1, -1):
                if whisper_words[wi].start <= max_time:
                    max_idx = wi + 1
                    break

            if max_idx - min_idx < 10:
                continue

            # Try constrained fuzzy match
            result = self._bounded_fuzzy_match(
                seg, whisper_words, whisper_texts, min_idx, max_idx,
            )
            if result:
                print(f"   Segment {seg.number}: rescued at {_fmt_time(result.start)} "
                      f"(confidence={result.confidence:.2f}, method=rescued_{result.method})")
                seg.start = result.start
                seg.confidence = result.confidence
                seg.method = f"rescued_{result.method}"
                seg.number_word_end = result.number_word_end

    def _bounded_fuzzy_match(
        self,
        seg: AlignedSegment,
        whisper_words: list[Word],
        whisper_texts: list[str],
        min_idx: int,
        max_idx: int,
    ) -> Optional[AlignedSegment]:
        """
        Like _fuzzy_context_match but constrained to [min_idx, max_idx].
        """
        candidates: list[tuple[int, float, str]] = []

        # Needle 1: body after "Number X" prefix
        body_words = seg.body.split()[:self.context_size + 3]
        clean_body = [w.lower().strip(".,!?;:'\"") for w in body_words]
        skip = 0
        if len(clean_body) >= 2 and clean_body[0] == "number":
            skip = 2
        needle = clean_body[skip:skip + self.context_size]
        if len(needle) >= 3:
            idx, score = self._sliding_window_match(
                needle, whisper_texts, min_idx, max_idx,
            )
            candidates.append((idx, score, "context"))

        # Needle 2: title sentence
        body_sentences = re.split(r'(?<=[.!?])\s+', seg.body, maxsplit=2)
        if len(body_sentences) > 1:
            title_words = body_sentences[1].split()[:self.context_size]
            title_needle = [w.lower().strip(".,!?;:'\"") for w in title_words]
            if len(title_needle) >= 3:
                idx, score = self._sliding_window_match(
                    title_needle, whisper_texts, min_idx, max_idx,
                )
                candidates.append((idx, score, "title_body"))

        # Needle 3: content after title
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
                idx, score = self._sliding_window_match(
                    content_needle, whisper_texts, min_idx, max_idx,
                )
                candidates.append((idx, score, "content_body"))

        if not candidates:
            return None

        best_idx, best_score, best_method = max(candidates, key=lambda x: x[1])

        if best_score < self.similarity_threshold:
            return None

        timestamp = whisper_words[best_idx].start
        number_word_end = 0.0

        if best_idx > 0:
            timestamp, number_word_end = self._backtrack_to_boundary(
                whisper_words, whisper_texts, best_idx, seg.number,
            )

        return AlignedSegment(
            number=seg.number,
            title=seg.title,
            body=seg.body,
            start=timestamp,
            end=0.0,
            words=[],
            confidence=round(best_score, 3),
            method=best_method,
            number_word_end=number_word_end,
        )

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
            print(f"   Segment {seg.number}: interpolated at {_fmt_time(seg.start)} "
                  f"(WARNING: manual review recommended)")
