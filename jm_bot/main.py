# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio
import re
import os
import json
import random
import string
from typing import Any, Dict, List, Optional, Tuple
import pathlib
import yaml
import time
import shutil
import traceback
from concurrent.futures import ProcessPoolExecutor

# OneBot 客户端与工具
from .onebot_ws import OneBotWSClient, message_array_to_plain, log_info, log_warn, log_err
from .config import load_config, AppConfig
from . import message as MSG

# 引入 PDF 工具（使用你迁移过来的 jm_bot/jm_pdf/main.py）
# - 仅调用 all2PDF（按你的示例），用于单本目录转 PDF
# 延迟导入已放入 _proc_all2pdf 内，避免主进程加载依赖


# -------- 命令解析 --------
# 支持可选的命令前缀（如 /jm、!jm、.jm、#jm），以及两端空白
# 使用 ^ 确保命令必须在消息开头（忽略前导空白）
CMD_JM = re.compile(r"^\s*[/.!#]?\s*jm\s+(\d+)\b", re.IGNORECASE)
CMD_ENABLE = re.compile(r"^\s*[/.!#]?\s*开启jm\s*$", re.IGNORECASE)
CMD_DISABLE = re.compile(r"^\s*[/.!#]?\s*关闭jm\s*$", re.IGNORECASE)
CMD_HELP = re.compile(r"^\s*[/.!#]?\s*帮助\s*$", re.IGNORECASE)
CMD_UPDATE = re.compile(r"^\s*[/.!#]?\s*更新jm\s*$", re.IGNORECASE)

# 预览页数量
PREVIEW_IMAGE_COUNT = 3
WORK_ROOT = "jm_bot/.work"
CLEANUP_AFTER_SEND = True

# 分群冷却状态（内存）
GROUP_COOLDOWN_NEXT_TS: Dict[int, float] = {}
GROUP_COOLDOWN_LOCKS: Dict[int, asyncio.Lock] = {}
# 标记某个群是否正在处理请求（避免在处理期间被再次触发）
GROUP_BUSY: Dict[int, bool] = {}

# 群开关持久化（默认关闭）
STATE_FILE = "jm_bot/group_state.json"
GROUP_ENABLED: Dict[int, bool] = {}
# 在模块导入时不要创建 asyncio.Lock(), 需要在运行时绑定到事件循环
STATE_LOCK: Optional[asyncio.Lock] = None

# 进程池（用于将下载与PDF转换放到独立进程，避免阻塞主进程/GIL 影响）
PROCESS_POOL: Optional[ProcessPoolExecutor] = None

def _get_process_pool() -> ProcessPoolExecutor:
    global PROCESS_POOL
    if PROCESS_POOL is None:
        # 单工作进程即可，避免并发下载造成风控；如需并行可调大
        PROCESS_POOL = ProcessPoolExecutor(max_workers=1)
    return PROCESS_POOL

# 供子进程执行的纯函数（必须顶层定义以便可pickle）
def _proc_download_album(album_id: str, jm_yaml_path: str) -> None:
    import jmcomic
    jm_opt = jmcomic.JmOption.from_file(jm_yaml_path)
    jmcomic.download_album(str(album_id), jm_opt)

def _generate_random_password(length: int = 6) -> str:
    """生成随机 6 位数字密码"""
    return ''.join(random.choices(string.digits, k=length))


def _get_pip_command() -> list:
    """
    获取当前 Python 环境的 pip 命令。
    使用 sys.executable -m pip 确保使用正确的 pip，支持 pipx/venv 等环境。
    """
    import sys
    return [sys.executable, "-m", "pip"]


def _proc_create_encrypted_zip(pdf_path: str, output_dir: str) -> Tuple[Optional[str], str]:
    """
    创建加密的 ZIP 压缩包。
    返回 (压缩包路径，密码)
    """
    try:
        import pyzipper
    except ImportError:
        log_warn("未安装 pyzipper，尝试安装...")
        import subprocess
        subprocess.check_call(_get_pip_command() + ["install", "pyzipper"])
        import pyzipper
    
    password = _generate_random_password(6)
    pdf_name = os.path.basename(pdf_path)
    zip_name = f"（密码：{password}）.zip"
    zip_path = os.path.join(output_dir, zip_name)
    
    # 使用 AES 加密创建 ZIP
    with pyzipper.AESZipFile(
        zip_path, 'w',
        compression=pyzipper.ZIP_LZMA,
        encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode('ascii'))
        zf.write(pdf_path, pdf_name)
    
    log_info(f"已创建加密压缩包：{zip_path}, 密码：{password}")
    return zip_path, password


