# -*- coding: utf-8 -*-
"""单章节完整流水线测试（使用真实 PDF / ZIP 逻辑与进程池）。"""

import asyncio
import re
from pathlib import Path

from PIL import Image

from jm_bot.bot import JmBot
from jm_bot.config import AppConfig, BotConfig, OneBotConfig
from jm_bot.jmcomic_api import AlbumMeta


def _fake_download(album_id, option_file):
    """模拟 jmcomic 下载：创建 <work>/<album>/<chapter>/图片。"""
    work_dir = Path(option_file).parent
    chapter = work_dir / str(album_id) / "111"
    chapter.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), (10, 20, 30)).save(chapter / "1.jpg")
    Image.new("RGB", (40, 60), (30, 20, 10)).save(chapter / "2.jpg")


def _fake_download_ten(album_id, option_file):
    """模拟下载 10 章，每章 1 张图片。"""
    work_dir = Path(option_file).parent
    album = work_dir / str(album_id)
    for index in range(1, 11):
        chapter = album / str(100000 + index)
        chapter.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 30), (index * 10, 20, 30)).save(
            chapter / "1.jpg"
        )


class FakeClient:
    def __init__(self):
        self.texts = []
        self.uploads = []
        self.deleted = []
        self._next_message_id = 1

    @staticmethod
    def _text(message):
        return "".join(
            str((segment.get("data") or {}).get("text", ""))
            for segment in message
            if segment.get("type") == "text"
        )

    async def send_group_message(self, group_id, message):
        text = self._text(message)
        message_id = self._next_message_id
        self._next_message_id += 1
        self.texts.append(text)
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": message_id},
        }

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return {"status": "ok", "retcode": 0}

    async def send_private_message(self, user_id, message):
        return {"status": "ok", "retcode": 0}

    async def upload_group_file(self, group_id, file_path, **kwargs):
        self.uploads.append((group_id, file_path, kwargs))
        return {"status": "ok", "retcode": 0}


def test_single_chapter_pipeline(tmp_path):
    jm_yaml = tmp_path / "jm.yml"
    jm_yaml.write_text("version: '2.0'\n", encoding="utf-8")

    cfg = AppConfig(
        onebot=OneBotConfig(ws_url="ws://127.0.0.1:1"),
        bot=BotConfig(
            per_group_cooldown_enabled=False,
            work_dir=tmp_path / "work",
            state_file=tmp_path / "state.json",
            jmcomic_config=jm_yaml,
            metadata_timeout=5,
            zip_encrypt=True,
            zip_password_length=6,
        ),
    )
    meta = AlbumMeta.from_episodes(
        "123456", "单章测试", 2, [("111", "1", "第1话")]
    )
    client = FakeClient()

    async def scenario():
        bot = JmBot(
            client,
            cfg,
            meta_fetcher=lambda album_id, option_file: meta,
            downloader=_fake_download,
            load_state=False,
        )
        try:
            await bot.handle_jm_command(10001, "123456")
        finally:
            bot.close()

    asyncio.run(scenario())

    assert len(client.uploads) == 1
    upload_path = Path(client.uploads[0][1])
    assert upload_path.suffix == ".zip"
    assert upload_path.is_file() is False  # 任务结束后临时目录已清理

    success = client.texts[-1]
    assert "已发送" in success
    assert "单章测试" in success
    assert "页数：2" in success
    assert "解压密码" in success

    # 发送“正在下载”后撤回“正在检查”（第一条消息 ID 为 1）
    assert any("正在检查" in text for text in client.texts)
    assert any("正在下载" in text for text in client.texts)
    assert not any("开始下载" in text for text in client.texts)
    assert client.deleted == [1]

    password = re.search(r"解压密码：(\d+)", success)
    assert password is not None
    assert password.group(1) not in upload_path.name

    # 任务目录已清理，不残留 <album>-<timestamp>-<hex>
    work = tmp_path / "work"
    assert list(work.iterdir()) == []


def test_ten_chapters_allowed(tmp_path):
    jm_yaml = tmp_path / "jm.yml"
    jm_yaml.write_text("version: '2.0'\n", encoding="utf-8")

    cfg = AppConfig(
        onebot=OneBotConfig(ws_url="ws://127.0.0.1:1"),
        bot=BotConfig(
            per_group_cooldown_enabled=False,
            work_dir=tmp_path / "work",
            state_file=tmp_path / "state.json",
            jmcomic_config=jm_yaml,
            metadata_timeout=5,
            zip_encrypt=True,
            zip_password_length=6,
            reject_multi_chapter=True,
            max_chapters=10,
        ),
    )
    meta = AlbumMeta.from_episodes(
        "654321",
        "十章测试",
        10,
        [
            (str(100000 + index), str(index), f"第{index}话")
            for index in range(1, 11)
        ],
    )
    client = FakeClient()

    async def scenario():
        bot = JmBot(
            client,
            cfg,
            meta_fetcher=lambda album_id, option_file: meta,
            downloader=_fake_download_ten,
            load_state=False,
        )
        try:
            await bot.handle_jm_command(10002, "654321")
        finally:
            bot.close()

    asyncio.run(scenario())

    assert len(client.uploads) == 1
    success = client.texts[-1]
    assert "已发送" in success
    assert "十章测试" in success
    assert "章节：10" in success
    assert "超过限制" not in success
    assert client.deleted == [1]
