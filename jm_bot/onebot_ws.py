# -*- coding: utf-8 -*-
"""
OneBot v11 正向 WebSocket 客户端（统一连接地址）

功能：
- 读取 jm_bot/config.yml（通过 jm_bot.config）获取统一 ws_url 与鉴权
- 建立到 OneBot 的单一 WS 连接（建议使用根路径 /）
- 解析事件推送（/event），并将其打印到终端（便于调试）
- 支持在同一连接上进行 API 调用（/api），含 echo 关联与超时
- 提供发送私聊/群聊消息的便捷方法（数组格式），兼容图片/文件等消息段

依赖：
  - websockets>=10
  - pyyaml（由 jm_bot.config 使用）
安装示例：
  pip install websockets pyyaml
"""

from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Dict, Optional, Tuple, Callable, List

import websockets
from websockets import WebSocketClientProtocol
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
import pathlib

from .config import load_config, build_ws_connect_params, AppConfig, OneBotConfig
from .message import Message, text

# -------------------------
# 日志输出
# -------------------------
def _ts() -> str:
    return time.strftime("%H:%M:%S")

def log_info(msg: str) -> None:
    print(f"[{_ts()}][INFO] {msg}")

def log_warn(msg: str) -> None:
    print(f"[{_ts()}][WARN] {msg}")

def log_err(msg: str) -> None:
    print(f"[{_ts()}][ERR ] {msg}")

# -------------------------
# URL helpers
# -------------------------
def _append_query(url: str, extra_params: Dict[str, str]) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    qs.update({k: v for k, v in extra_params.items() if v is not None})
    new_query = urlencode(qs)
    return urlunparse(parsed._replace(query=new_query))

