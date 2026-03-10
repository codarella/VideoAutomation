# =============================================================================
# VIDEO AUTOMATION V2 - SETUP GUIDE
# =============================================================================

## 📋 System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 10 | Windows 11 |
| RAM | 8 GB | 16 GB+ |
| GPU | None (CPU works) | NVIDIA with 6GB+ VRAM |
| Storage | 10 GB free | 50 GB+ free |
| Python | 3.9+ | 3.10 or 3.11 |

---

## 🚀 Quick Start Installation

### Step 1: Install Python
Download from https://www.python.org/downloads/
✅ Check "Add Python to PATH" during installation

### Step 2: Install FFmpeg
```cmd
# Option A: Using winget (Windows 11)
winget install ffmpeg

# Option B: Using chocolatey
choco install ffmpeg

# Option C: Manual download
# Download from https://ffmpeg.org/download.html
# Extract and add bin folder to system PATH
```

### Step 3: Create Project Folder
```cmd
mkdir C:\VideoAutomation
cd C:\VideoAutomation
```

### Step 4: Install Python Dependencies
```cmd
pip install -r requirements.txt
```

### Step 5: Install PyTorch with GPU Support (Recommended)
```cmd
# For NVIDIA GPU with CUDA 11.8
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# For NVIDIA GPU with CUDA 12.1
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# For CPU only (slower but works)
pip install torch torchvision torchaudio
```

### Step 6: Install Local LLM (Choose One)

#### Option A: LM Studio (Recommended for beginners)
1. Download from https://lmstudio.ai/
2. Install and open LM Studio
3. Search and download: `Qwen 2.5 7B Instruct`
4. Go to "Local Server" tab
5. Click "Start Server" (runs on localhost:1234)

#### Option B: Ollama
```cmd
# Download from https://ollama.ai/ and install
# Then run:
ollama pull qwen2.5:7b
ollama serve
```

---

## 📁 Project Structure Setup

Create this folder structure:
```
C:\VideoAutomation\
├── video_automation_v2.py      ← Main script
├── run_v2.bat                  ← Launcher
├── requirements.txt            ← Dependencies
├── llm_guidelines.json         ← LLM rules
│
└── video_workspace\
    ├── audio\                  ← Put your audio files here
    │   └── my_video.mp3
    │
    ├── scripts\                ← Put SRT transcripts here
    │   └── transcript.srt
    │
    ├── characters\             ← Your MC character image
    │   └── MC.png
    │
    ├── style_references\       ← Example images for style
    │   ├── style1.png
    │   ├── style2.png
    │   └── ... (add 10-30 images)
    │
    ├── intro\                  ← Intro video (optional)
    │   └── intro.mp4
    │
    ├── images\                 ← Generated images (auto-created)
    └── videos\                 ← Final videos (auto-created)
```

---

## 🔑 API Key Setup

### AI33.pro API Key
1. Go to https://ai33.pro/
2. Create account and get API key
3. Add to run_v2.bat:
```batch
set AI33_KEY=your_api_key_here
```

---

## ✅ Verify Installation

Run these commands to verify everything works:

```cmd
# Check Python
python --version

# Check FFmpeg
ffmpeg -version

# Check PyTorch
python -c "import torch; print(f'PyTorch: {torch.__version__}')"

# Check CUDA (if using GPU)
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# Check Whisper
python -c "import whisper; print('Whisper OK')"

# Check PIL
python -c "from PIL import Image; print('PIL OK')"

# Check requests
python -c "import requests; print('Requests OK')"
```

---

## 🎬 Running Your First Video

1. Place your audio file in `video_workspace/audio/`
2. Place your SRT transcript in `video_workspace/scripts/transcript.srt`
3. Add your MC character image to `video_workspace/characters/MC.png`
4. Add style reference images to `video_workspace/style_references/`
5. Make sure LM Studio server is running
6. Run:
```cmd
run_v2.bat
```

---

## 🔧 Troubleshooting

### "FFmpeg not found"
- Add FFmpeg to system PATH
- Or place ffmpeg.exe in C:\VideoAutomation\

### "CUDA out of memory"
- Close other GPU applications
- Use smaller Whisper model: change "medium" to "base" in code
- Or use CPU: set CUDA_VISIBLE_DEVICES=""

### "Local LLM not available"
- Make sure LM Studio server is running (click Start Server)
- Check if model is loaded (shows "READY")
- Verify port 1234 is not blocked

### "AI33 API error"
- Check your API key is correct
- Verify you have credits remaining
- Check internet connection

### "Module not found"
```cmd
pip install <module_name> --break-system-packages
```

---

## 📊 Performance Tips

| Setting | Faster | Higher Quality |
|---------|--------|----------------|
| Whisper model | "base" | "large" |
| Parallel workers | 10 | 5 |
| Image resolution | 1280x720 | 1920x1080 |
| LLM model | 7B | 14B+ |

---

## 📞 Support

- Check console output for specific error messages
- Verify all dependencies are installed
- Make sure all required files are in correct folders
- Test components individually before full run
