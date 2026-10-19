# -*- coding: utf-8 -*-
"""聊天命令解析。

支持的命令（前缀 ``/ . ! #`` 均可省略）::

    /jm <漫画ID>     拉取单章节漫画
    /帮助 /help      查看帮助
    /开启jm /enable  启用本群（管理员）
    /关闭jm /disable 禁用本群（管理员）
    /更新jm /update  更新 jmcomic 并重启（管理员）

解析前会做 NFKC 归一化（全角转半角）并移除 CQ 码。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Pattern

_PREFIX = r"^\s*[/.#!]?\s*"

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
_JM_ID_RE = re.compile(
    _PREFIX + r"jm\s*(?:#|id\s*[:=]?)?\s*(\d{1,10})\s*$",
    re.IGNORECASE,
)
_JM_EMPTY_RE = re.compile(_PREFIX + r"jm\s*$", re.IGNORECASE)

_PATTERNS: Dict[str, Pattern[str]] = {
    "enable": re.compile(_PREFIX + r"(?:开启\s*jm|enable)\s*$", re.IGNORECASE),
    "disable": re.compile(_PREFIX + r"(?:关闭\s*jm|disable)\s*$", re.IGNORECASE),
    "update": re.compile(_PREFIX + r"(?:更新\s*jm|update)\s*$", re.IGNORECASE),
    "help": re.compile(_PREFIX + r"(?:帮助|help|\?)\s*$", re.IGNORECASE),
}


@dataclass(frozen=True)
class Command:
    """解析后的命令。``album_id`` 仅对 ``jm`` 命令有意义。"""

    name: str
    album_id: str = ""
    text: str = ""


def normalize_text(text: str) -> str:
    """全角转半角、去掉 CQ 码并裁剪首尾空白。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = _CQ_RE.sub("", normalized)
    return normalized.strip()


def parse_command(text: str) -> Optional[Command]:
    """解析一条群消息文本；不是命令时返回 ``None``。"""
    normalized = normalize_text(text)
    if not normalized:
        return None

    match = _JM_ID_RE.match(normalized)
    if match:
        return Command("jm", match.group(1), normalized)

    if _JM_EMPTY_RE.match(normalized):
        # 缺少 ID，交给上层回复用法说明
        return Command("jm", "", normalized)

    for name, pattern in _PATTERNS.items():
        if pattern.match(normalized):
            return Command(name, "", normalized)

    return None