def _proc_all2pdf(album_dir: str, base_dir: str, title: str) -> Optional[str]:
    from jm_bot.jm_pdf import all2PDF  # 延迟导入以避免主进程加载时的依赖问题
    return all2PDF(album_dir, base_dir, title)

def _safe_rmtree(path: Optional[str]) -> None:
    try:
        if not path:
            return
        p = pathlib.Path(path)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    except Exception as e:
        log_warn(f"删除临时目录失败：{e!r} path={path}")

def _cleanup_startup_work_root() -> None:
    try:
        root = pathlib.Path(WORK_ROOT)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log_warn(f"启动时清理临时目录失败：{e!r}")


def _get_plain_text_from_event_message(evt: Dict[str, Any]) -> str:
    msg = evt.get("message")
    if isinstance(msg, list):
        try:
            return message_array_to_plain(msg)
        except Exception:
            return json.dumps(msg, ensure_ascii=False)
    return str(msg)


async def _call_and_get_message_id(coro) -> Optional[int]:
    """
    发送消息后取返回的 message_id（各实现可能不同，尝试从 data.message_id 获取）。
    """
    try:
        resp = await coro
        data = resp.get("data") or {}
        mid = data.get("message_id")
        if isinstance(mid, int):
            return mid
        # 有的实现是字符串
        if isinstance(mid, str) and mid.isdigit():
            return int(mid)
        return None
    except Exception as e:
        log_warn(f"Send message failed: {e!r}")
        return None


