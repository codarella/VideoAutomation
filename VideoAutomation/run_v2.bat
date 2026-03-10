@echo off
SETLOCAL EnableDelayedExpansion
REM =========================================================
REM YouTube Video Automation V2
REM With Local LLM support for better prompts
REM =========================================================

cd /d C:\VideoAutomation

echo.
echo =====================================================
echo  VIDEO AUTOMATION V2
echo  Now with Local LLM Support!
echo =====================================================
echo.

REM =========================================================
REM CONFIGURATION - EDIT THESE
REM =========================================================
set AI33_KEY=sk_ixdn5l6ymkwlnetx4dzrlaehlncwo3r2sy0v8igpjzpsjlrx
set AUDIO_FILE=video_workspace\audio\test_audio.mp3
set VIDEO_NAME=my_video
set VIDEO_TITLE=10 Things in Space
set WORKERS=10
set CHARACTER_RATE=0.20

REM JSON or SRT transcript file (supports both)
REM IMPORTANT: Rename your file to transcript.srt (no special characters!)
set TRANSCRIPT_FILE=video_workspace\scripts\transcript.srt

REM Model - leave EMPTY to select interactively
set MODEL=

REM =========================================================
REM LOCAL LLM SETTINGS (for better prompts)
REM Set USE_LLM=1 to enable, 0 to disable
REM =========================================================
set USE_LLM=1
set LLM_PROVIDER=lmstudio
set LLM_MODEL=qwen2.5-7b-instruct

REM =========================================================
REM Checks
REM =========================================================
echo Checking files...

if not exist "%AUDIO_FILE%" (
    echo ERROR: Audio not found: %AUDIO_FILE%
    pause
    exit /b 1
)
echo   [OK] Audio: %AUDIO_FILE%

set USE_TRANSCRIPT=0
if exist "%TRANSCRIPT_FILE%" (
    echo   [OK] Transcript: %TRANSCRIPT_FILE%
    set USE_TRANSCRIPT=1
) else (
    echo   [!] No transcript - will transcribe audio
)

if exist "video_workspace\style_references\*.*" (
    for /f %%a in ('dir /b "video_workspace\style_references\*.*" 2^>nul ^| find /c /v ""') do set STYLE_COUNT=%%a
    echo   [OK] Style references: !STYLE_COUNT! images
) else (
    echo   [!] No style references
)

if exist "video_workspace\characters\*.*" (
    echo   [OK] Character found
) else (
    echo   [!] No character
)

if exist "video_workspace\intro\*.mp4" (
    echo   [OK] Intro video found (will be muted)
) else (
    echo   [!] No intro video
)

echo.
echo Configuration:
echo   Video Name:  %VIDEO_NAME%
echo   Parallel:    %WORKERS% images
echo   Character:   %CHARACTER_RATE% (20%%)
echo   Transcript:  !USE_TRANSCRIPT! (1=yes)
echo   Local LLM:   %USE_LLM% (1=enabled)
if "%USE_LLM%"=="1" (
    echo   LLM Model:   %LLM_PROVIDER%/%LLM_MODEL%
)
echo.

REM =========================================================
REM Build and run command
REM =========================================================
set CMD=python video_automation_v2.py --audio "%AUDIO_FILE%" --name "%VIDEO_NAME%" --ai33-key "%AI33_KEY%" --title "%VIDEO_TITLE%" --workers %WORKERS% --character-rate %CHARACTER_RATE%

if "!USE_TRANSCRIPT!"=="1" (
    set CMD=!CMD! --transcript "%TRANSCRIPT_FILE%"
)

if "%USE_LLM%"=="1" (
    set CMD=!CMD! --use-llm --llm-provider %LLM_PROVIDER% --llm-model %LLM_MODEL%
)

if "%MODEL%"=="" (
    echo No model specified - will show interactive selection...
    set CMD=!CMD! --select-model
) else (
    echo Using model: %MODEL%
    set CMD=!CMD! --model "%MODEL%"
)

echo.
echo Running...
echo.

!CMD!

echo.
if %ERRORLEVEL% EQU 0 (
    echo =====================================================
    echo SUCCESS! Video: video_workspace\videos\%VIDEO_NAME%.mp4
    echo =====================================================
) else (
    echo FAILED! Check errors above.
)

ENDLOCAL
pause
