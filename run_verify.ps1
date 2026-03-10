$ffmpegBin = 'C:\Users\OVERLORD\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin'
$env:Path = $env:Path + ';' + $ffmpegBin
Set-Location 'C:\Users\OVERLORD\Videos\VideoAutomation\dependencyinstallation\files (1)'
python -X utf8 verify_installation.py
