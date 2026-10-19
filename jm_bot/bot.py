# -*- coding: utf-8 -*-
"""机器人核心业务逻辑：命令分发、群状态、下载流水线与多章节拒绝。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import multiprocessing
import os
import secrets
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import message as MSG
from .archive import ArchiveError, ArchiveResult, create_archive
from .commands import parse_command
from .config import AppConfig
from .jm_pdf import PdfError, build_pdf
from .jmcomic_api import (
    AlbumMeta,
    JmConfigError,
    download_album_sync,
    fetch_album_meta,
    prepare_option_file,
)
from .logging_utils import format_traceback, log_err, log_info, log_warn
from .onebot_ws import OneBotWSClient, is_api_success
from .paths import (
    ensure_dir,
    find_album_dir,
    order_chapter_dirs,
    remove_tree,
    safe_int,
    sanitize_filename,
    truncate_text,
)
from .texts import (
    GROUP_DISABLED_TEXT,
    HELP_TEXT,
    META_FAILED_CONTINUE_TEXT,
    META_FAILED_TEXT,
    NO_PERMISSION_TEXT,
    PROCESS_FAILED_TEXT,
    SEND_FAILED_TEXT,
    UPDATE_BUSY_TEXT,
    UPDATE_EXTERNAL_ENV_TEXT,
    UPDATE_IN_PROGRESS_TEXT,
    UPDATE_NO_PIP_TEXT,
    UPDATE_NOT_VENV_TEXT,
    UPDATE_OK_TEXT,
    UPDATE_START_TEXT,
    UPDATE_TASK_BUSY_TEXT,
    UPDATE_TIMEOUT_TEXT,
    USAGE_TEXT,
    format_multi_chapter_rejection,
    format_success_info,
)

MetaFetcher = Callable[[str, Path], AlbumMeta]
Downloader = Callable[[str, Path], None]
PdfBuilder = Callable[..., Any]
Archiver = Callable[..., Any]


class ProcessingError(Exception):
    """可预期的处理失败，``str(exc)`` 会直接发给群。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def _event_text(message: Any) -> str:
    """只提取文本消息段，忽略图片、@ 等，便于命令解析。"""
    if not isinstance(message, list):
        return str(message or "")

    parts: List[str] = []
    for segment in message:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        parts.append(str(data.get("text", "")))
    return "".join(parts)


def _clip(text: str, limit: int) -> str:
    """按字符截断但保留换行。"""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _running_in_venv() -> bool:
    # 只信任 sys.prefix，避免继承到错误的 VIRTUAL_ENV 环境变量后
    # 误把系统 Python 当成虚拟环境执行 pip。
    return sys.prefix != sys.base_prefix


def _is_project_venv(cfg: AppConfig) -> bool:
    """当前解释器是否来自项目自带的 .venv。"""
    if cfg.source_path is None:
        return False
    project_root = Path(cfg.source_path).resolve().parent.parent
    expected = (project_root / ".venv").resolve()
    current = Path(sys.prefix).resolve()
    return current == expected or expected in current.parents


# 与 requirements.txt 保持一致：在线更新只允许升级到受支持的主版本
JMCOMIC_UPGRADE_SPEC = "jmcomic>=2.7.9,<3.0"


