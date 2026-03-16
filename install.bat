@echo off
setlocal enabledelayedexpansion
REM =============================================================================
REM VIDEO AUTOMATION V2 - WINDOWS INSTALLER
REM =============================================================================

echo.
echo ============================================================
echo    VIDEO AUTOMATION V2 - DEPENDENCY INSTALLER
echo ============================================================
echo.

REM Check Python
echo [1/8] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    echo Install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python %PYVER%
echo.

REM Upgrade pip
echo [2/8] Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Install core dependencies
echo [3/8] Installing core dependencies...
pip install requests Pillow pydub tqdm colorama
echo.

REM Check for NVIDIA GPU and detect CUDA version
echo [4/8] Detecting GPU and CUDA version...
set HAS_GPU=0
set CUDA_VER=0
set CUDA_TAG=cpu
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    set HAS_GPU=1
    REM Parse CUDA version using Python (reliable across nvidia-smi formats)
    for /f %%a in ('python -c "import subprocess,re;o=subprocess.check_output('nvidia-smi',text=True);m=re.search(r'CUDA Version:\s*([\d.]+)',o);print(m.group(1) if m else '0')"') do set CUDA_VER=%%a
    echo NVIDIA GPU detected! Driver CUDA version: %CUDA_VER%
) else (
    echo No NVIDIA GPU detected. Will install CPU-only versions.
)
echo.

REM Install PyTorch with the right CUDA version
echo [5/8] Installing PyTorch...
if %HAS_GPU%==0 (
    echo Installing CPU-only PyTorch...
    pip install torch torchvision torchaudio
) else (
    REM Use Python to pick the right CUDA wheel (batch if-else is unreliable)
    for /f %%t in ('python -c "v=float('%CUDA_VER%');print('cu128' if v>=12.8 else 'cu126' if v>=12.6 else 'cu124' if v>=12.4 else 'cu121' if v>=12.1 else 'cu118')"') do set CUDA_TAG=%%t
    echo Installing PyTorch with !CUDA_TAG! support for CUDA %CUDA_VER%...
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/!CUDA_TAG!
)
echo.

REM Install Faster-Whisper
echo [6/8] Installing Faster-Whisper (transcription, medium model)...
if %HAS_GPU%==1 (
    pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
) else (
    pip install faster-whisper
)
echo Pre-downloading Whisper medium model (this may take a few minutes)...
python -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu', compute_type='int8')" 2>nul
if %errorlevel% equ 0 (
    echo Whisper medium model cached successfully!
) else (
    echo WARNING: Could not pre-download Whisper model. It will download on first run.
)
echo.

REM Install GUI and API deps
echo [7/8] Installing GUI and API dependencies...
pip install nicegui anthropic
echo.

REM Check and install FFmpeg
echo [8/8] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo FFmpeg not found. Attempting to install via winget...
    winget install ffmpeg >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo WARNING: Could not auto-install FFmpeg.
        echo Please install manually:
        echo   Option 1: winget install ffmpeg
        echo   Option 2: choco install ffmpeg
        echo   Option 3: https://ffmpeg.org/download.html
        echo.
        echo FFmpeg is REQUIRED for video compilation.
        echo.
    ) else (
        echo FFmpeg installed via winget!
    )
) else (
    echo FFmpeg found!
)
echo.

REM Create workspace folders
echo ============================================================
echo Creating workspace folders...
echo ============================================================
if not exist "video_workspace" mkdir video_workspace
if not exist "video_workspace\audio" mkdir video_workspace\audio
if not exist "video_workspace\scripts" mkdir video_workspace\scripts
if not exist "video_workspace\characters" mkdir video_workspace\characters
if not exist "video_workspace\style_references" mkdir video_workspace\style_references
if not exist "video_workspace\intro" mkdir video_workspace\intro
if not exist "video_workspace\images" mkdir video_workspace\images
if not exist "video_workspace\videos" mkdir video_workspace\videos
echo Done!
echo.

REM Summary
echo ============================================================
echo    INSTALLATION COMPLETE
echo ============================================================
echo.
echo Next steps:
echo.
echo 1. (Optional) Install Ollama for local LLM prompt generation
echo    https://ollama.ai/
echo    Then run: ollama pull qwen2.5:7b
echo.
echo 2. Place your audio files in video_workspace\audio\
echo.
echo 3. Run the GUI:
echo    python VideoAutomation\gui_nicegui.py
echo.
echo ============================================================
pause
