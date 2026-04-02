"""
AI33 image generator.

Generates content scene images via the AI33.pro API.
Supports style references and character overlays.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from video_automation.config import Config
from video_automation.models import Scene

# ── Model alias map ─────────────────────────────────────────────────────
# ai33.pro rebrands some models in their web UI.  This map lets users
# pass the friendly UI name (or a short slug) via --ai33-model and have
# it resolved to the real API model_id automatically.
MODEL_ALIASES: dict[str, str] = {
    # Nano Banana family  →  Gemini image models (ai33.pro UI rebranding)
    "nano-banana":                  "gemini-2.5-flash-image",           # Gemini 2.5 Flash Preview Image
    "nano-banana-pro":              "gemini-3-pro-image-preview",       # Gemini 3 Pro Image
    "nano-banana-2":                "gemini-3.1-flash-image-preview",   # Gemini 3.1 Flash Image
}


def resolve_model(name: str) -> str:
    """Resolve a friendly model name to the real API model_id."""
    key = name.strip().lower()
    return MODEL_ALIASES.get(key, name)


class AI33Generator:
    """Generate images via AI33 API with parallel workers."""

    def __init__(self, api_key: str, base_url: str = "https://api.ai33.pro",
                 model: str = "flux-2-pro"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = resolve_model(model)
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": api_key})
        self.lock = threading.Lock()
        self.total_credits = 0

        # Show resolved name if alias was used
        if self.model != model:
            print(f"   Model alias: {model} → {self.model}")

    def generate_batch(
        self,
        scenes: list[Scene],
        images_dir: Path,
        workspace: Path,
        config: Config,
        project=None,
    ) -> None:
        """Generate images for a batch of scenes using parallel workers."""
        from video_automation.generate.style_manager import StyleReferenceManager

        style_mgr = StyleReferenceManager(workspace / "style_references")
        char_path = self._find_character(workspace)

        # Skip scenes whose image already exists on disk (resume support)
        actually_need = []
        skipped = 0
        for scene in scenes:
            idx = scene.id
            out_path = images_dir / f"scene_{idx.replace('seg', '').replace('_scene', '_')}.png"
            if out_path.exists() and out_path.stat().st_size > 0:
                scene.image_path = str(out_path.relative_to(workspace))
                scene.status = "generated"
                skipped += 1
            else:
                actually_need.append(scene)

        if skipped:
            print(f"   Skipped {skipped} scenes (images already on disk)")

        if not actually_need:
            print(f"   All {len(scenes)} images already exist, nothing to generate.")
            return

        print(f"   Generating {len(actually_need)} images via AI33 ({self.model})...")

        # Checkpoint helper — saves project every N completions
        checkpoint_path = None
        if project:
            checkpoint_path = workspace / "scripts" / f"{project.name}_project.json"

        def _gen_one(scene: Scene) -> bool:
            idx = scene.id
            out_path = images_dir / f"scene_{idx.replace('seg', '').replace('_scene', '_')}.png"

            style_ref = style_mgr.get_next() if config.use_style_reference else None
            include_char = char_path if scene.include_character else None

            success, error, credits = self._generate_single(
                prompt=scene.prompt or "",
                output_path=str(out_path),
                style_ref_path=style_ref,
                character_path=include_char,
                config=config,
            )

            if success:
                # Burn title banner onto the image
                seg_title = scene.metadata.get("segment_title", "")
                if seg_title:
                    from video_automation.generate.title_overlay import TitleBarOverlay
                    TitleBarOverlay().apply(str(out_path), seg_title)

                scene.image_path = str(out_path.relative_to(workspace))
                scene.status = "generated"
                print(f"      ✓ {idx}  ({credits} credits)")
            else:
                print(f"      ✗ {idx}: {error}")
                scene.status = "failed"
                scene.metadata["last_error"] = error or "unknown"

            return success

        done = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = {pool.submit(_gen_one, s): s for s in actually_need}
            for future in as_completed(futures):
                try:
                    if future.result():
                        done += 1
                    else:
                        failed += 1
                except Exception as e:
                    scene = futures[future]
                    print(f"      CRASH {scene.id}: {e}")
                    scene.status = "failed"
                    failed += 1

                total = done + failed
                if total % 10 == 0:
                    with self.lock:
                        running = self.total_credits
                    print(f"      Progress: {total}/{len(actually_need)} "
                          f"({done} ok, {failed} failed) — {running} credits so far")
                    # Checkpoint every 10 images so we don't lose progress
                    if project and checkpoint_path:
                        project.save(checkpoint_path)

        # Final checkpoint
        if project and checkpoint_path:
            project.save(checkpoint_path)

        avg = self.total_credits / done if done else 0
        print(f"   AI33 complete: {done} generated, {failed} failed")
        print(f"   Credits: {self.total_credits} total, {avg:.0f} avg/image")

    def _generate_single(
        self,
        prompt: str,
        output_path: str,
        style_ref_path: str | None = None,
        character_path: str | None = None,
        config: Config | None = None,
    ) -> tuple[bool, str, int]:
        """Generate a single image. Returns (success, error, credits)."""
        cfg = config or Config()

        # Build prompt with image references
        refs = []
        if style_ref_path:
            refs.append("@img1")
        if character_path:
            refs.append(f"@img{len(refs) + 1}")

        final_prompt = f"{' '.join(refs)} {prompt}" if refs else prompt

        # Gemini models only accept 512/1K/2K/4K — map 1080p → 2K
        _GEMINI_RESOLUTION_MAP = {"1080p": "1K", "720p": "1K"}
        resolution = cfg.resolution
        if self.model.startswith("gemini-"):
            resolution = _GEMINI_RESOLUTION_MAP.get(resolution, resolution)

        data = {
            "prompt": final_prompt,
            "model_id": self.model,
            "generations_count": "1",
            "model_parameters": json.dumps({
                "aspect_ratio": cfg.aspect_ratio,
                "resolution": resolution,
            }),
        }

        files = []
        file_handles = []

        try:
            if style_ref_path and os.path.exists(style_ref_path):
                fh = open(style_ref_path, "rb")
                file_handles.append(fh)
                files.append(("assets", (os.path.basename(style_ref_path), fh, "image/png")))

            if character_path and os.path.exists(character_path):
                fh = open(character_path, "rb")
                file_handles.append(fh)
                files.append(("assets", (os.path.basename(character_path), fh, "image/png")))

            response = self.session.post(
                f"{self.base_url}/v1i/task/generate-image",
                data=data,
                files=files if files else None,
                timeout=120,
            )

            if response.status_code != 200:
                try:
                    msg = response.json().get("message", response.text[:200])
                except Exception:
                    msg = response.text[:200]
                return False, f"API {response.status_code}: {msg}", 0

            result = response.json()
            task_id = result.get("task_id")
            if not task_id:
                return False, f"No task_id: {result}", 0

            credits = result.get("estimated_credits", 500)

        except requests.exceptions.ConnectionError as e:
            return False, f"Connection error: {e}", 0
        except Exception as e:
            return False, f"Request error: {e}", 0
        finally:
            for fh in file_handles:
                try:
                    fh.close()
                except Exception:
                    pass

        # Poll for completion
        start_time = time.time()
        while (time.time() - start_time) < cfg.max_poll_time:
            time.sleep(cfg.poll_interval)

            try:
                status_resp = self.session.get(
                    f"{self.base_url}/v1/task/{task_id}", timeout=30
                )
                if status_resp.status_code != 200:
                    continue

                status_data = status_resp.json()
                status = status_data.get("status", "")

                if status == "done":
                    # Use actual cost from completed task (falls back to estimate)
                    actual_credits = status_data.get("credit_cost", credits)
                    images = status_data.get("metadata", {}).get("result_images", [])
                    if images:
                        image_url = images[0].get("imageUrl")
                        if image_url:
                            img_resp = requests.get(image_url, timeout=60)
                            if img_resp.status_code == 200:
                                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                                with open(output_path, "wb") as f:
                                    f.write(img_resp.content)

                                # Validate image
                                try:
                                    from PIL import Image
                                    with Image.open(output_path) as _img:
                                        _img.load()
                                except Exception:
                                    os.remove(output_path)
                                    return False, "Downloaded image is corrupt", 0

                                with self.lock:
                                    self.total_credits += actual_credits
                                return True, "", actual_credits

                    return False, "No image in result", 0

                elif status == "error":
                    err_msg = status_data.get("error_message", "Unknown error")
                    return False, f"Generation error: {err_msg}", 0

            except Exception:
                continue

        return False, f"Timeout after {cfg.max_poll_time}s", 0

    def _find_character(self, workspace: Path) -> str | None:
        """Find the MC character image in the workspace."""
        chars_dir = workspace / "characters"
        if not chars_dir.exists():
            return None

        mc = chars_dir / "MC.png"
        if mc.exists():
            return str(mc)

        for f in chars_dir.iterdir():
            if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                return str(f)

        return None
