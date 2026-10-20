# -*- coding: utf-8 -*-
"""聊天命令解析（严格语法）。

只接受以下**完全匹配**的命令，不能多字、不能少字、不能改大小写::

    /jm <漫画ID>     拉取单章节漫画（/jm 与 ID 之间恰好一个半角空格）
    /帮助            查看帮助
    /开启jm          启用本群（管理员）
    /关闭jm          禁用本群（管理员）
    /更新jm          更新 jmcomic 并重启（管理员）

以下写法都不再识别::

    ?  /help  /enable  /disable  /update
    jm 123  .jm 123  !jm 123  #jm 123
    /jm123  /jm  123  /jm #123  /jm id:123
    /jm 123 多余文字

解析前会做 NFKC 归一化（全角转半角）并移除 CQ 码；
只裁剪消息首尾空白，命令内部的空格必须严格匹配。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Pattern

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

# /jm 与 ID 之间必须恰好一个半角空格
_JM_RE = re.compile(r"/jm ([0-9]{1,10})")

_COMMANDS: Dict[str, Pattern[str]] = {
    "help": re.compile(r"/帮助"),
    "enable": re.compile(r"/开启jm"),
    "disable": re.compile(r"/关闭jm"),
    "update": re.compile(r"/更新jm"),
}


@dataclass(frozen=True)
class Command:
    """解析后的命令。``album_id`` 仅对 ``jm`` 命令有意义。"""

    name: str
    album_id: str = ""
    text: str = ""


def normalize_text(text: str) -> str:
    """全角转半角、去掉 CQ 码，并裁剪消息首尾空白。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = _CQ_RE.sub("", normalized)
    return normalized.strip()


def parse_command(text: str) -> Optional[Command]:
    """解析一条群消息；不是严格命令时返回 ``None``。"""
    normalized = normalize_text(text)
    if not normalized:
        return None

    match = _JM_RE.fullmatch(normalized)
    if match:
        return Command("jm", match.group(1), normalized)

    for name, pattern in _COMMANDS.items():
        if pattern.fullmatch(normalized):
            return Command(name, "", normalized)

    return None
