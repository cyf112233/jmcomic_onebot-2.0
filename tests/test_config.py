# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from jm_bot.config import ConfigError, load_config


def make_project(
    tmp_path: Path,
    *,
    bot_extra: str = "",
    onebot_extra: str = "",
    jmcomic_exists: bool = True,
):
    project = tmp_path / "project"
    (project / "jm_bot" / "jm_pdf").mkdir(parents=True, exist_ok=True)

    if jmcomic_exists:
        (project / "jm_bot" / "jm_pdf" / "config.yml").write_text(
            "version: '2.0'\n", encoding="utf-8"
        )

    config = project / "jm_bot" / "config.yml"
    config.write_text(
        "onebot:\n"
        '  ws_url: "ws://127.0.0.1:6700/"\n'
        + onebot_extra
        + "\nbot:\n"
        + bot_extra,
        encoding="utf-8",
    )
    return project, config


def test_defaults(tmp_path):
    project, config = make_project(tmp_path)
    cfg = load_config(str(config), project_root=project)

    assert cfg.bot.reject_multi_chapter is True
    assert cfg.bot.work_dir == project / "data" / "work"
    assert cfg.bot.state_file == project / "data" / "group_state.json"
    assert cfg.bot.jmcomic_config == (
        project / "jm_bot" / "jm_pdf" / "config.yml"
    )
    assert cfg.bot.admins == []
    assert cfg.source_path == config.resolve()


def test_overrides_and_type_coercion(tmp_path):
    project, config = make_project(
        tmp_path,
        bot_extra=(
            "  reject_multi_chapter: false\n"
            "  admins: ['123', 456, 123]\n"
            "  per_group_cooldown_enabled: 'yes'\n"
            "  per_group_cooldown_seconds: 30\n"
            '  work_dir: "runtime/work"\n'
            '  state_file: "runtime/state.json"\n'
            '  log_file: "logs/bot.log"\n'
            "  log_level: 'debug'\n"
            "  download_workers: 2\n"
        ),
    )
    cfg = load_config(str(config), project_root=project)

    assert cfg.bot.reject_multi_chapter is False
    assert cfg.bot.admins == [123, 456]
    assert cfg.bot.per_group_cooldown_enabled is True
    assert cfg.bot.per_group_cooldown_seconds == 30
    assert cfg.bot.work_dir == project / "runtime" / "work"
    assert cfg.bot.state_file == project / "runtime" / "state.json"
    assert cfg.bot.log_file == project / "logs" / "bot.log"
    assert cfg.bot.log_level == "DEBUG"
    assert cfg.bot.download_workers == 2


def test_invalid_ws_url(tmp_path):
    project, config = make_project(
        tmp_path,
        onebot_extra='  ws_url: "http://127.0.0.1:6700/"\n',
    )
    with pytest.raises(ConfigError, match="ws://"):
        load_config(str(config), project_root=project)


def test_missing_jmcomic_config(tmp_path):
    project, config = make_project(tmp_path, jmcomic_exists=False)
    with pytest.raises(ConfigError, match="jmcomic 配置文件未找到"):
        load_config(str(config), project_root=project)


def test_unsafe_work_dir(tmp_path):
    for value in ("/", ".", ".."):
        project, config = make_project(
            tmp_path, bot_extra=f'  work_dir: "{value}"\n'
        )
        with pytest.raises(ConfigError, match="work_dir 不安全"):
            load_config(str(config), project_root=project)


def test_state_file_inside_work_dir(tmp_path):
    project, config = make_project(
        tmp_path,
        bot_extra=(
            '  work_dir: "data/work"\n'
            '  state_file: "data/work/state.json"\n'
        ),
    )
    with pytest.raises(ConfigError, match="state_file"):
        load_config(str(config), project_root=project)


@pytest.mark.parametrize(
    "bot_extra",
    [
        "  pdf_resolution: 1000\n",
        "  download_workers: 0\n",
        "  zip_password_length: 2\n",
        "  log_level: 'LOUD'\n",
        "  per_group_cooldown_seconds: -1\n",
    ],
)
def test_invalid_ranges(tmp_path, bot_extra):
    project, config = make_project(tmp_path, bot_extra=bot_extra)
    with pytest.raises(ConfigError):
        load_config(str(config), project_root=project)


def test_missing_config_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ConfigError, match="配置文件未找到"):
        load_config(str(project / "nope.yml"), project_root=project)
