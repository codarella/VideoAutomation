"""
CLI entry point for the VideoAutomation pipeline.

Usage:
    python -m video_automation --audio audio/my_video.mp3 --script script.txt \\
        --name "My Video" --workspace video_workspace --count 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from video_automation.config import Config, PacingProfile
from video_automation.models import Project
from video_automation.pipeline import Pipeline


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="VideoAutomation — Top N listicle video generator",
    )

    # Required
    p.add_argument("--audio", required=True, help="Path to audio file (relative to workspace)")
    p.add_argument("--script", default="", help="Path to original narration script (relative to workspace)")
    p.add_argument("--name", required=True, help="Video name")
    p.add_argument("--workspace", default="video_workspace", help="Workspace directory")

    # Segment detection
    p.add_argument("--count", type=int, default=0, help="Expected segment count (0 = auto-detect from script)")
    p.add_argument("--direction", choices=["descending", "ascending"], default="descending",
                   help="Counting direction (default: descending)")

    # Pipeline control
    p.add_argument("--start-from", default="transcribe",
                   choices=["transcribe", "segment", "scene", "prompt", "generate", "compile"],
                   help="Pipeline stage to start from")
    p.add_argument("--stop-after", default="compile",
                   choices=["transcribe", "segment", "scene", "prompt", "generate", "compile"],
                   help="Pipeline stage to stop after")

    # Resume from existing project
    p.add_argument("--resume", action="store_true",
                   help="Resume from existing project.json (skip transcribe/segment if already done)")

    # API keys
    p.add_argument("--ai33-key", default="", help="AI33 API key")
    p.add_argument("--anthropic-key", default="", help="Anthropic API key for Claude")
    p.add_argument("--openai-key", default="", help="OpenAI API key")
    p.add_argument("--openai-model", default="gpt-4.1", help="OpenAI model (default: gpt-4.1)")
    p.add_argument("--gemini-key", default="", help="Gemini API key for LLM scene splitting")
    p.add_argument("--claude-model", default="claude-sonnet-4-6", help="Claude model")

    # Local LLM
    p.add_argument("--llm-provider", default="", choices=["", "ollama", "lmstudio"],
                   help="Local LLM provider")
    p.add_argument("--llm-model", default="", help="Local LLM model name")

    # Image generation
    p.add_argument("--ai33-model", default="bytedance-seedream-4.5", help="AI33 image model")
    p.add_argument("--max-workers", type=int, default=10, help="Parallel image generation workers")

    # Effects
    p.add_argument("--ken-burns", action="store_true", help="Enable Ken Burns zoom/pan effect")
    p.add_argument("--crossfade", action="store_true", help="Enable crossfade dissolves")

    # Whisper
    p.add_argument("--whisper-model", default="medium", help="Whisper model size")
    p.add_argument("--whisper-passes", type=int, default=3, help="Multi-pass Whisper consensus (1-3)")

    # Pacing
    p.add_argument("--fast-cut-window", type=float, default=60.0,
                   help="Seconds from start to use fast cuts")
    p.add_argument("--fast-cut-min", type=float, default=2.0, help="Min fast-cut duration")
    p.add_argument("--fast-cut-max", type=float, default=3.0, help="Max fast-cut duration")
    p.add_argument("--standard-min", type=float, default=4.0, help="Min standard duration")
    p.add_argument("--standard-max", type=float, default=7.0, help="Max standard duration")

    # Niche / visual style
    p.add_argument("--style", default="2d_western_cartoon",
                   choices=["2d_western_cartoon", "animals_nature",
                            "true_crime", "history", "tech_gadgets"],
                   help="Visual niche / style (default: 2d_western_cartoon)")

    # Misc
    p.add_argument("--find-dupes", action="store_true", help="Detect and regenerate duplicate images")
    p.add_argument("--regen-scenes", default="", help="Comma-separated 1-based scene indices to regenerate")
    p.add_argument("--intro-duration", type=float, default=0.0, help="Intro clip duration (0 = no intro)")

    return p.parse_args()


def main():
    args = parse_args()
    workspace = Path(args.workspace)

    # Build config
    config = Config(
        ai33_api_key=args.ai33_key,
        ai33_model=args.ai33_model,
        anthropic_api_key=args.anthropic_key,
        openai_api_key=args.openai_key,
        openai_model=args.openai_model,
        gemini_api_key=args.gemini_key,
        claude_model=args.claude_model,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        ken_burns=args.ken_burns,
        crossfade=args.crossfade,
        whisper_model=args.whisper_model,
        whisper_passes=args.whisper_passes,
        max_workers=args.max_workers,
        find_dupes=args.find_dupes,
        intro_duration=args.intro_duration,
        style=args.style,
        pacing=PacingProfile(
            fast_cut_window=args.fast_cut_window,
            fast_cut_min=args.fast_cut_min,
            fast_cut_max=args.fast_cut_max,
            standard_min=args.standard_min,
            standard_max=args.standard_max,
        ),
    )

    # Handle regen scenes
    if args.regen_scenes:
        config.regen_scenes = [int(x.strip()) for x in args.regen_scenes.split(",")]

    # Load or create project
    project_path = workspace / "scripts" / f"{args.name}_project.json"

    if project_path.exists():
        print(f"   Loading existing project: {project_path}")
        project = Project.load(project_path)
        # Ensure project.name matches CLI --name so save path == load path
        project.name = args.name
        # Update paths in case they changed
        if args.audio:
            project.audio_path = args.audio
        if args.script:
            project.script_path = args.script

        # Mark regen scenes for re-generation (keep existing prompt, just regenerate image)
        if config.regen_scenes:
            regen_set = set(config.regen_scenes)  # 1-based scene indices
            for idx, scene in enumerate(project.scenes):
                if (idx + 1) in regen_set:
                    # Delete existing image so it gets re-generated
                    if scene.image_path:
                        full = workspace / scene.image_path
                        if full.exists():
                            full.unlink()
                            print(f"   Deleted existing image: {scene.image_path}")
                    scene.status = "prompted"  # generate stage picks up "prompted"
                    scene.image_path = None
                    print(f"   Marked {scene.id} for regeneration")
    else:
        print(f"   Creating new project: {args.name}")
        project = Project(
            name=args.name,
            audio_path=args.audio,
            script_path=args.script,
            expected_count=args.count,
            counting_direction=args.direction,
            style=args.style,
        )

    # Run pipeline
    pipeline = Pipeline(config)
    pipeline.run(
        project=project,
        workspace=workspace,
        start_from=args.start_from,
        stop_after=args.stop_after,
    )


if __name__ == "__main__":
    main()
