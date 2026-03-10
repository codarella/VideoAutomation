@echo off
REM =============================================================================
REM VIDEO AUTOMATION V2 - WINDOWS INSTALLER
REM =============================================================================
REM This script installs all Python dependencies for the video automation project
REM =============================================================================

echo.
echo ============================================================
echo    VIDEO AUTOMATION V2 - DEPENDENCY INSTALLER
echo ============================================================
echo.

REM Check Python
echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found! Please install Python 3.9+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)
python --version
echo.

REM Check pip
echo [2/6] Checking pip...
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip not found! Reinstalling pip...
    python -m ensurepip --upgrade
)
echo.

REM Upgrade pip
echo [3/6] Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Install core dependencies
echo [4/6] Installing core dependencies...
pip install requests>=2.28.0
pip install Pillow>=9.0.0
pip install pydub>=0.25.1
pip install SpeechRecognition>=3.10.0
pip install tqdm>=4.65.0
pip install colorama>=0.4.6
echo.

REM Check for NVIDIA GPU
echo [5/6] Checking for NVIDIA GPU...
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    echo NVIDIA GPU detected! Installing PyTorch with CUDA support...
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
) else (
    echo No NVIDIA GPU detected. Installing CPU-only PyTorch...
    pip install torch torchvision torchaudio
)
echo.

REM Install Whisper
echo [6/6] Installing OpenAI Whisper...
pip install openai-whisper
echo.

REM Check FFmpeg
echo ============================================================
echo Checking FFmpeg...
echo ============================================================
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo WARNING: FFmpeg not found!
    echo.
    echo FFmpeg is REQUIRED for video compilation.
    echo.
    echo Install options:
    echo   1. winget install ffmpeg
    echo   2. choco install ffmpeg
    echo   3. Download from https://ffmpeg.org/download.html
    echo.
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
echo 1. Install FFmpeg if not already installed
echo    (winget install ffmpeg)
echo.
echo 2. Install LM Studio from https://lmstudio.ai/
echo    - Download and load Qwen 2.5 7B Instruct model
echo    - Start the local server
echo.
echo 3. Get AI33 API key from https://ai33.pro/
echo    - Add to run_v2.bat
echo.
echo 4. Add your files:
echo    - Audio: video_workspace\audio\
echo    - SRT transcript: video_workspace\scripts\transcript.srt
echo    - Character: video_workspace\characters\MC.png
echo    - Style images: video_workspace\style_references\
echo.
echo 5. Run: run_v2.bat
echo.
echo ============================================================
pause
