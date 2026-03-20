"""
Video Automation – NiceGUI frontend  (replaces gui.py; backend untouched)
Run:  python gui_nicegui.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nicegui import app, ui


# ── File Picker (tkinter, runs server-side) ──────────────────────────────────
async def _pick_file(title: str = "Select file", filetypes: list = None,
                     initial_dir: str = "") -> str | None:
    """Open a native OS file dialog via tkinter (runs on the server/local machine).
    Returns the selected path or None if cancelled."""
    import concurrent.futures
    def _tk_dialog():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        ft = filetypes or [("All files", "*.*")]
        path = filedialog.askopenfilename(
            title=title, filetypes=ft,
            initialdir=initial_dir or None,
        )
        root.destroy()
        return path or None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, _tk_dialog)
    return result


async def _pick_folder(title: str = "Select folder",
                       initial_dir: str = "") -> str | None:
    """Open a native OS folder dialog via tkinter."""
    import concurrent.futures
    def _tk_dialog():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(
            title=title,
            initialdir=initial_dir or None,
        )
        root.destroy()
        return path or None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, _tk_dialog)
    return result


# ── Constants ────────────────────────────────────────────────────────────────
PYTHON = sys.executable
AI33_KEY = "sk_ixdn5l6ymkwlnetx4dzrlaehlncwo3r2sy0v8igpjzpsjlrx"
DEFAULT_WORKSPACE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "video_workspace")
)
STATE_FILE = os.path.join(os.path.dirname(__file__), "gui_state.json")

AI_MODELS = [
    "flux-2-pro",
    "bytedance-seedream-4.5",
    "bytedance-seedream-5-lite",
    "bytedance-seedream-4",
    "flux-1-kontext",
    "gpt-image-1",
    "gpt-image-1.5",
    "nano-banana-pro",
    "nano-banana-2",
    "nano-banana",
    "kling-omni-image",
    "runway-gen4-image",
    "runway-gen4-image-turbo",
    "wan-2.5-preview-image",
]
LLM_PROVIDERS = ["ollama", "lmstudio", "claude"]
CLAUDE_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"]
PIPELINE_STAGES = ["transcribe", "segment", "prompt", "generate", "compile"]


# ── GUIState  (JSON persistence) ─────────────────────────────────────────────
class GUIState:
    _defaults = {
        "workspace": DEFAULT_WORKSPACE,
        "dark_mode": True,
        "recent_projects": [],
        "last_project": "",
        "ai_model": AI_MODELS[0],
        "llm_provider": "ollama",
        "llm_model": "qwen2.5:3b",
        "anthropic_key": "",
        "claude_model": CLAUDE_MODELS[0],
        "ken_burns": False,
        "crossfade": False,
        "crossfade_duration": "0.4",
        "image_workers": "10",
        "find_dupes": False,
        "dupe_threshold": "10",
        "script_path": "",
        "start_from": "transcribe",
        "stop_after": "compile",
    }

    def __init__(self):
        self._data = dict(self._defaults)
        self._load()

    # Fields that should always be plain strings (select components)
    _select_fields = {"ai_model", "llm_provider", "llm_model", "claude_model", "start_from", "stop_after"}

    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                # Normalize any select fields that got saved as raw event args
                for field in self._select_fields:
                    if field in data:
                        data[field] = _sel(data[field])
                self._data.update(data)
            except Exception:
                pass

    def save(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def __getitem__(self, k):
        return self._data.get(k, self._defaults.get(k))

    def __setitem__(self, k, v):
        self._data[k] = v
        self.save()

    def add_recent(self, name: str):
        lst = self._data.get("recent_projects", [])
        if name in lst:
            lst.remove(name)
        lst.insert(0, name)
        self._data["recent_projects"] = lst[:5]
        self.save()


# ── AppState  (runtime reactive state) ───────────────────────────────────────
@dataclass
class AppState:
    workspace: str = DEFAULT_WORKSPACE
    project_name: str = ""
    audio_path: str = ""
    script_path: str = ""
    ai_model: str = AI_MODELS[0]
    tx_auto: bool = True
    transcript_path: str = ""
    use_llm: bool = False
    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:3b"
    anthropic_key: str = ""
    claude_model: str = CLAUDE_MODELS[0]
    start_from: str = "transcribe"
    stop_after: str = "compile"
    resume: bool = False
    ken_burns: bool = False
    crossfade: bool = False
    crossfade_duration: str = "0.4"
    image_workers: str = "10"
    regen_scenes: str = ""
    find_dupes: bool = False
    dupe_threshold: str = "10"


# ── CommandBuilder ────────────────────────────────────────────────────────────
def build_command(s: AppState) -> list[str]:
    cmd = [
        PYTHON, "-X", "utf8", "-u", "-m", "video_automation",
        "--audio",      s.audio_path,
        "--name",       s.project_name,
        "--workspace",  s.workspace,
        "--ai33-key",   AI33_KEY,
        "--ai33-model", s.ai_model,
        "--start-from", s.start_from,
        "--stop-after", s.stop_after,
    ]

    if s.script_path.strip() and os.path.exists(s.script_path.strip()):
        cmd += ["--script", s.script_path.strip()]

    if s.resume:
        cmd.append("--resume")

    # If user chose "Use existing JSON" and provided a transcript path,
    # log it but do NOT silently override start_from — the user controls that directly
    if not s.tx_auto and s.transcript_path and os.path.exists(s.transcript_path):
        print(f"[GUI] Existing transcript available: {s.transcript_path}")

    if s.use_llm:
        if s.llm_provider == "claude":
            if s.anthropic_key.strip():
                cmd += ["--anthropic-key", s.anthropic_key.strip()]
            cmd += ["--claude-model", s.claude_model]
        else:
            cmd += ["--llm-provider", s.llm_provider]
            if s.llm_model.strip():
                cmd += ["--llm-model", s.llm_model.strip()]

    if s.ken_burns:
        cmd.append("--ken-burns")
    if s.crossfade:
        cmd.append("--crossfade")

    if s.regen_scenes.strip():
        cmd += ["--regen-scenes", s.regen_scenes.strip()]

    if s.image_workers.strip():
        cmd += ["--max-workers", s.image_workers.strip()]

    if s.find_dupes:
        cmd.append("--find-dupes")

    return cmd


# ── ProcessManager ────────────────────────────────────────────────────────────
class ProcessManager:
    def __init__(self):
        self._proc: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self._log_buffer: list[str] = []
        self.on_line = None        # callback(line: str)
        self.on_done = None        # callback(returncode: int)

    async def start(self, cmd: list[str]):
        if self.running:
            return
        self._log_buffer.clear()
        self.running = True
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        await self._stream()

    async def _stream(self):
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", errors="replace")
                self._log_buffer.append(line)
                if len(self._log_buffer) > 2000:
                    self._log_buffer.pop(0)
                if self.on_line:
                    self.on_line(line)
        finally:
            await self._proc.wait()
            rc = self._proc.returncode
            self.running = False
            self._proc = None
            if self.on_done:
                self.on_done(rc)

    def stop(self):
        if self._proc and self.running:
            try:
                self._proc.terminate()
            except Exception:
                pass


# ── PromptsManager ────────────────────────────────────────────────────────────
class PromptsManager:
    def __init__(self, workspace: str, name: str):
        self.workspace = workspace
        self.name = name
        self.path = os.path.join(workspace, "scripts", f"{name}_prompts.json")
        self.images_dir = os.path.join(workspace, "images", name)
        self._scenes: list[dict] = []

    def load(self) -> list[dict]:
        # Try prompts JSON first, fall back to project JSON
        project_path = os.path.join(self.workspace, "scripts", f"{self.name}_project.json")
        src = self.path if os.path.exists(self.path) else project_path
        if not os.path.exists(src):
            self._scenes = []
            return []
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            self._scenes = data
        else:
            self._scenes = data.get("scenes", [])
        return self._scenes

    def save(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            raw = self._scenes
        else:
            raw["scenes"] = self._scenes
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

    @property
    def scenes(self):
        return self._scenes

    def update_scene(self, scene_num: int, prompt: str, negative: str):
        for sc in self._scenes:
            if sc.get("scene") == scene_num:
                sc["prompt"] = prompt
                sc["negative"] = negative
                break
        self.save()

    def update_segment_title(self, old_segment: str, new_segment: str):
        """Update the segment field for every scene that belongs to old_segment."""
        for sc in self._scenes:
            if sc.get("segment", "") == old_segment:
                sc["segment"] = new_segment
        self.save()

    def assign_segment_to_range(self, start: float, end: float,
                                segment_label: str) -> tuple[int, str | None]:
        """
        Set the segment field for all scenes overlapping [start, end].

        Returns (count_updated, error_or_none).
        """
        if end <= start:
            return 0, f"Invalid range: {start:.2f}s - {end:.2f}s"
        if not segment_label.strip():
            return 0, "Segment label cannot be empty"

        count = 0
        for sc in self._scenes:
            p = self._parse_time(sc.get("time", ""))
            if not p:
                continue
            sc_start, sc_end = p
            # Scene overlaps the range if it doesn't end before or start after
            if sc_end > start and sc_start < end:
                sc["segment"] = segment_label.strip()
                count += 1

        if count:
            self.save()
        return count, None

    @staticmethod
    def _parse_time(time_str: str) -> tuple[float, float] | None:
        m = re.match(r'([\d.]+)s\s*-\s*([\d.]+)s', time_str or "")
        return (float(m.group(1)), float(m.group(2))) if m else None

    @staticmethod
    def _fmt_time(start: float, end: float) -> str:
        return f"{start:.2f}s - {end:.2f}s"

    def _rename_images(self, old_to_new: dict[int, int]):
        """
        Rename image files to match new scene numbers.

        old_to_new: {old_scene_num: new_scene_num}
        Images use 0-based index: scene N -> scene_{N-1:04d}.png

        Uses a two-pass temp-rename strategy to avoid collisions:
          Pass 1: old_name -> __tmp_new_name
          Pass 2: __tmp_new_name -> new_name
        """
        if not os.path.isdir(self.images_dir):
            return

        # Pass 1: rename to temp names
        temp_map: list[tuple[str, str]] = []  # (temp_path, final_path)
        for old_num, new_num in old_to_new.items():
            if old_num == new_num:
                continue
            old_idx = old_num - 1
            new_idx = new_num - 1
            old_path = os.path.join(self.images_dir, f"scene_{old_idx:04d}.png")
            if not os.path.exists(old_path):
                continue
            temp_path = os.path.join(self.images_dir, f"__tmp_scene_{new_idx:04d}.png")
            final_path = os.path.join(self.images_dir, f"scene_{new_idx:04d}.png")
            try:
                os.rename(old_path, temp_path)
                temp_map.append((temp_path, final_path))
            except OSError:
                pass

        # Pass 2: temp names -> final names
        for temp_path, final_path in temp_map:
            try:
                if os.path.exists(final_path):
                    # Target already exists (wasn't moved in pass 1) — back it up
                    backup = final_path + ".bak"
                    os.rename(final_path, backup)
                os.rename(temp_path, final_path)
            except OSError:
                pass

    def _delete_images(self, scene_nums: list[int]):
        """Delete image files for the given scene numbers."""
        if not os.path.isdir(self.images_dir):
            return
        for num in scene_nums:
            idx = num - 1
            path = os.path.join(self.images_dir, f"scene_{idx:04d}.png")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def set_scene_time(self, scene_num: int, new_start: float | None = None,
                       new_end: float | None = None, duration: float | None = None,
                       min_duration: float = 0.3) -> str | None:
        """
        Set a scene's time range and cascade-adjust neighbors.

        Specify either:
          - new_start + new_end  (explicit range)
          - new_start + duration (start anchored)
          - new_end + duration   (end anchored)
          - duration alone       (keeps current midpoint)

        Neighbors are adjusted so all scenes stay contiguous.
        Scenes shrunk below min_duration cause the adjustment to cascade further.
        Returns error string on failure, None on success.
        """
        idx = next((i for i, sc in enumerate(self._scenes) if sc.get("scene") == scene_num), None)
        if idx is None:
            return f"Scene {scene_num} not found"

        parsed = self._parse_time(self._scenes[idx].get("time", ""))
        if not parsed:
            return f"Could not parse timing for scene {scene_num}"
        cur_start, cur_end = parsed

        # Resolve the target range from whichever combo of args was given
        if new_start is not None and new_end is not None:
            pass  # both explicit
        elif new_start is not None and duration is not None:
            new_end = round(new_start + duration, 2)
        elif new_end is not None and duration is not None:
            new_start = round(new_end - duration, 2)
        elif duration is not None:
            mid = (cur_start + cur_end) / 2
            new_start = round(mid - duration / 2, 2)
            new_end = round(mid + duration / 2, 2)
        else:
            return "Provide (new_start + new_end), (start + duration), (end + duration), or duration"

        if new_start < 0:
            new_start = 0.0
        if new_end <= new_start:
            return f"Invalid range: {new_start:.2f}s - {new_end:.2f}s"

        # Set the target scene
        self._scenes[idx]["time"] = self._fmt_time(new_start, new_end)

        # Cascade backwards: adjust scenes before idx so their end matches our start
        boundary = new_start
        for i in range(idx - 1, -1, -1):
            p = self._parse_time(self._scenes[i].get("time", ""))
            if not p:
                break
            s, e = p
            if abs(e - boundary) < 0.001:
                break  # already aligned
            new_e = boundary
            if new_e - s < min_duration:
                # This scene got too short — shrink it to min and cascade further
                new_e = s + min_duration
                if new_e > boundary:
                    # Must also push this scene's start back
                    new_s = boundary - min_duration
                    if new_s < 0:
                        new_s = 0.0
                    self._scenes[i]["time"] = self._fmt_time(new_s, boundary)
                    boundary = new_s
                    continue
            self._scenes[i]["time"] = self._fmt_time(s, new_e)
            boundary = s  # stop cascading if scene wasn't shrunk below min
            if new_e - s >= min_duration:
                break

        # Cascade forwards: adjust scenes after idx so their start matches our end
        boundary = new_end
        for i in range(idx + 1, len(self._scenes)):
            p = self._parse_time(self._scenes[i].get("time", ""))
            if not p:
                break
            s, e = p
            if abs(s - boundary) < 0.001:
                break  # already aligned
            new_s = boundary
            if e - new_s < min_duration:
                # Scene too short — keep min duration and cascade
                new_e = new_s + min_duration
                self._scenes[i]["time"] = self._fmt_time(new_s, new_e)
                boundary = new_e
                continue
            self._scenes[i]["time"] = self._fmt_time(new_s, e)
            break  # no further cascade needed

        self.save()
        return None

    def set_prompt_at_range(self, start: float, end: float, prompt: str,
                           segment: str = "", scene_text: str = "",
                           negative: str = "") -> str | None:
        """
        Insert or replace a prompt at a specific time range.

        - Scenes fully inside [start, end] are removed.
        - Scenes partially overlapping are trimmed to the non-overlapping portion.
        - A new scene with the given prompt is inserted at the correct position.
        - All scenes are renumbered sequentially.
        Returns error string on failure, None on success.
        """
        if end <= start:
            return f"Invalid range: {start:.2f}s - {end:.2f}s"
        if start < 0:
            return "Start time cannot be negative"

        kept: list[dict] = []
        insert_idx = 0
        found_overlap = False
        removed_nums: list[int] = []  # scene numbers whose images get deleted

        for sc in self._scenes:
            old_num = sc.get("scene", 0)
            p = self._parse_time(sc.get("time", ""))
            if not p:
                kept.append(sc)
                continue
            sc_start, sc_end = p

            # No overlap — scene is entirely before the new range
            if sc_end <= start:
                kept.append(sc)
                insert_idx = len(kept)
                continue

            # No overlap — scene is entirely after the new range
            if sc_start >= end:
                if not found_overlap:
                    insert_idx = len(kept)
                kept.append(sc)
                continue

            # Some overlap exists
            found_overlap = True

            # Scene is fully consumed by the new range — drop it
            if sc_start >= start and sc_end <= end:
                removed_nums.append(old_num)
                continue

            # Scene starts before the new range — trim its end
            if sc_start < start:
                trimmed = dict(sc)
                trimmed["time"] = self._fmt_time(sc_start, start)
                kept.append(trimmed)
                insert_idx = len(kept)

            # Scene ends after the new range — trim its start
            if sc_end > end:
                trimmed = dict(sc)
                trimmed["time"] = self._fmt_time(end, sc_end)
                kept.append(trimmed)

        # Determine segment label: inherit from neighbors if not provided
        if not segment:
            if insert_idx > 0:
                segment = kept[insert_idx - 1].get("segment", "")
            elif insert_idx < len(kept):
                segment = kept[insert_idx].get("segment", "")

        # Build new scene entry
        new_scene = {
            "scene": 0,  # will be renumbered below
            "segment": segment,
            "time": self._fmt_time(start, end),
            "scene_text": scene_text,
            "prompt_source": "manual",
            "prompt": prompt,
            "negative": negative,
        }

        kept.insert(insert_idx, new_scene)

        # Build old→new scene number mapping for image renames
        old_to_new: dict[int, int] = {}
        for new_idx, sc in enumerate(kept):
            old_num = sc.get("scene", 0)
            new_num = new_idx + 1
            if old_num and old_num != new_num:
                old_to_new[old_num] = new_num
            sc["scene"] = new_num

        # Delete images for fully removed scenes, then rename the rest
        self._delete_images(removed_nums)
        self._rename_images(old_to_new)

        self._scenes = kept
        self.save()
        return None

    def insert_number_card(self, before_scene_num: int, number: int, title: str, card_duration: float = 2.5) -> str | None:
        """
        Insert a number card entry immediately before before_scene_num.
        The target scene's start_time is pushed forward by card_duration (it shrinks).
        All scenes from the insertion point onward are renumbered +1.
        Returns an error string on failure, or None on success.
        """
        target_idx = next((i for i, sc in enumerate(self._scenes) if sc.get("scene") == before_scene_num), None)
        if target_idx is None:
            return "Scene not found"
        target = self._scenes[target_idx]
        m = re.match(r'([\d.]+)s\s*-\s*([\d.]+)s', target.get("time", ""))
        if not m:
            return "Could not parse timing for that scene"
        t0, t1 = float(m.group(1)), float(m.group(2))
        card_end = round(t0 + card_duration, 2)
        if card_end >= t1:
            return f"Card duration ({card_duration}s) is longer than the scene ({t1 - t0:.2f}s)"
        # Shrink target scene start
        target["time"] = f"{card_end:.2f}s - {t1:.2f}s"
        # Renumber from insertion point onward and rename images
        old_to_new: dict[int, int] = {}
        for sc in self._scenes[target_idx:]:
            old_num = sc.get("scene", 0)
            new_num = old_num + 1
            old_to_new[old_num] = new_num
            sc["scene"] = new_num
        self._rename_images(old_to_new)
        # Build and insert card
        seg_label = f"#{number} {title}"
        card_entry = {
            "scene": before_scene_num,
            "segment": seg_label,
            "time": f"{t0:.2f}s - {card_end:.2f}s",
            "scene_text": f"Number {number},",
            "prompt_source": "template",
            "type": "number_card",
            "prompt": (
                f"Pure white background filling the entire 16:9 frame. "
                f"Single large bold black number {number} centered. "
                f"No other elements, no decorative details, no gradients, completely clean. "
                f"16:9 widescreen. No text or labels anywhere in the image."
            ),
            "negative": "",
        }
        self._scenes.insert(target_idx, card_entry)
        self.save()
        return None


# ── GalleryScanner ────────────────────────────────────────────────────────────
def scan_gallery(workspace: str, name: str) -> dict[int, dict]:
    """Returns {scene_number: {status, path, fname}}"""
    images_dir = os.path.join(workspace, "images", name)
    prompts_path = os.path.join(workspace, "scripts", f"{name}_prompts.json")
    project_path = os.path.join(workspace, "scripts", f"{name}_project.json")
    result: dict[int, dict] = {}

    scenes = []
    for src in (prompts_path, project_path):
        if os.path.exists(src):
            try:
                with open(src, encoding="utf-8") as f:
                    data = json.load(f)
                scenes = data if isinstance(data, list) else data.get("scenes", [])
                if scenes:
                    break
            except Exception:
                pass

    from PIL import Image as _PIL

    for idx, sc in enumerate(scenes):
        # Skip intro scenes — they use video clips, not images
        if sc.get("type") == "intro":
            continue
        snum = sc.get("scene") or (idx + 1)
        # Resolve image path: prefer explicit image_path from project JSON
        img_path_raw = sc.get("image_path") or sc.get("image") or ""
        if img_path_raw:
            # image_path may be relative to workspace (e.g. "images/Proj/scene_01_00.png")
            fpath = os.path.join(workspace, img_path_raw.replace("\\", "/"))
            fname = os.path.basename(fpath)
        else:
            fname = f"scene_{idx:04d}.png"
            fpath = os.path.join(images_dir, fname)
        if not os.path.exists(fpath):
            # Try to find image by scene ID or derived name
            # e.g. id "seg04_scene01" -> try "seg04_scene01.png" and "scene_04_01.png"
            scene_id = sc.get("id", "")
            found_by_id = False
            if scene_id and os.path.isdir(images_dir):
                import re as _re
                # Build candidate base names: original ID + transformed name
                bases = [scene_id]
                m = _re.match(r"seg(\d+)_scene(\d+)", scene_id)
                if m:
                    bases.append(f"scene_{m.group(1)}_{m.group(2)}")
                for base in bases:
                    for ext in (".png", ".jpg", ".jpeg", ".webp"):
                        candidate = os.path.join(images_dir, f"{base}{ext}")
                        if os.path.exists(candidate):
                            fpath = candidate
                            fname = f"{base}{ext}"
                            found_by_id = True
                            break
                    if found_by_id:
                        break
            if not found_by_id:
                # Also check for placeholder variant
                ph_path = os.path.join(images_dir, f"placeholder_{fname}")
                if os.path.exists(ph_path):
                    result[snum] = {"status": "PLACEHOLDER", "path": ph_path, "fname": f"placeholder_{fname}"}
                    continue
                status = "MISSING"
            else:
                # found by ID — verify it
                try:
                    with _PIL.open(fpath) as img:
                        img.verify()
                    status = "OK"
                except Exception:
                    status = "CORRUPT"
        else:
            try:
                with _PIL.open(fpath) as img:
                    img.verify()
                status = "OK"
            except Exception:
                status = "CORRUPT"
        result[snum] = {"status": status, "path": fpath, "fname": fname}

    return result


# ── Project helpers ───────────────────────────────────────────────────────────
def list_projects(workspace: str) -> list[dict]:
    seen_names: set[str] = set()
    projects = []

    # Discover from images/ folders (legacy + post-generate projects)
    images_root = os.path.join(workspace, "images")
    if os.path.isdir(images_root):
        for entry in sorted(os.scandir(images_root), key=lambda e: e.name.lower()):
            if not entry.is_dir():
                continue
            seen_names.add(entry.name)
            scene_count = sum(
                1 for f in os.scandir(entry.path)
                if f.name.startswith("scene_") and f.name.endswith(".png")
            )
            has_video = os.path.exists(
                os.path.join(workspace, "videos", f"{entry.name}.mp4")
            )
            has_prompts = os.path.exists(
                os.path.join(workspace, "scripts", f"{entry.name}_prompts.json")
            ) or os.path.exists(
                os.path.join(workspace, "scripts", f"{entry.name}_project.json")
            )
            projects.append({
                "name": entry.name,
                "scene_count": scene_count,
                "has_video": has_video,
                "has_prompts": has_prompts,
            })

    # Discover from scripts/*_project.json (projects that haven't generated images yet)
    scripts_root = os.path.join(workspace, "scripts")
    if os.path.isdir(scripts_root):
        for fname in sorted(os.listdir(scripts_root)):
            if fname.endswith("_project.json"):
                name = fname[:-len("_project.json")]
                if name and name not in seen_names:
                    seen_names.add(name)
                    has_video = os.path.exists(
                        os.path.join(workspace, "videos", f"{name}.mp4")
                    )
                    projects.append({
                        "name": name,
                        "scene_count": 0,
                        "has_video": has_video,
                        "has_prompts": True,
                    })

    projects.sort(key=lambda p: p["name"].lower())
    return projects


def auto_detect_transcript(workspace: str, name: str) -> str:
    candidate = os.path.join(workspace, "scripts", f"{name}_word_timestamps.json")
    return candidate if os.path.exists(candidate) else ""


def load_missing_scenes(workspace: str, name: str) -> str:
    needs = set()
    map_path = os.path.join(workspace, "scripts", f"{name}_sync_map.txt")
    if os.path.exists(map_path):
        with open(map_path) as f:
            for line in f:
                if line.startswith("scene"):
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 5 and parts[4] in ("MISSING", "PLACEHOLDER"):
                    needs.add(parts[0])
    images_dir = os.path.join(workspace, "images", name)
    if os.path.isdir(images_dir):
        for fname in os.listdir(images_dir):
            if fname.startswith("placeholder_scene_") and fname.endswith(".png"):
                try:
                    idx = int(fname[len("placeholder_scene_"):-4])
                    needs.add(str(idx + 1))
                except ValueError:
                    pass
    return ",".join(sorted(needs, key=lambda x: int(x))) if needs else ""


# ═══════════════════════════════════════════════════════════════════════════════
#  UI  BUILD
# ═══════════════════════════════════════════════════════════════════════════════
gui_state = GUIState()
app_state = AppState(
    workspace=gui_state["workspace"],
    ai_model=gui_state["ai_model"],
    llm_provider=gui_state["llm_provider"],
    llm_model=gui_state["llm_model"],
    anthropic_key=gui_state["anthropic_key"],
    claude_model=gui_state["claude_model"],
    ken_burns=gui_state["ken_burns"],
    crossfade=gui_state["crossfade"],
    crossfade_duration=gui_state["crossfade_duration"],
    image_workers=gui_state["image_workers"],
    find_dupes=gui_state["find_dupes"],
    dupe_threshold=gui_state["dupe_threshold"],
    script_path=gui_state["script_path"],
    start_from=gui_state["start_from"],
    stop_after=gui_state["stop_after"],
)
proc = ProcessManager()

# shared log element reference (populated in build_log_tab)
_log_el = None
_progress_el = None
_status_label = None
_tab_panels = None
_project_view = None
_selector_view = None
_gallery_container = None
_gallery_summary = None
_regen_input = None   # shared reactive input in Settings + Gallery
_log_tab = None       # tab reference for auto-switching to Log
_prompts_reload = None  # closure to reload prompts editor on tab switch
_gallery_tab = None   # tab reference for switching back to Gallery after single regen


def _sel(args):
    """Normalize a NiceGUI select/toggle event value.
    Handles plain string, {value, label} dict, and [index, {value, label}] list
    formats emitted by different NiceGUI/Quasar versions."""
    if isinstance(args, dict):
        return args.get("label", args)
    if isinstance(args, list):
        # [index, {value, label}] or [string] or [{value, label}]
        for item in args:
            if isinstance(item, dict) and "label" in item:
                return item["label"]
        # Fallback: find the first non-int element
        for item in args:
            if not isinstance(item, int):
                return item
        return args[0] if args else ""
    return args


# ── Log helpers ───────────────────────────────────────────────────────────────
def _push_log(line: str):
    if _log_el:
        _log_el.push(line)
    # progress parsing  e.g. "  ✓ 12/48"
    if _progress_el:
        m = re.search(r"(\d+)\s*/\s*(\d+)", line)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                _progress_el.set_value(a / b)


def _on_done(rc: int):
    if _log_el:
        msg = "\n--- Done ---\n" if rc == 0 else f"\n--- Exit code {rc} ---\n"
        _log_el.push(msg)
    if _progress_el:
        _progress_el.set_value(1.0 if rc == 0 else 0.0)
    if _status_label:
        _status_label.set_text("Done" if rc == 0 else f"Error (code {rc})")
    _set_running_state(False)
    if rc == 0:
        # auto-refresh gallery
        _refresh_gallery()
        # show video link if compiled
        _check_video_link()


def _set_running_state(running: bool):
    pass   # buttons are wired inside build functions; use ui.notify for now


def _check_video_link():
    vpath = os.path.join(
        app_state.workspace, "videos", f"{app_state.project_name}.mp4"
    )
    if os.path.exists(vpath) and _log_el:
        _log_el.push(f"\nVideo ready: {vpath}\n")


# ── Image static serving ──────────────────────────────────────────────────────
def _mount_images():
    images_root = os.path.join(app_state.workspace, "images")
    if os.path.isdir(images_root):
        app.add_static_files("/project_images", images_root)


def _img_url(name: str, fname: str, fpath: str = "") -> str:
    t = int(os.path.getmtime(fpath)) if fpath and os.path.exists(fpath) else 0
    return f"/project_images/{name}/{fname}?t={t}"


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE
# ═════════════════════════════════════════════════════════════════════════════
@ui.page("/")
def index():
    global _log_el, _progress_el, _status_label, _tab_panels
    global _project_view, _selector_view, _gallery_container, _gallery_summary
    global _regen_input  # noqa: F841

    dark = ui.dark_mode(gui_state["dark_mode"])

    # ── header ────────────────────────────────────────────────────────────────
    with ui.header(elevated=True).classes("items-center justify-between px-4 py-2"):
        ui.label("Video Automation").classes("text-h6 font-bold")
        with ui.row().classes("items-center gap-2"):
            ui.label("Dark").classes("text-sm")
            dm_switch = ui.switch(value=gui_state["dark_mode"])
            dm_switch.on("update:model-value", lambda e: (
                dark.set_value(e.args),
                gui_state.__setitem__("dark_mode", e.args),
            ))

    # ── status footer (must be direct page child, not nested) ────────────────
    with ui.footer().classes("px-4 py-1 items-center"):
        _status_label = ui.label("Idle").classes("text-sm text-grey")

    # ── two top-level views ───────────────────────────────────────────────────
    _selector_view = ui.column().classes("w-full p-4")
    _project_view  = ui.column().classes("w-full p-2")
    _project_view.set_visibility(False)

    # ── PROJECT SELECTOR ─────────────────────────────────────────────────────
    with _selector_view:
        _build_project_selector()

    # ── PROJECT WORKSPACE ────────────────────────────────────────────────────
    with _project_view:
        _build_workspace_view()

    # wire process callbacks
    proc.on_line = _push_log
    proc.on_done = _on_done
    _mount_images()

    # ── Restore last session (fixes blank-page on reconnect) ─────────────────
    last = gui_state["last_project"]
    if last:
        try:
            app_state.project_name = last
            _selector_view.set_visibility(False)
            _project_view.set_visibility(True)
            _refresh_gallery()
        except Exception:
            # On any restore failure, stay on selector so user can re-open project
            _selector_view.set_visibility(True)
            _project_view.set_visibility(False)


def _open_project(name: str):
    app_state.project_name = name
    gui_state.add_recent(name)
    gui_state["last_project"] = name
    _selector_view.set_visibility(False)
    _project_view.set_visibility(True)
    _refresh_gallery()


def _back_to_selector():
    gui_state["last_project"] = ""   # don't restore this session on reconnect
    _project_view.set_visibility(False)
    _selector_view.clear()
    _selector_view.set_visibility(True)
    with _selector_view:
        _build_project_selector()
    _selector_view.set_visibility(True)


# ── PROJECT SELECTOR ─────────────────────────────────────────────────────────
def _build_project_selector():
    with ui.card().classes("w-full max-w-5xl mx-auto"):
        ui.label("Select or Create Project").classes("text-h5 font-bold mb-2")

        # Workspace path
        with ui.row().classes("items-center gap-2 w-full mb-4"):
            ws_input = ui.input("Workspace path", value=app_state.workspace).classes(
                "flex-1"
            )
            def _set_ws(v):
                app_state.workspace = v
                gui_state["workspace"] = v
                _mount_images()
            ws_input.on("blur", lambda e: _set_ws(ws_input.value))

            async def _browse_workspace():
                path = await _pick_folder("Select Workspace Folder", app_state.workspace)
                if path:
                    ws_input.value = path
                    _set_ws(path)
            ui.button(icon="folder_open", on_click=_browse_workspace).props("flat round").tooltip("Browse for workspace folder")

            def _refresh_selector():
                _selector_view.clear()
                with _selector_view:
                    _build_project_selector()
            ui.button("Refresh", icon="refresh", on_click=_refresh_selector)

        ui.separator()

        # New project
        with ui.row().classes("items-center gap-2 mb-4"):
            new_name = ui.input("New project name").classes("flex-1")
            def _create():
                n = new_name.value.strip()
                if n:
                    _open_project(n)
                else:
                    ui.notify("Enter a project name", type="warning")
            ui.button("Create & Open", icon="add", color="primary", on_click=_create)

        ui.separator()
        ui.label("Existing Projects").classes("text-subtitle1 font-semibold mt-2")

        projects = list_projects(app_state.workspace)
        if not projects:
            ui.label("No projects found in workspace.").classes("text-grey")
        else:
            with ui.grid(columns=3).classes("w-full gap-3 mt-2"):
                for p in projects:
                    with ui.card().classes("cursor-pointer hover:shadow-lg") as card:
                        with ui.row().classes("justify-between items-start"):
                            ui.label(p["name"]).classes("font-semibold text-sm")
                            if p["has_video"]:
                                ui.badge("Video", color="green")
                        with ui.row().classes("gap-2 mt-1"):
                            ui.label(f"{p['scene_count']} scenes").classes("text-xs text-grey")
                            if p["has_prompts"]:
                                ui.badge("Prompts", color="blue")
                        ui.button(
                            "Open", icon="folder_open",
                            on_click=lambda _, nm=p["name"]: _open_project(nm)
                        ).classes("mt-2").props("flat dense")

        # Recent projects
        recent = gui_state["recent_projects"]
        if recent:
            ui.separator()
            ui.label("Recent").classes("text-subtitle1 font-semibold mt-2")
            with ui.row().classes("gap-2 flex-wrap"):
                for rp in recent:
                    ui.chip(
                        rp, icon="history",
                        on_click=lambda _, nm=rp: _open_project(nm)
                    ).props("clickable outline")


# ── WORKSPACE VIEW ────────────────────────────────────────────────────────────
def _build_workspace_view():
    global _log_el, _progress_el, _tab_panels, _log_tab, _gallery_tab
    global _gallery_container, _gallery_summary, _regen_input

    # breadcrumb + tabs — sticky at top
    with ui.card().classes("sticky top-0 z-50 shadow-md w-full").props("flat bordered"):
        with ui.row().classes("items-center gap-2 mb-1 px-2 pt-2"):
            ui.button("Projects", icon="arrow_back", on_click=_back_to_selector).props("flat")
            ui.label("/").classes("text-grey")
            ui.label("").classes("text-h6 font-bold").bind_text_from(app_state, "project_name")

        with ui.tabs().classes("w-full") as tabs:
            t_gen      = ui.tab("Generate",       icon="play_circle")
            t_gallery  = ui.tab("Gallery",        icon="grid_view")
            t_segments = ui.tab("Segments",       icon="segment")
            t_prompts  = ui.tab("Prompts Editor", icon="edit_note")
            t_settings = ui.tab("Settings",       icon="tune")
            t_log      = ui.tab("Log",            icon="terminal")
            _log_tab = t_log
            _gallery_tab = t_gallery

    def _on_tab_change(e):
        if e.value == "Prompts Editor" and _prompts_reload:
            _prompts_reload()

    tabs.on("update:model-value", _on_tab_change)

    with ui.tab_panels(tabs, value=t_gen).classes("w-full") as _tab_panels:
        with ui.tab_panel(t_gen):
            _build_generate_tab()
        with ui.tab_panel(t_gallery):
            _build_gallery_tab()
        with ui.tab_panel(t_segments):
            _build_segments_tab()
        with ui.tab_panel(t_prompts):
            _build_prompts_tab()
        with ui.tab_panel(t_settings):
            _build_settings_tab()
        with ui.tab_panel(t_log):
            _build_log_tab()


# ── GENERATE TAB ──────────────────────────────────────────────────────────────
def _build_generate_tab():
    # ── Core ────────────────────────────────────────────────────────────────
    with ui.card().classes("w-full mb-3"):
        ui.label("Core").classes("text-subtitle1 font-semibold")
        with ui.grid(columns=2).classes("w-full gap-2"):
            with ui.column().classes("col-span-2"):
                with ui.row().classes("items-center gap-2 w-full"):
                    audio_inp = ui.input("Audio file path").classes("flex-1").bind_value(
                        app_state, "audio_path"
                    )
                    async def _pick_audio():
                        path = await _pick_file(
                            "Select Audio File",
                            [("Audio files", "*.mp3 *.wav *.m4a *.ogg *.flac"), ("All files", "*.*")],
                            initial_dir=app_state.workspace,
                        )
                        if path:
                            app_state.audio_path = path
                            audio_inp.value = path
                            name = os.path.splitext(os.path.basename(path))[0]
                            tx = auto_detect_transcript(app_state.workspace, name)
                            if tx:
                                app_state.tx_auto = False
                                app_state.transcript_path = tx
                                ui.notify(f"Auto-detected transcript: {os.path.basename(tx)}", type="positive")
                    ui.button(icon="folder_open", on_click=_pick_audio).props("flat round").tooltip("Browse for audio file")

            ui.label("Project Name (set by project selector)").classes("text-sm text-grey col-span-2")

            with ui.column().classes("col-span-2"):
                ai_sel = ui.select(
                    AI_MODELS, label="AI Image Model",
                    value=app_state.ai_model
                ).classes("w-full")
                ai_sel.on("update:model-value", lambda e: (
                    setattr(app_state, "ai_model", _sel(e.args)),
                    gui_state.__setitem__("ai_model", _sel(e.args)),
                ))

    # ── Transcript ──────────────────────────────────────────────────────────
    with ui.card().classes("w-full mb-3"):
        ui.label("Transcript").classes("text-subtitle1 font-semibold")
        tx_toggle = ui.toggle(
            {True: "Auto-transcribe (Whisper)", False: "Use existing JSON"},
            value=app_state.tx_auto
        )
        tx_toggle.on("update:model-value", lambda e: setattr(app_state, "tx_auto", e.args))
        with ui.row().classes("items-center gap-2 w-full mt-2") as tx_row:
            tx_inp = ui.input("Transcript JSON path").classes("flex-1").bind_value(
                app_state, "transcript_path"
            )
            async def _pick_transcript():
                path = await _pick_file(
                    "Select Transcript JSON",
                    [("JSON files", "*.json"), ("All files", "*.*")],
                    initial_dir=os.path.join(app_state.workspace, "scripts"),
                )
                if path:
                    app_state.transcript_path = path
                    tx_inp.value = path
            ui.button(icon="folder_open", on_click=_pick_transcript).props("flat round").tooltip("Browse for transcript JSON")
        # hide row when auto
        def _update_tx_row():
            tx_row.set_visibility(not app_state.tx_auto)
        tx_toggle.on("update:model-value", lambda _: _update_tx_row())
        _update_tx_row()

    # ── LLM & Prompts ────────────────────────────────────────────────────────
    with ui.card().classes("w-full mb-3"):
        ui.label("LLM & Prompts").classes("text-subtitle1 font-semibold")
        with ui.row().classes("items-center gap-3 flex-wrap"):
            llm_cb = ui.checkbox("Generate with local LLM", value=app_state.use_llm)
            llm_cb.on("update:model-value", lambda e: setattr(app_state, "use_llm", e.args))

            prov_sel = ui.select(
                LLM_PROVIDERS, label="Provider", value=app_state.llm_provider
            ).classes("w-32")
            prov_sel.bind_visibility_from(llm_cb, "value")
            prov_sel.on("update:model-value", lambda e: (
                setattr(app_state, "llm_provider", _sel(e.args)),
                gui_state.__setitem__("llm_provider", _sel(e.args)),
                _fetch_llm_models(_sel(e.args)),
            ))

            llm_model_inp = ui.input("Model", value=app_state.llm_model).classes("w-48")
            llm_model_inp.bind_visibility_from(llm_cb, "value")
            llm_model_inp.bind_value(app_state, "llm_model")

        # Anthropic key (only when provider=claude)
        with ui.column().classes("w-full mt-2") as claude_row:
            with ui.row().classes("items-center gap-2 w-full"):
                ak_inp = ui.input("Anthropic API Key", password=True).classes("flex-1")
                ak_inp.bind_value(app_state, "anthropic_key")
                _show_key = [False]
                def _toggle_key_vis():
                    _show_key[0] = not _show_key[0]
                    ak_inp.props(f'type={"text" if _show_key[0] else "password"}')
                ui.button("Show/Hide", on_click=_toggle_key_vis).props("flat dense")
            claude_model_sel = ui.select(
                CLAUDE_MODELS, label="Claude Model", value=app_state.claude_model
            ).classes("w-64")
            claude_model_sel.on("update:model-value", lambda e: (
                setattr(app_state, "claude_model", _sel(e.args)),
                gui_state.__setitem__("claude_model", _sel(e.args)),
            ))

        def _update_claude_row():
            claude_row.set_visibility(app_state.use_llm and app_state.llm_provider == "claude")
        llm_cb.on("update:model-value", lambda _: _update_claude_row())
        prov_sel.on("update:model-value", lambda _: _update_claude_row())
        _update_claude_row()

    # ── Script File (required) ───────────────────────────────────────────────
    with ui.card().classes("w-full mb-3"):
        ui.label("Script File").classes("text-subtitle1 font-semibold")
        ui.label("Original narration script (required for segment detection)").classes("text-xs text-grey")
        with ui.row().classes("items-center gap-2 w-full"):
            script_inp = ui.input("Script file path").classes("flex-1").bind_value(
                app_state, "script_path"
            )
            script_inp.on("update:model-value", lambda e: (
                gui_state.__setitem__("script_path", e.args or ""),
            ))
            async def _pick_script():
                path = await _pick_file(
                    "Select Script File",
                    [("Text files", "*.txt"), ("All files", "*.*")],
                    initial_dir=app_state.workspace,
                )
                if path:
                    app_state.script_path = path
                    script_inp.value = path
                    gui_state["script_path"] = path
            ui.button(icon="folder_open", on_click=_pick_script).props("flat round").tooltip("Browse for script file")

    # ── Pipeline Control ──────────────────────────────────────────────────────
    with ui.card().classes("w-full mb-3"):
        ui.label("Pipeline Control").classes("text-subtitle1 font-semibold")
        with ui.row().classes("items-center gap-4 flex-wrap"):
            start_sel = ui.select(
                PIPELINE_STAGES, label="Start from",
                value=app_state.start_from
            ).classes("w-40")
            start_sel.on("update:model-value", lambda e: (
                setattr(app_state, "start_from", _sel(e.args)),
                gui_state.__setitem__("start_from", _sel(e.args)),
            ))
            stop_sel = ui.select(
                PIPELINE_STAGES, label="Stop after",
                value=app_state.stop_after
            ).classes("w-40")
            stop_sel.on("update:model-value", lambda e: (
                setattr(app_state, "stop_after", _sel(e.args)),
                gui_state.__setitem__("stop_after", _sel(e.args)),
            ))
            resume_cb = ui.checkbox("Resume from existing project", value=app_state.resume)
            resume_cb.on("update:model-value", lambda e: setattr(app_state, "resume", e.args))

    # ── Action buttons ───────────────────────────────────────────────────────
    with ui.row().classes("gap-3 mt-2 flex-wrap"):
        start_btn = ui.button("Generate", icon="play_arrow", color="positive")
        stop_btn  = ui.button("Stop", icon="stop", color="negative").props("disabled")
        regen_btn = ui.button("Regen Missing", icon="refresh", color="warning")
        corrupt_btn = ui.button("Scan Corrupt", icon="bug_report", color="purple")

        async def _start():
            if not app_state.audio_path or not os.path.exists(app_state.audio_path):
                ui.notify("Audio file not found", type="negative")
                return
            if not app_state.project_name:
                ui.notify("No project selected", type="negative")
                return
            cmd = build_command(app_state)
            print(f"[GUI] Command: {' '.join(cmd)}")
            if _log_el:
                _log_el.clear()
                _log_el.push(f"[CMD] {' '.join(cmd)}\n")
            if _progress_el:
                _progress_el.set_value(0)
            if _status_label:
                _status_label.set_text("Running...")
            start_btn.props("disabled")
            stop_btn.props(remove="disabled")
            await proc.start(cmd)
            start_btn.props(remove="disabled")
            stop_btn.props("disabled")

        def _stop():
            proc.stop()
            if _status_label:
                _status_label.set_text("Stopped")

        def _regen_missing():
            val = load_missing_scenes(app_state.workspace, app_state.project_name)
            app_state.regen_scenes = val
            if _regen_input:
                _regen_input.value = val
            ui.notify(f"Loaded: {val or 'none'}", type="info")

        async def _scan_corrupt():
            name = app_state.project_name
            ws = app_state.workspace
            images_dir = os.path.join(ws, "images", name)
            if not os.path.isdir(images_dir):
                ui.notify("Images folder not found", type="warning")
                return
            from PIL import Image as _PIL
            corrupt = []
            for fname in sorted(os.listdir(images_dir)):
                if not re.match(r"scene_\d{4}\.png", fname):
                    continue
                path = os.path.join(images_dir, fname)
                try:
                    with _PIL.open(path) as img:
                        img.load()
                except Exception:
                    idx = int(fname[6:10])
                    corrupt.append(idx + 1)
            if corrupt:
                val = ",".join(str(n) for n in corrupt)
                app_state.regen_scenes = val
                if _regen_input:
                    _regen_input.value = val
                ui.notify(f"{len(corrupt)} corrupt: {val}", type="warning")
            else:
                ui.notify("No corrupt images found", type="positive")

        start_btn.on("click", _start)
        stop_btn.on("click", _stop)
        regen_btn.on("click", _regen_missing)
        corrupt_btn.on("click", _scan_corrupt)


# ── GALLERY TAB ───────────────────────────────────────────────────────────────
_gallery_search = ""
_gallery_filter = "All"


def _build_gallery_tab():
    global _gallery_container, _gallery_summary

    with ui.row().classes("items-center gap-3 mb-3 flex-wrap"):
        _gallery_summary = ui.label("").classes("text-sm text-grey flex-1")
        search_input = ui.input(label="Search", placeholder="scene #, segment, prompt...").props(
            'dense clearable').classes("w-48")

        def _on_search(e):
            global _gallery_search
            _gallery_search = (e.value or "").strip()
            _refresh_gallery(filter_val=_gallery_filter)
        search_input.on("update:model-value", _on_search)

        filter_sel = ui.select(
            ["All", "OK", "Needs Regen"], value="All", label="Filter"
        ).classes("w-32")
        ui.button("Refresh", icon="refresh", on_click=_refresh_gallery).props("flat")
        ui.button("Regen All Missing", icon="refresh", color="warning",
                  on_click=lambda: _regen_all_missing()).props("flat")

    _gallery_container = ui.column().classes("w-full")
    _refresh_gallery()

    # Generation tasks panel (collapsible, shows live logs per regen)
    global _regen_panel, _regen_tasks_container
    with ui.expansion("Generation Tasks", icon="construction").classes(
        "w-full mt-2 text-sm"
    ) as _regen_panel:
        _regen_tasks_container = ui.column().classes("w-full gap-1")
        with _regen_tasks_container:
            ui.label("No active tasks.").classes("text-grey text-xs")

    # Timer to sync thread-generated logs to UI every second
    ui.timer(1.0, _sync_regen_ui)

    def _filter_change(e):
        global _gallery_filter
        _gallery_filter = _sel(e.args)
        _refresh_gallery(filter_val=_gallery_filter)
    filter_sel.on("update:model-value", _filter_change)


def _regen_all_missing():
    val = load_missing_scenes(app_state.workspace, app_state.project_name)
    app_state.regen_scenes = val
    if _regen_input:
        _regen_input.value = val
    ui.notify(f"Loaded {len(val.split(',')) if val else 0} scenes", type="info")


def _refresh_gallery(filter_val: str = "All"):
    if not _gallery_container:
        return
    _gallery_container.clear()
    name = app_state.project_name
    if not name:
        return

    data = scan_gallery(app_state.workspace, name)
    total = len(data)
    ok_cnt = sum(1 for v in data.values() if v["status"] == "OK")
    ph_cnt = sum(1 for v in data.values() if v["status"] == "PLACEHOLDER")
    mis_cnt = sum(1 for v in data.values() if v["status"] in ("MISSING", "CORRUPT"))

    if _gallery_summary:
        _gallery_summary.set_text(
            f"{total} scenes — {ok_cnt} OK, {ph_cnt} placeholder, {mis_cnt} missing/corrupt"
        )

    # load scene metadata for detail dialog
    pm = PromptsManager(app_state.workspace, name)
    scenes_list = pm.load()
    # Map by snum: use "scene" field if present, otherwise 1-based list index
    scenes_map = {}
    for i, sc in enumerate(scenes_list):
        key = sc.get("scene") or (i + 1)
        scenes_map[key] = sc

    # Apply status filter
    filtered = {
        snum: info for snum, info in data.items()
        if filter_val == "All"
        or (filter_val == "OK" and info["status"] == "OK")
        or (filter_val == "Needs Regen" and info["status"] != "OK")
    }

    # Apply search filter
    search = _gallery_search.lower() if _gallery_search else ""
    if search:
        search_result = {}
        for snum, info in filtered.items():
            # Match against scene number
            if search.isdigit() and int(search) == snum:
                search_result[snum] = info
                continue
            # Match against scene metadata
            sc = scenes_map.get(snum, {})
            searchable = " ".join([
                str(snum),
                sc.get("id", ""),
                sc.get("text", ""),
                sc.get("prompt", "") or "",
                sc.get("metadata", {}).get("segment_title", ""),
                f"seg{sc.get('metadata', {}).get('segment_number', '')}",
            ]).lower()
            if search in searchable:
                search_result[snum] = info
        filtered = search_result

    with _gallery_container:
        if not filtered:
            ui.label("No scenes to display.").classes("text-grey")
            return
        with ui.grid(columns=4).classes("w-full gap-3"):
            for snum, info in sorted(filtered.items()):
                _build_gallery_card(snum, info, scenes_map.get(snum, {}), name)


STATUS_COLORS = {
    "OK": "green",
    "PLACEHOLDER": "orange",
    "MISSING": "red",
    "CORRUPT": "red",
}


def _build_gallery_card(snum: int, info: dict, scene_data: dict, name: str):
    status = info["status"]
    is_generating = snum in _regen_in_flight
    color = "blue" if is_generating else STATUS_COLORS.get(status, "grey")
    display_status = "GENERATING..." if is_generating else status
    img_url = _img_url(name, info["fname"], info["path"]) if info.get("path") and os.path.exists(info["path"]) else None

    def _click_detail(_, sd=scene_data, ip=info.get("path", ""), s=snum):
        _open_detail(sd, ip, name, snum=s)

    with ui.card().classes("relative overflow-hidden hover:shadow-xl"):
        # image area — always clickable to see prompt/details
        if is_generating:
            with ui.column().classes("w-full aspect-video items-center justify-center bg-blue-50"):
                ui.spinner("dots", size="xl", color="primary")
                ui.label(f"Scene {snum}").classes("text-xs text-grey")
        elif img_url:
            img_el = ui.image(img_url).classes("w-full aspect-video object-cover cursor-pointer")
            img_el.on("click", _click_detail)
        else:
            icon_el = ui.icon("broken_image", size="4rem").classes(
                "w-full text-grey py-8 text-center block cursor-pointer")
            icon_el.on("click", _click_detail)

        # badges row
        with ui.row().classes("justify-between items-center px-1 py-1"):
            with ui.row().classes("gap-1"):
                ui.badge(str(snum), color="blue-grey").props("rounded")
                ui.badge(display_status, color=color).props("rounded")
            async def _on_regen(_, s=snum, st=status):
                await _regen_or_confirm(s, st)
            btn = ui.button(
                icon="refresh",
                on_click=_on_regen
            ).props("flat round dense").tooltip("Regenerate now")
            if is_generating:
                btn.props("disable")


def _regen_scene(snum: int):
    """Queue scene number into the regen field (Settings tab use)."""
    current = app_state.regen_scenes.strip()
    parts = [p for p in current.split(",") if p.strip()] if current else []
    if str(snum) not in parts:
        parts.append(str(snum))
    app_state.regen_scenes = ",".join(parts)
    if _regen_input:
        _regen_input.value = app_state.regen_scenes


_regen_in_flight: set[int] = set()  # scene numbers currently regenerating
_regen_tasks: dict[int, dict] = {}  # snum -> {status, logs[], log_el, row_el}
_regen_panel: object = None         # the expansion panel UI element
_regen_tasks_container: object = None
_regen_needs_gallery_refresh: bool = False  # flag for timer to trigger gallery refresh


def _regen_log(snum: int, msg: str):
    """Append a log line to a regen task (thread-safe — UI sync via timer)."""
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    task = _regen_tasks.get(snum)
    if task:
        task["logs"].append(line)
        task["_dirty"] = True


def _update_regen_task_status(snum: int, new_status: str):
    """Update status for a regen task (thread-safe — UI sync via timer)."""
    task = _regen_tasks.get(snum)
    if not task:
        return
    task["status"] = new_status
    task["_dirty"] = True


def _sync_regen_ui():
    """Periodic UI sync — pushes buffered logs, status, and gallery refresh."""
    global _regen_needs_gallery_refresh

    for snum, task in _regen_tasks.items():
        if not task.get("_dirty"):
            continue
        task["_dirty"] = False

        # Sync log lines
        log_el = task.get("log_el")
        if log_el:
            flushed = task.get("_flushed", 0)
            for line in task["logs"][flushed:]:
                log_el.push(line)
            task["_flushed"] = len(task["logs"])

        # Sync status badge
        badge = task.get("badge_el")
        if badge:
            status = task["status"]
            color = {"running": "blue", "done": "green", "failed": "red"}.get(status, "grey")
            badge.set_text(status.upper())
            badge.props(f'color="{color}"')

        # Collapse expansion on completion
        if task["status"] in ("done", "failed") and task.get("expansion_el"):
            exp = task["expansion_el"]
            if hasattr(exp, '_props') and exp._props.get('model-value', True):
                exp.close()

    # Refresh gallery when a regen finishes (runs in proper UI context)
    if _regen_needs_gallery_refresh:
        _regen_needs_gallery_refresh = False
        _refresh_gallery(filter_val=_gallery_filter)


def _build_regen_task_row(snum: int):
    """Build a UI row for a single regen task inside the panel."""
    task = _regen_tasks[snum]
    with ui.expansion(f"Scene {snum}", icon="image").classes("w-full") as exp:
        exp.open()
        task["expansion_el"] = exp
        with ui.row().classes("items-center gap-2"):
            badge = ui.badge("RUNNING", color="blue").props("rounded")
            task["badge_el"] = badge
            ui.spinner("dots", size="sm", color="primary").bind_visibility_from(
                task, "status", backward=lambda s: s == "running")
        log = ui.log(max_lines=50).classes("w-full h-32 text-xs")
        task["log_el"] = log
        # Replay any logs already captured
        for line in task["logs"]:
            log.push(line)
        task["_flushed"] = len(task["logs"])


async def _regen_or_confirm(snum: int, status: str):
    """Regenerate the scene immediately — no confirmation dialog."""
    try:
        await _regen_scene_now(snum)
    except Exception as e:
        import traceback
        traceback.print_exc()
        ui.notify(f"Regen error: {e}", type="negative", timeout=10000)


async def _regen_scene_now(snum: int):
    """Regenerate a single scene via direct API call (non-blocking, parallel-safe)."""
    if snum in _regen_in_flight:
        ui.notify(f"Scene {snum} is already regenerating", type="warning")
        return
    if not app_state.project_name:
        ui.notify("No project selected", type="negative")
        return

    # Load scene data from project JSON
    workspace = Path(app_state.workspace)
    project_path = workspace / "scripts" / f"{app_state.project_name}_project.json"
    if not project_path.exists():
        ui.notify("No project.json found", type="negative")
        return

    with open(project_path, encoding="utf-8") as f:
        project_data = json.load(f)
    scenes = project_data.get("scenes", [])
    idx = snum - 1  # snum is 1-based
    if idx < 0 or idx >= len(scenes):
        ui.notify(f"Scene {snum} out of range", type="negative")
        return

    scene = scenes[idx]
    prompt = scene.get("prompt") or ""
    if not prompt:
        ui.notify(f"Scene {snum} has no prompt — run prompt stage first", type="warning")
        return

    # Determine output path from existing image_path or construct one
    scene_id = scene.get("id", f"scene{idx:04d}")
    if scene.get("image_path"):
        out_path = workspace / scene["image_path"].replace("\\", "/")
    else:
        images_dir = workspace / "images" / app_state.project_name
        fname = f"scene_{scene_id.replace('seg', '').replace('_scene', '_')}.png"
        out_path = images_dir / fname

    # Set up task tracking
    _regen_in_flight.add(snum)
    _regen_tasks[snum] = {"status": "running", "logs": []}

    # Build the task row in the panel
    if _regen_tasks_container:
        with _regen_tasks_container:
            _build_regen_task_row(snum)
    if _regen_panel:
        _regen_panel.open()

    _regen_log(snum, f"Starting generation: {scene_id}")
    _regen_log(snum, f"Model: {app_state.ai_model}")
    _regen_log(snum, f"Output: {out_path.name}")
    _regen_log(snum, f"Prompt: {prompt[:120]}...")
    _refresh_gallery()  # show spinner on the card

    def _do_generate() -> tuple[bool, str]:
        """Blocking API call — runs in a thread."""
        import requests as _req
        import time as _time

        api_key = AI33_KEY
        model = app_state.ai_model
        base_url = "https://api.ai33.pro"
        session = _req.Session()
        session.headers.update({"xi-api-key": api_key})

        # Delete existing image
        if out_path.exists():
            out_path.unlink()

        data = {
            "prompt": prompt,
            "model_id": model,
            "generations_count": "1",
            "model_parameters": json.dumps({
                "aspect_ratio": "16:9",
                "resolution": "2K",
            }),
        }

        # Style reference
        style_dir = workspace / "style_references"
        style_ref = None
        if style_dir.exists():
            for f in style_dir.iterdir():
                if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    style_ref = str(f)
                    break

        files = []
        file_handles = []
        try:
            if style_ref and os.path.exists(style_ref):
                fh = open(style_ref, "rb")
                file_handles.append(fh)
                files.append(("assets", (os.path.basename(style_ref), fh, "image/png")))
                data["prompt"] = f"@img1 {data['prompt']}"
                _regen_log(snum, f"Using style ref: {os.path.basename(style_ref)}")

            _regen_log(snum, "Submitting to API...")
            resp = session.post(
                f"{base_url}/v1i/task/generate-image",
                data=data,
                files=files if files else None,
                timeout=120,
            )
        finally:
            for fh in file_handles:
                try:
                    fh.close()
                except Exception:
                    pass

        if resp.status_code != 200:
            try:
                msg = resp.json().get("message", resp.text[:200])
            except Exception:
                msg = resp.text[:200]
            _regen_log(snum, f"API error {resp.status_code}: {msg}")
            return False, f"API {resp.status_code}: {msg}"

        result = resp.json()
        task_id = result.get("task_id")
        if not task_id:
            _regen_log(snum, f"No task_id in response")
            return False, f"No task_id: {result}"
        _regen_log(snum, f"Task submitted: {task_id}")
        _regen_log(snum, "Polling for completion...")

        # Poll for completion (max 5 minutes, check every 4s)
        start = _time.time()
        consecutive_errors = 0
        poll_count = 0
        while (_time.time() - start) < 300:
            _time.sleep(4)
            poll_count += 1
            elapsed = int(_time.time() - start)
            try:
                sr = session.get(f"{base_url}/v1/task/{task_id}", timeout=(5, 15))
                consecutive_errors = 0
                if sr.status_code != 200:
                    _regen_log(snum, f"Poll #{poll_count} ({elapsed}s): HTTP {sr.status_code}")
                    continue
                sd = sr.json()
                status = sd.get("status", "")

                if poll_count % 3 == 0:
                    _regen_log(snum, f"Poll #{poll_count} ({elapsed}s): {status}")

                if status == "done":
                    _regen_log(snum, f"Generation complete ({elapsed}s)")
                    images = sd.get("metadata", {}).get("result_images", [])
                    if images:
                        image_url = images[0].get("imageUrl")
                        if image_url:
                            _regen_log(snum, "Downloading image...")
                            img_resp = _req.get(image_url, timeout=(5, 30))
                            if img_resp.status_code == 200:
                                out_path.parent.mkdir(parents=True, exist_ok=True)
                                out_path.write_bytes(img_resp.content)

                                # Validate
                                from PIL import Image as _PIL
                                try:
                                    with _PIL.open(out_path) as _img:
                                        _img.load()
                                except Exception:
                                    out_path.unlink(missing_ok=True)
                                    _regen_log(snum, "Downloaded image is corrupt")
                                    return False, "Downloaded image is corrupt"

                                # Apply title overlay
                                seg_title = scene.get("metadata", {}).get("segment_title", "")
                                if seg_title:
                                    try:
                                        from video_automation.generate.title_overlay import TitleBarOverlay
                                        TitleBarOverlay().apply(str(out_path), seg_title)
                                        _regen_log(snum, f"Title overlay: {seg_title}")
                                    except Exception:
                                        pass

                                _regen_log(snum, "Image saved successfully!")
                                return True, ""
                    _regen_log(snum, "No image in API result")
                    return False, "No image in result"

                elif status in ("error", "failed"):
                    err = sd.get("error_message", sd.get("message", "Unknown"))
                    _regen_log(snum, f"API {status}: {err}")
                    return False, f"Generation {status}: {err}"

            except _req.exceptions.Timeout:
                consecutive_errors += 1
                _regen_log(snum, f"Poll #{poll_count}: timeout ({consecutive_errors}/5)")
                if consecutive_errors >= 5:
                    return False, "Too many consecutive timeouts polling task status"
                continue
            except Exception as exc:
                consecutive_errors += 1
                _regen_log(snum, f"Poll #{poll_count}: error: {exc}")
                if consecutive_errors >= 5:
                    return False, f"Too many poll errors: {exc}"
                continue

        _regen_log(snum, "Timeout after 300s")
        return False, "Timeout after 300s"

    try:
        success, error = await asyncio.to_thread(_do_generate)
    except Exception as e:
        import traceback
        traceback.print_exc()
        success, error = False, str(e)
        _regen_log(snum, f"Exception: {e}")

    _regen_in_flight.discard(snum)

    if success:
        # Update project JSON status
        try:
            with open(project_path, encoding="utf-8") as f:
                pdata = json.load(f)
            pdata["scenes"][idx]["status"] = "generated"
            pdata["scenes"][idx]["image_path"] = str(out_path.relative_to(workspace))
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(pdata, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        _update_regen_task_status(snum, "done")
        _regen_log(snum, "DONE")
        ui.notify(f"Scene {snum} regenerated!", type="positive")
    else:
        # Update project JSON status to failed
        try:
            with open(project_path, encoding="utf-8") as f:
                pdata = json.load(f)
            pdata["scenes"][idx]["status"] = "failed"
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(pdata, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        _update_regen_task_status(snum, "failed")
        _regen_log(snum, f"FAILED: {error}")
        ui.notify(f"Scene {snum} failed: {error}", type="negative", timeout=10000)

    # Flag for timer to refresh gallery and update UI (ensures proper UI context)
    global _regen_needs_gallery_refresh
    _regen_needs_gallery_refresh = True


# ── IMAGE DETAIL DIALOG ───────────────────────────────────────────────────────
def _open_detail(scene_data: dict, image_path: str, name: str, snum: int = 0):
    # Resolve fields from both prompts.json and project.json formats
    scene_id = scene_data.get("id", "")
    scene_num = scene_data.get("scene") or snum
    time_str = scene_data.get("time", "")
    if not time_str and "start" in scene_data and "end" in scene_data:
        time_str = f"{scene_data['start']:.2f}s - {scene_data['end']:.2f}s"
    segment_str = scene_data.get("segment", "")
    if not segment_str:
        meta = scene_data.get("metadata", {})
        seg_num = meta.get("segment_number", "")
        seg_title = meta.get("segment_title", "")
        segment_str = f"#{seg_num}: {seg_title}" if seg_num else seg_title
    scene_text = scene_data.get("scene_text", "") or scene_data.get("text", "")
    prompt_val = scene_data.get("prompt", "") or ""
    negative_val = scene_data.get("negative", "") or ""
    prompt_src = scene_data.get("prompt_source", "unknown") or "unknown"

    with ui.dialog() as dlg:
        dlg.props("maximized")
        with ui.card().classes("w-full h-full overflow-auto"):
            with ui.row().classes("w-full gap-4"):
                # left: image
                with ui.column().classes("flex-1"):
                    if image_path and os.path.exists(image_path):
                        fname = os.path.basename(image_path)
                        ui.image(_img_url(name, fname, image_path)).classes("w-full rounded")
                    else:
                        ui.icon("broken_image", size="8rem").classes("text-grey")

                # right: metadata + editable prompt
                with ui.column().classes("w-96 gap-2"):
                    ui.label(f"Scene {scene_num}").classes("text-h6 font-bold")
                    if scene_id:
                        ui.label(scene_id).classes("text-xs text-grey-6 font-mono")
                    ui.label(f"Time: {time_str}").classes("text-sm text-grey")
                    if segment_str:
                        ui.label(f"Segment: {segment_str}").classes("text-sm")
                    if scene_text:
                        ui.label("Scene text:").classes("text-sm font-semibold mt-1")
                        ui.label(scene_text).classes("text-sm text-grey")

                    with ui.row().classes("gap-2 mt-1"):
                        ui.badge(prompt_src, color="blue")

                    ui.label("Prompt:").classes("text-sm font-semibold mt-2")
                    prompt_ta = ui.textarea(value=prompt_val).classes("w-full")
                    prompt_ta.props("rows=6")

                    ui.label("Negative prompt:").classes("text-sm font-semibold mt-1")
                    neg_ta = ui.textarea(value=negative_val).classes("w-full")
                    neg_ta.props("rows=3")

                    with ui.row().classes("gap-2 mt-2"):
                        def _save_prompt():
                            _save_scene_prompt(name, scene_num, prompt_ta.value, neg_ta.value)
                            ui.notify("Prompt saved", type="positive")

                        async def _save_and_regen():
                            _save_scene_prompt(name, scene_num, prompt_ta.value, neg_ta.value)
                            dlg.close()
                            await _regen_scene_now(int(scene_num))

                        ui.button("Save Prompt", icon="save", color="primary",
                                  on_click=_save_prompt)
                        ui.button("Save & Regen", icon="refresh", color="warning",
                                  on_click=_save_and_regen)
                        ui.button("Close", on_click=dlg.close).props("flat")
    dlg.open()


def _save_scene_prompt(name: str, snum: int, prompt: str, negative: str):
    """Save prompt to whichever file format the project uses."""
    workspace = app_state.workspace
    idx = snum - 1  # snum is 1-based

    # Try project.json first
    project_path = os.path.join(workspace, "scripts", f"{name}_project.json")
    if os.path.exists(project_path):
        with open(project_path, encoding="utf-8") as f:
            pdata = json.load(f)
        scenes = pdata.get("scenes", [])
        if 0 <= idx < len(scenes):
            scenes[idx]["prompt"] = prompt
            if negative:
                scenes[idx]["negative"] = negative
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(pdata, f, indent=2, ensure_ascii=False)
            return

    # Fall back to prompts.json
    pm = PromptsManager(workspace, name)
    pm.load()
    pm.update_scene(snum, prompt, negative)


# ── SEGMENT EDITOR TAB ────────────────────────────────────────────────────────
def _manifest_path_for_project() -> str:
    return os.path.join(app_state.workspace, "scripts",
                        f"{app_state.project_name}_segments_manifest.json")


def _find_transcript_for_editor() -> str:
    name = app_state.project_name
    ws   = app_state.workspace
    candidate = os.path.join(ws, "scripts", f"{name}_word_timestamps.json")
    return candidate if os.path.exists(candidate) else ""


def _load_manifest_segments() -> list[dict]:
    path = _manifest_path_for_project()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return sorted(data.get("segments", []), key=lambda s: s["start_time"])
    except Exception:
        return []


def _save_segments_manifest(segments: list[dict]) -> str | None:
    """Validate and write segments_manifest.json. Returns error string or None."""
    if not segments:
        return "No segments to save."
    numbers = [s["number"] for s in segments]
    if len(numbers) != len(set(numbers)):
        return "Duplicate segment numbers found."
    starts = [s["start_time"] for s in segments]
    if starts != sorted(starts):
        return "Segment start times are not in ascending order."
    os.makedirs(os.path.join(app_state.workspace, "scripts"), exist_ok=True)
    path = _manifest_path_for_project()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "segments": segments}, f, indent=2)
    return None


async def _call_claude_suggest(transcript_entries: list[dict]) -> list[dict] | str:
    """Call Claude API to suggest segment boundaries. Returns list of dicts or error string."""
    api_key = app_state.anthropic_key.strip()
    if not api_key:
        return "No Anthropic API key set. Enter it in the Generate tab."

    # Build a compact transcript with timestamps for Claude
    lines = []
    for e in transcript_entries:
        lines.append(f"[{e['start']:.2f}s] {e['text']}")
    transcript_text = "\n".join(lines)

    system_prompt = (
        "You are analyzing a video narration transcript. Identify every point where a new "
        "numbered topic begins. The narrator typically says 'Number X' followed by a topic name. "
        "Do NOT flag incidental uses of the word 'number' inside sentences. "
        "Return ONLY valid JSON array, no explanation:\n"
        '[{"number": 10, "title": "Short Topic Title", "start_time": 1.02}, ...]\n'
        "Rules:\n"
        "- title: concise, under 50 chars, Title Case\n"
        "- start_time: timestamp (seconds) of the word 'Number'\n"
        "- Only include actual numbered topics"
    )
    user_msg = f"Transcript:\n{transcript_text}"

    def _sync_call():
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=app_state.claude_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": user_msg}],
            system=system_prompt,
        )
        return msg.content[0].text

    loop = asyncio.get_event_loop()
    try:
        raw = await loop.run_in_executor(None, _sync_call)
        # Strip markdown fences if Claude wrapped it
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw.strip())
        parsed = json.loads(raw)
        for item in parsed:
            item.setdefault("card_duration", 2.5)
        return parsed
    except json.JSONDecodeError:
        return f"Claude returned invalid JSON:\n{raw[:300]}"
    except Exception as e:
        return f"Claude API error: {e}"


def _build_segments_tab():
    # ── state shared across closures ──────────────────────────────────────────
    seg_rows: list[dict] = []   # each: {number, title, start_time, card_duration}

    # ── toolbar ───────────────────────────────────────────────────────────────
    with ui.row().classes("items-center gap-3 mb-3 flex-wrap w-full"):
        status_lbl = ui.label("").classes("text-sm text-grey flex-1")

        load_btn  = ui.button("Load & Suggest", icon="auto_fix_high", color="primary")
        add_btn   = ui.button("Add Segment",    icon="add",            color="secondary")
        clear_btn = ui.button("Clear Manifest", icon="delete_forever", color="negative").props("flat")
        save_btn  = ui.button("Save Segments",  icon="save",           color="positive")

    ui.separator()

    # ── segment rows container ────────────────────────────────────────────────
    rows_col = ui.column().classes("w-full gap-2")

    # ── header row ────────────────────────────────────────────────────────────
    with ui.row().classes("w-full gap-2 text-xs text-grey font-semibold px-1"):
        ui.label("#").classes("w-12 text-center")
        ui.label("Title").classes("flex-1")
        ui.label("Start time (s)").classes("w-28 text-center")
        ui.label("Card dur (s)").classes("w-24 text-center")
        ui.label("").classes("w-8")   # delete button column

    def _refresh_status():
        path = _manifest_path_for_project()
        if os.path.exists(path):
            status_lbl.set_text(f"Manifest saved — {len(seg_rows)} segments  |  {path}")
        elif seg_rows:
            status_lbl.set_text(f"{len(seg_rows)} segments — unsaved")
        else:
            status_lbl.set_text("No manifest for this project")

    def _render_rows():
        rows_col.clear()
        with rows_col:
            for idx, seg in enumerate(seg_rows):
                _build_segment_row(idx, seg)
        _refresh_status()

    def _build_segment_row(idx: int, seg: dict):
        with ui.row().classes("w-full gap-2 items-center"):
            num_inp = ui.number(value=seg.get("number", idx + 1), min=1, max=99, step=1).classes("w-12")
            num_inp.props("dense outlined")
            num_inp.on("update:model-value", lambda e, i=idx: seg_rows[i].update({"number": int(e.args or 1)}))

            title_inp = ui.input(value=seg.get("title", "")).classes("flex-1")
            title_inp.props("dense outlined")
            title_inp.on("update:model-value", lambda e, i=idx: seg_rows[i].update({"title": e.args or ""}))

            start_inp = ui.number(value=seg.get("start_time", 0.0), min=0, step=0.01, format="%.2f").classes("w-28")
            start_inp.props("dense outlined")
            start_inp.on("update:model-value", lambda e, i=idx: seg_rows[i].update({"start_time": float(e.args or 0)}))

            card_inp = ui.number(value=seg.get("card_duration", 2.5), min=0.5, max=10, step=0.5, format="%.1f").classes("w-24")
            card_inp.props("dense outlined")
            card_inp.on("update:model-value", lambda e, i=idx: seg_rows[i].update({"card_duration": float(e.args or 2.5)}))

            def _delete(i=idx):
                seg_rows.pop(i)
                _render_rows()
            ui.button(icon="close", on_click=_delete).props("flat round dense color=negative").classes("w-8")

    # ── populate from manifest if it exists ───────────────────────────────────
    existing = _load_manifest_segments()
    if existing:
        seg_rows.extend(existing)
    _render_rows()

    # ── button handlers ───────────────────────────────────────────────────────
    async def _on_load_suggest():
        tx_path = _find_transcript_for_editor()
        if not tx_path:
            ui.notify("No transcript found for this project. Run transcription first.", type="warning")
            return

        load_btn.props("loading disabled")
        status_lbl.set_text("Calling Claude to suggest segments...")

        try:
            with open(tx_path, encoding="utf-8") as f:
                tx_data = json.load(f)
            entries = tx_data.get("entries", [])
            if not entries:
                ui.notify("Transcript has no word entries.", type="warning")
                return

            result = await _call_claude_suggest(entries)

            if isinstance(result, str):
                ui.notify(result, type="negative", timeout=8000)
                status_lbl.set_text("Suggestion failed.")
                return

            seg_rows.clear()
            seg_rows.extend(result)
            _render_rows()
            ui.notify(f"Claude suggested {len(result)} segments — review and save.", type="positive")

        except Exception as e:
            ui.notify(f"Error: {e}", type="negative")
            status_lbl.set_text("Error during suggestion.")
        finally:
            load_btn.props(remove="loading disabled")

    def _on_add():
        last_start = seg_rows[-1]["start_time"] + 30.0 if seg_rows else 0.0
        seg_rows.append({"number": len(seg_rows) + 1, "title": "", "start_time": last_start, "card_duration": 2.5})
        _render_rows()

    def _on_clear():
        path = _manifest_path_for_project()
        if os.path.exists(path):
            os.remove(path)
        seg_rows.clear()
        _render_rows()
        ui.notify("Manifest cleared.", type="info")

    def _on_save():
        err = _save_segments_manifest(seg_rows)
        if err:
            ui.notify(err, type="negative")
        else:
            ui.notify(f"Saved {len(seg_rows)} segments to manifest.", type="positive")
            _refresh_status()

    load_btn.on("click",  lambda: asyncio.ensure_future(_on_load_suggest()))
    add_btn.on("click",   _on_add)
    clear_btn.on("click", _on_clear)
    save_btn.on("click",  _on_save)


def _build_assign_segment_ui(pm: PromptsManager, reload_fn):
    """Standalone UI for assigning a segment label to a time range. No closure issues."""
    with ui.expansion("Assign Segment to Range", icon="label").classes("w-full mb-2"):
        with ui.card().classes("w-full gap-2 pa-3"):
            with ui.row().classes("items-end gap-3 w-full"):
                start_inp = ui.number("Start (s)", value=0.0, min=0, step=0.1,
                                      format="%.2f").classes("w-32")
                end_inp   = ui.number("End (s)", value=0.0, min=0, step=0.1,
                                      format="%.2f").classes("w-32")
                label_inp = ui.input("Segment label (e.g. #10 Kepler-20E)").classes("flex-1")
                label_inp.props("dense")

            preview_lbl = ui.label("").classes("text-xs text-grey")

            def do_preview():
                s = float(start_inp.value or 0)
                e = float(end_inp.value or 0)
                if e <= s:
                    preview_lbl.set_text("")
                    return
                hits = []
                for sc in pm.scenes:
                    p = pm._parse_time(sc.get("time", ""))
                    if not p:
                        continue
                    sc_s, sc_e = p
                    if sc_e > s and sc_s < e:
                        sn = sc.get("scene", "?")
                        cur = sc.get("segment", "")
                        hits.append(f"Scene {sn} ({sc.get('time','')}) [{cur}]")
                if hits:
                    txt = f"{len(hits)} scenes: " + " | ".join(hits[:10])
                    if len(hits) > 10:
                        txt += f" ... +{len(hits)-10} more"
                    preview_lbl.set_text(txt)
                else:
                    preview_lbl.set_text("No scenes in this range")

            start_inp.on("update:model-value", lambda _: do_preview())
            end_inp.on("update:model-value", lambda _: do_preview())

            def do_apply():
                s = float(start_inp.value or 0)
                e = float(end_inp.value or 0)
                lbl = (label_inp.value or "").strip()
                if not lbl:
                    ui.notify("Segment label is required", type="warning")
                    return
                count, err = pm.assign_segment_to_range(s, e, lbl)
                if err:
                    ui.notify(err, type="negative")
                else:
                    ui.notify(f"Assigned '{lbl}' to {count} scenes", type="positive")
                    reload_fn()

            ui.button("Apply", icon="check", color="primary",
                      on_click=lambda: do_apply()).classes("mt-1")


_SEG_COLORS = [
    "#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4",
    "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6", "#f43f5e",
    "#a855f7", "#64748b", "#d946ef", "#0ea5e9", "#84cc16",
]


def _build_timeline_ui(pm: PromptsManager, reload_fn):
    """Visual timeline — pure HTML/CSS/JS rendered in a single ui.html block."""
    scenes = pm.scenes
    if not scenes:
        return

    parsed = []
    for sc in scenes:
        t = pm._parse_time(sc.get("time", ""))
        if t:
            parsed.append({
                "scene": sc.get("scene", 0),
                "start": t[0], "end": t[1],
                "segment": sc.get("segment", ""),
                "text": (sc.get("scene_text", "") or "")[:40],
            })
    if not parsed:
        return

    total_start = parsed[0]["start"]
    total_end = parsed[-1]["end"]
    total_dur = total_end - total_start
    if total_dur <= 0:
        return

    seg_names = list(dict.fromkeys(p["segment"] for p in parsed))
    seg_color = {name: _SEG_COLORS[i % len(_SEG_COLORS)] for i, name in enumerate(seg_names)}

    import html as html_mod
    scenes_json = json.dumps(parsed)

    # Build legend HTML
    legend_parts = []
    for seg_name in seg_names:
        c = seg_color[seg_name]
        display = html_mod.escape(seg_name) if seg_name else "(no segment)"
        legend_parts.append(
            f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:8px">'
            f'<span style="width:12px;height:12px;background:{c};border-radius:2px;display:inline-block"></span>'
            f'<span style="font-size:11px">{display}</span></span>'
        )
    legend_html = "".join(legend_parts)

    # Build block HTML
    blocks = []
    for p in parsed:
        c = seg_color.get(p["segment"], "#666")
        dur = p["end"] - p["start"]
        pct = (dur / total_dur) * 100
        left = ((p["start"] - total_start) / total_dur) * 100
        snum = p["scene"]
        label = str(snum) if pct > 2.5 else ""
        tip = html_mod.escape(f"Scene {snum} | {p['start']:.1f}s-{p['end']:.1f}s | {dur:.1f}s | {p['segment']} | {p['text']}")
        blocks.append(
            f'<div class="tl-block" data-scene="{snum}" data-start="{p["start"]}" '
            f'data-end="{p["end"]}" data-seg="{html_mod.escape(p["segment"])}" '
            f'title="{tip}" '
            f'style="left:{left:.3f}%;width:{pct:.3f}%;background:{c}">'
            f'{label}</div>'
        )
    blocks_html = "".join(blocks)

    mid = (total_start + total_end) / 2

    timeline_html = f'''
    <style>
      .tl-wrap {{ font-family: sans-serif; }}
      .tl-legend {{ margin-bottom: 6px; display: flex; flex-wrap: wrap; }}
      .tl-bar {{ position: relative; height: 52px; border: 1px solid #555; border-radius: 4px;
                 overflow: hidden; user-select: none; cursor: pointer; background: #1a1a2e; }}
      .tl-block {{ position: absolute; height: 100%; display: flex; align-items: center;
                   justify-content: center; font-size: 10px; color: white; box-sizing: border-box;
                   border-right: 1px solid rgba(0,0,0,0.3); cursor: pointer;
                   text-shadow: 0 0 3px rgba(0,0,0,0.9); transition: opacity 0.12s, outline 0.12s; }}
      .tl-block:hover {{ outline: 2px solid white; outline-offset: -2px; z-index: 2; }}
      .tl-block.selected {{ outline: 2px solid #fff; outline-offset: -2px; opacity: 1 !important; z-index: 1; }}
      .tl-block.dimmed {{ opacity: 0.35; }}
      .tl-axis {{ display: flex; justify-content: space-between; font-size: 10px; color: #888; margin-top: 2px; }}
      .tl-info {{ font-size: 12px; color: #aaa; margin-top: 4px; min-height: 18px; }}
      .tl-controls {{ display: flex; align-items: center; gap: 8px; margin-top: 6px; }}
      .tl-input {{ flex: 1; padding: 4px 8px; border: 1px solid #555; border-radius: 4px;
                   background: #222; color: #eee; font-size: 13px; }}
      .tl-btn {{ padding: 5px 14px; border: none; border-radius: 4px; cursor: pointer;
                 font-size: 12px; font-weight: 600; }}
      .tl-btn-primary {{ background: #3b82f6; color: white; }}
      .tl-btn-primary:hover {{ background: #2563eb; }}
      .tl-btn-flat {{ background: transparent; color: #aaa; border: 1px solid #555; }}
      .tl-btn-flat:hover {{ background: #333; }}
    </style>
    <div class="tl-wrap" id="timeline-root">
      <div class="tl-legend">{legend_html}</div>
      <div class="tl-bar" id="tl-bar">{blocks_html}</div>
      <div class="tl-axis">
        <span>{total_start:.1f}s</span><span>{mid:.1f}s</span><span>{total_end:.1f}s</span>
      </div>
      <div class="tl-info" id="tl-info">Click a scene to select start, click another for end</div>
      <div class="tl-controls">
        <input class="tl-input" id="tl-label-input" placeholder="Segment label (e.g. #10 Kepler-20E)" />
        <button class="tl-btn tl-btn-primary" id="tl-assign-btn">Assign Segment</button>
        <button class="tl-btn tl-btn-flat" id="tl-clear-btn">Clear</button>
      </div>
    </div>
    '''

    timeline_script = '''
    <script>
    (function() {
      const root = document.getElementById('timeline-root');
      if (!root) return;
      let selStart = null, selEnd = null;
      const blocks = root.querySelectorAll('.tl-block');
      const info = document.getElementById('tl-info');
      const labelInp = document.getElementById('tl-label-input');

      function updateVisual() {
        blocks.forEach(b => {
          b.classList.remove('selected', 'dimmed');
          if (selStart !== null && selEnd !== null) {
            const bs = parseFloat(b.dataset.start);
            const be = parseFloat(b.dataset.end);
            if (bs >= selStart && be <= selEnd) {
              b.classList.add('selected');
            } else {
              b.classList.add('dimmed');
            }
          }
        });
      }

      blocks.forEach(b => {
        b.addEventListener('click', () => {
          const bs = parseFloat(b.dataset.start);
          const be = parseFloat(b.dataset.end);
          const sn = b.dataset.scene;
          if (selStart === null) {
            selStart = bs;
            selEnd = be;
            info.textContent = 'Start: Scene ' + sn + ' (' + bs.toFixed(1) + 's). Click another scene for end.';
          } else {
            selStart = Math.min(selStart, bs);
            selEnd = Math.max(selEnd, be);
            let count = 0;
            blocks.forEach(bb => {
              const s2 = parseFloat(bb.dataset.start);
              const e2 = parseFloat(bb.dataset.end);
              if (e2 > selStart && s2 < selEnd) count++;
            });
            info.textContent = 'Range: ' + selStart.toFixed(1) + 's - ' + selEnd.toFixed(1) + 's (' + count + ' scenes)';
            const segs = new Set();
            blocks.forEach(bb => {
              const s2 = parseFloat(bb.dataset.start);
              const e2 = parseFloat(bb.dataset.end);
              if (e2 > selStart && s2 < selEnd) segs.add(bb.dataset.seg);
            });
            if (segs.size === 1) labelInp.value = [...segs][0];
          }
          updateVisual();
        });
      });

      document.getElementById('tl-clear-btn').addEventListener('click', () => {
        selStart = null; selEnd = null;
        info.textContent = 'Selection cleared. Click a scene to start.';
        labelInp.value = '';
        updateVisual();
      });

      document.getElementById('tl-assign-btn').addEventListener('click', () => {
        if (selStart === null || selEnd === null) {
          info.textContent = 'Select a range first!';
          return;
        }
        const lbl = labelInp.value.trim();
        if (!lbl) {
          info.textContent = 'Enter a segment label first!';
          return;
        }
        emitEvent('timeline_assign', {start: selStart, end: selEnd, label: lbl});
      });
    })();
    </script>
    '''

    ui.html(timeline_html)
    ui.add_body_html(timeline_script)

    def on_assign(e):
        data = e.args
        lbl = data.get("label", "").strip()
        s = float(data.get("start", 0))
        end = float(data.get("end", 0))
        if not lbl or end <= s:
            ui.notify("Invalid selection", type="warning")
            return
        count, err = pm.assign_segment_to_range(s, end, lbl)
        if err:
            ui.notify(err, type="negative")
        else:
            ui.notify(f"Assigned '{lbl}' to {count} scenes", type="positive")
            reload_fn()

    ui.on("timeline_assign", on_assign)


# ── PROMPTS EDITOR TAB ────────────────────────────────────────────────────────
def _build_prompts_tab():
    global _prompts_reload
    pm_ref: list[PromptsManager] = []
    dirty: dict[int, dict] = {}

    with ui.row().classes("items-center gap-3 mb-3 w-full"):
        search = ui.input("Search prompts...").classes("flex-1")
        ui.button("Save All Changes", icon="save", color="primary",
                  on_click=lambda: _bulk_save(pm_ref, dirty))
        ui.button("Reload", icon="refresh",
                  on_click=lambda: _load_prompts_editor(pm_ref, dirty, cont, search))

    cont = ui.column().classes("w-full")
    _load_prompts_editor(pm_ref, dirty, cont, search)
    search.on("update:model-value", lambda _: _load_prompts_editor(pm_ref, dirty, cont, search))

    # Store reload closure so tab-change handler can trigger it
    _prompts_reload = lambda: _load_prompts_editor(pm_ref, dirty, cont, search)


def _load_prompts_editor(pm_ref, dirty, cont, search_el):
    pm = PromptsManager(app_state.workspace, app_state.project_name)
    pm.load()
    pm_ref.clear()
    pm_ref.append(pm)
    cont.clear()
    kw = (search_el.value or "").lower().strip()

    def _reload():
        _load_prompts_editor(pm_ref, dirty, cont, search_el)

    def _open_insert_card_dialog(snum: int):
        """Open a dialog to insert a number card before scene snum."""
        with ui.dialog() as dlg, ui.card().classes("w-96 gap-3"):
            ui.label(f"Insert Number Card Before Scene {snum}").classes("text-subtitle1 font-semibold")
            num_inp  = ui.number("Number (e.g. 9)", value=1, min=1, max=99, step=1).classes("w-full")
            title_inp = ui.input("Title (e.g. Titan's Methane Seas)").classes("w-full")
            dur_inp  = ui.number("Card duration (seconds)", value=2.5, min=0.5, max=10, step=0.5).classes("w-full")
            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                def _confirm():
                    n   = int(num_inp.value or 1)
                    ttl = title_inp.value.strip() or f"Segment {n}"
                    dur = float(dur_inp.value or 2.5)
                    err = pm.insert_number_card(snum, n, ttl, dur)
                    dlg.close()
                    if err:
                        ui.notify(err, type="negative")
                    else:
                        ui.notify(f"Number card #{n} inserted before scene {snum}", type="positive")
                        _reload()
                ui.button("Insert", color="primary", on_click=_confirm)
        dlg.open()

    with cont:
        scenes = pm.scenes
        if not scenes:
            ui.label("No prompts file found for this project.").classes("text-grey")
            return

        # ── Visual Timeline ───────────────────────────────────────────
        _build_timeline_ui(pm, _reload)

        # ── Insert Prompt at Time Range ───────────────────────────────
        with ui.expansion("Insert / Replace Prompt at Time Range", icon="schedule").classes("w-full mb-2"):
            with ui.card().classes("w-full gap-2 pa-3"):
                with ui.row().classes("items-end gap-3 w-full"):
                    range_start = ui.number("Start (s)", value=0.0, min=0, step=0.1,
                                            format="%.2f").classes("w-32")
                    range_end   = ui.number("End (s)", value=0.0, min=0, step=0.1,
                                            format="%.2f").classes("w-32")
                    range_seg   = ui.input("Segment (optional)").classes("flex-1")
                    range_seg.props("dense")
                range_text = ui.input("Scene text (spoken words, optional)").classes("w-full")
                range_text.props("dense")
                range_prompt = ui.textarea("Prompt").classes("w-full")
                range_prompt.props("rows=4")
                range_neg = ui.textarea("Negative prompt (optional)").classes("w-full")
                range_neg.props("rows=2")

                # Preview which scenes will be affected
                preview_label = ui.label("").classes("text-xs text-grey")

                def _update_preview():
                    s, e = float(range_start.value or 0), float(range_end.value or 0)
                    if e <= s:
                        preview_label.set_text("")
                        return
                    affected = []
                    for sc in pm.scenes:
                        p = pm._parse_time(sc.get("time", ""))
                        if not p:
                            continue
                        sc_s, sc_e = p
                        if sc_e > s and sc_s < e:
                            sn = sc.get("scene", "?")
                            if sc_s >= s and sc_e <= e:
                                affected.append(f"Scene {sn} ({sc.get('time','')}) — REPLACED")
                            else:
                                affected.append(f"Scene {sn} ({sc.get('time','')}) — trimmed")
                    if affected:
                        preview_label.set_text("Affected: " + " | ".join(affected))
                    else:
                        preview_label.set_text("No existing scenes in this range (will insert)")

                range_start.on("update:model-value", lambda _: _update_preview())
                range_end.on("update:model-value", lambda _: _update_preview())

                def _apply_range():
                    s = float(range_start.value or 0)
                    e = float(range_end.value or 0)
                    p = (range_prompt.value or "").strip()
                    if not p:
                        ui.notify("Prompt is required", type="warning")
                        return
                    err = pm.set_prompt_at_range(
                        start=s, end=e, prompt=p,
                        segment=(range_seg.value or "").strip(),
                        scene_text=(range_text.value or "").strip(),
                        negative=(range_neg.value or "").strip(),
                    )
                    if err:
                        ui.notify(err, type="negative")
                    else:
                        ui.notify(f"Prompt set at {s:.2f}s - {e:.2f}s", type="positive")
                        _reload()

                ui.button("Apply", icon="check", color="primary",
                          on_click=_apply_range).classes("mt-1")

        # ── Assign Segment to Range ──────────────────────────────────
        _build_assign_segment_ui(pm, _reload)

        # Group scenes by segment value (preserving order of first appearance)
        groups: list[tuple[str, list[dict]]] = []
        seen_segs: dict[str, int] = {}
        for sc in scenes:
            seg = sc.get("segment", "(no segment)")
            if seg not in seen_segs:
                seen_segs[seg] = len(groups)
                groups.append((seg, []))
            groups[seen_segs[seg]][1].append(sc)

        for seg_label, seg_scenes in groups:
            # Filter to keyword match
            visible = [
                sc for sc in seg_scenes
                if not kw
                or kw in sc.get("prompt", "").lower()
                or kw in sc.get("scene_text", "").lower()
                or kw in seg_label.lower()
            ]
            if not visible:
                continue

            # ── Segment header card ────────────────────────────────────────
            with ui.card().classes("w-full mb-1 px-3 py-2 bg-blue-grey-1"):
                with ui.row().classes("items-center gap-2 w-full"):
                    seg_inp = ui.input(value=seg_label).classes("flex-1 text-sm font-semibold")
                    seg_inp.props("dense outlined")
                    def _update_seg(old=seg_label, inp=seg_inp):
                        new_val = inp.value.strip()
                        if new_val and new_val != old:
                            pm.update_segment_title(old, new_val)
                            ui.notify(f"Segment title updated", type="positive")
                            _reload()
                    ui.button("Update All", icon="label", on_click=_update_seg).props("flat dense color=primary")

            # ── Scene rows ────────────────────────────────────────────────
            for sc in visible:
                snum  = sc.get("scene", 0)
                ptext = sc.get("prompt", "")
                ntext = sc.get("negative", "")
                stxt  = sc.get("scene_text", "")
                is_card = sc.get("type") == "number_card"
                header = f"Scene {snum}  |  {sc.get('time','')}  |  {stxt[:60]}"
                if is_card:
                    header = "🔢 " + header

                with ui.expansion(header).classes("w-full"):
                    if not is_card:
                        # Editable prompt + negative for content scenes
                        p_ta = ui.textarea(label="Prompt", value=ptext).classes("w-full")
                        p_ta.props("rows=5")
                        n_ta = ui.textarea(label="Negative", value=ntext).classes("w-full mt-1")
                        n_ta.props("rows=2")
                        def _save_one(s=snum, pt=p_ta, nt=n_ta):
                            pm.update_scene(s, pt.value, nt.value)
                            dirty.pop(s, None)
                            ui.notify(f"Scene {s} saved", type="positive")
                        p_ta.on("update:model-value",
                                lambda _, s=snum, pt=p_ta, nt=n_ta: dirty.update({s: {"prompt": pt.value, "negative": nt.value}}))
                        with ui.row().classes("gap-2 mt-1"):
                            ui.button("Save", icon="save", on_click=_save_one).props("flat dense")
                            ui.button("Insert Card Before", icon="add_box",
                                      on_click=lambda _, s=snum: _open_insert_card_dialog(s)
                                      ).props("flat dense color=secondary")
                    else:
                        # Number card: show info only, allow deletion
                        ui.label(f"Number card — {sc.get('time','')}").classes("text-sm text-grey")
                        def _delete_card(s=snum):
                            idx = next((i for i, x in enumerate(pm.scenes) if x.get("scene") == s), None)
                            if idx is not None:
                                pm.scenes.pop(idx)
                                # Renumber everything after
                                for x in pm.scenes[idx:]:
                                    x["scene"] = x.get("scene", 1) - 1
                                pm.save()
                                ui.notify(f"Card deleted, scenes renumbered", type="info")
                                _reload()
                        ui.button("Delete Card", icon="delete", color="negative",
                                  on_click=_delete_card).props("flat dense")


def _bulk_save(pm_ref, dirty):
    if not pm_ref:
        ui.notify("Load prompts first", type="warning")
        return
    pm = pm_ref[0]
    for snum, vals in dirty.items():
        pm.update_scene(snum, vals.get("prompt", ""), vals.get("negative", ""))
    dirty.clear()
    ui.notify("All changes saved", type="positive")


# ── SETTINGS TAB ─────────────────────────────────────────────────────────────
def _build_settings_tab():
    global _regen_input

    # Visual Effects
    with ui.card().classes("w-full mb-3"):
        ui.label("Visual Effects").classes("text-subtitle1 font-semibold")
        kb = ui.checkbox("Ken Burns zoom/pan", value=app_state.ken_burns)
        kb.on("update:model-value", lambda e: (
            setattr(app_state, "ken_burns", e.args),
            gui_state.__setitem__("ken_burns", e.args),
        ))
        cf = ui.checkbox("Crossfade dissolve", value=app_state.crossfade)
        cf.on("update:model-value", lambda e: (
            setattr(app_state, "crossfade", e.args),
            gui_state.__setitem__("crossfade", e.args),
        ))
        with ui.row().classes("items-center gap-2 mt-1"):
            ui.label("Fade duration (s):").classes("text-sm")
            cf_dur = ui.input(value=app_state.crossfade_duration).classes("w-20")
            cf_dur.bind_visibility_from(cf, "value")
            cf_dur.on("update:model-value", lambda e: (
                setattr(app_state, "crossfade_duration", e.args),
                gui_state.__setitem__("crossfade_duration", e.args),
            ))

    # Workers
    with ui.card().classes("w-full mb-3"):
        ui.label("Workers").classes("text-subtitle1 font-semibold")
        with ui.row().classes("items-center gap-4"):
            ui.label("Image gen workers").classes("text-sm")
            iw = ui.number(value=int(app_state.image_workers), min=1, max=20, step=1).classes("w-24")
            def _update_workers(e):
                val = int(e.args) if e.args is not None else 10
                val = max(1, min(20, val))
                app_state.image_workers = str(val)
                gui_state["image_workers"] = str(val)
            iw.on("update:model-value", _update_workers)

    # Duplicate Detection
    with ui.card().classes("w-full mb-3"):
        ui.label("Duplicate Detection").classes("text-subtitle1 font-semibold")
        fd = ui.checkbox("Find & regen duplicate images", value=app_state.find_dupes)
        fd.on("update:model-value", lambda e: (
            setattr(app_state, "find_dupes", e.args),
            gui_state.__setitem__("find_dupes", e.args),
        ))
        with ui.row().classes("items-center gap-2 mt-1"):
            ui.label("Threshold (lower = stricter):").classes("text-sm")
            dt = ui.number(value=int(app_state.dupe_threshold), min=1, max=30, step=1).classes("w-20")
            dt.bind_visibility_from(fd, "value")
            dt.on("update:model-value", lambda e: (
                setattr(app_state, "dupe_threshold", str(int(e.args or 10))),
                gui_state.__setitem__("dupe_threshold", str(int(e.args or 10))),
            ))

    # Regen scenes (shared with gallery)
    with ui.card().classes("w-full mb-3"):
        ui.label("Re-generate Scenes").classes("text-subtitle1 font-semibold")
        ui.label("Comma-separated 1-based scene numbers").classes("text-xs text-grey")
        _regen_input = ui.input(
            placeholder="e.g. 5,12,18",
            value=app_state.regen_scenes
        ).classes("w-full")
        _regen_input.on("update:model-value", lambda e: setattr(app_state, "regen_scenes", e.args or ""))

    # Recompile button
    with ui.row().classes("mt-2"):
        async def _recompile():
            import copy
            s = copy.copy(app_state)
            s.start_from = "compile"
            s.stop_after = "compile"
            s.resume = True
            cmd = build_command(s)
            if _log_el:
                _log_el.clear()
            if _status_label:
                _status_label.set_text("Compiling...")
            await proc.start(cmd)
        ui.button("Recompile Video", icon="movie", color="secondary",
                  on_click=_recompile)


# ── LOG TAB ───────────────────────────────────────────────────────────────────
def _build_log_tab():
    global _log_el, _progress_el

    with ui.row().classes("items-center gap-2 mb-2"):
        ui.button("Clear", icon="delete", on_click=lambda: _log_el.clear() if _log_el else None).props("flat dense")
        ui.button("Copy All", icon="content_copy",
                  on_click=lambda: ui.run_javascript(
                      "navigator.clipboard.writeText(document.querySelector('.nicegui-log').innerText)"
                  )).props("flat dense")

    _progress_el = ui.linear_progress(value=0).classes("w-full mb-2")
    _log_el = ui.log(max_lines=2000).classes("w-full h-96 font-mono text-xs")


# ── LLM model fetcher ─────────────────────────────────────────────────────────
def _fetch_llm_models(provider: str):
    async def _do():
        try:
            if provider == "ollama":
                url = "http://localhost:11434/api/tags"
            else:
                url = "http://localhost:1234/v1/models"
            loop = asyncio.get_event_loop()
            def _req():
                with urllib.request.urlopen(url, timeout=4) as r:
                    return json.loads(r.read())
            data = await loop.run_in_executor(None, _req)
            if provider == "ollama":
                models = [m["name"] for m in data.get("models", [])]
            else:
                models = [m["id"] for m in data.get("data", [])]
            if models:
                app_state.llm_model = models[0]
                ui.notify(f"{len(models)} model(s) found", type="positive")
        except Exception:
            ui.notify(f"{provider} not reachable — type model name manually", type="warning")
    asyncio.ensure_future(_do())


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ui.run(
        title="Video Automation",
        port=8080,
        reload=False,
        dark=gui_state["dark_mode"],
        favicon="🎬",
        reconnect_timeout=30,
    )
