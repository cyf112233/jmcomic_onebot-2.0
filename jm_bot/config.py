# -*- coding: utf-8 -*-
"""
配置加载与适配

- 读取 jm_bot/config.yml 中的统一 OneBot WS 连接配置
- 提供构建 WS 连接所需的 URL 与请求头（Authorization 或 query 参数）
- 提供简单的 Bot 配置（终端日志等）

依赖：
  - PyYAML（yaml）：用于解析 YAML 配置文件
    安装：pip install pyyaml
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
import pathlib
import yaml


DEFAULT_CONFIG_PATH = pathlib.Path("jm_bot/config.yml")


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


@dataclass
class AppConfig:
    onebot: OneBotConfig
    bot: BotConfig


class ConfigError(Exception):
    pass


def _ensure_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v)


def _ensure_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _ensure_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    if isinstance(v, (int, float)):
        return bool(v)
    return default


def _ensure_int_list(v: Any) -> List[int]:
    """
    将输入解析为 int 列表。支持：
      - 已是 list：元素可为 str/int，尽力转为 int
      - 其他类型：返回空列表
    """
    out: List[int] = []
    if isinstance(v, list):
        for item in v:
            try:
                if isinstance(item, str) and item.isdigit():
                    out.append(int(item))
                else:
                    out.append(int(item))
            except Exception:
                continue
    return out


def load_config(path: Optional[str] = None) -> AppConfig:
    """
    从 YAML 加载配置。
    :param path: 配置文件路径，默认 jm_bot/config.yml
    """
    cfg_path = pathlib.Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"配置文件未找到: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    ob_raw: Dict[str, Any] = raw.get("onebot", {}) or {}
    bot_raw: Dict[str, Any] = raw.get("bot", {}) or {}

    ws_url = _ensure_str(ob_raw.get("ws_url"), "ws://127.0.0.1:6700/")
    if not ws_url.startswith(("ws://", "wss://")):
        raise ConfigError(f"onebot.ws_url 必须以 ws:// 或 wss:// 开头：{ws_url}")

    onebot = OneBotConfig(
        ws_url=ws_url,
        access_token=_ensure_str(ob_raw.get("access_token"), ""),
        connect_timeout=_ensure_int(ob_raw.get("connect_timeout"), 10),
        reconnect_interval=_ensure_int(ob_raw.get("reconnect_interval"), 3),
        use_query_token=_ensure_bool(ob_raw.get("use_query_token"), False),
    )

    bot = BotConfig(
        name=_ensure_str(bot_raw.get("name"), "jm-bot"),
        verbose_event_log=_ensure_bool(bot_raw.get("verbose_event_log"), True),
        per_group_cooldown_enabled=_ensure_bool(bot_raw.get("per_group_cooldown_enabled"), False),
        per_group_cooldown_seconds=_ensure_int(bot_raw.get("per_group_cooldown_seconds"), 300),
        admins=_ensure_int_list(bot_raw.get("admins")),
    )

    return AppConfig(onebot=onebot, bot=bot)


def build_ws_connect_params(ob: OneBotConfig) -> Tuple[str, Dict[str, str]]:
    """
    基于 OneBot 配置构建 WebSocket 连接所需参数。
    返回 (url, headers)
      - 若 use_query_token=True，token 通过 query 参数附加
      - 否则通过 Authorization: Bearer <token> 传递
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
    """为 url 附加/合并 query 参数"""
    parsed = urlparse(url)
    original_qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    original_qs.update({k: v for k, v in extra_params.items() if v is not None})
    new_query = urlencode(original_qs)
    new_parsed = parsed._replace(query=new_query)
    return urlunparse(new_parsed)
