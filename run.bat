@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist .venv (echo Run install.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m vc_translator %*
if errorlevel 1 pause
