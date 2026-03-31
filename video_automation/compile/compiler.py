"""
Video compiler — one clean path.

Encodes each scene as an individual clip with exact duration,
concatenates with -c copy, then muxes audio.
Effects (Ken Burns, crossfade) applied as optional transforms.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from video_automation.config import Config
from video_automation.models import Project, Scene


# Ken Burns zoom patterns: (zoom expr, x expr, y expr)
KB_PATTERNS = [
    ("min(zoom+0.0006,1.5)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("if(lte(zoom,1.0),1.5,max(1.001,zoom-0.0006))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("1.12", "0", "ih/2-(ih/zoom/2)"),
    ("1.12", "iw-(iw/zoom)", "ih/2-(ih/zoom/2)"),
    ("min(zoom+0.0006,1.3)", "0", "0"),
    ("min(zoom+0.0006,1.3)", "iw-(iw/zoom)", "ih-(ih/zoom)"),
]


class VideoCompiler:
    """Compile scenes + audio into final video."""

    def __init__(self, config: Config):
        self.config = config

    def compile(self, project: Project, workspace: Path) -> bool:
        """Compile all scenes into the final video."""
        workspace = Path(workspace)
        audio_path = workspace / project.audio_path
        output_path = workspace / "videos" / f"{project.name}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not audio_path.exists():
            print(f"   ERROR: Audio not found: {audio_path}")
            return False

        # Get audio duration via ffprobe
        total_duration = project.audio_duration or self._probe_duration(str(audio_path))

        print(f"\n   Compiling video: {project.name}")
        print(f"   Scenes: {len(project.scenes)}, Audio: {total_duration:.1f}s")

        temp_dir = tempfile.mkdtemp(prefix="va_compile_")

        try:
            # Create black background image
            black_img = os.path.join(temp_dir, "black.png")
            self._create_black_image(black_img)

            # Prepare intro clip if available
            intro_clip = self._prepare_intro(workspace, temp_dir)

            # Encode each scene as a clip
            clips = []
            cursor = 0.0

            for i, scene in enumerate(project.scenes):
                t0 = scene.start
                t1 = scene.end

                # Fill gap before this scene
                if t0 > cursor + 0.001:
                    gap_clip = os.path.join(temp_dir, f"gap_{i:04d}.mp4")
                    self._encode_image_clip(black_img, t0 - cursor, gap_clip)
                    clips.append(gap_clip)

                # Intro scenes: use intro video clip instead of an image
                if scene.type == "intro" and intro_clip:
                    duration = t1 - t0
                    trimmed_intro = os.path.join(temp_dir, f"intro_trimmed_{i:04d}.mp4")
                    intro_kwargs = dict(capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
                    if os.name == "nt":
                        intro_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    r = subprocess.run([
                        "ffmpeg", "-y", "-nostdin", "-i", intro_clip,
                        "-t", f"{duration:.3f}",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                        "-pix_fmt", "yuv420p", "-r", str(self.config.fps),
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
                        "-an", trimmed_intro,
                    ], **intro_kwargs)
                    if r.returncode == 0:
                        clips.append(trimmed_intro)
                        cursor = t1
                        print(f"   Added intro clip ({duration:.1f}s)")
                        continue
                    else:
                        print(f"   WARNING: Intro clip encode failed, falling back to black")

                # Resolve image path
                img_path = self._resolve_image(scene, workspace, temp_dir, i)

                # Encode scene clip (no Ken Burns zoom on number cards)
                clip_path = os.path.join(temp_dir, f"clip_{i:04d}.mp4")
                pattern_idx = i  # for Ken Burns pattern cycling
                use_zoom = scene.type != "number_card"
                has_title_bar = bool(scene.metadata.get("segment_title"))
                self._encode_image_clip(
                    img_path, t1 - t0, clip_path, pattern_idx,
                    apply_ken_burns=use_zoom, has_title_bar=has_title_bar,
                )
                clips.append(clip_path)
                cursor = t1

            # Trailing gap
            tail = total_duration - cursor
            if tail > 0.001:
                tail_clip = os.path.join(temp_dir, "tail.mp4")
                self._encode_image_clip(black_img, tail, tail_clip)
                clips.append(tail_clip)

            # Concat all clips with -c copy
            print(f"   Concatenating {len(clips)} clips...")
            images_video = os.path.join(temp_dir, "images.mp4")
            if not self._concat_clips(clips, images_video):
                return False

            # Apply crossfade if enabled
            if self.config.crossfade:
                faded = self._apply_crossfade(clips, temp_dir)
                if faded:
                    images_video = faded

            # Mux with audio
            print(f"   Muxing audio...")
            success = self._mux_audio(images_video, str(audio_path), str(output_path))

            if success and output_path.exists():
                size_mb = output_path.stat().st_size / (1024 * 1024)
                final_dur = self._probe_duration(str(output_path))
                diff = final_dur - total_duration
                print(f"\n   Video: {output_path}")
                print(f"   Duration: {final_dur:.3f}s (audio: {total_duration:.3f}s, diff: {diff:+.3f}s)")
                print(f"   Size: {size_mb:.1f} MB")
                return True

            return False

        except Exception as e:
            print(f"   ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Title bar takes up 8% of image height (must match TitleBarOverlay.bar_fraction)
    TITLE_BAR_FRACTION = 0.08

    def _encode_image_clip(
        self, img_path: str, duration: float, clip_path: str,
        pattern_idx: int = 0, apply_ken_burns: bool = True,
        has_title_bar: bool = False,
    ) -> bool:
        """Encode a single image as a video clip with exact duration."""
        fps = self.config.fps
        use_kb = self.config.ken_burns and apply_ken_burns

        if use_kb and has_title_bar:
            # Split image: static title bar on top, Ken Burns on content below
            vf = self._build_split_zoom_filter(duration, pattern_idx)
        elif use_kb:
            frames = max(int(math.ceil(duration * fps)) + 1, 1)
            z, x, y = KB_PATTERNS[pattern_idx % len(KB_PATTERNS)]
            vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,"
                  f"crop=3840:2160,"
                  f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps={fps},"
                  f"setsar=1")
        else:
            vf = ("scale=1920:1080:force_original_aspect_ratio=increase,"
                  "crop=1920:1080")

        # Split-zoom uses complex filtergraph (-filter_complex), others use -vf
        use_complex = use_kb and has_title_bar

        kwargs = dict(capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-loop", "1", "-framerate", str(fps),
            "-i", img_path,
            "-t", f"{duration:.6f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps),
        ]
        if use_complex:
            cmd += ["-filter_complex", vf, "-map", "[out]"]
        else:
            cmd += ["-vf", vf]
        cmd.append(clip_path)

        r = subprocess.run(cmd, **kwargs)

        if r.returncode != 0:
            stderr = r.stderr[-800:].strip() if r.stderr else ""
            print(f"      FFmpeg clip error: {stderr}")
            return False
        return True

    def _build_split_zoom_filter(self, duration: float, pattern_idx: int) -> str:
        """Build a complex filtergraph that keeps the title bar static
        while applying Ken Burns zoom to the content area below it."""
        fps = self.config.fps
        frames = max(int(math.ceil(duration * fps)) + 1, 1)
        z, x, y = KB_PATTERNS[pattern_idx % len(KB_PATTERNS)]

        # Upscaled dimensions (2x for zoompan quality)
        up_w, up_h = 3840, 2160
        bar_h_up = int(up_h * self.TITLE_BAR_FRACTION)
        content_h_up = up_h - bar_h_up

        # Output dimensions
        out_w, out_h = 1920, 1080
        bar_h_out = int(out_h * self.TITLE_BAR_FRACTION)
        content_h_out = out_h - bar_h_out

        return (
            f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase,"
            f"crop={up_w}:{up_h},"
            f"split[bar][content];"
            # Title bar: crop top portion, zoompan with z=1 (no zoom, just scale down)
            # Using zoompan ensures same frame count as content stream
            f"[bar]crop={up_w}:{bar_h_up}:0:0,"
            f"zoompan=z='1':x='0':y='0':d={frames}"
            f":s={out_w}x{bar_h_out}:fps={fps}[title];"
            # Content: crop below title bar, apply Ken Burns zoom
            f"[content]crop={up_w}:{content_h_up}:0:{bar_h_up},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}"
            f":s={out_w}x{content_h_out}:fps={fps}[body];"
            # Stack title bar on top of zoomed content
            f"[title][body]vstack,setsar=1[out]"
        )

    def _concat_clips(self, clips: list[str], output: str) -> bool:
        """Concatenate clips with -c copy."""
        list_file = output + ".txt"
        with open(list_file, "w") as f:
            for c in clips:
                # Escape single quotes for ffmpeg concat demuxer
                safe_path = os.path.abspath(c).replace(chr(92), '/').replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        kwargs = dict(capture_output=True, text=True, timeout=7200, stdin=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        r = subprocess.run([
            "ffmpeg", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", output,
        ], **kwargs)

        if r.returncode != 0:
            print(f"   FFmpeg concat FAILED: {r.stderr[-300:]}")
            return False
        return True

    def _mux_audio(self, video: str, audio: str, output: str) -> bool:
        """Mux video with audio track."""
        kwargs = dict(capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        r = subprocess.run([
            "ffmpeg", "-y", "-nostdin",
            "-i", video,
            "-i", audio,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output,
        ], **kwargs)

        if r.returncode != 0:
            print(f"   Audio mux failed: {r.stderr[-200:]}")
            # Fall back to video-only
            shutil.copy(video, output)
        return os.path.exists(output)

    def _apply_crossfade(self, clips: list[str], temp_dir: str) -> str | None:
        """Apply crossfade dissolves between clips (optional)."""
        # Crossfade is complex with many clips — only worth it for small counts
        # For now, return None (no crossfade applied)
        # TODO: implement xfade filter chain for crossfade support
        return None

    def _resolve_image(
        self, scene: Scene, workspace: Path, temp_dir: str, idx: int,
    ) -> str:
        """Get the image path for a scene, with placeholder fallback."""
        if scene.image_path:
            full_path = workspace / scene.image_path
            if full_path.exists():
                return str(full_path)

        # Generate placeholder
        placeholder = os.path.join(temp_dir, f"placeholder_{idx:04d}.png")
        self._create_placeholder(scene.id, placeholder)
        return placeholder

    def _prepare_intro(self, workspace: Path, temp_dir: str) -> str | None:
        """Find and prepare the intro clip."""
        intro_dir = workspace / "intro"
        if not intro_dir.exists():
            return None

        for f in intro_dir.iterdir():
            if f.suffix.lower() in (".mp4", ".mov"):
                if self.config.intro_duration > 0:
                    trimmed = os.path.join(temp_dir, "intro.mp4")
                    intro_kwargs = dict(capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
                    if os.name == "nt":
                        intro_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    r = subprocess.run([
                        "ffmpeg", "-y", "-nostdin", "-i", str(f),
                        "-t", f"{self.config.intro_duration:.3f}",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                        "-pix_fmt", "yuv420p", "-r", str(self.config.fps),
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
                        trimmed,
                    ], **intro_kwargs)
                    if r.returncode == 0:
                        return trimmed
                return str(f)
        return None

    def _create_black_image(self, path: str) -> None:
        """Create a 1920x1080 black PNG."""
        from PIL import Image
        img = Image.new("RGB", (self.config.image_width, self.config.image_height), (0, 0, 0))
        img.save(path, "PNG")

    def _create_placeholder(self, scene_id: str, path: str) -> None:
        """Generate a dark placeholder PNG for failed scenes."""
        from PIL import Image, ImageDraw, ImageFont

        w, h = self.config.image_width, self.config.image_height
        img = Image.new("RGB", (w, h), (20, 20, 30))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("arialbd.ttf", 60)
        except Exception:
            font = ImageFont.load_default()

        text = f"Scene: {scene_id}\nImage Missing"
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (w - (bbox[2] - bbox[0])) // 2
        y = (h - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), text, fill=(200, 60, 60), font=font)
        img.save(path, "PNG")

    @staticmethod
    def _probe_duration(path: str) -> float:
        """Get media duration via ffprobe."""
        try:
            probe_kwargs = dict(capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            if os.name == "nt":
                probe_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                **probe_kwargs,
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0
