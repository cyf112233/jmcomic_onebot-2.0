# -*- coding: utf-8 -*-
"""
OneBot v11 消息段（数组格式）构造工具

提供便捷函数来构造各类消息段，保证与 OneBot 协议兼容。
大多数段的 data 字段值类型为字符串，以便与 CQ 码互转。
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional, Union
import base64
import pathlib
import re


MessageSegment = Dict[str, Any]
Message = List[MessageSegment]


def _seg(seg_type: str, data: Optional[Dict[str, Any]] = None) -> MessageSegment:
    return {"type": seg_type, "data": {} if data is None else data}


def text(content: str) -> MessageSegment:
    """纯文本消息段"""
    return _seg("text", {"text": content})


def face(face_id: Union[int, str]) -> MessageSegment:
    """QQ 表情消息段"""
    return _seg("face", {"id": str(face_id)})


def at(qq: Union[int, str]) -> MessageSegment:
    """@某人，qq='all' 表示全体成员"""
    qq_str = str(qq)
    return _seg("at", {"qq": qq_str})


def reply(message_id: Union[int, str]) -> MessageSegment:
    """引用回复某条消息"""
    return _seg("reply", {"id": str(message_id)})


def image(
    file: str,
    type_: Optional[str] = None,  # 'flash' 表示闪照
    cache: Optional[Union[bool, int]] = None,
    proxy: Optional[Union[bool, int]] = None,
    timeout: Optional[Union[int, float]] = None,
) -> MessageSegment:
    """图片消息段，file 支持：
    - 收到的图片文件名（由上游返回）
    - 本地文件路径（将自动转换为 file:/// URI）
    - 网络 URL（http/https）
    - Base64（以 base64:// 前缀）
    """
    file_value = _normalize_file_input(file)
    data: Dict[str, Any] = {"file": file_value}
    if type_:
        data["type"] = type_
    if cache is not None:
        data["cache"] = _bool_to_01(cache)
    if proxy is not None:
        data["proxy"] = _bool_to_01(proxy)
    if timeout is not None:
        data["timeout"] = timeout
    return _seg("image", data)


def record(
    file: str,
    magic: Optional[Union[bool, int]] = None,  # 1 表示变声
    cache: Optional[Union[bool, int]] = None,
    proxy: Optional[Union[bool, int]] = None,
    timeout: Optional[Union[int, float]] = None,
) -> MessageSegment:
    file_value = _normalize_file_input(file)
    data: Dict[str, Any] = {"file": file_value}
    if magic is not None:
        data["magic"] = _bool_to_01(magic)
    if cache is not None:
        data["cache"] = _bool_to_01(cache)
    if proxy is not None:
        data["proxy"] = _bool_to_01(proxy)
    if timeout is not None:
        data["timeout"] = timeout
    return _seg("record", data)


def video(
    file: str,
    cache: Optional[Union[bool, int]] = None,
    proxy: Optional[Union[bool, int]] = None,
    timeout: Optional[Union[int, float]] = None,
) -> MessageSegment:
    file_value = _normalize_file_input(file)
    data: Dict[str, Any] = {"file": file_value}
    if cache is not None:
        data["cache"] = _bool_to_01(cache)
    if proxy is not None:
        data["proxy"] = _bool_to_01(proxy)
    if timeout is not None:
        data["timeout"] = timeout
    return _seg("video", data)

def file_segment(file: str, name: Optional[str] = None) -> MessageSegment:
    """
    Non-standard extension: generic file message segment (supported by some implementations like go-cqhttp).
    file accepts local path/URL/base64://; name sets the display name.
    """
    file_value = _normalize_file_input(file)
    data: Dict[str, Any] = {"file": file_value}
    if name:
        data["name"] = name
    return _seg("file", data)


def dice() -> MessageSegment:
    return _seg("dice", {})


def rps() -> MessageSegment:
    return _seg("rps", {})


def share(url: str, title: str, content_text: Optional[str] = None, image_url: Optional[str] = None) -> MessageSegment:
    data: Dict[str, Any] = {"url": url, "title": title}
    if content_text:
        data["content"] = content_text
    if image_url:
        data["image"] = image_url
    return _seg("share", data)


def location(lat: Union[str, float], lon: Union[str, float], title: Optional[str] = None, content_text: Optional[str] = None) -> MessageSegment:
    data: Dict[str, Any] = {"lat": str(lat), "lon": str(lon)}
    if title:
        data["title"] = title
    if content_text:
        data["content"] = content_text
    return _seg("location", data)


def music_platform(type_: str, song_id: Union[int, str]) -> MessageSegment:
    """音乐分享：type_ in {'qq','163','xm'}"""
    return _seg("music", {"type": type_, "id": str(song_id)})


def music_custom(url: str, audio: str, title: str, content_text: Optional[str] = None, image_url: Optional[str] = None) -> MessageSegment:
    data: Dict[str, Any] = {"type": "custom", "url": url, "audio": audio, "title": title}
    if content_text:
        data["content"] = content_text
    if image_url:
        data["image"] = image_url
    return _seg("music", data)


def xml(data_text: str) -> MessageSegment:
    return _seg("xml", {"data": data_text})


def json_card(data_text: str) -> MessageSegment:
    return _seg("json", {"data": data_text})


def contact_qq(qq_id: Union[int, str]) -> MessageSegment:
    return _seg("contact", {"type": "qq", "id": str(qq_id)})


def contact_group(group_id: Union[int, str]) -> MessageSegment:
    return _seg("contact", {"type": "group", "id": str(group_id)})


def node_id(message_id: Union[int, str]) -> MessageSegment:
    """合并转发节点（引用已有消息ID）"""
    return _seg("node", {"id": str(message_id)})


def node_custom(user_id: Union[int, str], nickname: str, content: Union[str, Message]) -> MessageSegment:
    """合并转发自定义节点
    content 可为 CQ 码字符串或数组格式（消息段列表）
    """
    data: Dict[str, Any] = {"user_id": str(user_id), "nickname": nickname, "content": content}
    return _seg("node", data)


# 便捷组合构造

def message_of(*segments: MessageSegment) -> Message:
    """用多个消息段快速构造消息数组"""
    return list(segments)


def text_message(content: str) -> Message:
    return [text(content)]


def text_and_image(content: str, image_file: str) -> Message:
    return [text(content), image(image_file)]


# 工具函数

_windows_drive_re = re.compile(r"^[A-Za-z]:[\\/]")


def _normalize_file_input(file: str) -> str:
    """将本地路径转换为 file:/// URI；保持 http(s) 与 base64 前缀原样。"""
    if file.startswith("http://") or file.startswith("https://") or file.startswith("base64://") or file.startswith("file:///"):
        return file
    # Windows 绝对路径或相对路径统一转换为 file URI
    p = pathlib.Path(file).expanduser().resolve()
    # path.as_uri() 会生成正确的 file:/// URL 并自动处理空格等
    return p.as_uri()


def encode_file_to_base64_uri(file_path: str) -> str:
    """读取本地文件并返回 base64:// 前缀的内联数据（适合快速发送小文件）。"""
    path = pathlib.Path(file_path).expanduser()
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"base64://{b64}"


def _bool_to_01(v: Union[bool, int]) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    return 1 if int(v) != 0 else 0