def _to_file_uri(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://") or path.startswith("base64://") or path.startswith("file://"):
        return path
    p = pathlib.Path(path).expanduser().resolve()
    try:
        return p.as_uri()
    except Exception:
        return str(p)

# -------------------------
# 工具：将数组格式消息转为终端可读文本
# -------------------------
def message_array_to_plain(m: Message) -> str:
    """
    将 OneBot 数组格式消息转为简单的终端可读文本，便于调试打印。
    非 text 段以 [type] 或 [type:brief] 形式提示。
    """
    parts = []
    for seg in m:
        seg_type = str(seg.get("type", ""))
        data = seg.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            parts.append(f"[@{data.get('qq','')}]")
        elif seg_type == "image":
            val = data.get("file", "")
            brief = "..." if isinstance(val, str) and len(val) > 40 else val
            parts.append(f"[image:{brief}]")
        else:
            parts.append(f"[{seg_type}]")
    return "".join(parts)

# -------------------------
# OneBot 客户端
# -------------------------
class OneBotWSClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ob: OneBotConfig = cfg.onebot
        self._ws: Optional[WebSocketClientProtocol] = None
        self._recv_task: Optional[asyncio.Task] = None

        # echo -> Future 映射，用于关联 API 请求与响应
        self._pending: Dict[str, asyncio.Future] = {}
        self._echo_seq = 0

        # 事件回调（可由主逻辑注入）
        self.on_event: Optional[Callable[[Dict[str, Any]], None]] = None

    async def run_forever(self) -> None:
        """
        持续重连与运行。
        """
        url, headers = build_ws_connect_params(self._ob)
        reconnect_interval = max(1, int(self._ob.reconnect_interval))

        while True:
            try:
                log_info(f"Connecting OneBot WS: {url}")
                # Normalize auth: move token to query parameter to avoid incompatibility with extra_headers
                auth = headers.get("Authorization", "")
                if isinstance(auth, str) and auth.startswith("Bearer "):
                    token = auth[7:]
                    url = _append_query(url, {"access_token": token})
                    headers = {}
                async with websockets.connect(
                    url,
                    open_timeout=self._ob.connect_timeout,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=16 * 1024 * 1024,  # 保护：最大消息 16MB
                ) as ws:
                    self._ws = ws
                    log_info("OneBot WS connected.")
                    await self._handle_connected(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_warn(f"WS disconnected: {e!r}")
            finally:
                self._ws = None
                # 取消接收任务
                if self._recv_task and not self._recv_task.done():
                    self._recv_task.cancel()
                # 将所有 pending 置为异常
                for echo, fut in list(self._pending.items()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("connection lost"))
                self._pending.clear()

            log_info(f"Reconnecting after {reconnect_interval}s ...")
            await asyncio.sleep(reconnect_interval)

    async def _handle_connected(self, ws: WebSocketClientProtocol) -> None:
        self._recv_task = asyncio.create_task(self._receiver_loop(ws))
        try:
            await self._recv_task
        finally:
            if self._recv_task and not self._recv_task.done():
                self._recv_task.cancel()

    async def _receiver_loop(self, ws: WebSocketClientProtocol) -> None:
        """
        接收循环：处理事件推送与 API 响应。
        """
        while True:
            raw = await ws.recv()
            try:
                msg = json.loads(raw)
            except Exception:
                log_warn(f"Non-JSON frame: {raw!r}")
                continue

            # API 响应（带 status/retcode，一般含 echo）
            if "status" in msg or "retcode" in msg:
                echo = str(msg.get("echo", ""))
                fut = self._pending.pop(echo, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                else:
                    log_warn(f"Unmatched API response: echo={echo} resp={msg}")
                continue

            # 事件推送
            if "post_type" in msg:
                self._handle_event(msg)
                continue

            log_warn(f"Unknown frame: {msg}")

    def _handle_event(self, evt: Dict[str, Any]) -> None:
        """
        默认打印到终端。需要时可由主逻辑注入 on_event 进行处理。
        """
        if self.cfg.bot.verbose_event_log:
            self._print_event(evt)
        if self.on_event:
            try:
                self.on_event(evt)
            except Exception as e:
                log_err(f"on_event error: {e!r}")

    def _print_event(self, evt: Dict[str, Any]) -> None:
        pt = evt.get("post_type")
        if pt == "message":
            mtype = evt.get("message_type")
            user_id = evt.get("user_id")
            group_id = evt.get("group_id")
            message = evt.get("message")

            # message 既可能是字符串，也可能是数组格式
            if isinstance(message, list):
                plain = message_array_to_plain(message)  # 数组格式转可读文本
            else:
                plain = str(message)

            if mtype == "group":
                log_info(f"[Group:{group_id}] <{user_id}> {plain}")
            elif mtype == "private":
                log_info(f"[Private] <{user_id}> {plain}")
            else:
                log_info(f"[Msg:{mtype}] <{user_id}> {plain}")
        else:
            # 其他事件类型简单打印
            log_info(f"[Event:{pt}] {evt}")

    # -------------------------
    # API 调用
    # -------------------------
    async def call_api(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 20.0) -> Dict[str, Any]:
        ws = self._require_ws()
        echo = self._next_echo()
        req = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }
        payload = json.dumps(req, ensure_ascii=False)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        await ws.send(payload)
        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            return resp
        except Exception:
            # 超时或异常，清理 pending
            self._pending.pop(echo, None)
            raise

    async def send_private_message(self, user_id: int, message: Message) -> Dict[str, Any]:
        """
        发送私聊消息（数组格式）
        """
        return await self.call_api("send_private_msg", {"user_id": int(user_id), "message": message})

    async def send_group_message(self, group_id: int, message: Message) -> Dict[str, Any]:
        """
        发送群聊消息（数组格式）
        """
        return await self.call_api("send_group_msg", {"group_id": int(group_id), "message": message})

    async def send_private_forward(self, user_id: int, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        发送私聊合并转发消息
        nodes: 由消息段 type="node" 的数组（可用 message.node_custom(...) 构建）
        """
        return await self.call_api("send_private_forward_msg", {"user_id": int(user_id), "messages": nodes})

    async def send_group_forward(self, group_id: int, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        发送群聊合并转发消息
        nodes: 由消息段 type="node" 的数组（可用 message.node_custom(...) 构建）
        """
        return await self.call_api("send_group_forward_msg", {"group_id": int(group_id), "messages": nodes})


    async def upload_private_file(self, user_id: int, file_path: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        上传私聊文件（需 OneBot 实现支持，如 go-cqhttp）
        参数:
          - user_id: 私聊对象 QQ
          - file_path: 本地路径/URL/base64://
          - name: 展示名称（默认取文件名）
        返回:
          - OneBot 实现自定义的数据，若包含 message_id 则可用于合并转发节点
        """
        uri = _to_file_uri(file_path)
        params: Dict[str, Any] = {"user_id": int(user_id), "file": uri, "name": name or pathlib.Path(file_path).name}
        return await self.call_api("upload_private_file", params)

    async def upload_group_file(self, group_id: int, file_path: str, name: Optional[str] = None, folder: Optional[str] = None) -> Dict[str, Any]:
        """
        上传群文件（需 OneBot 实现支持，如 go-cqhttp）
        """
        uri = _to_file_uri(file_path)
        params: Dict[str, Any] = {"group_id": int(group_id), "file": uri, "name": name or pathlib.Path(file_path).name}
        if folder:
            params["folder"] = folder
        return await self.call_api("upload_group_file", params)
    # -------------------------
    # 内部辅助
    # -------------------------
    def _require_ws(self) -> WebSocketClientProtocol:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        return self._ws

    def _next_echo(self) -> str:
        self._echo_seq += 1
        return f"jm-bot-{self._echo_seq}"

# -------------------------
# 简易启动器（仅用于本阶段终端验证）
# -------------------------
async def _demo_main() -> None:
    """
    运行示例：
      python -m jm_bot.onebot_ws
    说明：
      - 仅建立连接并打印事件，不自动回复。
      - 你可在 REPL 或其他逻辑中通过 OneBotWSClient 实例调用 send_* 方法发送消息。
    """
    cfg = load_config()
    client = OneBotWSClient(cfg)
    await client.run_forever()

if __name__ == "__main__":
    try:
        asyncio.run(_demo_main())
    except KeyboardInterrupt:
        log_info("Interrupted by user")
