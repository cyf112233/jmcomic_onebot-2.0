#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目依赖引导（不污染系统 Python）。

设计原则
--------
1. 默认只在项目内创建 ``.venv`` 并安装 ``requirements.txt``；
2. 绝不向系统 Python 安装包，因此不会触发 PEP 668
   ``externally-managed-environment``；
3. 设置 ``JM_BOT_PYTHON`` 或传入 ``--python`` 时，视为用户自管环境
   （uv / conda / poetry / 手工 venv），脚本只检查、不安装；
4. ``--check`` / ``--no-install`` 只检查，不产生任何修改。

常用命令::

    python3 scripts/bootstrap.py                 # 创建 .venv 并安装
    python3 scripts/bootstrap.py --check         # 只检查
    python3 scripts/bootstrap.py --print-python  # 准备环境并输出解释器路径
    python3 scripts/bootstrap.py --venv /tmp/x   # 指定虚拟环境目录
    JM_BOT_PYTHON=/path/to/venv/bin/python python3 scripts/bootstrap.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

if sys.version_info < (3, 9):
    print(
        f"[setup] 需要 Python 3.9+，当前为 {sys.version.split()[0]}。",
        file=sys.stderr,
    )
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
DEFAULT_VENV = ROOT / ".venv"
MARKER_NAME = ".jm_bot_deps"

# 在目标解释器中执行，输出缺失/版本过低的包名
CHECK_CODE = r"""
import importlib.metadata as md
import re

mods = [
    ("websockets", "websockets"),
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("jmcomic", "jmcomic"),
    ("pyzipper", "pyzipper"),
]
minimums = {
    "websockets": (10, 0),
    "PyYAML": (6, 0),
    "Pillow": (10, 0),
    "jmcomic": (2, 7, 9),
    "pyzipper": (0, 3, 6),
}

def parse_version(value):
    parts = []
    for part in re.split(r"[.\-+]", str(value)):
        match = re.match(r"(\d+)", part)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)

def version_at_least(installed, minimum):
    length = max(len(installed), len(minimum))
    left = installed + (0,) * (length - len(installed))
    right = minimum + (0,) * (length - len(minimum))
    return left >= right

missing = []
for module, package in mods:
    try:
        __import__(module)
    except Exception:
        missing.append(package)
        continue
    try:
        installed = md.version(package)
    except Exception:
        missing.append(package)
        continue
    if not version_at_least(parse_version(installed), minimums[package]):
        missing.append(package)

for module, package in mods:
    if package in missing:
        continue
    try:
        print(f"{package}=={md.version(package)}")
    except Exception:
        pass

if missing:
    print("MISSING=" + ",".join(missing))
"""


