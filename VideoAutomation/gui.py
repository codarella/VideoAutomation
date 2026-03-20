import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import subprocess
import threading
import os
import sys
import re
import urllib.request
import json as _json

PYTHON = sys.executable

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

AI33_KEY = "sk_ixdn5l6ymkwlnetx4dzrlaehlncwo3r2sy0v8igpjzpsjlrx"
DEFAULT_WORKSPACE = os.path.join(os.path.dirname(__file__), "..", "video_workspace")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Video Automation")
        self.resizable(False, False)
        self._proc = None
        self._build()

    def _build(self):
        pad = {"padx": 8, "pady": 2}

        # ── CORE ─────────────────────────────────────────────────────
        core = ttk.LabelFrame(self, text="  Core  ", padding=6)
        core.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 3))

        tk.Label(core, text="Audio File:").grid(row=0, column=0, sticky="e", **pad)
        self.audio_var = tk.StringVar()
        tk.Entry(core, textvariable=self.audio_var, width=52).grid(row=0, column=1, **pad)
        tk.Button(core, text="Browse", command=self._pick_audio).grid(row=0, column=2, **pad)

        tk.Label(core, text="Output Name:").grid(row=1, column=0, sticky="e", **pad)
        self.name_var = tk.StringVar()
        tk.Entry(core, textvariable=self.name_var, width=52).grid(row=1, column=1, **pad)

        tk.Label(core, text="Workspace:").grid(row=2, column=0, sticky="e", **pad)
        self.workspace_var = tk.StringVar(value=os.path.normpath(DEFAULT_WORKSPACE))
        tk.Entry(core, textvariable=self.workspace_var, width=52).grid(row=2, column=1, **pad)
        tk.Button(core, text="Browse", command=self._pick_workspace).grid(row=2, column=2, **pad)

        tk.Label(core, text="AI Image Model:").grid(row=3, column=0, sticky="e", **pad)
        self.model_var = tk.StringVar(value=AI_MODELS[0])
        ttk.Combobox(core, textvariable=self.model_var, values=AI_MODELS,
                     width=49, state="readonly").grid(row=3, column=1, **pad)

        # ── TRANSCRIPT ───────────────────────────────────────────────
        tx = ttk.LabelFrame(self, text="  Transcript  ", padding=6)
        tx.grid(row=1, column=0, sticky="ew", padx=10, pady=3)

        self.tx_auto_var = tk.BooleanVar(value=True)
        tk.Radiobutton(tx, text="Auto-transcribe (Faster-Whisper)",
                       variable=self.tx_auto_var, value=True,
                       command=self._toggle_transcript).grid(row=0, column=0, sticky="w", **pad)
        tk.Radiobutton(tx, text="Use existing JSON:",
                       variable=self.tx_auto_var, value=False,
                       command=self._toggle_transcript).grid(row=0, column=1, sticky="w", **pad)
        self.transcript_var = tk.StringVar()
        self.transcript_entry = tk.Entry(tx, textvariable=self.transcript_var,
                                         width=32, state="disabled")
        self.transcript_entry.grid(row=0, column=2, **pad)
        self.transcript_btn = tk.Button(tx, text="Browse",
                                        command=self._pick_transcript, state="disabled")
        self.transcript_btn.grid(row=0, column=3, **pad)

        # ── NOTEBOOK ─────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.grid(row=2, column=0, sticky="ew", padx=10, pady=3)

        # ── Tab 1: LLM & Prompts ─────────────────────────────────────
        t1 = ttk.Frame(nb, padding=6)
        nb.add(t1, text="  LLM & Prompts  ")

        self.use_llm_var = tk.BooleanVar(value=False)
        self.use_llm_cb = tk.Checkbutton(t1, text="Generate with local LLM",
                       variable=self.use_llm_var, command=self._toggle_llm)
        self.use_llm_cb.grid(row=0, column=0, sticky="w", **pad)

        tk.Label(t1, text="Provider:").grid(row=0, column=1, sticky="e", **pad)
        self.llm_provider_var = tk.StringVar(value="ollama")
        self.llm_provider_cb = ttk.Combobox(t1, textvariable=self.llm_provider_var,
                                             values=LLM_PROVIDERS, width=11, state="disabled")
        self.llm_provider_cb.grid(row=0, column=2, sticky="w", **pad)
        self.llm_provider_cb.bind("<<ComboboxSelected>>", lambda _: self._on_provider_change())

        tk.Label(t1, text="Model:").grid(row=0, column=3, sticky="e", **pad)
        self.llm_model_var = tk.StringVar(value="qwen2.5:3b")
        self.llm_model_cb = ttk.Combobox(t1, textvariable=self.llm_model_var,
                                          width=20, state="disabled")
        self.llm_model_cb.grid(row=0, column=4, sticky="w", **pad)

        self.llm_status_var = tk.StringVar()
        tk.Label(t1, textvariable=self.llm_status_var, fg="gray",
                 font=("Arial", 8)).grid(row=0, column=5, sticky="w", padx=4)

        # Row 1: Anthropic API key (shown only when provider == "claude")
        self.anthropic_key_lbl = tk.Label(t1, text="Anthropic API Key:")
        self.anthropic_key_var = tk.StringVar()
        self.anthropic_key_entry = tk.Entry(t1, textvariable=self.anthropic_key_var,
                                            width=48, show="*", state="disabled")
        self.anthropic_key_show_var = tk.BooleanVar(value=False)
        self.anthropic_key_show_btn = tk.Button(
            t1, text="Show", width=5,
            command=self._toggle_key_visibility)
        # Hidden by default — shown via _on_provider_change() when "claude" selected
        # (widgets are gridded but immediately removed so layout is preserved)
        self.anthropic_key_lbl.grid(row=1, column=0, columnspan=2, sticky="e", **pad)
        self.anthropic_key_entry.grid(row=1, column=2, columnspan=3, sticky="ew", **pad)
        self.anthropic_key_show_btn.grid(row=1, column=5, **pad)
        self.anthropic_key_lbl.grid_remove()
        self.anthropic_key_entry.grid_remove()
        self.anthropic_key_show_btn.grid_remove()

        # Row 2: Claude model selector (shown only when provider == "claude")
        CLAUDE_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"]
        self.claude_model_lbl = tk.Label(t1, text="Claude Model:")
        self.claude_model_var = tk.StringVar(value=CLAUDE_MODELS[0])
        self.claude_model_cb = ttk.Combobox(t1, textvariable=self.claude_model_var,
                                            values=CLAUDE_MODELS, width=30, state="disabled")
        self.claude_model_lbl.grid(row=2, column=0, columnspan=2, sticky="e", **pad)
        self.claude_model_cb.grid(row=2, column=2, columnspan=2, sticky="w", **pad)
        self.claude_model_lbl.grid_remove()
        self.claude_model_cb.grid_remove()

        ttk.Separator(t1, orient="horizontal").grid(
            row=3, column=0, columnspan=6, sticky="ew", pady=(4, 2))

        # ── Tab 2: Pipeline & Effects ─────────────────────────────────
        t2 = ttk.Frame(nb, padding=6)
        nb.add(t2, text="  Pipeline & Effects  ")

        STAGES = ["transcribe", "segment", "prompt", "generate", "compile"]

        tk.Label(t2, text="Start from:").grid(row=0, column=0, sticky="e", **pad)
        self.start_from_var = tk.StringVar(value="transcribe")
        ttk.Combobox(t2, textvariable=self.start_from_var, values=STAGES,
                     width=14, state="readonly").grid(row=0, column=1, sticky="w", **pad)

        tk.Label(t2, text="Stop after:").grid(row=0, column=2, sticky="e", **pad)
        self.stop_after_var = tk.StringVar(value="compile")
        ttk.Combobox(t2, textvariable=self.stop_after_var, values=STAGES,
                     width=14, state="readonly").grid(row=0, column=3, sticky="w", **pad)

        self.resume_var = tk.BooleanVar(value=False)
        tk.Checkbutton(t2, text="Resume from existing project",
                       variable=self.resume_var).grid(row=0, column=4, columnspan=2, sticky="w", **pad)

        ttk.Separator(t2, orient="horizontal").grid(
            row=1, column=0, columnspan=8, sticky="ew", pady=(4, 2))

        # Script file (required for segment detection)
        tk.Label(t2, text="Script File:").grid(row=2, column=0, sticky="e", **pad)
        self.script_var = tk.StringVar()
        tk.Entry(t2, textvariable=self.script_var, width=40).grid(
            row=2, column=1, columnspan=3, sticky="w", **pad)
        tk.Button(t2, text="Browse", command=self._pick_script).grid(row=2, column=4, **pad)
        tk.Label(t2, text="original narration script", fg="gray",
                 font=("Arial", 8)).grid(row=2, column=5, sticky="w", padx=4)

        ttk.Separator(t2, orient="horizontal").grid(
            row=3, column=0, columnspan=8, sticky="ew", pady=(4, 2))

        self.ken_burns_var = tk.BooleanVar(value=False)
        tk.Checkbutton(t2, text="Ken Burns",
                       variable=self.ken_burns_var).grid(row=4, column=0, sticky="w", **pad)

        self.crossfade_var = tk.BooleanVar(value=False)
        tk.Checkbutton(t2, text="Crossfade",
                       variable=self.crossfade_var).grid(row=4, column=1, sticky="w", **pad)

        tk.Label(t2, text="Image workers:").grid(row=4, column=2, sticky="e", padx=(12, 2))
        self.gen_workers_var = tk.StringVar(value="10")
        tk.Spinbox(t2, textvariable=self.gen_workers_var, from_=1, to=20,
                   width=4).grid(row=4, column=3, sticky="w", **pad)

        tk.Label(t2, text="Re-gen scenes:").grid(row=5, column=0, sticky="e", **pad)
        self.regen_scenes_var = tk.StringVar(value="")
        tk.Entry(t2, textvariable=self.regen_scenes_var, width=40).grid(
            row=5, column=1, columnspan=3, sticky="w", **pad)
        tk.Label(t2, text="1-based, e.g. 5,12", fg="gray",
                 font=("Arial", 8)).grid(row=5, column=4, sticky="w", padx=4)

        self.find_dupes_var = tk.BooleanVar(value=False)
        tk.Checkbutton(t2, text="Find & regen dupes",
                       variable=self.find_dupes_var).grid(row=6, column=0, columnspan=2, sticky="w", **pad)

        # ── BUTTON ROW ───────────────────────────────────────────────
        btn_row = tk.Frame(self)
        btn_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 2))
        btn_row.columnconfigure(0, weight=1)

        self.start_btn = tk.Button(btn_row, text="  Generate Video",
                                   bg="#2d7d46", fg="white",
                                   font=("Arial", 11, "bold"), command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.stop_btn = tk.Button(btn_row, text="Stop",
                                  bg="#8b0000", fg="white",
                                  font=("Arial", 11, "bold"), command=self._stop,
                                  state="disabled", width=6)
        self.stop_btn.grid(row=0, column=1, padx=3)

        tk.Button(btn_row, text="Regen Missing",
                  command=self._load_missing_scenes,
                  bg="#7d4a2d", fg="white",
                  font=("Arial", 10), width=13).grid(row=0, column=2, padx=3)

        tk.Button(btn_row, text="Scan Corrupt",
                  command=self._scan_corrupt,
                  bg="#4a2d7d", fg="white",
                  font=("Arial", 10), width=12).grid(row=0, column=3, padx=(3, 0))

        # ── LOG ───────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="  Output  ", padding=4)
        log_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 8))
        self.log = scrolledtext.ScrolledText(log_frame, width=92, height=14,
                                             state="disabled", font=("Consolas", 9))
        self.log.pack()

    # ── toggles ──────────────────────────────────────────────────────
    def _toggle_transcript(self):
        use_existing = not self.tx_auto_var.get()
        self.transcript_entry.config(state="normal" if use_existing else "disabled")
        self.transcript_btn.config(state="normal" if use_existing else "disabled")

    def _toggle_llm(self):
        on = self.use_llm_var.get()
        self.llm_provider_cb.config(state="readonly" if on else "disabled")
        if on:
            self._on_provider_change()
        else:
            self.llm_model_cb.config(state="disabled")
            self.llm_status_var.set("")

    def _toggle_key_visibility(self):
        if self.anthropic_key_show_var.get():
            self.anthropic_key_entry.config(show="")
            self.anthropic_key_show_btn.config(text="Hide")
            self.anthropic_key_show_var.set(False)
        else:
            self.anthropic_key_entry.config(show="*")
            self.anthropic_key_show_btn.config(text="Show")
            self.anthropic_key_show_var.set(True)

    # ── widget-state helpers ─────────────────────────────────────────
    def _update_llm_widgets(self, on: bool):
        self.llm_provider_cb.config(state="readonly" if on else "disabled")
        if not on:
            self.llm_model_cb.config(state="disabled")
            self.llm_status_var.set("")
            self.anthropic_key_lbl.grid_remove()
            self.anthropic_key_entry.grid_remove()
            self.anthropic_key_show_btn.grid_remove()
            self.claude_model_lbl.grid_remove()
            self.claude_model_cb.grid_remove()

    def _on_provider_change(self):
        provider = self.llm_provider_var.get()
        if provider == "claude":
            self.llm_model_cb.config(state="disabled")
            self.llm_model_var.set("claude (Anthropic API)")
            self.llm_status_var.set("")
            self.anthropic_key_lbl.grid()
            self.anthropic_key_entry.config(state="normal")
            self.anthropic_key_entry.grid()
            self.anthropic_key_show_btn.grid()
            self.claude_model_lbl.grid()
            self.claude_model_cb.config(state="readonly")
            self.claude_model_cb.grid()
        else:
            self.anthropic_key_lbl.grid_remove()
            self.anthropic_key_entry.grid_remove()
            self.anthropic_key_show_btn.grid_remove()
            self.claude_model_lbl.grid_remove()
            self.claude_model_cb.grid_remove()
            self.llm_model_cb.config(state="readonly")
            self.llm_status_var.set("Fetching models...")
            threading.Thread(target=self._fetch_llm_models, args=(provider,), daemon=True).start()

    def _fetch_llm_models(self, provider):
        try:
            if provider == "ollama":
                url = "http://localhost:11434/api/tags"
                with urllib.request.urlopen(url, timeout=4) as r:
                    data = _json.loads(r.read())
                models = [m["name"] for m in data.get("models", [])]
                status = f"{len(models)} model(s) loaded" if models else "No models found"
            else:
                url = "http://localhost:1234/v1/models"
                with urllib.request.urlopen(url, timeout=4) as r:
                    data = _json.loads(r.read())
                models = [m["id"] for m in data.get("data", [])]
                status = f"{len(models)} model(s) loaded" if models else "No models found"

            def _apply():
                self.llm_model_cb["values"] = models
                if models:
                    if self.llm_model_var.get() not in models:
                        self.llm_model_var.set(models[0])
                self.llm_status_var.set(status)
            self.after(0, _apply)

        except Exception:
            def _err():
                self.llm_model_cb["values"] = []
                provider_name = "Ollama" if provider == "ollama" else "LM Studio"
                self.llm_status_var.set(f"{provider_name} not reachable — type manually")
                self.llm_model_cb.config(state="normal")
            self.after(0, _err)

    # ── file pickers ─────────────────────────────────────────────────
    def _pick_audio(self):
        path = filedialog.askopenfilename(
            filetypes=[("Audio", "*.mp3 *.wav *.aac *.m4a *.flac"), ("All", "*.*")])
        if path:
            self.audio_var.set(path)
            name = os.path.splitext(os.path.basename(path))[0]
            if not self.name_var.get():
                self.name_var.set(name)
            self._auto_detect_transcript(name)

    def _auto_detect_transcript(self, name: str):
        """If a word_timestamps.json exists for this name, switch to it automatically."""
        workspace = self.workspace_var.get().strip()
        candidate = os.path.normpath(
            os.path.join(workspace, "scripts", f"{name}_word_timestamps.json"))
        if os.path.exists(candidate):
            self.tx_auto_var.set(False)
            self.transcript_var.set(candidate)
            self.transcript_entry.config(state="normal")
            self.transcript_btn.config(state="normal")
            self._log(f"Found existing transcript: {os.path.basename(candidate)}\n")

    def _pick_workspace(self):
        folder = filedialog.askdirectory()
        if folder:
            self.workspace_var.set(folder)

    def _pick_transcript(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if path:
            self.transcript_var.set(path)

    def _pick_script(self):
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if path:
            self.script_var.set(path)

    # ── regen missing ─────────────────────────────────────────────────
    def _load_missing_scenes(self):
        """Read sync_map.txt + scan images folder; fill regen_scenes with scenes needing regen."""
        name      = self.name_var.get().strip()
        workspace = self.workspace_var.get().strip()
        if not name:
            self._log("ERROR: Set Output Name first.\n")
            return

        needs_regen = set()

        # Source 1: sync_map.txt (MISSING or PLACEHOLDER status)
        map_path = os.path.join(workspace, "scripts", f"{name}_sync_map.txt")
        if os.path.exists(map_path):
            with open(map_path) as f:
                for line in f:
                    if line.startswith("scene"):
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 5 and parts[4] in ("MISSING", "PLACEHOLDER"):
                        needs_regen.add(parts[0])
        else:
            self._log(f"No sync map at {map_path} — scanning images folder only.\n")

        # Source 2: placeholder_ files sitting in the images folder
        images_dir = os.path.join(workspace, "images", name)
        if os.path.isdir(images_dir):
            for fname in os.listdir(images_dir):
                if fname.startswith("placeholder_scene_") and fname.endswith(".png"):
                    # placeholder_scene_0081.png → scene index 81 → scene number 82
                    try:
                        idx = int(fname[len("placeholder_scene_"):-4])
                        needs_regen.add(str(idx + 1))  # scene number is 1-based (index+1)
                    except ValueError:
                        pass

        if not needs_regen:
            self._log("Nothing needs regen — all images present and real.\n")
            return

        sorted_scenes = sorted(needs_regen, key=lambda x: int(x))
        self.regen_scenes_var.set(",".join(sorted_scenes))
        self._log(f"Loaded {len(sorted_scenes)} scene(s) needing regen into Re-gen field:\n"
                  f"  {','.join(sorted_scenes)}\n\n"
                  f"Click 'Generate Video' to regenerate them.\n"
                  f"Tip: use --resume to preserve existing project state.\n")

    # ── scan corrupt ─────────────────────────────────────────────────
    def _scan_corrupt(self):
        name      = self.name_var.get().strip()
        workspace = self.workspace_var.get().strip()
        if not name:
            self._log("ERROR: Set Output Name first.\n")
            return
        images_dir = os.path.join(workspace, "images", name)
        if not os.path.isdir(images_dir):
            self._log(f"ERROR: Images folder not found:\n  {images_dir}\n")
            return
        self._log(f"Scanning {images_dir} for corrupt images...\n")
        threading.Thread(target=self._scan_corrupt_worker,
                         args=(images_dir,), daemon=True).start()

    def _scan_corrupt_worker(self, images_dir):
        from PIL import Image as _PILImage
        corrupt = []
        files = sorted(
            f for f in os.listdir(images_dir)
            if re.match(r"scene_\d{4}\.png", f)
        )
        for fname in files:
            path = os.path.join(images_dir, fname)
            try:
                with _PILImage.open(path) as img:
                    img.load()
            except Exception:
                # scene_0010.png → index 10 → scene number 11
                idx = int(fname[6:10])
                corrupt.append(idx + 1)

        def _apply():
            if not corrupt:
                self._log("No corrupt images found — all PNGs are valid.\n")
                return
            scene_str = ",".join(str(n) for n in corrupt)
            self.regen_scenes_var.set(scene_str)
            self._log(
                f"Found {len(corrupt)} corrupt image(s): {scene_str}\n"
                f"Loaded into Re-gen field. Click 'Generate Video' to fix them.\n"
                f"Tip: enable 'Load prompts from JSON' so timestamps stay locked.\n"
            )
        self.after(0, _apply)

    # ── log ──────────────────────────────────────────────────────────
    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._log("\n--- Stopped by user ---\n")

    # ── run ──────────────────────────────────────────────────────────
    def _start(self):
        audio = self.audio_var.get().strip()
        name  = self.name_var.get().strip()

        if not audio or not os.path.exists(audio):
            self._log("ERROR: Audio file not found.\n")
            return
        if not name:
            self._log("ERROR: Output name is required.\n")
            return

        self.start_btn.config(state="disabled", text="Running...")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        threading.Thread(target=self._run, args=(audio, name), daemon=True).start()

    def _run(self, audio, name):
        workspace = self.workspace_var.get().strip()
        model     = self.model_var.get()
        script    = self.script_var.get().strip()

        cmd = [
            PYTHON, "-X", "utf8", "-u", "-m", "video_automation",
            "--audio",      audio,
            "--name",       name,
            "--workspace",  workspace,
            "--ai33-key",   AI33_KEY,
            "--ai33-model", model,
            "--start-from", self.start_from_var.get(),
            "--stop-after", self.stop_after_var.get(),
        ]

        if script and os.path.exists(script):
            cmd += ["--script", script]

        if self.resume_var.get():
            cmd.append("--resume")

        if not self.tx_auto_var.get():
            t = self.transcript_var.get().strip()
            if t and os.path.exists(t):
                # Existing transcript available — skip transcription, start from segment
                idx = cmd.index("--start-from")
                if cmd[idx + 1] == "transcribe":
                    cmd[idx + 1] = "segment"

        if self.use_llm_var.get():
            if self.llm_provider_var.get() == "claude":
                key = self.anthropic_key_var.get().strip()
                if key:
                    cmd += ["--anthropic-key", key]
                cmd += ["--claude-model", self.claude_model_var.get()]
            else:
                cmd += ["--llm-provider", self.llm_provider_var.get()]
                m = self.llm_model_var.get().strip()
                if m:
                    cmd += ["--llm-model", m]

        if self.ken_burns_var.get():
            cmd.append("--ken-burns")
        if self.crossfade_var.get():
            cmd.append("--crossfade")

        regen = self.regen_scenes_var.get().strip()
        if regen:
            cmd += ["--regen-scenes", regen]

        gw = self.gen_workers_var.get().strip()
        if gw:
            cmd += ["--max-workers", gw]

        if self.find_dupes_var.get():
            cmd.append("--find-dupes")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env)
            self.after(0, lambda: self.stop_btn.config(state="normal"))
            in_traceback = False
            last_error = ""
            for line in self._proc.stdout:
                stripped = line.strip()
                if stripped.startswith("Traceback (most recent call last)"):
                    in_traceback = True
                    last_error = ""
                    continue
                if in_traceback:
                    if stripped.startswith("File ") or stripped.startswith("raise ") or not stripped or line.startswith("  "):
                        continue
                    last_error = stripped
                    in_traceback = False
                    self.after(0, self._log, f"ERROR: {last_error}\n")
                    continue
                self.after(0, self._log, line)
            self._proc.wait()
            self.after(0, self._log, "\n--- Done ---\n")
        except Exception as e:
            self.after(0, self._log, f"ERROR: {e}\n")
        finally:
            self._proc = None
            self.after(0, lambda: self.stop_btn.config(state="disabled"))
            self.after(0, lambda: self.start_btn.config(state="normal", text="  Generate Video"))


if __name__ == "__main__":
    App().mainloop()
