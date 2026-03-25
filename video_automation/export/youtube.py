"""
YouTube chapter export — generates chapter timestamps from project segments.

YouTube auto-detects chapters when the description contains lines like:
    0:00 Introduction
    0:38 Number 10: The Higgs Boson
    2:15 Number 9: Dark Matter

Requirements: first chapter must start at 0:00, minimum 3 chapters,
each at least 10 seconds long.
"""

from __future__ import annotations

from pathlib import Path

from video_automation.models import Project


def export_chapters(project: Project, workspace: Path) -> str:
    """Generate YouTube chapter text from project segments.

    Returns the chapter text and saves it to workspace/videos/{name}_chapters.txt.
    """
    if not project.aligned_segments:
        raise ValueError("No aligned segments in project — run the segment stage first")

    lines: list[str] = []

    # Intro chapter (YouTube requires first chapter at 0:00)
    first_seg = project.aligned_segments[0]
    if first_seg.start > 1.0:
        lines.append("0:00 Introduction")

    # Each numbered segment becomes a chapter
    for seg in project.aligned_segments:
        ts = _format_timestamp(seg.start)
        lines.append(f"{ts} Number {seg.number}: {seg.title}")

    text = "\n".join(lines)

    # Save to workspace
    out_dir = workspace / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{project.name}_chapters.txt"
    out_path.write_text(text, encoding="utf-8")

    return text


def _format_timestamp(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS for YouTube."""
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
