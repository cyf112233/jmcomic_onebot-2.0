# -*- coding: utf-8 -*-
"""统一日志配置与快捷函数。"""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Union

LOGGER_NAME = "jm_bot"
_LOG_FORMAT = "[%(asctime)s][%(levelname)-5s] %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Union[str, Path]] = None,
) -> None:
    """初始化日志输出（控制台 + 可选滚动文件）。"""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    # 重复调用时先移除本模块添加的 handler，避免日志重复
    for handler in list(logger.handlers):
        if getattr(handler, "_jm_bot_handler", False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - 清理失败不影响主流程
                pass

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    console._jm_bot_handler = True  # type: ignore[attr-defined]
    logger.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        file_handler._jm_bot_handler = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)


def _logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_debug(message: Any) -> None:
    _logger().debug(message)


def log_info(message: Any) -> None:
    _logger().info(message)


def log_warn(message: Any) -> None:
    _logger().warning(message)


def log_err(message: Any) -> None:
    _logger().error(message)


def format_traceback(exc: Optional[BaseException] = None) -> str:
    """格式化异常堆栈；不传参数时使用当前异常。"""
    if exc is None:
        return traceback.format_exc()
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


def log_exception(prefix: str, exc: Optional[BaseException] = None) -> str:
    """记录异常并返回堆栈文本。"""
    detail = format_traceback(exc)
    log_err(f"{prefix}\n{detail}")
    return detail
