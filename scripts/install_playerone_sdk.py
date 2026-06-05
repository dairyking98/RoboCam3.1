"""
install_playerone_sdk.py
========================
Downloads the Player One Camera SDK for the current platform and extracts
the Python wrapper (pyPOACamera.py) and the native library into
``<project_root>/vendor/playerone/``.

This script also patches the pyPOACamera.py wrapper to support cross-platform
loading (Linux/macOS/Windows) and absolute path resolution.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import sys
import tarfile
import zipfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# SDK download URLs
# ---------------------------------------------------------------------------
SDK_URLS = {
    "Windows": "https://player-one-astronomy.com/download/softwares/PlayerOne_Camera_SDK_Windows_V3.10.0.zip",
    "Linux":   "https://player-one-astronomy.com/download/softwares/PlayerOne_Camera_SDK_Linux_V3.10.0.tar.gz",
    "Darwin":  "https://player-one-astronomy.com/download/softwares/PlayerOne_Camera_SDK_MacOS_V3.10.0.tar.gz",
}

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent

def _vendor_dir() -> Path:
    return _project_root() / "PlayerOne_Camera_SDK_Linux_V3.10.0"

def _download(url: str) -> bytes:
    print(f"  Downloading {url} …", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "RoboCam/3.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        data = bytearray()
        chunk = 65536
        while True:
            block = resp.read(chunk)
            if not block:
                break
            data.extend(block)
            if total:
                pct = len(data) * 100 // total
                print(f"\r  {pct:3d}%  {len(data)//1024} KB / {total//1024} KB", end="", flush=True)
        print()
    return bytes(data)

def _patch_wrapper(wrapper_path: Path):
    """Patch pyPOACamera.py to load the correct library on Linux/RPi."""
    print(f"  Patching {wrapper_path.name} for cross-platform support...")
    
    patch_content = """from ctypes import *
import numpy as np
from enum import Enum
import os as _os, sys as _sys, pathlib as _pathlib

# RoboCam 3.1 patch: resolve the library path relative to this file
_sdk_dir = _pathlib.Path(__file__).resolve().parent
if _sys.platform == "win32":
    if hasattr(_os, "add_dll_directory"):
        _os.add_dll_directory(str(_sdk_dir))
    _lib_name = "PlayerOneCamera.dll"
elif _sys.platform == "darwin":
    _lib_name = "libPlayerOneCamera.dylib"
else:
    _lib_name = "libPlayerOneCamera.so"

_lib_path = str(_sdk_dir / _lib_name)

if _sys.platform == "linux" or _sys.platform == "linux2":
    import ctypes as _ctypes
    _old_cwd = _os.getcwd()
    try:
        _os.chdir(str(_sdk_dir))
        _old_ld_path = _os.environ.get("LD_LIBRARY_PATH", "")
        _os.environ["LD_LIBRARY_PATH"] = str(_sdk_dir) + (":" + _old_ld_path if _old_ld_path else "")
        dll = _ctypes.CDLL(_lib_path, mode=_ctypes.RTLD_GLOBAL)
    finally:
        _os.chdir(_old_cwd)
else:
    dll = cdll.LoadLibrary(_lib_path)

del _os, _sys, _pathlib, _sdk_dir, _lib_name, _lib_path
"""
    
    try:
        content = wrapper_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        
        start_idx = 0
        for i, line in enumerate(lines):
            if "class POABayerPattern" in line:
                start_idx = i
                break
        
        if start_idx == 0:
            for i, line in enumerate(lines):
                s_line = line.strip()
                if s_line and not s_line.startswith(("from", "import", "dll", "cdll", "#", "'''", '"""')):
                    start_idx = i
                    break
        
        new_content = patch_content + "\n" + "\n".join(lines[start_idx:])
        wrapper_path.write_text(new_content, encoding="utf-8")
        print("  Patch applied successfully (original loader removed).")
    except Exception as e:
        print(f"  FAILED to patch wrapper: {e}")

def _extract_zip(data: bytes, dest: Path):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        wrapper_match = next((n for n in names if n.endswith("python/pyPOACamera.py")), None)
        if wrapper_match:
            (dest / "python").mkdir(exist_ok=True)
            (dest / "python" / "pyPOACamera.py").write_bytes(zf.read(wrapper_match))
            print("  Extracted: pyPOACamera.py")
        
        dll_match = next((n for n in names if n.endswith("lib/x64/PlayerOneCamera.dll")), None)
        if dll_match:
            (dest / "python" / "PlayerOneCamera.dll").write_bytes(zf.read(dll_match))
            print("  Extracted: PlayerOneCamera.dll")

def _extract_tar(data: bytes, dest: Path):
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        members = tf.getnames()
        arch = platform.machine().lower()
        
        wrapper_match = next((m for m in members if m.endswith("python/pyPOACamera.py")), None)
        if wrapper_match:
            (dest / "python").mkdir(exist_ok=True)
            content = tf.extractfile(tf.getmember(wrapper_match)).read()
            (dest / "python" / "pyPOACamera.py").write_bytes(content)
            print("  Extracted: pyPOACamera.py")

        if arch in ["aarch64", "arm64"]:
            preferred = ["arm64", "arm32", "x64"]
        elif arch.startswith("arm"):
            preferred = ["arm32", "x64"]
        else:
            preferred = ["x64"]

        lib_bases = ["libPlayerOneCamera.so", "libPlayerOneCamera.so.3", "libPlayerOneCamera.so.3.10.0"]
        for base in lib_bases:
            for p_arch in preferred:
                src_pattern = f"lib/{p_arch}/{base}"
                match = next((m for m in members if m.endswith(src_pattern)), None)
                if match:
                    content = tf.extractfile(tf.getmember(match)).read()
                    (dest / "python" / base).write_bytes(content)
                    print(f"  Extracted: {base} (from {src_pattern})")
                    break

        rules_src = "udev/99-player_one_astronomy.rules"
        match = next((m for m in members if m.endswith(rules_src)), None)
        if match:
            content = tf.extractfile(tf.getmember(match)).read()
            (dest / "99-player_one_astronomy.rules").write_bytes(content)
            print("  Extracted: 99-player_one_astronomy.rules")

def main():
    os_name = platform.system()
    if os_name not in SDK_URLS:
        print(f"Unsupported platform: {os_name}")
        sys.exit(1)

    url = SDK_URLS[os_name]
    dest = _vendor_dir()
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Installing Player One Camera SDK for {os_name} ({platform.machine()}) …")
    try:
        data = _download(url)
    except Exception as e:
        print(f"  ERROR: Download failed: {e}")
        sys.exit(1)

    if url.endswith(".zip"):
        _extract_zip(data, dest)
    else:
        _extract_tar(data, dest)

    wrapper_path = dest / "python" / "pyPOACamera.py"
    if wrapper_path.exists():
        _patch_wrapper(wrapper_path)

    print(f"\nPlayer One SDK installed to: {dest}")

if __name__ == "__main__":
    main()
