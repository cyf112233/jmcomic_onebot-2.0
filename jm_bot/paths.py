# -*- coding: utf-8 -*-
"""路径、文件名与文本处理工具。

本模块只依赖标准库，供机器人主流程与 PDF 模块共用。
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

# 项目根目录（jm_bot 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 支持直接读取的图片后缀
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
)

# 任务目录命名：<漫画ID>-<unix时间戳>-<6位随机hex>
JOB_DIR_RE = re.compile(r"^\d{1,10}-\d{10}-[0-9a-f]{6}$")

_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_NATURAL_RE = re.compile(r"(\d+)")
_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_int(value: Any) -> Optional[int]:
    """尽力把输入转换为 int，失败返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def resolve_path(value: Union[str, Path], base: Path = PROJECT_ROOT) -> Path:
    """把相对路径解析到 base 下，并做用户目录展开。"""
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def ensure_dir(path: Union[str, Path]) -> Path:
    """确保目录存在并返回 Path。"""
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def remove_tree(path: Union[str, Path]) -> None:
    """安静地删除目录树（不存在时忽略）。"""
    try:
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except OSError:
        pass


def cleanup_job_dirs(work_dir: Union[str, Path]) -> int:
    """清理 work_dir 下由本机器人创建的旧任务目录。

    只删除严格匹配 ``JOB_DIR_RE`` 的目录，避免误删用户数据。
    """
    root = Path(work_dir)
    if not root.is_dir():
        return 0

    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0

    for child in children:
        try:
            if child.is_dir() and JOB_DIR_RE.match(child.name):
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def is_safe_work_dir(
    path: Union[str, Path], project_root: Path = PROJECT_ROOT
) -> Tuple[bool, str]:
    """检查工作目录是否适合被机器人自动清理。"""
    try:
        resolved = Path(path).resolve()
        root = Path(project_root).resolve()
    except OSError:
        return False, "路径无法解析"

    if resolved == Path(resolved.anchor):
        return False, "不能使用文件系统根目录"
    try:
        if resolved == Path.home().resolve():
            return False, "不能使用用户主目录"
    except OSError:
        pass
    if resolved == root:
        return False, "不能使用项目根目录"
    if resolved in root.parents:
        return False, "不能使用项目根目录的父目录"
    return True, ""


def has_image_files(directory: Union[str, Path]) -> bool:
    """目录下是否存在至少一张受支持的图片。"""
    path = Path(directory)
    if not path.is_dir():
        return False
    try:
        return any(
            entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES
            for entry in path.iterdir()
        )
    except OSError:
        return False


def sanitize_filename(
    name: Any, fallback: str = "jm", max_length: int = 120
) -> str:
    """把漫画标题转换为跨平台安全的文件名片段。"""
    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    text = _ILLEGAL_FILENAME_RE.sub("_", text)
    text = _WHITESPACE_RE.sub(" ", text).strip(" .")

    if not text:
        text = fallback

    # Windows 保留名（CON、NUL 等）
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"

    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip(" .")

    return text or fallback


def natural_key(value: Union[str, Path]) -> Tuple[object, ...]:
    """自然排序键：``2.jpg`` 排在 ``10.jpg`` 之前。"""
    name = Path(value).name if isinstance(value, Path) else str(value)
    parts = _NATURAL_RE.split(name)
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def truncate_text(value: Any, limit: int = 80, ellipsis: str = "…") -> str:
    """把任意文本压成单行并截断，用于群消息。"""
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


def strip_cq_codes(text: str) -> str:
    """移除 CQ 码（例如 [CQ:at,qq=123]）。"""
    return _CQ_RE.sub("", str(text or ""))


def format_bytes(size: int) -> str:
    """格式化文件大小。"""
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def find_album_dir(work_dir: Union[str, Path], album_id: str) -> Optional[Path]:
    """在任务目录中定位漫画根目录。

    正常情况下 jmcomic 使用 ``Bd_Aid_Pid`` 规则，目录就是
    ``<work_dir>/<album_id>``。这里保留扫描兜底，兼容旧配置。
    """
    root = Path(work_dir)
    if not root.is_dir():
        return None

    exact = root / str(album_id)
    if exact.is_dir():
        return exact

    try:
        candidates = [
            entry
            for entry in root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError:
        return None

    with_images = []
    for candidate in candidates:
        if has_image_files(candidate):
            with_images.append(candidate)
            continue
        try:
            if any(
                child.is_dir() and has_image_files(child)
                for child in candidate.iterdir()
            ):
                with_images.append(candidate)
        except OSError:
            continue

    if len(with_images) == 1:
        return with_images[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def order_chapter_dirs(
    album_dir: Union[str, Path], chapter_ids: Sequence[str] = ()
) -> List[Path]:
    """按元数据中的章节顺序返回章节目录，未匹配的目录按自然序追加。"""
    root = Path(album_dir)
    if not root.is_dir():
        return []

    try:
        subdirs = [
            entry
            for entry in root.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(".")
            and has_image_files(entry)
        ]
    except OSError:
        return []

    # 有些旧配置会把图片直接放在漫画根目录
    if not subdirs:
        return [root] if has_image_files(root) else []

    by_name = {entry.name: entry for entry in subdirs}
    ordered: List[Path] = []
    used = set()

    for chapter_id in chapter_ids:
        candidate = by_name.get(str(chapter_id))
        if candidate is not None and candidate.name not in used:
            ordered.append(candidate)
            used.add(candidate.name)

    rest = sorted(
        (entry for entry in subdirs if entry.name not in used),
        key=natural_key,
    )
    ordered.extend(rest)
    return ordered
