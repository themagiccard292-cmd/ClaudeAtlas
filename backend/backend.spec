# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for backend.exe (the OS-automation sidecar).
#
#   cd backend
#   pyinstaller --noconfirm --clean --distpath dist --workpath build backend.spec
#
# Produces dist/backend/backend.exe (+ _internal/). One-dir rather than one-file on purpose:
# a one-file exe re-extracts hundreds of MB to %TEMP% on every launch (slow start, and the
# self-extracting stub is a classic antivirus false-positive). electron-builder bundles the
# whole folder via `extraResources`, so the user still gets a single installer / portable exe.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# Packages that load code/data dynamically (COM typelibs, native DLLs, model runtimes, ...).
for pkg in ("pywinauto", "comtypes", "pynput", "mss", "pyautogui", "faster_whisper", "ctranslate2", "av", "tokenizers", "onnxruntime", "edge_tts", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # optional / platform-specific package not installed
        print(f"[backend.spec] skipping {pkg}: {exc}")

hiddenimports += collect_submodules("uvicorn") + collect_submodules("anyio") + collect_submodules("websockets")
hiddenimports += [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl", "uvicorn.lifespan.on",
    "pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32", "mss.windows",
    "pywinauto.uia_element_info", "pywinauto.controls.uiawrapper", "pywinauto.controls.uia_controls", "pywinauto.uia_defines",
    "pywinauto.application", "pywinauto.findwindows", "comtypes.client", "win32timezone",
    "app.server", "app.runner", "app.osagent_tools", "app.mcp_server", "app.claude_cli", "app.safety", "app.uia", "app.voice",
]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "matplotlib", "scipy", "pandas", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=True is deliberate: stdout/stderr must exist (uvicorn/logging crash when they are None).
    # Electron starts it with windowsHide:true, so no console window is ever visible.
    console=True,
    icon="../build/icon.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="backend")
