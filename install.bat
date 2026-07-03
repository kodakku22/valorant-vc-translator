@echo off
rem ============================================================
rem  Valorant VC Translator - setup (CPU baseline)
rem  GPU (RTX) transcription: run install_gpu.bat after this.
rem ============================================================
cd /d "%~dp0"

where python >nul 2>nul || (echo Python not found. Install Python 3.11+ first. & pause & exit /b 1)

if not exist .venv (
    echo Creating venv...
    python -m venv .venv || (pause & exit /b 1)
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (pause & exit /b 1)

echo.
echo Setup finished.
echo   - GPU transcription (recommended, RTX 20xx+): run install_gpu.bat
echo   - Start:            run.bat
echo   - List devices:     run.bat --list-devices
pause
