# -*- coding: utf-8 -*-

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_bootstrap():
    path = ROOT / "scripts" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("jm_bot_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_python_does_not_follow_venv_symlink():
    bootstrap = _load_bootstrap()
    resolved = bootstrap._resolve_python(sys.executable)
    # 虚拟环境的 bin/python 是符号链接；解析符号链接会丢失 venv 上下文
    assert resolved == Path(sys.executable).absolute()


def test_check_external_interpreter():
    bootstrap = _load_bootstrap()
    assert bootstrap.main(["--check", "--python", sys.executable]) == 0


def test_check_mode_does_not_create_venv(tmp_path):
    bootstrap = _load_bootstrap()
    venv = tmp_path / "not-created"
    assert bootstrap.main(["--check", "--venv", str(venv)]) == 0
    assert not venv.exists()


def test_requirements_hash_is_stable():
    bootstrap = _load_bootstrap()
    assert bootstrap._requirements_hash() == bootstrap._requirements_hash()


def test_create_venv_rejects_regular_file(tmp_path):
    bootstrap = _load_bootstrap()
    path = tmp_path / "venv"
    path.write_text("not a directory", encoding="utf-8")
    assert bootstrap._create_venv(path) is False
