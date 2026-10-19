#!/usr/bin/env bash
# ============================================================
# 首次安装 / 更新依赖（Linux / macOS）
#
# 在项目内创建 .venv，并用其中的 pip 安装 requirements.txt。
# 不会修改系统 Python。
# ============================================================

set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERR] 未找到 $PY。请先安装 Python 3.9+。" >&2
    exit 1
fi

exec "$PY" scripts/bootstrap.py "$@"
