# -*- coding: utf-8 -*-
"""程序入口：依赖检查、配置加载、信号处理与运行循环。"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import importlib.util
import signal
import sys
from typing import List, Optional, Sequence

from . import __version__

_REQUIRED_MODULES = (
    ("websockets", "websockets"),
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("jmcomic", "jmcomic"),
    ("pyzipper", "pyzipper"),
)


def _missing_dependencies() -> List[str]:
    missing = []
    for module, package in _REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    return missing


def _print_missing(missing: Sequence[str]) -> None:
    print(
        "[ERR] 缺少 Python 依赖：" + ", ".join(missing),
        file=sys.stderr,
    )
    print(
        "      请运行 scripts/setup.sh（Linux/macOS）或 "
        "scripts/setup.ps1（Windows）创建项目虚拟环境；",
        file=sys.stderr,
    )
    print(
        "      或手动执行：python -m venv .venv && "
        ".venv/bin/python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    print(
        "      不要使用 sudo pip 安装到系统 Python，以免触发 "
        "PEP 668 externally-managed-environment。",
        file=sys.stderr,
    )


def _run_check(config_path: Optional[str]) -> int:
    missing = _missing_dependencies()
    if missing:
        _print_missing(missing)
        return 2

    print(f"jm-bot {__version__}")
    print(f"Python {sys.version.split()[0]} — {sys.executable}")

    for module, package in _REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            print(f"[ERR] 导入 {module} 失败：{exc}")
            return 2
        try:
            version = importlib.metadata.version(package)
        except Exception:
            version = "?"
        print(f"  {package}: {version}")

    from .config import ConfigError, load_config
    from .paths import ensure_dir

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"[ERR] 配置无效：{exc}")
        return 2

    print(f"配置文件：{cfg.source_path}")
    print(f"OneBot：{cfg.onebot.ws_url}")
    print(f"管理员数量：{len(cfg.bot.admins)}")
    print(f"工作目录：{cfg.bot.work_dir}")
    print(f"jmcomic 配置：{cfg.bot.jmcomic_config}")
    print(
        "多章节拒绝："
        + ("开启" if cfg.bot.reject_multi_chapter else "关闭")
    )

    try:
        work_dir = ensure_dir(cfg.bot.work_dir)
        probe = work_dir / ".jm_bot_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"[ERR] 工作目录不可写：{cfg.bot.work_dir}（{exc}）")
        return 2

    print("[OK] 依赖与配置检查通过。")
    return 0


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows 等平台不支持 add_signal_handler
            continue


async def _run(cfg) -> int:
    from .bot import JmBot
    from .logging_utils import log_err, log_info
    from .onebot_ws import OneBotWSClient
    from .paths import cleanup_job_dirs, ensure_dir

    ensure_dir(cfg.bot.work_dir)
    if not cfg.bot.keep_files:
        removed = cleanup_job_dirs(cfg.bot.work_dir)
        if removed:
            log_info(f"已清理 {removed} 个上次遗留的任务目录。")

    log_info(
        f"jm-bot {__version__} 启动（配置：{cfg.source_path}，"
        f"多章节拒绝：{'开启' if cfg.bot.reject_multi_chapter else '关闭'}）"
    )

    client = OneBotWSClient(cfg)
    bot = JmBot(client, cfg)
    client.on_event = bot.on_event

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    run_task = asyncio.create_task(client.run_forever(), name="onebot")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop")
    restart_task = asyncio.create_task(
        bot.restart_event.wait(), name="restart"
    )
    tasks = {run_task, stop_task, restart_task}

    try:
        done, _ = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        await client.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bot.close()

    if restart_task in done and restart_task.result():
        log_info("已收到重启请求，退出码 3。")
        return 3

    if stop_task in done:
        log_info("收到停止信号，正在退出。")
        return 0

    if run_task in done and not run_task.cancelled():
        exc = run_task.exception()
        if exc is not None:
            log_err(f"OneBot 客户端异常退出：{exc!r}")
            return 1

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jm-bot",
        description="JMComic OneBot v11 QQ 机器人（仅支持单章节漫画）",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="配置文件路径（默认 jm_bot/config.yml，也可用 JM_BOT_CONFIG）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查依赖、配置与工作目录后退出，不连接 OneBot",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"jm-bot {__version__}",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _run_check(args.config)

    missing = _missing_dependencies()
    if missing:
        _print_missing(missing)
        return 2

    from .config import ConfigError, load_config

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[ERR] 配置无效：{exc}", file=sys.stderr)
        return 2

    from .logging_utils import setup_logging

    try:
        setup_logging(cfg.bot.log_level, cfg.bot.log_file)
    except OSError as exc:
        print(f"[ERR] 初始化日志失败：{exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - 顶层兜底
        from .logging_utils import log_exception

        log_exception("程序异常退出", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
