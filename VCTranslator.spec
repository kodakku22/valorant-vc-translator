# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller VCTranslator.spec --noconfirm   (or run build_exe.bat)
from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_dynamic_libs, collect_submodules)

datas = collect_data_files("faster_whisper") + collect_data_files("silero_vad")
datas += [("vc_translator/webui", "vc_translator/webui")]
binaries = collect_dynamic_libs("ctranslate2")
hiddenimports = collect_submodules("vc_translator")

# pywebview (EdgeChromium backend on Windows: pythonnet + WebView2 loader)
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium",
                  "ruamel.yaml"]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "IPython", "jupyter", "PyQt5", "PySide6", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VCTranslator",
    icon="assets/icon.ico",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed app -- logs go to data/app.log
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VCTranslator",
)
