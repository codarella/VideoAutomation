"""
Pipeline orchestrator with composable stages.

Replaces the old boolean mode flags (compile_only, prompts_only, etc.)
with start_from / stop_after + scene.status for partial re-runs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from video_automation.config import Config
from video_automation.models import Project


# ── Stage interface ───────────────────────────────────────────────────────

class Stage(ABC):
    """Base class for pipeline stages."""

    name: str = ""

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def execute(self, project: Project, workspace: Path) -> None:
        """Mutate project in-place. Raise on fatal error."""
        ...

    def should_skip(self, project: Project) -> bool:
        """Return True if this stage has nothing to do."""
        return False


# ── Stage implementations (lazy imports to avoid circular deps) ───────────

class TranscribeStage(Stage):
    name = "transcribe"

    def execute(self, project: Project, workspace: Path) -> None:
        # Skip if words already loaded
        if project.words:
            print(f"   Transcribe: {len(project.words)} words already loaded, skipping.")
            return

        audio_path = workspace / project.audio_path
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")

        from video_automation.transcribe.whisper import MultiPassTranscriber

        transcriber = MultiPassTranscriber(
            model_size=self.config.whisper_model,
            device=self.config.whisper_device,
            compute_type=self.config.whisper_compute_type,
        )
        words, duration = transcriber.transcribe(
            audio_path,
            num_passes=self.config.whisper_passes,
        )
        project.words = words
        project.audio_duration = duration

        # Early checkpoint so transcription survives later crashes
        project_path = workspace / "scripts" / f"{project.name}_project.json"
        project.save(project_path)
        print(f"   Saved early checkpoint: {project_path.name}")

    def should_skip(self, project: Project) -> bool:
        return len(project.words) > 0


class SegmentStage(Stage):
    name = "segment"

    def execute(self, project: Project, workspace: Path) -> None:
        if not project.words:
            # Try to auto-load an existing transcript file
            self._try_load_transcript(project, workspace)

        if not project.words:
            raise ValueError("No words in project — run transcribe stage first")

        # Get audio duration if not set
        if not project.audio_duration and project.words:
            project.audio_duration = project.words[-1].end

        script_path = workspace / project.script_path
        if not script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")

        from video_automation.segment.script_parser import ScriptParser
        from video_automation.segment.aligner import ScriptAligner
        from video_automation.segment.scene_splitter import SceneSplitter
        from video_automation.segment.validator import SegmentValidator, print_alignment_report

        # Layer 1: Parse script for structure
        print("\n   ── Script Parsing ──")
        parser = ScriptParser()
        script_text = script_path.read_text(encoding="utf-8")
        script_segments = parser.parse(script_text)

        parse_errors = parser.validate_against_expected(
            script_segments,
            project.expected_count,
            project.counting_direction,
        )
        if parse_errors:
            for e in parse_errors:
                print(f"   WARNING: {e}")

        # Layer 3: Align script structure to Whisper timestamps
        print("\n   ── Script-Audio Alignment ──")
        aligner = ScriptAligner()
        aligned = aligner.align(
            script_segments,
            project.words,
            project.audio_duration,
        )

        # Validate alignment
        validator = SegmentValidator()
        align_errors = validator.validate_alignment(
            aligned,
            project.expected_count,
            project.counting_direction,
        )
        print_alignment_report(aligned)

        if not aligned:
            raise RuntimeError(
                "Alignment produced 0 segments. Check that the script contains "
                "'Number X' markers and that the audio matches the script."
            )

        if align_errors:
            print("   CRITICAL: Alignment errors detected. Proceeding with best effort.")

        # Split into scenes
        print("\n   ── Scene Splitting ──")
        splitter = SceneSplitter(
            pacing=self.config.pacing,
            character_rate=self.config.character_rate,
        )

        # Determine intro end (before first segment)
        intro_end = aligned[0].start if aligned else 0.0

        scenes = splitter.split_all(
            aligned,
            intro_end=intro_end,
            audio_duration=project.audio_duration,
        )

        scene_errors = validator.validate_scenes(scenes, project.audio_duration)
        project.scenes = scenes

        print(f"   Created {len(scenes)} scenes "
              f"({len([s for s in scenes if s.type == 'number_card'])} cards, "
              f"{len([s for s in scenes if s.type == 'content'])} content)")

    def _try_load_transcript(self, project: Project, workspace: Path) -> None:
        """Auto-detect and load an existing transcript file for this project."""
        from video_automation.transcribe.loader import load_transcript_json

        scripts_dir = workspace / "scripts"
        if not scripts_dir.exists():
            return

        # Build search names: project name + audio filename stem (if different)
        search_names = [project.name]
        if project.audio_path:
            audio_stem = Path(project.audio_path).stem
            if audio_stem != project.name:
                search_names.append(audio_stem)

        # Search patterns in priority order
        suffixes = ["_word_timestamps.json", "_timeline.json", "_transcript.json"]

        for name in search_names:
            for suffix in suffixes:
                candidate = scripts_dir / f"{name}{suffix}"
                if candidate.exists():
                    print(f"   Auto-loading transcript: {candidate.name}")
                    words, duration = load_transcript_json(candidate)
                    if words:
                        project.words = words
                        project.audio_duration = duration
                        return

        # Fallback: search for any JSON file matching project name or audio name
        for name in search_names:
            for json_file in sorted(scripts_dir.glob(f"{name}*.json")):
                if json_file.stem.endswith("_project") or json_file.stem.endswith("_prompts"):
                    continue
                print(f"   Trying transcript file: {json_file.name}")
                words, duration = load_transcript_json(json_file)
                if words:
                    project.words = words
                    project.audio_duration = duration
                    return

        print("   No existing transcript files found.")


class PromptStage(Stage):
    name = "prompt"

    def execute(self, project: Project, workspace: Path) -> None:
        # Only generate prompts for scenes that need them
        needs_prompt = [s for s in project.scenes if s.needs_prompt()]
        if not needs_prompt:
            print("   Prompt: all scenes already prompted, skipping.")
            return

        print(f"   Generating prompts for {len(needs_prompt)} scenes...")

        # Try Claude API first, then local LLM, then template
        if self.config.anthropic_api_key:
            from video_automation.prompt.claude_batch import ClaudeBatchPromptGenerator
            generator = ClaudeBatchPromptGenerator(
                api_key=self.config.anthropic_api_key,
                model=self.config.claude_model,
            )
            generator.generate(project, workspace)
        elif self.config.llm_provider:
            from video_automation.prompt.local_llm import LocalLLMPromptGenerator
            generator = LocalLLMPromptGenerator(
                provider=self.config.llm_provider,
                model=self.config.llm_model,
                url=self.config.llm_url,
            )
            generator.generate(project, workspace)
        else:
            from video_automation.prompt.template import TemplatePromptGenerator
            generator = TemplatePromptGenerator()
            generator.generate(project, workspace)

    def should_skip(self, project: Project) -> bool:
        return not any(s.needs_prompt() for s in project.scenes)


class GenerateStage(Stage):
    name = "generate"

    def execute(self, project: Project, workspace: Path) -> None:
        images_dir = workspace / "images" / project.name

        # Reset any scene marked "generated" but missing its file on disk
        reset = 0
        for scene in project.scenes:
            if scene.status == "generated" and scene.type != "intro":
                if scene.image_path:
                    full = workspace / scene.image_path
                    if not full.exists() or full.stat().st_size == 0:
                        scene.status = "prompted"
                        scene.image_path = None
                        reset += 1
                else:
                    scene.status = "prompted"
                    reset += 1
        if reset:
            print(f"   Reset {reset} scenes (marked generated but no file on disk)")

        needs_image = [s for s in project.scenes if s.needs_image()]
        if not needs_image:
            print("   Generate: all scenes already generated, skipping.")
            return

        print(f"   Generating images for {len(needs_image)} scenes...")

        from video_automation.generate.ai33 import AI33Generator
        from video_automation.generate.number_card import NumberCardGenerator

        images_dir.mkdir(parents=True, exist_ok=True)

        # Number cards: render locally with PIL
        card_gen = NumberCardGenerator()
        for scene in needs_image:
            if scene.type == "number_card":
                num = scene.metadata.get("segment_number", 0)
                card_path = images_dir / f"number_card_{num:02d}.png"
                card_gen.generate(num, str(card_path))
                scene.image_path = str(card_path.relative_to(workspace))
                scene.status = "generated"

        # Content scenes: generate via AI33
        content_needs = [s for s in needs_image if s.type == "content" and s.needs_image()]
        if content_needs and self.config.ai33_api_key:
            ai33 = AI33Generator(
                api_key=self.config.ai33_api_key,
                base_url=self.config.ai33_base_url,
                model=self.config.ai33_model,
            )
            ai33.generate_batch(content_needs, images_dir, workspace, self.config, project=project)

    def should_skip(self, project: Project) -> bool:
        # Never skip — execute() does its own file-existence check
        return False


class CompileStage(Stage):
    name = "compile"

    def execute(self, project: Project, workspace: Path) -> None:
        from video_automation.compile.compiler import VideoCompiler

        compiler = VideoCompiler(self.config)
        compiler.compile(project, workspace)


# ── Pipeline orchestrator ─────────────────────────────────────────────────

STAGE_ORDER = ["transcribe", "segment", "prompt", "generate", "compile"]

ALL_STAGES = {
    "transcribe": TranscribeStage,
    "segment": SegmentStage,
    "prompt": PromptStage,
    "generate": GenerateStage,
    "compile": CompileStage,
}


class Pipeline:
    """
    Orchestrates pipeline stages with start_from/stop_after.

    Replaces old mode flags:
    - Full pipeline:   run(start_from="transcribe", stop_after="compile")
    - Compile only:    run(start_from="compile", stop_after="compile")
    - Prompts only:    run(start_from="transcribe", stop_after="prompt")
    - Regen scenes:    set status→"planned", run(start_from="generate")
    - Retimeline:      run(start_from="segment") with new PacingProfile
    """

    def __init__(self, config: Config):
        self.config = config

    def run(
        self,
        project: Project,
        workspace: Path,
        start_from: str = "transcribe",
        stop_after: str = "compile",
    ) -> None:
        """Run pipeline stages sequentially with checkpointing."""
        workspace = Path(workspace)
        project_path = workspace / "scripts" / f"{project.name}_project.json"

        started = False
        for stage_name in STAGE_ORDER:
            if stage_name == start_from:
                started = True
            if not started:
                continue

            stage_cls = ALL_STAGES[stage_name]
            stage = stage_cls(self.config)

            print(f"\n{'='*60}")
            print(f"   STAGE: {stage_name.upper()}")
            print(f"{'='*60}")

            if stage.should_skip(project):
                print(f"   Skipped (nothing to do)")
            else:
                stage.execute(project, workspace)

            # Checkpoint after each stage
            project.save(project_path)
            print(f"   Saved checkpoint: {project_path.name}")

            if stage_name == stop_after:
                break

        print(f"\n   Pipeline complete ({start_from} → {stop_after})")
