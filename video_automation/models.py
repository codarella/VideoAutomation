"""
Single source of truth data models for the VideoAutomation pipeline.

All pipeline stages read and write through the Project model.
One unified JSON file replaces the old word_timestamps + timeline + prompts trio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional


@dataclass
class Word:
    """A single transcribed word with timing information."""
    text: str
    start: float
    end: float
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class AlignedSegmentData:
    """Serializable snapshot of an aligned segment for use in the PROMPT stage."""
    number: int
    title: str
    body: str
    start: float
    end: float
    word_indices: tuple[int, int]          # (start_idx, end_idx) into project.words
    number_word_end: float = 0.0


@dataclass
class Scene:
    """
    A single visual scene in the video timeline.

    Scenes are ordered, gapless, and cover the full audio duration.
    Each scene carries its own words, prompt, and image path.
    The status field enables partial re-runs.
    """
    id: str                                                          # e.g. "seg06_scene03"
    type: Literal["intro", "number_card", "content"]
    start: float
    end: float
    text: str                                                        # Narration text for this scene
    words: list[Word] = field(default_factory=list)                  # Word-level timestamps
    prompt: Optional[str] = None                                     # Image generation prompt
    prompt_source: Optional[str] = None                              # "claude-api", "ollama", "template"
    image_path: Optional[str] = None                                 # Relative path to generated image
    status: Literal["planned", "prompted", "generated", "failed"] = "planned"
    include_character: bool = False
    metadata: dict = field(default_factory=dict)                     # segment_number, segment_title, etc.

    @property
    def duration(self) -> float:
        return self.end - self.start

    def needs_prompt(self) -> bool:
        return self.status == "planned" and self.type != "intro"

    def needs_image(self) -> bool:
        return self.status in ("prompted", "failed")

    def is_done(self) -> bool:
        return self.status == "generated"


@dataclass
class Project:
    """
    The unified project model — single source of truth.

    Replaces the old trio of word_timestamps.json + timeline.json + prompts.json.
    Whisper transcribes into project.words, segment detection populates project.scenes.
    """
    name: str
    audio_path: str                                                  # Relative to workspace
    script_path: str                                                 # Original narration script
    expected_count: int                                               # e.g. 10 for a "top 10" video
    counting_direction: Literal["descending", "ascending"] = "descending"
    words: list[Word] = field(default_factory=list)                  # Full word-level transcript
    scenes: list[Scene] = field(default_factory=list)                # THE timeline
    aligned_segments: list[AlignedSegmentData] = field(default_factory=list)  # For Claude splitting
    style: str = "2d_western_cartoon"
    audio_duration: float = 0.0                                      # Total audio length in seconds
    config: dict = field(default_factory=dict)                       # Pipeline settings

    # ── Query helpers ─────────────────────────────────────────────

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    def scenes_by_status(self, status: str) -> list[Scene]:
        return [s for s in self.scenes if s.status == status]

    def scenes_by_type(self, scene_type: str) -> list[Scene]:
        return [s for s in self.scenes if s.type == scene_type]

    def content_scenes(self) -> list[Scene]:
        return [s for s in self.scenes if s.type == "content"]

    def number_card_scenes(self) -> list[Scene]:
        return [s for s in self.scenes if s.type == "number_card"]

    # ── Validation ────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Validate timeline integrity. Returns list of error messages (empty = valid)."""
        errors = []

        if not self.scenes:
            errors.append("No scenes defined")
            return errors

        for i, scene in enumerate(self.scenes):
            if scene.end <= scene.start:
                errors.append(f"Scene {scene.id}: end ({scene.end:.3f}) <= start ({scene.start:.3f})")

            if scene.duration < 0.01:
                errors.append(f"Scene {scene.id}: duration too short ({scene.duration:.4f}s)")

            if i > 0:
                prev = self.scenes[i - 1]
                gap = abs(scene.start - prev.end)
                if gap > 0.001:
                    errors.append(
                        f"Gap of {gap:.4f}s between {prev.id} (end={prev.end:.3f}) "
                        f"and {scene.id} (start={scene.start:.3f})"
                    )

        if self.audio_duration > 0:
            last_end = self.scenes[-1].end
            diff = abs(last_end - self.audio_duration)
            if diff > 0.05:
                errors.append(
                    f"Last scene ends at {last_end:.3f}s but audio is {self.audio_duration:.3f}s "
                    f"(diff={diff:.3f}s)"
                )

        return errors

    def validate_segment_count(self) -> list[str]:
        """Validate that we found the expected number of segments."""
        errors = []
        cards = self.number_card_scenes()
        if len(cards) != self.expected_count:
            errors.append(
                f"Expected {self.expected_count} segments but found {len(cards)} number cards"
            )

        if self.counting_direction == "descending":
            expected_numbers = list(range(self.expected_count, 0, -1))
        else:
            expected_numbers = list(range(1, self.expected_count + 1))

        found_numbers = [s.metadata.get("segment_number") for s in cards]
        if found_numbers != expected_numbers:
            errors.append(
                f"Expected segment order {expected_numbers} but found {found_numbers}"
            )

        return errors

    # ── Serialization ─────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Save project to a single JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "name": self.name,
            "audio_path": self.audio_path,
            "script_path": self.script_path,
            "expected_count": self.expected_count,
            "counting_direction": self.counting_direction,
            "style": self.style,
            "audio_duration": self.audio_duration,
            "config": self.config,
            "words": [_word_to_dict(w) for w in self.words],
            "scenes": [_scene_to_dict(s) for s in self.scenes],
            "aligned_segments": [_aligned_segment_to_dict(a) for a in self.aligned_segments],
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path) -> Project:
        """Load project from a JSON file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        words = [_dict_to_word(w) for w in data.get("words", [])]
        scenes = [_dict_to_scene(s) for s in data.get("scenes", [])]
        aligned_segments = [
            _dict_to_aligned_segment(a) for a in data.get("aligned_segments", [])
        ]

        return cls(
            name=data["name"],
            audio_path=data["audio_path"],
            script_path=data["script_path"],
            expected_count=data["expected_count"],
            counting_direction=data.get("counting_direction", "descending"),
            words=words,
            scenes=scenes,
            aligned_segments=aligned_segments,
            style=data.get("style", "2d_western_cartoon"),
            audio_duration=data.get("audio_duration", 0.0),
            config=data.get("config", {}),
        )

    def __repr__(self) -> str:
        valid = "valid" if not self.validate() else f"{len(self.validate())} errors"
        status_counts = {}
        for s in self.scenes:
            status_counts[s.status] = status_counts.get(s.status, 0) + 1
        return (
            f"Project('{self.name}', {self.scene_count} scenes, "
            f"{self.audio_duration:.1f}s, {status_counts}, {valid})"
        )


# ── Serialization helpers ─────────────────────────────────────────

def _word_to_dict(w: Word) -> dict:
    return {"text": w.text, "start": w.start, "end": w.end, "confidence": w.confidence}


def _dict_to_word(d: dict) -> Word:
    return Word(
        text=d["text"],
        start=d["start"],
        end=d["end"],
        confidence=d.get("confidence", 1.0),
    )


def _scene_to_dict(s: Scene) -> dict:
    return {
        "id": s.id,
        "type": s.type,
        "start": s.start,
        "end": s.end,
        "text": s.text,
        "words": [_word_to_dict(w) for w in s.words],
        "prompt": s.prompt,
        "prompt_source": s.prompt_source,
        "image_path": s.image_path,
        "status": s.status,
        "include_character": s.include_character,
        "metadata": s.metadata,
    }


def _dict_to_scene(d: dict) -> Scene:
    return Scene(
        id=d["id"],
        type=d["type"],
        start=d["start"],
        end=d["end"],
        text=d.get("text", ""),
        words=[_dict_to_word(w) for w in d.get("words", [])],
        prompt=d.get("prompt"),
        prompt_source=d.get("prompt_source"),
        image_path=d.get("image_path"),
        status=d.get("status", "planned"),
        include_character=d.get("include_character", False),
        metadata=d.get("metadata", {}),
    )


def _aligned_segment_to_dict(a: AlignedSegmentData) -> dict:
    return {
        "number": a.number,
        "title": a.title,
        "body": a.body,
        "start": a.start,
        "end": a.end,
        "word_indices": list(a.word_indices),
        "number_word_end": a.number_word_end,
    }


def _dict_to_aligned_segment(d: dict) -> AlignedSegmentData:
    return AlignedSegmentData(
        number=d["number"],
        title=d["title"],
        body=d["body"],
        start=d["start"],
        end=d["end"],
        word_indices=tuple(d["word_indices"]),
        number_word_end=d.get("number_word_end", 0.0),
    )
