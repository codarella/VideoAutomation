"""
Parse the original written script for "Number X" segment structure.

This is Layer 1 of the ironclad detection system — zero hallucination risk
because it reads the source text, not Whisper output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Number word ↔ digit mapping ───────────────────────────────────────────

WORD_TO_DIGIT: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

DIGIT_TO_WORD: dict[int, str] = {v: k for k, v in WORD_TO_DIGIT.items()}

# Build regex that matches segment headers like "Number 9." or "Number Nine."
# Two requirements to distinguish real headers from casual mentions:
#   1. Must be at the start of a line (or start of text)
#   2. Must be followed by a period or colon after the number
# This filters out mid-sentence mentions like "responsible for number four."
_number_words = "|".join(WORD_TO_DIGIT.keys())
NUMBER_PATTERN = re.compile(
    r'(?:^|\n)\s*[Nn]umber\s+(' + _number_words + r'|\d+)\s*[.:]',
    re.IGNORECASE,
)


@dataclass
class ScriptSegment:
    """A segment extracted from the original script."""
    number: int                                  # e.g. 10, 9, 8...
    title: str                                   # Text immediately after "Number X" up to first period/newline
    body: str                                    # Full text from "Number X" to next "Number Y"
    char_offset: int                             # Character offset of "Number X" in the script
    context_before: str                          # ~50 words before this "Number X"
    context_after: str                           # ~50 words after this "Number X"
    number_phrase: str                           # The exact matched phrase, e.g. "Number 10" or "Number ten"


class ScriptParser:
    """
    Parse the original written script for "Number X" structure.

    This is ground truth — the script cannot hallucinate.
    Handles both digit ("Number 9") and word ("Number Nine") forms.
    """

    def parse(self, script_text: str) -> list[ScriptSegment]:
        """
        Find all "Number X" occurrences in the script.

        Returns ordered list of ScriptSegment objects.
        """
        matches = list(NUMBER_PATTERN.finditer(script_text))

        if not matches:
            print("   WARNING: No 'Number X' patterns found in script!")
            return []

        segments = []
        script_words = script_text.split()

        for idx, match in enumerate(matches):
            # Parse the number (word or digit)
            raw = match.group(1).lower()
            if raw in WORD_TO_DIGIT:
                num_val = WORD_TO_DIGIT[raw]
            elif raw.isdigit():
                num_val = int(raw)
            else:
                continue

            # The regex may include a leading newline/whitespace — find actual "Number" start
            full = match.group(0)
            n_pos = full.lower().index("number")
            real_start = match.start() + n_pos

            # Extract the body text (from this match to the next, or end)
            body_start = real_start
            if idx + 1 < len(matches):
                next_full = matches[idx + 1].group(0)
                next_n_pos = next_full.lower().index("number")
                body_end = matches[idx + 1].start() + next_n_pos
            else:
                body_end = len(script_text)
            body = script_text[body_start:body_end].strip()

            # Extract title: text after "Number X." up to first sentence-ending punctuation or newline
            after_number = script_text[match.end():body_end].strip()
            title_match = re.match(r'^[.:]?\s*(.+?)(?:[.!?\n]|$)', after_number)
            title = title_match.group(1).strip() if title_match else after_number[:80]

            # Context windows for fuzzy alignment
            context_before = self._get_context(script_text, real_start, direction="before", num_words=50)
            context_after = self._get_context(script_text, match.end(), direction="after", num_words=50)

            # Build the clean number phrase (without leading newline/whitespace)
            number_phrase = full[n_pos:].rstrip(".:")

            segments.append(ScriptSegment(
                number=num_val,
                title=title,
                body=body,
                char_offset=real_start,
                context_before=context_before,
                context_after=context_after,
                number_phrase=number_phrase,
            ))

        print(f"   Found {len(segments)} segments in script: "
              f"{[s.number for s in segments]}")

        return segments

    def _get_context(self, text: str, offset: int, direction: str, num_words: int) -> str:
        """Extract N words before or after a character offset."""
        if direction == "before":
            chunk = text[:offset]
            words = chunk.split()
            return " ".join(words[-num_words:])
        else:
            chunk = text[offset:]
            words = chunk.split()
            return " ".join(words[:num_words])

    def validate_against_expected(
        self,
        segments: list[ScriptSegment],
        expected_count: int,
        direction: str = "descending",
    ) -> list[str]:
        """
        Validate parsed segments against expected count and order.
        Returns list of error messages (empty = valid).
        """
        errors = []

        if len(segments) != expected_count:
            errors.append(
                f"Expected {expected_count} segments but found {len(segments)}: "
                f"{[s.number for s in segments]}"
            )

        if direction == "descending":
            expected_order = list(range(expected_count, 0, -1))
        else:
            expected_order = list(range(1, expected_count + 1))

        found_order = [s.number for s in segments]
        if found_order != expected_order:
            errors.append(
                f"Expected order {expected_order} but found {found_order}"
            )

        # Check for duplicate numbers
        seen = set()
        for s in segments:
            if s.number in seen:
                errors.append(f"Duplicate segment number: {s.number}")
            seen.add(s.number)

        return errors


def get_intro_text(script_text: str, segments: list[ScriptSegment]) -> str:
    """Extract the intro text before the first 'Number X'."""
    if not segments:
        return script_text.strip()
    first_offset = segments[0].char_offset
    return script_text[:first_offset].strip()
