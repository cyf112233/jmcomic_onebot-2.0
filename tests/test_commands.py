# -*- coding: utf-8 -*-

import pytest

from jm_bot.commands import parse_command


@pytest.mark.parametrize(
    ("text", "name", "album_id"),
    [
        ("/jm 123456", "jm", "123456"),
        ("jm123456", "jm", "123456"),
        ("!jm 123456", "jm", "123456"),
        (".jm 123456", "jm", "123456"),
        ("#jm 123456", "jm", "123456"),
        ("／ｊｍ　１２３４５６", "jm", "123456"),
        ("[CQ:at,qq=10001] /jm 123456", "jm", "123456"),
        ("/jm #123456", "jm", "123456"),
        ("/jm id: 123456", "jm", "123456"),
        ("/jm", "jm", ""),
        ("/jm   ", "jm", ""),
        ("/帮助", "help", ""),
        ("/help", "help", ""),
        ("?", "help", ""),
        ("/开启jm", "enable", ""),
        ("/enable", "enable", ""),
        ("/关闭jm", "disable", ""),
        ("/disable", "disable", ""),
        ("/更新jm", "update", ""),
        ("/update", "update", ""),
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
        "jmcomic 123456",
        "/jm 123456 abc",
        "/jm abc",
        "/jm 12345678901",  # 超过 10 位
        "帮助我",
    ],
)
def test_parse_invalid_commands(text):
    assert parse_command(text) is None