def _prepare_temp_yaml(jm_yaml_src_path: str, work_dir: str) -> str:
    with open(jm_yaml_src_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "dir_rule" not in data or not isinstance(data["dir_rule"], dict):
        data["dir_rule"] = {}
    data["dir_rule"]["base_dir"] = work_dir
    os.makedirs(work_dir, exist_ok=True)
    temp_yaml = os.path.join(work_dir, "config.yml")
    with open(temp_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    return temp_yaml


def _find_album_dir_in_work(work_dir: str, album_id: str) -> Optional[str]:
    """
    在 work_dir 下查找漫画目录（优化版本，避免不必要的遍历）
    """
    base = pathlib.Path(work_dir)
    if not base.exists():
        return None
    
    # 1. 优先：精确匹配 album_id
    exact = base / album_id
    if exact.exists() and exact.is_dir():
        return str(exact)
    
    # 2. 次选：目录名包含 album_id（第一个匹配即返回）
    try:
        for p in base.iterdir():
            if p.is_dir() and album_id in p.name:
                return str(p)
    except Exception:
        pass
    
    # 3. 最后：返回第一个目录（通常 work_dir 只有一个下载的目录）
    try:
        for p in base.iterdir():
            if p.is_dir():
                return str(p)
    except Exception:
        pass
    
    return None


def _load_jm_pdf_base_dir(config_path: str = "jm_bot/jm_pdf/config.yml") -> str:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return str(((data.get("dir_rule") or {}).get("base_dir")) or ".")


def _find_album_dir(base_dir: str, album_id: str) -> Optional[str]:
    """
    在 base_dir 下查找目录：
      - 优先 base_dir/album_id
      - 其次 目录名包含 album_id 的第一个匹配
      - 若都未找到，返回 None
    """
    base = pathlib.Path(base_dir)
    if not base.exists():
        return None
    exact = base / album_id
    if exact.exists() and exact.is_dir():
        return str(exact)

    # 次选：包含 album_id 的目录
    for entry in base.iterdir():
        if entry.is_dir() and album_id in entry.name:
            return str(entry)
    return None


def _list_numeric_subdirs(root: pathlib.Path) -> List[pathlib.Path]:
    items: List[Tuple[int, pathlib.Path]] = []
    if not root.exists():
        return []
    for entry in root.iterdir():
        if entry.is_dir():
            try:
                idx = int(entry.name)
                items.append((idx, entry))
            except Exception:
                continue
    items.sort(key=lambda x: x[0])
    return [p for _, p in items]


def _list_images_in_dir(d: pathlib.Path) -> List[pathlib.Path]:
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files: List[pathlib.Path] = []
    if not d.exists():
        return files
    for entry in d.iterdir():
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            files.append(entry)
    files.sort(key=lambda p: p.name)
    return files


def _collect_preview_images(album_dir: str, limit: int = PREVIEW_IMAGE_COUNT) -> List[str]:
    """
    收集若干预览图片路径（优先按数字子目录排序，每个章节内按文件名排序）。
    """
    root = pathlib.Path(album_dir)
    previews: List[str] = []
    chapters = _list_numeric_subdirs(root)
    if not chapters:
        chapters = [root]
    for ch in chapters:
        for p in _list_images_in_dir(ch):
            previews.append(str(p))
            if len(previews) >= limit:
                return previews
    return previews


async def _download_album_with_jmcomic(album_id: str, jm_yaml_path: str) -> None:
    """
    使用独立进程执行 jmcomic 下载，彻底避免阻塞主进程事件循环。
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_get_process_pool(), _proc_download_album, album_id, jm_yaml_path)


async def handle_jm_command(client: OneBotWSClient, cfg: AppConfig, group_id: int, album_id: str) -> None:
    """
    整个处理流水线（见模块顶部说明）。
    """
    work_dir: Optional[str] = None
    # 1) 群内确认开始
    await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text(f"开始处理 jm {album_id}，请稍候……")]))

    # 获取机器人自身账号（用于给自己发）
    self_id: Optional[int] = None
    try:
        info = await client.call_api("get_login_info", {})
        self_id = info.get("data", {}).get("user_id")
        if isinstance(self_id, str) and self_id.isdigit():
            self_id = int(self_id)
    except Exception as e:
        log_warn(f"获取登录信息失败：{e!r}")

    if not self_id:
        await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text("发送失败")]))
        tb = "无法获取机器人自身QQ号（get_login_info 返回空或异常）"
        await _notify_admins(client, cfg, f"[处理异常] group={group_id}\n{tb}")
        if CLEANUP_AFTER_SEND:
            _safe_rmtree(work_dir)
        return

    # 2) 下载与 PDF 合成
    jm_yaml_src = "jm_bot/jm_pdf/config.yml"
    work_dir = os.path.join(WORK_ROOT, f"{album_id}-{int(time.time())}")
    os.makedirs(work_dir, exist_ok=True)
    temp_yaml = _prepare_temp_yaml(jm_yaml_src, work_dir)
    base_dir = work_dir

    # 下载
    try:
        await _download_album_with_jmcomic(album_id, temp_yaml)
    except Exception as e:
        await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text("发送失败")]))
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        await _notify_admins(client, cfg, f"[下载失败] group={group_id} album={album_id}\n{tb}")
        if CLEANUP_AFTER_SEND:
            _safe_rmtree(work_dir)
        return

    # 定位本地目录
    album_dir = _find_album_dir_in_work(work_dir, album_id)
    if not album_dir:
        await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text("发送失败")]))
        await _notify_admins(client, cfg, f"[定位目录失败] group={group_id} album={album_id} work_dir={work_dir}")
        if CLEANUP_AFTER_SEND:
            _safe_rmtree(work_dir)
        return

    # PDF 合成（使用你迁移过来的 all2PDF）
    pdf_path: Optional[str] = None
    try:
        loop = asyncio.get_running_loop()
        pdf_path = await loop.run_in_executor(_get_process_pool(), _proc_all2pdf, album_dir, base_dir, os.path.basename(album_dir))
    except Exception as e:
        log_warn(f"PDF 合成失败（继续流程，仅发送图片与文本）：{e!r}")

    # 创建加密 ZIP 压缩包
    zip_path: Optional[str] = None
    zip_password: Optional[str] = None
    if pdf_path and os.path.exists(pdf_path):
        try:
            loop = asyncio.get_running_loop()
            zip_path, zip_password = await loop.run_in_executor(_get_process_pool(), _proc_create_encrypted_zip, pdf_path, base_dir)
            log_info(f"压缩包已创建：{zip_path}, 密码：{zip_password}")
        except Exception as e:
            log_warn(f"创建加密压缩包失败：{e!r}")
            zip_path = None

    # 3) 向群发送加密压缩包（只发送一次，不回退）
    ok = False
    last_exc_tb: Optional[str] = None
    file_sent_info = None
    file_to_send = zip_path if (zip_path and os.path.exists(zip_path)) else pdf_path
    
    if file_to_send and os.path.exists(file_to_send):
        file_name = os.path.basename(file_to_send)
        try:
            # 使用群文件上传接口发送（增加超时时间，大文件可能需要更久）
            resp = await client.upload_group_file(group_id, file_to_send, name=file_name, timeout=120.0)
            file_sent_info = resp
            # 只要返回了响应就认为成功
            ok = True
            log_info(f"upload_group_file 完成：{resp}")
        except asyncio.TimeoutError:
            # 超时错误：文件可能已经发送成功，只是响应慢了
            log_warn("upload_group_file 超时（文件可能已发送成功）")
            ok = True  # 视为成功，避免误报
        except Exception as e:
            # 其他异常才认为是失败
            log_warn(f"upload_group_file 失败：{e!r}")
            last_exc_tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            ok = False
    else:
        log_warn("未生成文件，无法发送到群")

    # 发送详细信息到群（只有在成功发送 ZIP 压缩包时才发送详情）
    if ok and zip_path and zip_password:
        try:
            # 统计图片总数作为参考
            total_images = 0
            root = pathlib.Path(album_dir)
            chapters = _list_numeric_subdirs(root)
            if not chapters:
                chapters = [root]
            for ch in chapters:
                total_images += len(_list_images_in_dir(ch))

            # 获取漫画名字（从目录名提取）
            comic_name = os.path.basename(album_dir)
            
            info_text = (
                f"📚 漫画：{comic_name}\n"
                f"漫画 ID: {album_id}\n"
                f"图片数量：{total_images}\n"
                f"压缩包：{os.path.basename(zip_path)}\n"
                f"解压密码：{zip_password}"
            )
            await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text(info_text)]))
        except Exception as e:
            log_warn(f"发送信息到群失败：{e!r}")

    if ok:
        await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text(f"已完成 jm {album_id} 的发送。")]))
    else:
        await _call_and_get_message_id(client.send_group_message(group_id, [MSG.text("发送失败")]))
        await _notify_admins(client, cfg, f"[群发送失败] group={group_id} album={album_id}\n{last_exc_tb or '(无异常堆栈)'}")
    if CLEANUP_AFTER_SEND:
        _safe_rmtree(work_dir)

# 工具：管理员判断与群开关持久化
def _is_admin(cfg: AppConfig, user_id: Any) -> bool:
    try:
        uid = int(user_id)
    except Exception:
        return False
    return uid in (cfg.bot.admins or [])

def _load_group_state() -> None:
    try:
        p = pathlib.Path(STATE_FILE)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            GROUP_ENABLED.clear()
            for k, v in data.items():
                try:
                    gid = int(k)
                    GROUP_ENABLED[gid] = bool(v)
                except Exception:
                    continue
    except Exception as e:
        log_warn(f"加载群开关状态失败：{e!r}")

async def _save_group_state() -> None:
    try:
        p = pathlib.Path(STATE_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        # STATE_LOCK 可能在模块导入时未初始化，需要动态创建
        global STATE_LOCK
        if STATE_LOCK is None:
            STATE_LOCK = asyncio.Lock()
        async with STATE_LOCK:
            content = json.dumps({str(k): bool(v) for k, v in GROUP_ENABLED.items()}, ensure_ascii=False, indent=2)
            # 使用同步写入（快速）即可
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(p)
    except Exception as e:
        log_warn(f"保存群开关状态失败：{e!r}")

def is_group_enabled(group_id: int) -> bool:
    return GROUP_ENABLED.get(int(group_id), False)

async def set_group_enabled(group_id: int, enabled: bool) -> None:
    GROUP_ENABLED[int(group_id)] = bool(enabled)
    await _save_group_state()

async def _notify_admins(client: OneBotWSClient, cfg: AppConfig, text: str) -> None:
    """
    将错误详情私发给管理员列表（若配置存在）。
    """
    admins = getattr(cfg.bot, "admins", []) or []
    for uid in admins:
        try:
            await _call_and_get_message_id(client.send_private_message(int(uid), [MSG.text(text)]))
            await asyncio.sleep(0.05)
        except Exception as e:
            log_warn(f"通知管理员失败 uid={uid}: {e!r}")

async def handle_update_command(client: OneBotWSClient, cfg: AppConfig, group_id: int) -> None:
    """
    处理更新命令：更新 jmcomic 库并重启项目
    """
    import sys
    import subprocess
    
    await _call_and_get_message_id(
        client.send_group_message(group_id, [MSG.text("开始更新 jmcomic 库，请稍候...")])
    )
    
    try:
        # 执行 pip install --upgrade jmcomic (使用通用 pip 命令，支持 pipx/venv 等环境)
        log_info("开始更新 jmcomic 库...")
        result = subprocess.run(
            _get_pip_command() + ["install", "--upgrade", "jmcomic"],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            log_info(f"jmcomic 更新成功：{result.stdout}")
            await _call_and_get_message_id(
                client.send_group_message(group_id, [MSG.text("jmcomic 库更新成功！正在重启项目...")])
            )
            
            # 等待消息发送完成
            await asyncio.sleep(1)
            
            # 通过退出码 3 触发重启（由启动脚本处理）
            log_info("准备重启项目（退出码 3）...")
            sys.exit(3)
        else:
            error_msg = f"更新失败：{result.stderr}"
            log_err(error_msg)
            await _call_and_get_message_id(
                client.send_group_message(group_id, [MSG.text(f"更新失败：\n{result.stderr[:500]}")])
            )
    except subprocess.TimeoutExpired:
        await _call_and_get_message_id(
            client.send_group_message(group_id, [MSG.text("更新超时，请稍后重试")])
        )
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log_err(f"更新异常：{tb}")
        await _call_and_get_message_id(
            client.send_group_message(group_id, [MSG.text(f"更新异常：{str(e)[:200]}")])
        )

# -----------------------------
# 启动与事件注册
# -----------------------------
async def _check_group_cooldown(cfg: AppConfig, group_id: int) -> Tuple[bool, int]:
    """
    检查该群是否处于冷却中。
    返回 (允许执行, 剩余秒数)；若未开启冷却或配置为0则永远允许。
    注意：不在此处开启冷却；应在命令执行结束后调用 _start_group_cooldown。
    """
    enabled = getattr(cfg.bot, "per_group_cooldown_enabled", False)
    seconds = int(getattr(cfg.bot, "per_group_cooldown_seconds", 0) or 0)
    if not enabled or seconds <= 0:
        return True, 0
    gid = int(group_id)
    # 统一以 int 作为键，避免 str/int 混用导致查不到
    lock = GROUP_COOLDOWN_LOCKS.setdefault(gid, asyncio.Lock())
    async with lock:
        now = time.time()
        next_ts = GROUP_COOLDOWN_NEXT_TS.get(gid, 0.0)
        if now < next_ts:
            remain = int(next_ts - now + 0.999)
            log_info(f"分群冷却：group={gid} 冷却中 剩余={remain}s")
            return False, remain
        # 无冷却或已到期
        return True, 0

async def _start_group_cooldown(cfg: AppConfig, group_id: int) -> None:
    """
    开启该群的冷却计时（若启用且秒数>0）。应在命令执行结束后调用（无论成功失败）。
    """
    enabled = getattr(cfg.bot, "per_group_cooldown_enabled", False)
    seconds = int(getattr(cfg.bot, "per_group_cooldown_seconds", 0) or 0)
    if not enabled or seconds <= 0:
        return
    gid = int(group_id)
    lock = GROUP_COOLDOWN_LOCKS.setdefault(gid, asyncio.Lock())
    async with lock:
        ts = time.time() + seconds
        GROUP_COOLDOWN_NEXT_TS[gid] = ts
        log_info(f"启动分群冷却：group={gid} seconds={seconds} until={ts}")

def _install_event_handler(client: OneBotWSClient, cfg: AppConfig) -> None:
    async def on_event(evt: Dict[str, Any]) -> None:
        if evt.get("post_type") != "message":
            return
        if evt.get("message_type") != "group":
            return

        group_id = evt.get("group_id")
        if not group_id:
            return

        user_id = evt.get("user_id")
        
        # 延迟获取文本，只在需要时才处理
        text: Optional[str] = None
        
        def get_text() -> str:
            nonlocal text
            if text is None:
                text = _get_plain_text_from_event_message(evt)
            return text

        # 管理开关命令（使用正则严格匹配）
        if CMD_ENABLE.match(get_text()):
            if _is_admin(cfg, user_id):
                await set_group_enabled(int(group_id), True)
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("已开启 jm 功能，本群成员可使用 /jm 和 /帮助。")])
                )
            else:
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("无权限：仅机器人管理员可用 /开启jm。")])
                )
            return

        if CMD_DISABLE.match(get_text()):
            if _is_admin(cfg, user_id):
                await set_group_enabled(int(group_id), False)
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("已关闭 jm 功能。")])
                )
            else:
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("无权限：仅机器人管理员可用 /关闭jm。")])
                )
            return

        if CMD_UPDATE.match(get_text()):
            if _is_admin(cfg, user_id):
                await handle_update_command(client, cfg, int(group_id))
            else:
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("无权限：仅机器人管理员可用 /更新jm。")])
                )
            return

        if CMD_HELP.match(get_text()):
            # 未开启则提示
            if not is_group_enabled(int(group_id)):
                await _call_and_get_message_id(
                    client.send_group_message(int(group_id), [MSG.text("本群未开启 jm 功能，请机器人管理员发送 /开启jm。")])
                )
                return
            help_text = (
                "命令列表：\n"
                "1) /jm <本子ID> - 拉取漫画并发送（普通用户可用）\n"
                "2) /帮助 - 显示本帮助（普通用户可用）\n"
                "3) /开启jm - 启用本群（机器人管理员可用）\n"
                "4) /关闭jm - 禁用本群（机器人管理员可用）\n"
                "5) /更新jm - 更新jmcomic库并重启（机器人管理员可用）"
            )
            await _call_and_get_message_id(
                client.send_group_message(int(group_id), [MSG.text(help_text)])
            )
            return

        # 解析 /jm 命令（使用 match 确保命令在开头）
        m = CMD_JM.match(get_text())
        if not m:
            return

        album_id = m.group(1)
        log_info(f"命令触发：group={group_id} jm {album_id}")
        # 群开关检查
        if not is_group_enabled(int(group_id)):
            await _call_and_get_message_id(
                client.send_group_message(int(group_id), [MSG.text("本群未开启 jm 功能，请机器人管理员发送 /开启jm。")])
            )
            return
        # 冷却检查（分群）
        allowed, remain = await _check_group_cooldown(cfg, int(group_id))
        if not allowed:
            await _call_and_get_message_id(
                client.send_group_message(int(group_id), [MSG.text(f"冷却中，还需 {remain}s 后才能再次使用 /jm")])
            )
            return
        # 串行处理，避免并发下载拥塞；若群正在处理则拒绝
        gid = int(group_id)
        if GROUP_BUSY.get(gid):
            await _call_and_get_message_id(
                client.send_group_message(gid, [MSG.text("当前已有任务在处理，请稍候再试。")])
            )
            return
        GROUP_BUSY[gid] = True
        try:
            await handle_jm_command(client, cfg, gid, album_id)
        except Exception as e:
            log_err(f"处理命令异常：{e!r}")
            await _call_and_get_message_id(client.send_group_message(gid, [MSG.text("发送失败")]))
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            await _notify_admins(client, cfg, f"[处理异常] group={group_id}\n{tb}")
        finally:
            # 清理“正在处理”标记，并在命令执行完成后开启冷却（符合“执行后开始冷却”的需求）
            GROUP_BUSY.pop(gid, None)
            await _start_group_cooldown(cfg, gid)

    # 将同步 on_event 包装为异步执行
    def wrapper(evt: Dict[str, Any]) -> None:
        asyncio.create_task(on_event(evt))

    client.on_event = wrapper


async def _main_async() -> None:
    cfg = load_config()
    # 初始化运行时锁与加载持久化群开关
    global STATE_LOCK
    if STATE_LOCK is None:
        STATE_LOCK = asyncio.Lock()
    _load_group_state()
    _cleanup_startup_work_root()
    client = OneBotWSClient(cfg)
    _install_event_handler(client, cfg)
    try:
        await client.run_forever()
    finally:
        # 关闭进程池，避免子进程残留
        global PROCESS_POOL
        if PROCESS_POOL is not None:
            PROCESS_POOL.shutdown(wait=False, cancel_futures=True)
            PROCESS_POOL = None


if __name__ == "__main__":
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        log_info("Interrupted by user")
