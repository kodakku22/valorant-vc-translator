@echo off
rem ============================================================
rem  Replaces CPU torch with the CUDA build (~3 GB download).
rem  Needed for GPU transcription with faster-whisper: the CUDA
rem  torch wheel bundles the cuBLAS/cuDNN DLLs CTranslate2 needs.
rem  Requires an NVIDIA GPU with recent drivers (RTX 50xx OK).
rem
rem  Notes:
rem  - The version must be pinned explicitly: a plain
rem    "pip install torch --upgrade" keeps the CPU build when PyPI
rem    has an equal-or-newer version number.
rem  - Must be a cu12x build: CTranslate2 4.8 links against CUDA 12
rem    (cublas64_12.dll), so a cu130 torch does not provide the
rem    DLLs it needs.
rem ============================================================
cd /d "%~dp0"
if not exist .venv (echo Run install.bat first. & pause & exit /b 1)

call .venv\Scripts\activate.bat
pip install "torch==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128 || (pause & exit /b 1)

python -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print('OK:', torch.cuda.get_device_name(0))" || (echo CUDA check failed & pause & exit /b 1)

echo.
echo GPU setup finished. Start with run.bat
pause
