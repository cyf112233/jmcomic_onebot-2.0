# -*- coding: utf-8 -*-
"""配置加载与校验。

配置文件默认位于 ``jm_bot/config.yml``，所有相对路径都相对于
项目根目录解析。新增字段都带有默认值，旧配置文件仍可继续使用。
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml

from .paths import PROJECT_ROOT, is_safe_work_dir, resolve_path

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "jm_bot" / "config.yml"
DEFAULT_JMCOMIC_CONFIG = PROJECT_ROOT / "jm_bot" / "jm_pdf" / "config.yml"

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """配置文件缺失或内容非法。"""


@dataclass
class OneBotConfig:
    ws_url: str
    access_token: str = ""
    connect_timeout: int = 10
    reconnect_interval: int = 3
    use_query_token: bool = False


@dataclass
class BotConfig:
    name: str = "jm-bot"
    verbose_event_log: bool = True
    per_group_cooldown_enabled: bool = False
    per_group_cooldown_seconds: int = 0
    admins: List[int] = field(default_factory=list)

    # 多章节拒绝：默认开启。检测在下载前完成，下载后还有兜底校验。
    reject_multi_chapter: bool = True

    # 群默认是否启用（未在状态文件中出现时生效）
    group_enabled_by_default: bool = False

    # 路径
    work_dir: pathlib.Path = field(
        default_factory=lambda: PROJECT_ROOT / "data" / "work"
    )
    state_file: pathlib.Path = field(
        default_factory=lambda: PROJECT_ROOT / "data" / "group_state.json"
    )
    jmcomic_config: pathlib.Path = field(
        default_factory=lambda: DEFAULT_JMCOMIC_CONFIG
    )

    # 下载 / PDF
    download_workers: int = 1
    metadata_timeout: int = 60
    pdf_resolution: int = 150
    pdf_max_width: int = 0
    keep_files: bool = False

    # 压缩包
    zip_encrypt: bool = True
    zip_password_length: int = 6

    # 上传 / 更新
    upload_timeout: int = 300
    update_timeout: int = 180

    # 日志与通知
    notify_admins_on_error: bool = True
    log_level: str = "INFO"
    log_file: Optional[pathlib.Path] = None


@dataclass
class AppConfig:
    onebot: OneBotConfig
    bot: BotConfig
    source_path: Optional[pathlib.Path] = None


def _ensure_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _ensure_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _ensure_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _ensure_int_list(value: Any) -> List[int]:
    """解析管理员 QQ 列表，去重并忽略非法项。"""
    result: List[int] = []
    seen = set()
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                number = int(item)
            except (TypeError, ValueError, OverflowError):
                continue
            if number > 0 and number not in seen:
                seen.add(number)
                result.append(number)
    return result


def _raw_section(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {key} 必须是一个映射（YAML mapping）")
    return value


def _path_value(raw: Dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if value is None or str(value).strip() == "":
        return default
    return str(value)


def _resolve_config_path(
    path: Optional[str], project_root: pathlib.Path
) -> pathlib.Path:
    if path:
        candidate = pathlib.Path(str(path)).expanduser()
        if not candidate.is_absolute():
            candidate = pathlib.Path.cwd() / candidate
        return candidate.resolve()

    env_value = os.environ.get("JM_BOT_CONFIG", "").strip()
    if env_value:
        env_path = pathlib.Path(env_value).expanduser()
        if not env_path.is_absolute():
            env_path = pathlib.Path.cwd() / env_path
        return env_path.resolve()

    return project_root / "jm_bot" / "config.yml"


def _check_range(
    name: str, value: int, minimum: int, maximum: int
) -> int:
    if value < minimum or value > maximum:
        raise ConfigError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间，当前值：{value}"
        )
    return value


def load_config(
    path: Optional[str] = None,
    *,
    project_root: Optional[pathlib.Path] = None,
) -> AppConfig:
    """从 YAML 加载配置并做完整校验。"""
    base = pathlib.Path(project_root or PROJECT_ROOT).resolve()
    config_path = _resolve_config_path(path, base)

    if not config_path.is_file():
        raise ConfigError(f"配置文件未找到：{config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ConfigError(f"读取配置文件失败：{config_path}（{exc}）") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件内容必须是 YAML 映射：{config_path}")

    ob_raw = _raw_section(raw, "onebot")
    bot_raw = _raw_section(raw, "bot")

    ws_url = _ensure_str(
        ob_raw.get("ws_url"), "ws://127.0.0.1:6700/"
    ).strip()
    parsed_url = urlparse(ws_url)
    if parsed_url.scheme not in ("ws", "wss") or not parsed_url.netloc:
        raise ConfigError(
            f"onebot.ws_url 必须是合法的 ws:// 或 wss:// 地址：{ws_url}"
        )

    onebot = OneBotConfig(
        ws_url=ws_url,
        access_token=_ensure_str(ob_raw.get("access_token"), ""),
        connect_timeout=_check_range(
            "onebot.connect_timeout",
            _ensure_int(ob_raw.get("connect_timeout"), 10),
            1,
            300,
        ),
        reconnect_interval=_check_range(
            "onebot.reconnect_interval",
            _ensure_int(ob_raw.get("reconnect_interval"), 3),
            1,
            3600,
        ),
        use_query_token=_ensure_bool(
            ob_raw.get("use_query_token"), False
        ),
    )

    work_dir = resolve_path(
        _path_value(bot_raw, "work_dir", "data/work"), base
    )
    safe, reason = is_safe_work_dir(work_dir, base)
    if not safe:
        raise ConfigError(f"bot.work_dir 不安全：{reason}（{work_dir}）")
    if work_dir.exists() and not work_dir.is_dir():
        raise ConfigError(f"bot.work_dir 已存在但不是目录：{work_dir}")

    state_file = resolve_path(
        _path_value(bot_raw, "state_file", "data/group_state.json"), base
    )
    if state_file == work_dir or work_dir in state_file.parents:
        raise ConfigError("bot.state_file 不能位于 bot.work_dir 内")

    jmcomic_config = resolve_path(
        _path_value(
            bot_raw,
            "jmcomic_config",
            "jm_bot/jm_pdf/config.yml",
        ),
        base,
    )
    if not jmcomic_config.is_file():
        raise ConfigError(f"jmcomic 配置文件未找到：{jmcomic_config}")
    try:
        jmcomic_data = yaml.safe_load(
            jmcomic_config.read_text(encoding="utf-8")
        ) or {}
    except Exception as exc:
        raise ConfigError(
            f"读取 jmcomic 配置文件失败：{jmcomic_config}（{exc}）"
        ) from exc
    if not isinstance(jmcomic_data, dict):
        raise ConfigError(
            f"jmcomic 配置文件内容必须是 YAML 映射：{jmcomic_config}"
        )

    log_level = _ensure_str(
        bot_raw.get("log_level"), "INFO"
    ).strip().upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(
            f"bot.log_level 必须是 {sorted(_LOG_LEVELS)} 之一"
        )

    log_file_value = str(bot_raw.get("log_file") or "").strip()
    log_file = (
        resolve_path(log_file_value, base) if log_file_value else None
    )

    bot = BotConfig(
        name=_ensure_str(bot_raw.get("name"), "jm-bot").strip() or "jm-bot",
        verbose_event_log=_ensure_bool(
            bot_raw.get("verbose_event_log"), True
        ),
        per_group_cooldown_enabled=_ensure_bool(
            bot_raw.get("per_group_cooldown_enabled"), False
        ),
        per_group_cooldown_seconds=_check_range(
            "bot.per_group_cooldown_seconds",
            _ensure_int(bot_raw.get("per_group_cooldown_seconds"), 0),
            0,
            86400,
        ),
        admins=_ensure_int_list(bot_raw.get("admins")),
        reject_multi_chapter=_ensure_bool(
            bot_raw.get("reject_multi_chapter"), True
        ),
        group_enabled_by_default=_ensure_bool(
            bot_raw.get("group_enabled_by_default"), False
        ),
        work_dir=work_dir,
        state_file=state_file,
        jmcomic_config=jmcomic_config,
        download_workers=_check_range(
            "bot.download_workers",
            _ensure_int(bot_raw.get("download_workers"), 1),
            1,
            4,
        ),
        metadata_timeout=_check_range(
            "bot.metadata_timeout",
            _ensure_int(bot_raw.get("metadata_timeout"), 60),
            5,
            600,
        ),
        pdf_resolution=_check_range(
            "bot.pdf_resolution",
            _ensure_int(bot_raw.get("pdf_resolution"), 150),
            72,
            600,
        ),
        pdf_max_width=_check_range(
            "bot.pdf_max_width",
            _ensure_int(bot_raw.get("pdf_max_width"), 0),
            0,
            10000,
        ),
        keep_files=_ensure_bool(bot_raw.get("keep_files"), False),
        zip_encrypt=_ensure_bool(bot_raw.get("zip_encrypt"), True),
        zip_password_length=_check_range(
            "bot.zip_password_length",
            _ensure_int(bot_raw.get("zip_password_length"), 6),
            4,
            32,
        ),
        upload_timeout=_check_range(
            "bot.upload_timeout",
            _ensure_int(bot_raw.get("upload_timeout"), 300),
            30,
            3600,
        ),
        update_timeout=_check_range(
            "bot.update_timeout",
            _ensure_int(bot_raw.get("update_timeout"), 180),
            30,
            1800,
        ),
        notify_admins_on_error=_ensure_bool(
            bot_raw.get("notify_admins_on_error"), True
        ),
        log_level=log_level,
        log_file=log_file,
    )

    return AppConfig(onebot=onebot, bot=bot, source_path=config_path)


def build_ws_connect_params(
    ob: OneBotConfig,
) -> Tuple[str, Dict[str, str]]:
    """构建 WebSocket 连接参数。

    返回 ``(url, headers)``：

    * ``use_query_token=True`` 时 token 放在 query；
    * 否则通过 ``Authorization: Bearer <token>`` 请求头传递。
    """
    headers: Dict[str, str] = {}
    url = ob.ws_url
    token = ob.access_token.strip()

    if token:
        if ob.use_query_token:
            url = _append_query(url, {"access_token": token})
        else:
            headers["Authorization"] = f"Bearer {token}"

    return url, headers


def _append_query(url: str, extra_params: Dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {key: value for key, value in extra_params.items() if value is not None}
    )
    return urlunparse(parsed._replace(query=urlencode(query)))


__all__ = [
    "AppConfig",
    "BotConfig",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_JMCOMIC_CONFIG",
    "OneBotConfig",
    "build_ws_connect_params",
    "load_config",
]
