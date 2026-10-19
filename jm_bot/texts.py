# -*- coding: utf-8 -*-
"""集中管理发送到 QQ 的文案，便于统一调整与测试。"""

from __future__ import annotations

from typing import Optional

from .archive import ArchiveResult
from .jmcomic_api import AlbumMeta
from .paths import format_bytes, truncate_text

GROUP_DISABLED_TEXT = "本群尚未开启 jm 功能，请机器人管理员发送 /开启jm。"

NO_PERMISSION_TEXT = "无权限：该命令仅机器人管理员可用。"

USAGE_TEXT = (
    "用法：/jm <漫画ID>\n"
    "例如：/jm 123456\n"
    "仅支持单章节漫画。"
)

HELP_TEXT = (
    "命令列表：\n"
    "1) /jm <漫画ID> — 获取单章节漫画并发送加密压缩包\n"
    "2) /帮助 — 显示本帮助\n"
    "3) /开启jm — 启用本群（机器人管理员）\n"
    "4) /关闭jm — 禁用本群（机器人管理员）\n"
    "5) /更新jm — 更新 jmcomic 并重启（机器人管理员）\n"
    "\n"
    "说明：为避免生成超大文件，本机器人会拒绝多章节漫画，"
    "只处理单章节本子。"
)

META_FAILED_TEXT = (
    "⚠️ 获取漫画信息失败，未开始下载。\n"
    "请确认漫画 ID 正确后重试；若持续失败请联系管理员。"
)

META_FAILED_CONTINUE_TEXT = (
    "⚠️ 无法获取漫画信息，将尝试直接下载。"
)

PROCESS_FAILED_TEXT = "❌ 处理失败，请稍后重试或联系机器人管理员。"

SEND_FAILED_TEXT = "❌ 文件发送失败，请稍后重试或联系机器人管理员。"

UPDATE_START_TEXT = "开始更新 jmcomic，请稍候……"

UPDATE_OK_TEXT = "✅ jmcomic 更新成功，正在重启机器人……"

UPDATE_TIMEOUT_TEXT = "❌ 更新超时，请稍后重试或手动更新。"

UPDATE_BUSY_TEXT = "已有更新任务正在进行，请稍候。"

UPDATE_IN_PROGRESS_TEXT = "机器人正在更新 jmcomic，请稍后再试。"

UPDATE_TASK_BUSY_TEXT = "当前有漫画任务正在处理，请稍后再执行更新。"

UPDATE_NOT_VENV_TEXT = (
    "⚠️ 当前机器人没有运行在虚拟环境中，已拒绝在线更新。\n"
    "请使用 scripts/setup.sh 创建 .venv，并通过 ./start_jm_bot 启动；\n"
    "或在使用 uv/conda/poetry 管理的环境中手动执行：\n"
    "python -m pip install -U jmcomic"
)

UPDATE_EXTERNAL_ENV_TEXT = (
    "⚠️ 在线更新仅支持项目自带的 .venv。\n"
    "当前 Python 环境由其他工具管理（uv/conda/poetry 或系统包管理器），\n"
    "请使用对应工具更新 jmcomic，或运行 scripts/setup.sh。"
)

UPDATE_NO_PIP_TEXT = (
    "⚠️ 当前 Python 环境中没有可用的 pip，已拒绝在线更新。\n"
    "请使用创建该环境的包管理器（uv/conda/poetry 等）更新 jmcomic。"
)


def format_multi_chapter_rejection(
    meta: Optional[AlbumMeta] = None,
    actual_count: Optional[int] = None,
) -> str:
    """生成多章节拒绝文案。"""
    count = (
        actual_count
        if actual_count is not None
        else (meta.chapter_count if meta else 0)
    )
    lines = ["❌ 已拒绝处理：检测到多章节漫画"]

    if meta is not None:
        lines.append(f"📚 标题：{truncate_text(meta.name or '未知', 80)}")
        lines.append(f"🆔 漫画 ID：{meta.album_id}")

    lines.append(f"📖 章节数：{count}")
    lines.append("")
    lines.append("本机器人仅支持单章节漫画，多章节本子不会下载。")
    lines.append("请发送单章节漫画的 ID 后重试。")

    if meta is not None:
        titles = [chapter.title for chapter in meta.chapters if chapter.title]
        if titles:
            preview = " / ".join(truncate_text(title, 30) for title in titles[:3])
            if count > 3:
                preview += " …"
            lines.append(f"章节示例：{preview}")

    return "\n".join(lines)


def format_success_info(
    meta: Optional[AlbumMeta],
    album_id: str,
    archive: ArchiveResult,
    *,
    uncertain: bool = False,
) -> str:
    """发送文件后的详细信息。"""
    header = "✅ 已发送"
    if uncertain:
        header += "（上传响应超时，请到群文件中确认）"

    lines = [header]
    lines.append(
        f"📚 漫画：{truncate_text(meta.name if meta else '未知', 80)}"
    )
    lines.append(f"🆔 ID：{album_id}")

    if meta is not None:
        if meta.page_count:
            lines.append(f"🖼️ 页数：{meta.page_count}")
        lines.append(f"📖 章节：{meta.chapter_count}")

    lines.append(f"📦 文件：{archive.path.name}")
    if archive.password:
        lines.append(f"🔑 解压密码：{archive.password}")

    try:
        lines.append(f"📏 大小：{format_bytes(archive.path.stat().st_size)}")
    except OSError:
        pass

    return "\n".join(lines)


__all__ = [
    "GROUP_DISABLED_TEXT",
    "HELP_TEXT",
    "META_FAILED_CONTINUE_TEXT",
    "META_FAILED_TEXT",
    "NO_PERMISSION_TEXT",
    "PROCESS_FAILED_TEXT",
    "SEND_FAILED_TEXT",
    "UPDATE_BUSY_TEXT",
    "UPDATE_EXTERNAL_ENV_TEXT",
    "UPDATE_IN_PROGRESS_TEXT",
    "UPDATE_NO_PIP_TEXT",
    "UPDATE_NOT_VENV_TEXT",
    "UPDATE_OK_TEXT",
    "UPDATE_START_TEXT",
    "UPDATE_TASK_BUSY_TEXT",
    "UPDATE_TIMEOUT_TEXT",
    "USAGE_TEXT",
    "format_multi_chapter_rejection",
    "format_success_info",
]
