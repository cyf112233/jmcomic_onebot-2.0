# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

from jm_bot import bot as bot_module
from jm_bot.bot import JmBot
from jm_bot.config import AppConfig, BotConfig, OneBotConfig
from jm_bot.jmcomic_api import AlbumMeta

MULTI_META = AlbumMeta.from_episodes(
    "422866",
    "多章节测试漫画",
    120,
    [
        (str(100001 + index), str(index + 1), f"第{index + 1}话")
        for index in range(12)
    ],
)

EVENT_TEMPLATE = {
    "post_type": "message",
    "message_type": "group",
    "group_id": 10001,
    "user_id": 20002,
}


def _event(text: str, user_id: int = 20002) -> dict:
    event = dict(EVENT_TEMPLATE)
    event["user_id"] = user_id
    event["message"] = [{"type": "text", "data": {"text": text}}]
    return event


class FakeClient:
    def __init__(self):
        self.group_messages = []
        self.private_messages = []
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
        self.group_messages.append((group_id, text))
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": message_id},
        }

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return {"status": "ok", "retcode": 0}

    async def send_private_message(self, user_id, message):
        self.private_messages.append((user_id, self._text(message)))
        return {"status": "ok", "retcode": 0}

    async def upload_group_file(self, group_id, file_path, **kwargs):
        self.uploads.append((group_id, file_path, kwargs))
        return {"status": "ok", "retcode": 0}


def _config(tmp_path: Path, admins=()) -> AppConfig:
    jm_yaml = tmp_path / "jm.yml"
    jm_yaml.write_text("version: '2.0'\n", encoding="utf-8")
    return AppConfig(
        onebot=OneBotConfig(ws_url="ws://127.0.0.1:1"),
        bot=BotConfig(
            admins=list(admins),
            per_group_cooldown_enabled=False,
            work_dir=tmp_path / "work",
            state_file=tmp_path / "state.json",
            jmcomic_config=jm_yaml,
            metadata_timeout=5,
            reject_multi_chapter=True,
        ),
    )


def _bot(tmp_path, client, calls, admins=()):
    return JmBot(
        client,
        _config(tmp_path, admins=admins),
        meta_fetcher=lambda album_id, option_file: MULTI_META,
        downloader=lambda album_id, option_file: calls.append(album_id),
        load_state=False,
    )


def test_multi_chapter_is_rejected_before_download(tmp_path):
    async def scenario():
        client = FakeClient()
        calls = []
        bot = _bot(tmp_path, client, calls, admins=[99999])
        await bot.handle_jm_command(10001, "422866")
        bot.close()
        return client, calls

    client, calls = asyncio.run(scenario())
    texts = [text for _, text in client.group_messages]

    assert texts, "至少应发送检查消息"
    assert any("正在检查" in text for text in texts)
    assert any("多章节" in text or "超过限制" in text for text in texts)
    assert any(
        f"章节数：{MULTI_META.chapter_count}" in text for text in texts
    )
    assert any("上限：10 章" in text for text in texts)
    assert not any("正在下载" in text for text in texts)
    assert client.deleted == []
    assert calls == [], "超过上限不应进入下载阶段"
    assert client.uploads == []
    assert client.private_messages == []


def test_disabled_group_does_not_start_download(tmp_path):
    async def scenario():
        client = FakeClient()
        calls = []
        bot = _bot(tmp_path, client, calls)
        await bot.on_event(_event("/jm 422866"))
        bot.close()
        return client, calls

    client, calls = asyncio.run(scenario())
    texts = [text for _, text in client.group_messages]

    assert any("尚未开启" in text for text in texts)
    assert not any("正在检查" in text for text in texts)
    assert calls == []


def test_enabled_group_rejects_multi_chapter(tmp_path):
    async def scenario():
        client = FakeClient()
        calls = []
        bot = _bot(tmp_path, client, calls)
        await bot.set_group_enabled(10001, True)
        await bot.on_event(_event("/jm 422866"))
        bot.close()
        return client, calls

    client, calls = asyncio.run(scenario())
    texts = [text for _, text in client.group_messages]

    assert any("正在检查" in text for text in texts)
    assert any("超过限制" in text for text in texts)
    assert not any("正在下载" in text for text in texts)
    assert client.deleted == []
    assert calls == []


def test_incomplete_jm_command_is_ignored(tmp_path):
    async def scenario():
        client = FakeClient()
        calls = []
        bot = _bot(tmp_path, client, calls)
        await bot.set_group_enabled(10001, True)
        await bot.on_event(_event("/jm"))
        bot.close()
        return client, calls

    client, calls = asyncio.run(scenario())

    assert client.group_messages == []
    assert client.deleted == []
    assert calls == []


def test_question_mark_is_not_a_command(tmp_path):
    async def scenario():
        client = FakeClient()
        calls = []
        bot = _bot(tmp_path, client, calls)
        await bot.set_group_enabled(10001, True)
        await bot.on_event(_event("?"))
        await bot.on_event(_event("？"))
        bot.close()
        return client, calls

    client, calls = asyncio.run(scenario())

    assert client.group_messages == []
    assert client.deleted == []
    assert calls == []


def test_admin_enable_requires_permission(tmp_path):
    async def scenario():
        client = FakeClient()
        bot = _bot(tmp_path, client, [], admins=[])
        await bot.on_event(_event("/开启jm"))
        bot.close()
        return client

    client = asyncio.run(scenario())
    texts = [text for _, text in client.group_messages]
    assert any("无权限" in text for text in texts)


def test_admin_can_enable_group(tmp_path):
    async def scenario():
        client = FakeClient()
        bot = _bot(tmp_path, client, [], admins=[20002])
        await bot.on_event(_event("/开启jm"))
        enabled = bot.is_group_enabled(10001)
        bot.close()
        return client, enabled

    client, enabled = asyncio.run(scenario())
    texts = [text for _, text in client.group_messages]

    assert enabled is True
    assert any("已开启" in text for text in texts)
    assert (tmp_path / "state.json").is_file()


def test_project_venv_detection(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.source_path = tmp_path / "jm_bot" / "config.yml"

    monkeypatch.setattr(bot_module.sys, "prefix", str(tmp_path / ".venv"))
    assert bot_module._is_project_venv(cfg) is True

    monkeypatch.setattr(
        bot_module.sys, "prefix", str(tmp_path / "conda-env")
    )
    assert bot_module._is_project_venv(cfg) is False