def _pip_upgrade_jmcomic(timeout: int) -> subprocess.CompletedProcess:
    """在虚拟环境内升级 jmcomic（阻塞函数，放到线程执行）。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            JMCOMIC_UPGRADE_SPEC,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class JmBot:
    """OneBot 事件处理器。"""

    def __init__(
        self,
        client: OneBotWSClient,
        cfg: AppConfig,
        *,
        meta_fetcher: Optional[MetaFetcher] = None,
        downloader: Optional[Downloader] = None,
        pdf_builder: Optional[PdfBuilder] = None,
        archiver: Optional[Archiver] = None,
        load_state: bool = True,
    ) -> None:
        self.client = client
        self.cfg = cfg

        self._meta_fetcher = meta_fetcher or fetch_album_meta
        self._downloader = downloader or download_album_sync
        self._pdf_builder = pdf_builder or build_pdf
        self._archiver = archiver or create_archive

        self._admin_ids: Set[int] = set(cfg.bot.admins)
        self._busy: Set[int] = set()
        self._cooldown_until: Dict[int, float] = {}
        self._cooldown_locks: Dict[int, asyncio.Lock] = {}

        self._state_lock = asyncio.Lock()
        self._updating = False
        self._pool: Optional[ProcessPoolExecutor] = None
        self.restart_event = asyncio.Event()

        self.group_enabled: Dict[int, bool] = {}
        if load_state:
            self._load_state()

    # ------------------------------------------------------------------
    # 群状态
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        path = Path(self.cfg.bot.state_file)

        # 兼容 2.0 旧版状态文件 jm_bot/group_state.json
        if not path.is_file() and self.cfg.source_path:
            legacy = Path(self.cfg.source_path).parent / "group_state.json"
            if legacy.is_file():
                log_info(
                    f"检测到旧版群状态文件，已兼容读取：{legacy}"
                )
                path = legacy

        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log_warn(f"读取群状态文件失败，将使用默认值：{exc!r}")
            return

        if not isinstance(data, dict):
            log_warn(f"群状态文件格式不正确，已忽略：{path}")
            return

        loaded: Dict[int, bool] = {}
        for key, value in data.items():
            group_id = safe_int(key)
            if group_id is None:
                continue
            if isinstance(value, bool):
                loaded[group_id] = value
            elif isinstance(value, (int, float)):
                loaded[group_id] = bool(value)
            elif isinstance(value, str):
                loaded[group_id] = value.strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
        self.group_enabled = loaded
        if loaded:
            log_info(f"已加载 {len(loaded)} 个群的开关状态。")

    async def _save_state(self) -> None:
        try:
            path = Path(self.cfg.bot.state_file)
            ensure_dir(path.parent)
            payload = {
                str(key): bool(value)
                for key, value in sorted(self.group_enabled.items())
            }
            content = json.dumps(
                payload, ensure_ascii=False, indent=2, sort_keys=True
            )
            async with self._state_lock:
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(content, encoding="utf-8")
                os.replace(temporary, path)
        except Exception as exc:
            log_warn(f"保存群状态失败：{exc!r}")

    def is_group_enabled(self, group_id: int) -> bool:
        return self.group_enabled.get(
            int(group_id), bool(self.cfg.bot.group_enabled_by_default)
        )

    async def set_group_enabled(self, group_id: int, enabled: bool) -> None:
        self.group_enabled[int(group_id)] = bool(enabled)
        await self._save_state()

    # ------------------------------------------------------------------
    # 冷却
    # ------------------------------------------------------------------
    async def _check_cooldown(self, group_id: int) -> Tuple[bool, int]:
        if (
            not self.cfg.bot.per_group_cooldown_enabled
            or self.cfg.bot.per_group_cooldown_seconds <= 0
        ):
            return True, 0

        lock = self._cooldown_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            until = self._cooldown_until.get(group_id, 0.0)
            if now < until:
                remain = max(1, int(math.ceil(until - now)))
                return False, remain
            return True, 0

    async def _start_cooldown(self, group_id: int) -> None:
        if (
            not self.cfg.bot.per_group_cooldown_enabled
            or self.cfg.bot.per_group_cooldown_seconds <= 0
        ):
            return
        lock = self._cooldown_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            until = time.monotonic() + self.cfg.bot.per_group_cooldown_seconds
            self._cooldown_until[group_id] = until

    # ------------------------------------------------------------------
    # 消息发送
    # ------------------------------------------------------------------
    async def _send_group(self, group_id: int, text: str) -> bool:
        try:
            await self.client.send_group_message(
                int(group_id), [MSG.text(str(text))]
            )
            return True
        except Exception as exc:
            log_warn(f"发送群消息失败 group={group_id}：{exc!r}")
            return False

    async def _notify_admins(self, text: str) -> None:
        if not self.cfg.bot.notify_admins_on_error or not self._admin_ids:
            return

        content = _clip(text, 1800)
        for admin_id in sorted(self._admin_ids):
            try:
                await self.client.send_private_message(
                    admin_id, [MSG.text(content)]
                )
            except Exception as exc:
                log_warn(f"通知管理员失败 uid={admin_id}：{exc!r}")
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    async def on_event(self, event: Dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        if event.get("message_type") != "group":
            return

        group_id = safe_int(event.get("group_id"))
        user_id = safe_int(event.get("user_id"))
        if group_id is None:
            return

        command = parse_command(_event_text(event.get("message")))
        if command is None:
            return

        if command.name == "enable":
            await self._cmd_enable(group_id, user_id)
        elif command.name == "disable":
            await self._cmd_disable(group_id, user_id)
        elif command.name == "update":
            await self._cmd_update(group_id, user_id)
        elif command.name == "help":
            await self._cmd_help(group_id)
        elif command.name == "jm":
            if not command.album_id:
                await self._send_group(group_id, USAGE_TEXT)
                return
            await self._request_jm(group_id, command.album_id)

    def _is_admin(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id in self._admin_ids

    async def _cmd_enable(
        self, group_id: int, user_id: Optional[int]
    ) -> None:
        if not self._is_admin(user_id):
            await self._send_group(group_id, NO_PERMISSION_TEXT)
            return
        if self.is_group_enabled(group_id):
            await self._send_group(group_id, "本群已开启 jm 功能。")
            return
        await self.set_group_enabled(group_id, True)
        await self._send_group(
            group_id,
            "✅ 已开启 jm 功能，本群成员可使用 /jm <ID> 和 /帮助。",
        )

    async def _cmd_disable(
        self, group_id: int, user_id: Optional[int]
    ) -> None:
        if not self._is_admin(user_id):
            await self._send_group(group_id, NO_PERMISSION_TEXT)
            return
        if not self.is_group_enabled(group_id):
            await self._send_group(group_id, "本群当前未开启 jm 功能。")
            return
        await self.set_group_enabled(group_id, False)
        await self._send_group(group_id, "✅ 已关闭 jm 功能。")

    async def _cmd_help(self, group_id: int) -> None:
        if not self.is_group_enabled(group_id):
            await self._send_group(group_id, GROUP_DISABLED_TEXT)
            return
        await self._send_group(group_id, HELP_TEXT)

    async def _cmd_update(
        self, group_id: int, user_id: Optional[int]
    ) -> None:
        if not self._is_admin(user_id):
            await self._send_group(group_id, NO_PERMISSION_TEXT)
            return
        await self._handle_update(group_id)

    async def _request_jm(self, group_id: int, album_id: str) -> None:
        if not self.is_group_enabled(group_id):
            await self._send_group(group_id, GROUP_DISABLED_TEXT)
            return

        if self._updating:
            await self._send_group(group_id, UPDATE_IN_PROGRESS_TEXT)
            return

        allowed, remain = await self._check_cooldown(group_id)
        if not allowed:
            await self._send_group(
                group_id, f"冷却中，还需 {remain} 秒后才能再次使用 /jm。"
            )
            return

        if group_id in self._busy:
            await self._send_group(
                group_id, "当前已有任务在处理，请稍候再试。"
            )
            return

        self._busy.add(group_id)
        try:
            await self.handle_jm_command(group_id, album_id)
        except Exception as exc:
            detail = format_traceback(exc)
            log_err(
                f"命令处理异常 group={group_id} album={album_id}：{exc!r}"
            )
            await self._send_group(group_id, PROCESS_FAILED_TEXT)
            await self._notify_admins(
                f"[命令处理异常] group={group_id} album={album_id}\n{detail}"
            )
        finally:
            self._busy.discard(group_id)
            await self._start_cooldown(group_id)

    # ------------------------------------------------------------------
    # 核心流水线
    # ------------------------------------------------------------------
    async def handle_jm_command(self, group_id: int, album_id: str) -> None:
        """下载并发送一本漫画；多章节会在下载前直接拒绝。"""
        album_id = str(album_id)
        await self._send_group(
            group_id, f"正在检查 jm {album_id} ……"
        )
        log_info(f"开始检查：group={group_id} album={album_id}")

        meta, detail = await self._fetch_meta(album_id)

        if meta is None:
            if self.cfg.bot.reject_multi_chapter:
                # 无法确认章节数时宁可不下载，避免误下整部多章节本
                await self._send_group(group_id, META_FAILED_TEXT)
                await self._notify_admins(
                    f"[获取漫画信息失败] group={group_id} "
                    f"album={album_id}\n{detail}"
                )
                return
            log_warn(
                f"获取漫画信息失败，按配置继续尝试下载：album={album_id}"
            )
            await self._send_group(group_id, META_FAILED_CONTINUE_TEXT)
            await self._notify_admins(
                f"[获取漫画信息失败，继续下载] group={group_id} "
                f"album={album_id}\n{detail}"
            )
        elif (
            self.cfg.bot.reject_multi_chapter
            and meta.is_multi_chapter
        ):
            log_info(
                f"拒绝多章节漫画：album={album_id} title={meta.name!r} "
                f"chapters={meta.chapter_count}"
            )
            await self._send_group(
                group_id, format_multi_chapter_rejection(meta)
            )
            return

        await self._send_group(
            group_id, f"开始下载 jm {album_id}，请稍候……"
        )
        await self._process_album(group_id, album_id, meta)

    async def _fetch_meta(
        self, album_id: str
    ) -> Tuple[Optional[AlbumMeta], str]:
        try:
            meta = await asyncio.wait_for(
                asyncio.to_thread(
                    self._meta_fetcher,
                    album_id,
                    self.cfg.bot.jmcomic_config,
                ),
                timeout=self.cfg.bot.metadata_timeout,
            )
        except asyncio.TimeoutError:
            return (
                None,
                f"获取漫画信息超时（{self.cfg.bot.metadata_timeout} 秒）",
            )
        except Exception as exc:
            return None, format_traceback(exc)

        if not isinstance(meta, AlbumMeta):
            return None, f"元数据返回类型异常：{type(meta)!r}"

        log_info(
            f"漫画信息：{truncate_text(meta.name, 60)} "
            f"(ID={meta.album_id}, 章节={meta.chapter_count}, "
            f"页数={meta.page_count})"
        )
        return meta, ""

    async def _process_album(
        self,
        group_id: int,
        album_id: str,
        meta: Optional[AlbumMeta],
    ) -> None:
        work_root = ensure_dir(self.cfg.bot.work_dir)
        work_dir = ensure_dir(
            work_root
            / f"{album_id}-{int(time.time())}-{secrets.token_hex(3)}"
        )

        try:
            try:
                option_file = prepare_option_file(
                    self.cfg.bot.jmcomic_config, work_dir
                )
            except JmConfigError as exc:
                raise ProcessingError(
                    "生成 jmcomic 任务配置失败，请联系管理员。",
                    detail=format_traceback(exc),
                ) from exc

            log_info(f"任务目录：{work_dir}")
            await self._download(album_id, option_file)

            album_dir = find_album_dir(work_dir, album_id)
            if album_dir is None:
                raise ProcessingError(
                    "下载完成但未找到漫画目录，请稍后重试或联系管理员。"
                )

            chapter_ids = (
                [chapter.photo_id for chapter in meta.chapters]
                if meta is not None
                else []
            )
            chapter_dirs = order_chapter_dirs(album_dir, chapter_ids)
            if not chapter_dirs:
                raise ProcessingError(
                    "未找到任何图片，下载可能失败，请稍后重试。"
                )

            # 下载后兜底：即使预检漏掉，也绝不发送多章节本
            if (
                self.cfg.bot.reject_multi_chapter
                and len(chapter_dirs) > 1
            ):
                log_warn(
                    f"下载后发现 {len(chapter_dirs)} 个章节，执行兜底拒绝："
                    f"album={album_id}"
                )
                await self._send_group(
                    group_id,
                    format_multi_chapter_rejection(
                        meta, actual_count=len(chapter_dirs)
                    ),
                )
                return

            if (
                meta is not None
                and meta.chapter_count > 0
                and len(chapter_dirs) < meta.chapter_count
            ):
                raise ProcessingError(
                    f"下载不完整（预期 {meta.chapter_count} 章，"
                    f"实际 {len(chapter_dirs)} 章），请稍后重试。"
                )

            if (
                meta is not None
                and len(chapter_dirs) > meta.chapter_count
            ):
                log_warn(
                    f"实际章节数多于元数据：actual={len(chapter_dirs)} "
                    f"meta={meta.chapter_count} album={album_id}"
                )

            title = meta.name if meta is not None else ""
            safe_title = sanitize_filename(
                title or f"jm_{album_id}",
                fallback=f"jm_{album_id}",
                max_length=80,
            )
            base_name = f"{safe_title}_{album_id}"

            pdf_path = await self._build_pdf(
                chapter_dirs,
                work_dir / f"{base_name}.pdf",
                title or safe_title,
            )
            archive = await self._make_archive(
                pdf_path, work_dir, base_name
            )

            ok, uncertain, upload_error = await self._upload(
                group_id, archive.path
            )
            if not ok:
                await self._send_group(group_id, SEND_FAILED_TEXT)
                await self._notify_admins(
                    f"[上传失败] group={group_id} album={album_id}\n"
                    f"{upload_error}"
                )
                return

            await self._send_group(
                group_id,
                format_success_info(
                    meta, album_id, archive, uncertain=uncertain
                ),
            )
            log_info(
                f"处理完成：group={group_id} album={album_id} "
                f"file={archive.path.name}"
            )

        except ProcessingError as exc:
            log_err(
                f"处理失败：group={group_id} album={album_id} "
                f"{exc} | {exc.detail or ''}"
            )
            await self._send_group(group_id, str(exc))
            await self._notify_admins(
                f"[处理失败] group={group_id} album={album_id}\n"
                f"{exc.detail or format_traceback(exc)}"
            )
        except Exception as exc:
            detail = format_traceback(exc)
            log_err(
                f"处理异常：group={group_id} album={album_id} {exc!r}"
            )
            await self._send_group(group_id, PROCESS_FAILED_TEXT)
            await self._notify_admins(
                f"[处理异常] group={group_id} album={album_id}\n{detail}"
            )
        finally:
            if self.cfg.bot.keep_files:
                log_info(f"已保留任务目录：{work_dir}")
            else:
                remove_tree(work_dir)

    async def _download(self, album_id: str, option_file: Path) -> None:
        started = time.monotonic()
        try:
            await self._run_pool(
                self._downloader, album_id, option_file
            )
        except Exception as exc:
            raise ProcessingError(
                "下载失败，请稍后重试或联系管理员。",
                detail=format_traceback(exc),
            ) from exc
        log_info(
            f"下载完成：album={album_id} "
            f"耗时 {time.monotonic() - started:.1f} 秒"
        )

    async def _build_pdf(
        self,
        chapter_dirs: List[Path],
        output_path: Path,
        title: str,
    ) -> Path:
        try:
            if self._pdf_builder is build_pdf:
                job = partial(
                    build_pdf,
                    resolution=self.cfg.bot.pdf_resolution,
                    max_width=self.cfg.bot.pdf_max_width,
                )
            else:
                job = self._pdf_builder
            result = await self._run_pool(
                job, chapter_dirs, output_path, title
            )
            path = Path(result)
            if not path.is_file():
                raise PdfError(f"PDF 文件未生成：{path}")
            return path
        except Exception as exc:
            raise ProcessingError(
                "PDF 生成失败，请联系管理员。",
                detail=format_traceback(exc),
            ) from exc

    async def _make_archive(
        self, pdf_path: Path, output_dir: Path, display_name: str
    ) -> ArchiveResult:
        try:
            if self._archiver is create_archive:
                job = partial(
                    create_archive,
                    display_name=display_name,
                    encrypt=self.cfg.bot.zip_encrypt,
                    password_length=self.cfg.bot.zip_password_length,
                )
                result = await self._run_pool(job, pdf_path, output_dir)
            else:
                result = await self._run_pool(
                    self._archiver, pdf_path, output_dir, display_name
                )
            if isinstance(result, ArchiveResult):
                archive = result
            else:
                archive = ArchiveResult(Path(result))
            if not archive.path.is_file():
                raise ArchiveError(f"压缩包未生成：{archive.path}")
            return archive
        except Exception as exc:
            raise ProcessingError(
                "压缩包创建失败，请联系管理员。",
                detail=format_traceback(exc),
            ) from exc

    async def _upload(
        self, group_id: int, path: Path
    ) -> Tuple[bool, bool, str]:
        """上传文件，返回 (成功, 是否超时不确定, 错误详情)。"""
        last_error = ""
        for use_uri in (True, False):
            try:
                response = await self.client.upload_group_file(
                    group_id,
                    str(path),
                    name=path.name,
                    timeout=self.cfg.bot.upload_timeout,
                    use_uri=use_uri,
                )
            except asyncio.TimeoutError:
                # 上传可能已经成功，只是响应超时；避免重复上传
                log_warn(
                    f"上传群文件超时 group={group_id} file={path.name}"
                )
                return True, True, ""
            except Exception as exc:
                last_error = format_traceback(exc)
                log_warn(
                    f"上传群文件失败 group={group_id} "
                    f"use_uri={use_uri}：{exc!r}"
                )
                continue

            if is_api_success(response):
                return True, False, ""
            last_error = f"上传接口返回失败：{response}"
            log_warn(last_error)

        return False, False, last_error

    # ------------------------------------------------------------------
    # 进程池
    # ------------------------------------------------------------------
    def _get_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            workers = max(
                1, min(4, int(self.cfg.bot.download_workers))
            )
            # 使用 spawn：避免在 asyncio + 线程环境下 fork 导致死锁，
            # 同时让 Linux/Windows/macOS 行为一致。
            context = multiprocessing.get_context("spawn")
            self._pool = ProcessPoolExecutor(
                max_workers=workers, mp_context=context
            )
        return self._pool

    async def _run_pool(self, func: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_pool(), func, *args)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    async def _handle_update(self, group_id: int) -> None:
        if self._updating:
            await self._send_group(group_id, UPDATE_BUSY_TEXT)
            return
        if self._busy:
            await self._send_group(group_id, UPDATE_TASK_BUSY_TEXT)
            return
        if not _running_in_venv():
            await self._send_group(group_id, UPDATE_NOT_VENV_TEXT)
            return
        if not _is_project_venv(self.cfg):
            await self._send_group(group_id, UPDATE_EXTERNAL_ENV_TEXT)
            return
        if importlib.util.find_spec("pip") is None:
            await self._send_group(group_id, UPDATE_NO_PIP_TEXT)
            return

        self._updating = True
        try:
            await self._send_group(group_id, UPDATE_START_TEXT)
            result = await asyncio.to_thread(
                _pip_upgrade_jmcomic, self.cfg.bot.update_timeout
            )
            if result.returncode == 0:
                log_info("jmcomic 更新成功，准备重启。")
                await self._send_group(group_id, UPDATE_OK_TEXT)
                self.restart_event.set()
            else:
                detail = (result.stderr or result.stdout or "").strip()
                log_err(f"jmcomic 更新失败：{detail}")
                await self._send_group(
                    group_id, f"❌ 更新失败：\n{_clip(detail, 800)}"
                )
        except subprocess.TimeoutExpired:
            log_err("jmcomic 更新超时。")
            await self._send_group(group_id, UPDATE_TIMEOUT_TEXT)
        except Exception as exc:
            detail = format_traceback(exc)
            log_err(f"jmcomic 更新异常：{exc!r}")
            await self._send_group(
                group_id, f"❌ 更新异常：\n{_clip(detail, 800)}"
            )
        finally:
            self._updating = False


__all__ = ["JmBot", "ProcessingError"]
