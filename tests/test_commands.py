# -*- coding: utf-8 -*-

import pytest

from jm_bot.commands import parse_command


@pytest.mark.parametrize(
    ("text", "name", "album_id"),
    [
        ("/jm 123456", "jm", "123456"),
        ("/jm 1", "jm", "1"),
        ("/jm 1234567890", "jm", "1234567890"),
        ("  /jm 123456  ", "jm", "123456"),
        ("/jm 123456\n", "jm", "123456"),
        ("／ｊｍ　１２３４５６", "jm", "123456"),
        ("[CQ:at,qq=10001] /jm 123456", "jm", "123456"),
        ("/帮助", "help", ""),
        ("/开启jm", "enable", ""),
        ("/关闭jm", "disable", ""),
        ("/更新jm", "update", ""),
    ],
)
def test_parse_valid_commands(text, name, album_id):
    command = parse_command(text)
    assert command is not None
    assert command.name == name
    assert command.album_id == album_id


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hello",
        "123456",
        "jmcomic 123456",
        "?",
        "？",
        "/help",
        "/enable",
        "/disable",
        "/update",
        "帮助",
        "开启jm",
        "关闭jm",
        "更新jm",
        "jm 123456",
        ".jm 123456",
        "!jm 123456",
        "#jm 123456",
        "/jm123456",
        "/jm  123456",
        "/jm\t123456",
        "/ jm 123456",
        "//jm 123456",
        "/jm",
        "/jm ",
        "/jm abc",
        "/jm 123456 abc",
        "/jm 123456 789",
        "/jm 123456。",
        "/jm 12345678901",  # 超过 10 位
        "/JM 123456",
        "/开启 jm",
        "/关闭 jm",
        "/更新 jm",
        "? /jm 123456",
    ],
)
def test_parse_invalid_commands(text):
    assert parse_command(text) is None