def _log(message: str) -> None:
    print(f"[setup] {message}", file=sys.stderr)


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _resolve_python(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        # 不要 resolve()：虚拟环境的 bin/python 通常是指向基础解释器的符号链接，
        # 解析后会丢失 venv 上下文（sys.prefix 变成系统 Python）。
        return candidate.absolute()
    found = shutil.which(value)
    if found:
        return Path(found).absolute()
    raise SystemExit(f"找不到 Python 解释器：{value}")


def _run(
    command: Sequence[str],
    *,
    timeout: int = 180,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _check_requirements(python: Path) -> Tuple[List[str], str]:
    try:
        process = _run([str(python), "-c", CHECK_CODE], timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"无法执行目标 Python：{python}（{exc}）")
        return ["<无法执行 Python>"], ""

    output = process.stdout or ""
    missing: List[str] = []
    for line in output.splitlines():
        if line.startswith("MISSING="):
            missing = [
                item for item in line[len("MISSING=") :].split(",") if item
            ]
            break
    return missing, output


def _in_venv(python: Path) -> bool:
    try:
        process = _run(
            [
                str(python),
                "-c",
                "import sys; print(int(sys.prefix != sys.base_prefix))",
            ],
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process.returncode == 0 and process.stdout.strip() == "1"


def _has_pip(python: Path) -> bool:
    try:
        process = _run([str(python), "-m", "pip", "--version"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return process.returncode == 0


def _ensure_pip(python: Path) -> bool:
    if _has_pip(python):
        return True
    _log("目标环境没有 pip，尝试通过 ensurepip 安装……")
    try:
        process = _run(
            [str(python), "-m", "ensurepip", "--upgrade"], timeout=180
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"ensurepip 失败：{exc}")
        return False
    if process.returncode != 0:
        _log((process.stderr or process.stdout or "").strip())
        return False
    return _has_pip(python)


def _create_venv(venv: Path) -> bool:
    if venv.exists():
        if not venv.is_dir():
            _log(f"虚拟环境路径已存在且不是目录：{venv}")
            return False
        if _venv_python(venv).is_file():
            return True
        _log(f"检测到不完整的虚拟环境，正在重建：{venv}")
        shutil.rmtree(venv, ignore_errors=True)

    _log(f"创建虚拟环境：{venv}")
    try:
        process = _run(
            [sys.executable, "-m", "venv", str(venv)], timeout=300
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"创建虚拟环境失败：{exc}")
        return False

    if process.returncode != 0:
        _log("创建虚拟环境失败。")
        _log("Debian/Ubuntu 请先安装：sudo apt install python3-venv")
        detail = (process.stderr or process.stdout or "").strip()
        if detail:
            _log(detail)
        return False

    return _venv_python(venv).is_file()


def _install(python: Path, index_url: Optional[str]) -> bool:
    if not _ensure_pip(python):
        _log("无法准备 pip，请检查该环境是否由 uv/conda 等工具管理。")
        return False

    if not REQUIREMENTS.is_file():
        _log(f"依赖清单不存在：{REQUIREMENTS}")
        return False

    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(REQUIREMENTS),
    ]
    if index_url:
        command.extend(["--index-url", index_url])

    _log("安装依赖：" + " ".join(command))
    try:
        # pip 输出走 stderr，保证 --print-python 的 stdout 只有解释器路径
        process = subprocess.run(
            command, stdout=sys.stderr, stderr=sys.stderr
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"pip 执行失败：{exc}")
        return False

    if process.returncode != 0:
        _log("pip 安装失败，请检查网络或使用 --index-url 指定镜像。")
        return False

    return True


def _requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _marker_file(venv: Path) -> Path:
    return venv / MARKER_NAME


def _marker_matches(venv: Path) -> bool:
    marker = _marker_file(venv)
    try:
        return marker.read_text(encoding="utf-8").strip() == _requirements_hash()
    except OSError:
        return False


def _write_marker(venv: Path) -> None:
    try:
        _marker_file(venv).write_text(
            _requirements_hash() + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _log(f"写入依赖标记失败：{exc}")


def _report_missing(missing: Sequence[str], *, external: bool) -> None:
    _log("缺少依赖或版本过低：" + ", ".join(missing))
    if external:
        _log(
            "检测到外部 Python 环境（JM_BOT_PYTHON / --python），"
            "已跳过自动安装，避免干扰 uv/conda/poetry 等包管理器。"
        )
        _log("请使用对应工具安装 requirements.txt 中的依赖。")
    else:
        _log(
            "请运行 scripts/setup.sh（Linux/macOS）或 "
            "scripts/setup.ps1（Windows）完成安装。"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/bootstrap.py",
        description="为 jm-bot 准备隔离的 Python 虚拟环境和依赖",
    )
    parser.add_argument(
        "--venv",
        default=os.environ.get("JM_BOT_VENV", str(DEFAULT_VENV)),
        help="项目虚拟环境目录（默认 .venv）",
    )
    parser.add_argument(
        "--python",
        default=os.environ.get("JM_BOT_PYTHON"),
        help="使用已有的 Python 解释器（只检查，不安装）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查依赖，不创建虚拟环境、不安装",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="同 --check，只检查",
    )
    parser.add_argument(
        "--print-python",
        action="store_true",
        help="准备完成后把应使用的 Python 路径输出到 stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新安装依赖（仅项目虚拟环境）",
    )
    parser.add_argument(
        "--index-url",
        default=None,
        help="pip 索引地址（也可用 PIP_INDEX_URL 环境变量）",
    )
    args = parser.parse_args(argv)

    if not REQUIREMENTS.is_file():
        _log(f"依赖清单不存在：{REQUIREMENTS}")
        return 2

    # 外部环境：只检查，绝不调用 pip 安装
    external_value = args.python or os.environ.get("JM_BOT_PYTHON")
    if external_value:
        try:
            target = _resolve_python(external_value)
        except SystemExit as exc:
            _log(str(exc))
            return 2

        if not _in_venv(target):
            _log(
                f"注意：{target} 不是虚拟环境。脚本只做依赖检查，"
                "不会向其安装任何包。"
            )

        missing, output = _check_requirements(target)
        if missing:
            _report_missing(missing, external=True)
            return 1

        if args.check and output and not args.print_python:
            print(output, end="")
        if args.print_python:
            print(target)
        _log(f"外部 Python 环境依赖检查通过：{target}")
        return 0

    venv = Path(args.venv).expanduser()
    if not venv.is_absolute():
        venv = ROOT / venv
    venv = venv.resolve()

    if venv == ROOT or venv in ROOT.parents or venv == Path(venv.anchor):
        _log(f"拒绝将项目根目录或文件系统根目录作为虚拟环境：{venv}")
        return 2

    venv_python = _venv_python(venv)

    check_only = args.check or args.no_install
    if check_only:
        target = venv_python if venv_python.is_file() else Path(sys.executable)
        missing, output = _check_requirements(target)
        if missing:
            _report_missing(missing, external=False)
            return 1
        if output and not args.print_python:
            print(output, end="")
        if args.print_python:
            print(target)
        _log(f"依赖检查通过：{target}")
        return 0

    if not venv_python.is_file() and not _create_venv(venv):
        return 2

    missing, _ = _check_requirements(venv_python)
    need_install = args.force or bool(missing) or not _marker_matches(venv)

    if need_install:
        if not _install(venv_python, args.index_url):
            return 2
        missing, _ = _check_requirements(venv_python)
        if missing:
            _report_missing(missing, external=False)
            return 2
        _write_marker(venv)
        _log("依赖安装完成。")
    else:
        _log("依赖已就绪，跳过安装。")

    if args.print_python:
        print(venv_python)
    else:
        _log(f"虚拟环境就绪：{venv_python}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
