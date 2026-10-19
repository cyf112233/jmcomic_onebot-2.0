# ============================================================
# 首次安装 / 更新依赖（Windows PowerShell）
#
# 在项目内创建 .venv，并用其中的 pip 安装 requirements.txt。
# 不会修改系统 Python。
# ============================================================

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 scripts/bootstrap.py @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python scripts/bootstrap.py @args
} else {
    Write-Error "未找到 Python 3。请先从 https://www.python.org/ 安装 Python 3.9+，并勾选 Add to PATH。"
    exit 1
}

exit $LASTEXITCODE
