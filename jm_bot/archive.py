# -*- coding: utf-8 -*-
"""PDF 压缩包创建（可选 AES 加密）。"""

from __future__ import annotations

import secrets
import string
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .logging_utils import log_info
from .paths import ensure_dir, format_bytes, sanitize_filename


class ArchiveError(Exception):
    """压缩包创建失败。"""


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    password: Optional[str] = None


def generate_password(length: int = 6) -> str:
    """生成纯数字密码，便于手机输入。"""
    length = max(4, min(32, int(length)))
    return "".join(secrets.choice(string.digits) for _ in range(length))


def create_archive(
    source: Union[str, Path],
    output_dir: Union[str, Path],
    *,
    display_name: str = "",
    encrypt: bool = True,
    password_length: int = 6,
    password: Optional[str] = None,
) -> ArchiveResult:
    """把生成的 PDF 打包为 ZIP。

    ``encrypt=True`` 时使用 pyzipper 的 AES 加密；密码写在压缩包
    文件名中，即使群消息发送失败用户也能看到密码。
    """
    source_path = Path(source)
    if not source_path.is_file():
        raise ArchiveError(f"要压缩的文件不存在：{source_path}")

    output_path = ensure_dir(output_dir)
    archive_password: Optional[str] = None
    if encrypt:
        archive_password = password or generate_password(password_length)

    base_name = sanitize_filename(
        display_name or source_path.stem,
        fallback=source_path.stem or "jm",
        max_length=80,
    )

    if encrypt:
        file_name = f"{base_name}（密码{archive_password}）.zip"
    else:
        file_name = f"{base_name}.zip"

    target = output_path / file_name
    if target.exists():
        # 正常情况下不会发生（任务目录唯一），保留兜底
        target = target.with_name(
            f"{target.stem}_{secrets.token_hex(2)}{target.suffix}"
        )

    if encrypt:
        try:
            import pyzipper
        except ImportError as exc:  # pragma: no cover - 依赖缺失由 bootstrap 处理
            raise ArchiveError(
                "缺少 pyzipper，无法创建加密压缩包。"
                "请运行 scripts/setup.sh 安装依赖。"
            ) from exc

        with pyzipper.AESZipFile(
            target,
            "w",
            compression=pyzipper.ZIP_STORED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(archive_password.encode("ascii"))
            archive.write(source_path, arcname=source_path.name)
    else:
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_STORED
        ) as archive:
            archive.write(source_path, arcname=source_path.name)

    try:
        size = format_bytes(target.stat().st_size)
    except OSError:  # pragma: no cover - 文件系统异常
        size = "未知大小"

    log_info(
        f"已创建压缩包：{target.name}（{size}，"
        f"{'AES 加密' if encrypt else '未加密'}）"
    )
    return ArchiveResult(target, archive_password)


__all__ = [
    "ArchiveError",
    "ArchiveResult",
    "create_archive",
    "generate_password",
]
