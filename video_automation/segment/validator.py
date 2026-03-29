"""
Cross-validate segment detection results.

Checks alignment quality, segment ordering, timing consistency,
and flags issues for human review.
"""

from __future__ import annotations

from video_automation.models import Project, Scene
from video_automation.segment.aligner import AlignedSegment


class SegmentValidator:
    """Validate aligned segments and final scene list."""

    def validate_alignment(
        self,
        aligned: list[AlignedSegment],
        expected_count: int,
        direction: str = "descending",
    ) -> list[str]:
        """Validate the alignment results before scene splitting."""
        errors = []
        warnings = []

        # Count check
        if len(aligned) != expected_count:
            errors.append(
                f"Expected {expected_count} segments, got {len(aligned)}"
            )

        # Order check
        if direction == "descending":
            expected = list(range(expected_count, 0, -1))
        else:
            expected = list(range(1, expected_count + 1))

        found = [s.number for s in aligned]
        if found != expected[:len(found)]:
            errors.append(f"Segment order mismatch: expected {expected}, got {found}")

        # Monotonic timestamps
        for i in range(1, len(aligned)):
            if aligned[i].start <= aligned[i - 1].start:
                errors.append(
                    f"Segment {aligned[i].number} starts at {aligned[i].start:.2f}s "
                    f"but segment {aligned[i-1].number} starts at {aligned[i-1].start:.2f}s "
                    f"(not monotonically increasing)"
                )

        # Reasonable segment durations
        for seg in aligned:
            dur = seg.end - seg.start
            if dur < 5.0:
                warnings.append(
                    f"Segment {seg.number}: very short duration ({dur:.1f}s)"
                )
            if dur > 600.0:
                warnings.append(
                    f"Segment {seg.number}: very long duration ({dur:.1f}s)"
                )

        # Low confidence alignments
        for seg in aligned:
            if seg.confidence < 0.5:
                warnings.append(
                    f"Segment {seg.number}: low confidence alignment "
                    f"({seg.confidence:.2f}, method={seg.method})"
                )

        # Print summary
        if errors:
            print(f"   ALIGNMENT ERRORS ({len(errors)}):")
            for e in errors:
                print(f"      ✗ {e}")
        if warnings:
            print(f"   ALIGNMENT WARNINGS ({len(warnings)}):")
            for w in warnings:
                print(f"      ⚠ {w}")
        if not errors and not warnings:
            print(f"   Alignment validated: {len(aligned)} segments OK")

        return errors

    def validate_scenes(self, scenes: list[Scene], audio_duration: float) -> list[str]:
        """Validate the final scene list for timeline integrity."""
        errors = []

        if not scenes:
            return ["No scenes to validate"]

        for i, scene in enumerate(scenes):
            # Duration check
            if scene.end <= scene.start:
                errors.append(f"Scene {scene.id}: end <= start")
            if scene.duration < 0.01:
                errors.append(f"Scene {scene.id}: too short ({scene.duration:.4f}s)")

            # Gapless check
            if i > 0:
                gap = abs(scene.start - scenes[i - 1].end)
                if gap > 0.001:
                    errors.append(
                        f"Gap of {gap:.4f}s between {scenes[i-1].id} and {scene.id}"
                    )

        # Audio coverage check
        if audio_duration > 0:
            diff = abs(scenes[-1].end - audio_duration)
            if diff > 0.05:
                errors.append(
                    f"Last scene ends at {scenes[-1].end:.3f}s "
                    f"but audio is {audio_duration:.3f}s (diff={diff:.3f}s)"
                )

        if errors:
            print(f"   SCENE ERRORS ({len(errors)}):")
            for e in errors:
                print(f"      ✗ {e}")
        else:
            print(f"   Scene validation passed: {len(scenes)} scenes, "
                  f"{scenes[-1].end:.1f}s total")

        return errors


def print_alignment_report(aligned: list[AlignedSegment]) -> None:
    """Print a human-readable alignment report."""
    print("\n   --- Alignment Report ---")
    for seg in aligned:
        dur = seg.end - seg.start
        conf_bar = "#" * int(seg.confidence * 10) + "." * (10 - int(seg.confidence * 10))
        s_min, s_sec = int(seg.start // 60), seg.start % 60
        e_min, e_sec = int(seg.end // 60), seg.end % 60
        d_min, d_sec = int(dur // 60), dur % 60
        print(
            f"   #{seg.number:2d}  {s_min}:{s_sec:05.2f} - {e_min}:{e_sec:05.2f}  "
            f"({d_min}:{d_sec:04.1f})  [{conf_bar}]  {seg.method:12s}  {seg.title[:40]}"
        )
    print()
