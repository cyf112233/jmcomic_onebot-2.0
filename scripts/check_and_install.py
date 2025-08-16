# -*- coding: utf-8 -*-
"""
Pre-flight dependency checker and auto-installer (Windows/Generic)

Features:
- Verify required Python packages are installed and meet minimum versions
- If missing or outdated, install/upgrade with the current interpreter
- Fallbacks: try --user, then retry with Tsinghua mirror

Usage:
  python scripts/check_and_install.py
Called by start_jm_bot.bat before launching the bot.
"""

from __future__ import annotations
import importlib
import subprocess
import sys
from typing import List, Tuple, Optional


# (import_name, pip_name, version_spec, min_version_for_check)
REQUIRES: List[Tuple[str, str, str, Optional[str]]] = [
    # websockets 10+ API is recommended
    ("websockets", "websockets", ">=10.0", "10.0"),
    ("yaml", "pyyaml", "", None),
    # Pillow's import name is PIL
    ("PIL", "Pillow", "", None),
    # For JM downloads
    ("jmcomic", "jmcomic", "", None),
]

# Tsinghua mirror for better reliability in CN networks
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _installed_version(import_name: str) -> Optional[str]:
    try:
        mod = importlib.import_module(import_name)
    except Exception:
        return None
    ver = getattr(mod, "__version__", None)
    if ver:
        return str(ver)
    try:
        from importlib.metadata import version as md_version  # py3.8+
        return md_version(import_name)
    except Exception:
        return None


def _version_less_than(v: str, min_v: str) -> bool:
    # Lightweight comparator to avoid extra dependencies (packaging.version)
    def normalize(s: str) -> List[int]:
        parts: List[int] = []
        for p in s.replace("-", ".").split("."):
            try:
                parts.append(int(p))
            except Exception:
                break
        return parts

    a = normalize(v)
    b = normalize(min_v)
    L = max(len(a), len(b))
    a += [0] * (L - len(a))
    b += [0] * (L - len(b))
    return a < b


def _run_pip(args: List[str]) -> bool:
    try:
        print(f"[PIP] {' '.join(args)}")
        subprocess.check_call([sys.executable, "-m", "pip"] + args)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERR ] pip failed (return code {e.returncode})")
        return False


def ensure_package(import_name: str, pip_name: str, version_spec: str = "", min_version: Optional[str] = None) -> bool:
    """
    Ensure the package is installed and meets version requirements.
    Returns True if satisfied or installed successfully; False otherwise.
    """
    cur_ver = _installed_version(import_name)
    if cur_ver:
        if min_version and _version_less_than(cur_ver, min_version):
            print(f"[INFO] Detected {import_name}=={cur_ver}, below required {min_version}, upgrading...")
        else:
            print(f"[OK  ] Found {import_name}=={cur_ver}")
            return True
    else:
        print(f"[MISS] Not found: {import_name}. Will install {pip_name}{version_spec or ''}")

    target = f"{pip_name}{version_spec}" if version_spec else pip_name

    # 1) Default index install/upgrade
    if cur_ver:
        ok = _run_pip(["install", "--upgrade", target])
    else:
        ok = _run_pip(["install", target])

    # 2) Retry with --user
    if not ok:
        print(f"[INFO] Retry with --user: {target}")
        if cur_ver:
            ok = _run_pip(["install", "--upgrade", "--user", target])
        else:
            ok = _run_pip(["install", "--user", target])

    # 3) Retry with Tsinghua mirror
    if not ok:
        print(f"[INFO] Retry with Tsinghua mirror: {target}")
        if cur_ver:
            ok = _run_pip(["install", "--upgrade", "-i", MIRROR, target])
        else:
            ok = _run_pip(["install", "-i", MIRROR, target])

    if not ok:
        print(f"[FAIL] Failed to install/upgrade {target}. Please check your network/permissions.")
        return False

    # Verify again
    new_ver = _installed_version(import_name)
    if not new_ver:
        print(f"[FAIL] Installed but cannot import {import_name}. Please check your environment.")
        return False

    if min_version and _version_less_than(new_ver, min_version):
        print(f"[FAIL] {import_name} version {new_ver} is still below minimum {min_version}")
        return False

    print(f"[OK  ] {import_name} is ready ({new_ver})")
    return True


def main() -> int:
    print("=== JM Bot dependency check ===")
    all_ok = True
    for import_name, pip_name, version_spec, min_version in REQUIRES:
        if not ensure_package(import_name, pip_name, version_spec, min_version):
            all_ok = False
    print("===============================")
    if not all_ok:
        print("[WARN] Some dependencies failed to install. Startup may fail. Re-run this script or install manually.")
        return 1
    print("[DONE] All dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
