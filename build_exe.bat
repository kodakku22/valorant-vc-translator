@echo off
rem ============================================================
rem  Builds dist\VCTranslator\VCTranslator.exe (onedir, windowed)
rem  Notes: the output folder is several GB (CUDA torch included)
rem  and the build takes 10-20 minutes.
rem ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run install.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat

pip install pyinstaller pillow --quiet || (pause & exit /b 1)
python scripts\make_icon.py || (pause & exit /b 1)
pyinstaller VCTranslator.spec --noconfirm || (pause & exit /b 1)

copy /Y config.yaml dist\VCTranslator\ >nul
copy /Y glossary.yaml dist\VCTranslator\ >nul

echo.
echo Build finished: dist\VCTranslator\VCTranslator.exe
echo Desktop shortcut: powershell -ExecutionPolicy Bypass -File make_shortcut.ps1
pause
