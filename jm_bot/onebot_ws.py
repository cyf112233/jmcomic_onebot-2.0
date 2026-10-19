# -*- coding: utf-8 -*-
"""OneBot v11 正向 WebSocket 客户端。

特性：

* 单条连接同时处理事件推送与 API 响应（echo 关联）；
* 断线自动重连，指数退避；
* 兼容 websockets 10 ~ 17（请求头参数名不同）；
* 事件回调以异步任务执行，异常会记录完整堆栈；
* 日志中的 access_token 会被脱敏。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import random
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets

from .config import AppConfig, OneBotConfig, build_ws_connect_params
from .logging_utils import format_traceback, log_err, log_info, log_warn
from .message import Message

# 一条 WS 消息的最大体积（16MB）
MAX_FRAME_SIZE = 16 * 1024 * 1024


def message_array_to_plain(message: Message) -> str:
    """把数组格式消息转换为终端可读文本。"""
    parts: List[str] = []
    for segment in message:
        seg_type = str(segment.get("type", ""))
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            parts.append(f"[@{data.get('qq', '')}]")
        elif seg_type == "image":
            value = data.get("file", "")
            brief = "..." if isinstance(value, str) and len(value) > 40 else value
            parts.append(f"[image:{brief}]")
        elif seg_type == "reply":
            parts.append(f"[reply:{data.get('id', '')}]")
        else:
            parts.append(f"[{seg_type}]")
    return "".join(parts)


def _redact_url(url: str) -> str:
    """隐藏 URL query 中的 access_token，避免日志泄露。"""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "***" if key.lower() in ("access_token", "token") else value)
        for key, value in pairs
    ]
    return urlunparse(parsed._replace(query=urlencode(redacted)))


def compute_backoff(
    attempt: int,
    base: float,
    cap: float = 60.0,
    jitter: float = 0.2,
) -> float:
    """第 ``attempt`` 次重连的等待秒数（指数退避 + 抖动）。"""
    delay = min(max(1.0, float(base)) * (2 ** max(0, int(attempt) - 1)), cap)
    if jitter > 0:
        delay += random.uniform(0, delay * jitter)
    return delay


def is_api_success(response: Any) -> bool:
    """判断 OneBot API 响应是否成功。"""
    if not isinstance(response, dict):
        return False

    status = response.get("status")
    if status is not None and str(status).lower() not in (
        "ok",
        "async",
        "accepted",
    ):
        return False

    retcode = response.get("retcode")
    if retcode is None:
        return True
    try:
        return int(retcode) == 0
    except (TypeError, ValueError):
        return False


def _append_query(url: str, extra: Dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(extra)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _file_value(file_path: str, *, use_uri: bool) -> str:
    """把本地文件转换为 OneBot 可接受的形式。"""
    text = str(file_path)
    if text.startswith(
        ("http://", "https://", "base64://", "file://")
    ):
        return text
    path = pathlib.Path(text).expanduser().resolve()
    return path.as_uri() if use_uri else str(path)


class OneBotWSClient:
    """极简 OneBot v11 客户端。"""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ob: OneBotConfig = cfg.onebot
        self._ws: Optional[Any] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._event_tasks: Set[asyncio.Task] = set()
        self._echo_seq = 0
        self._stopping = False

        # 由主逻辑注入；可以是普通函数或协程函数
        self.on_event: Optional[Callable[[Dict[str, Any]], Any]] = None

    # ------------------------------------------------------------------
    # 连接与接收
    # ------------------------------------------------------------------
    async def run_forever(self) -> None:
        """持续连接、接收事件，断线后自动重连。"""
        attempt = 0
        base = max(1, int(self._ob.reconnect_interval))

        while not self._stopping:
            url, kwargs = self._connect_kwargs()
            try:
                log_info(f"正在连接 OneBot WS：{_redact_url(url)}")
                async with websockets.connect(url, **kwargs) as ws:
                    self._ws = ws
                    attempt = 0
                    log_info("OneBot WS 已连接。")
                    await self._receiver_loop(ws)
                    if not self._stopping:
                        log_warn("OneBot WS 连接已关闭。")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stopping:
                    log_warn(f"OneBot WS 断开：{exc!r}")
            finally:
                self._ws = None
                self._fail_pending(ConnectionError("OneBot WS 连接已断开"))

            if self._stopping:
                break

            attempt += 1
            delay = compute_backoff(attempt, base)
            log_info(f"{delay:.1f} 秒后重连（第 {attempt} 次）……")
            await asyncio.sleep(delay)

    def _connect_kwargs(self) -> tuple[str, Dict[str, Any]]:
        url, headers = build_ws_connect_params(self._ob)
        kwargs: Dict[str, Any] = {
            "open_timeout": self._ob.connect_timeout,
            "ping_interval": 30,
            "ping_timeout": 20,
            "close_timeout": 10,
            "max_size": MAX_FRAME_SIZE,
        }

        if headers:
            try:
                parameters = inspect.signature(websockets.connect).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "additional_headers" in parameters:
                kwargs["additional_headers"] = headers
            elif "extra_headers" in parameters:
                kwargs["extra_headers"] = headers
            else:
                # 老版本 websockets 不支持请求头时退回 query 传 token
                url = _append_query(
                    url, {"access_token": self._ob.access_token.strip()}
                )

        return url, kwargs

    async def _receiver_loop(self, ws: Any) -> None:
        while True:
            raw = await ws.recv()
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")

            try:
                message = json.loads(raw)
            except Exception:
                log_warn(f"收到非 JSON 数据：{raw!r}")
                continue

            if not isinstance(message, dict):
                log_warn(f"收到未知 JSON 结构：{message!r}")
                continue

            # 事件推送
            if "post_type" in message:
                self._handle_event(message)
                continue

            # API 响应
            if "status" in message or "retcode" in message or "echo" in message:
                echo = str(message.get("echo", ""))
                future = self._pending.pop(echo, None)
                if future is not None and not future.done():
                    future.set_result(message)
                else:
                    log_warn(f"收到无法匹配的 API 响应：echo={echo}")
                continue

            log_warn(f"收到未知帧：{message}")

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _handle_event(self, event: Dict[str, Any]) -> None:
        if self.cfg.bot.verbose_event_log:
            self._print_event(event)

        if self.on_event is None:
            return

        try:
            result = self.on_event(event)
        except Exception as exc:
            log_err(
                f"事件回调同步异常：{exc!r}\n{format_traceback(exc)}"
            )
            return

        if not inspect.isawaitable(result):
            return

        task = asyncio.create_task(result)
        self._event_tasks.add(task)
        task.add_done_callback(self._on_event_done)

    def _on_event_done(self, task: asyncio.Task) -> None:
        self._event_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log_err(
                f"事件处理异常：{exc!r}\n{format_traceback(exc)}"
            )

    def _print_event(self, event: Dict[str, Any]) -> None:
        post_type = event.get("post_type")
        if post_type != "message":
            log_info(f"[Event:{post_type}] {event}")
            return

        message_type = event.get("message_type")
        user_id = event.get("user_id")
        group_id = event.get("group_id")
        message = event.get("message")

        if isinstance(message, list):
            plain = message_array_to_plain(message)
        else:
            plain = str(message)

        if message_type == "group":
            log_info(f"[Group:{group_id}] <{user_id}> {plain}")
        elif message_type == "private":
            log_info(f"[Private] <{user_id}> {plain}")
        else:
            log_info(f"[Msg:{message_type}] <{user_id}> {plain}")

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    async def call_api(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        ws = self._require_ws()
        echo = self._next_echo()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future

        payload = json.dumps(
            {"action": action, "params": params or {}, "echo": echo},
            ensure_ascii=False,
        )

        try:
            await ws.send(payload)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(echo, None)
            if not future.done():
                future.cancel()

    async def send_private_message(
        self, user_id: int, message: Message
    ) -> Dict[str, Any]:
        return await self.call_api(
            "send_private_msg",
            {"user_id": int(user_id), "message": message},
        )

    async def send_group_message(
        self, group_id: int, message: Message
    ) -> Dict[str, Any]:
        return await self.call_api(
            "send_group_msg",
            {"group_id": int(group_id), "message": message},
        )

    async def send_private_forward(
        self, user_id: int, nodes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self.call_api(
            "send_private_forward_msg",
            {"user_id": int(user_id), "messages": nodes},
        )

    async def send_group_forward(
        self, group_id: int, nodes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self.call_api(
            "send_group_forward_msg",
            {"group_id": int(group_id), "messages": nodes},
        )

    async def upload_private_file(
        self,
        user_id: int,
        file_path: str,
        name: Optional[str] = None,
        *,
        use_uri: bool = True,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": int(user_id),
            "file": _file_value(file_path, use_uri=use_uri),
            "name": name or pathlib.Path(file_path).name,
        }
        return await self.call_api("upload_private_file", params)

    async def upload_group_file(
        self,
        group_id: int,
        file_path: str,
        name: Optional[str] = None,
        folder: Optional[str] = None,
        timeout: float = 120.0,
        *,
        use_uri: bool = True,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "group_id": int(group_id),
            "file": _file_value(file_path, use_uri=use_uri),
            "name": name or pathlib.Path(file_path).name,
        }
        if folder:
            params["folder"] = folder
        return await self.call_api("upload_group_file", params, timeout=timeout)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def stop(self) -> None:
        """主动停止客户端。"""
        self._stopping = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

        for task in list(self._event_tasks):
            task.cancel()
        self._event_tasks.clear()
        self._fail_pending(ConnectionError("客户端已停止"))

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _fail_pending(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _require_ws(self) -> Any:
        if self._ws is None:
            raise RuntimeError("OneBot WebSocket 尚未连接")
        return self._ws

    def _next_echo(self) -> str:
        self._echo_seq += 1
        return f"jm-bot-{self._echo_seq}"


__all__ = [
    "OneBotWSClient",
    "compute_backoff",
    "is_api_success",
    "message_array_to_plain",
]
