#!/usr/bin/env python3
"""
VIDEO AUTOMATION V2 - INSTALLATION VERIFIER

Run this script to check if all dependencies are properly installed.

Usage:
    python verify_installation.py
"""

import sys
import os
import subprocess
from pathlib import Path

def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)

def print_ok(text):
    print(f"  ✓ {text}")

def print_fail(text):
    print(f"  ✗ {text}")

def print_warn(text):
    print(f"  ⚠ {text}")

def check_python():
    """Check Python version."""
    print_header("PYTHON")
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    
    if version.major >= 3 and version.minor >= 9:
        print_ok(f"Python {version_str}")
        return True
    else:
        print_fail(f"Python {version_str} - Need 3.9+")
        return False

def check_module(module_name, import_name=None, version_attr="__version__"):
    """Check if a Python module is installed."""
    import_name = import_name or module_name
    try:
        module = __import__(import_name)
        version = getattr(module, version_attr, "unknown")
        print_ok(f"{module_name}: {version}")
        return True
    except ImportError as e:
        print_fail(f"{module_name}: Not installed - pip install {module_name}")
        return False

def check_torch():
    """Check PyTorch and CUDA."""
    try:
        import torch
        print_ok(f"PyTorch: {torch.__version__}")
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            cuda_version = torch.version.cuda
            print_ok(f"CUDA: {cuda_version} ({gpu_name})")
        else:
            print_warn("CUDA: Not available (using CPU - slower)")
        return True
    except ImportError:
        print_fail("PyTorch: Not installed")
        print("  Install with: pip install torch torchvision torchaudio")
        return False

def check_whisper():
    """Check Whisper installation."""
    try:
        import whisper
        print_ok("Whisper: Installed")
        return True
    except ImportError:
        print_fail("Whisper: Not installed")
        print("  Install with: pip install openai-whisper")
        return False

def check_ffmpeg():
    """Check FFmpeg installation."""
    print_header("FFMPEG")
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], 
            capture_output=True, 
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            # Extract version from first line
            first_line = result.stdout.split('\n')[0]
            print_ok(f"FFmpeg: {first_line}")
            return True
    except FileNotFoundError:
        pass
    except Exception as e:
        print_fail(f"FFmpeg check error: {e}")
    
    print_fail("FFmpeg: Not found")
    print("  Install with: winget install ffmpeg")
    print("  Or download from: https://ffmpeg.org/download.html")
    return False

def check_ffprobe():
    """Check FFprobe installation."""
    try:
        result = subprocess.run(
            ["ffprobe", "-version"], 
            capture_output=True, 
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print_ok("FFprobe: Available")
            return True
    except:
        pass
    
    print_warn("FFprobe: Not found (usually included with FFmpeg)")
    return False

def check_local_llm():
    """Check if local LLM server is running."""
    print_header("LOCAL LLM")
    
    import requests
    
    # Check LM Studio
    try:
        resp = requests.get("http://localhost:1234/v1/models", timeout=3)
        if resp.status_code == 200:
            print_ok("LM Studio: Server running on localhost:1234")
            return True
    except:
        pass
    
    # Check Ollama
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            print_ok("Ollama: Server running on localhost:11434")
            return True
    except:
        pass
    
    print_warn("Local LLM: No server detected")
    print("  Start LM Studio and click 'Start Server'")
    print("  Or run: ollama serve")
    return False

def check_workspace():
    """Check workspace folder structure."""
    print_header("WORKSPACE")
    
    workspace = Path("video_workspace")
    required_folders = [
        "audio",
        "scripts", 
        "characters",
        "style_references",
        "intro",
        "images",
        "videos"
    ]
    
    if not workspace.exists():
        print_warn(f"Workspace folder not found: {workspace}")
        print("  Run install.bat to create folders")
        return False
    
    all_ok = True
    for folder in required_folders:
        folder_path = workspace / folder
        if folder_path.exists():
            # Count files
            files = list(folder_path.glob("*"))
            file_count = len([f for f in files if f.is_file()])
            print_ok(f"{folder}/: {file_count} files")
        else:
            print_fail(f"{folder}/: Missing")
            all_ok = False
    
    return all_ok

def check_files():
    """Check required files."""
    print_header("REQUIRED FILES")
    
    files = {
        "video_automation_v2.py": "Main script",
        "run_v2.bat": "Launcher",
        "llm_guidelines.json": "LLM rules",
        "video_workspace/characters/MC.png": "Your character",
        "video_workspace/scripts/transcript.srt": "Transcript (optional)"
    }
    
    all_ok = True
    for file_path, description in files.items():
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            print_ok(f"{file_path} ({size:,} bytes)")
        else:
            if "optional" in description.lower():
                print_warn(f"{file_path}: {description}")
            else:
                print_fail(f"{file_path}: Missing - {description}")
                all_ok = False
    
    # Count style references
    style_dir = Path("video_workspace/style_references")
    if style_dir.exists():
        styles = list(style_dir.glob("*.png")) + list(style_dir.glob("*.jpg"))
        if len(styles) >= 5:
            print_ok(f"Style references: {len(styles)} images")
        else:
            print_warn(f"Style references: Only {len(styles)} images (recommend 10-30)")
    
    return all_ok

def main():
    print("\n" + "=" * 60)
    print("  VIDEO AUTOMATION V2 - INSTALLATION VERIFIER")
    print("=" * 60)
    
    results = {}
    
    # Python modules
    print_header("PYTHON MODULES")
    results["python"] = check_python()
    results["requests"] = check_module("requests")
    results["pillow"] = check_module("Pillow", "PIL", "VERSION")
    results["pydub"] = check_module("pydub")
    results["speech_recognition"] = check_module("SpeechRecognition", "speech_recognition")
    
    # AI/ML
    print_header("AI/ML LIBRARIES")
    results["torch"] = check_torch()
    results["whisper"] = check_whisper()
    
    # FFmpeg
    results["ffmpeg"] = check_ffmpeg()
    results["ffprobe"] = check_ffprobe()
    
    # Local LLM
    results["local_llm"] = check_local_llm()
    
    # Workspace
    results["workspace"] = check_workspace()
    results["files"] = check_files()
    
    # Summary
    print_header("SUMMARY")
    
    critical = ["python", "requests", "pillow", "pydub", "ffmpeg"]
    recommended = ["torch", "whisper", "local_llm"]
    
    critical_ok = all(results.get(k, False) for k in critical)
    recommended_ok = all(results.get(k, False) for k in recommended)
    
    if critical_ok and recommended_ok:
        print("\n  🎉 All checks passed! Ready to generate videos.\n")
    elif critical_ok:
        print("\n  ⚠ Critical dependencies OK, but some recommended items missing.")
        print("    The system will work, but may be slower or have reduced features.\n")
    else:
        print("\n  ❌ Some critical dependencies are missing.")
        print("    Please install them before running the automation.\n")
    
    return 0 if critical_ok else 1

if __name__ == "__main__":
    sys.exit(main())
