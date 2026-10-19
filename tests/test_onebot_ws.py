# -*- coding: utf-8 -*-

from pathlib import Path
from urllib.parse import unquote

import pytest

from jm_bot.onebot_ws import (
    _file_value,
    _redact_url,
    compute_backoff,
    is_api_success,
    message_array_to_plain,
)


def test_message_array_to_plain():
    message = [
        {"type": "text", "data": {"text": "/jm "}},
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "image", "data": {"file": "abc.jpg"}},
        {"type": "reply", "data": {"id": "9"}},
    ]
    text = message_array_to_plain(message)
    assert "/jm " in text
    assert "[@10001]" in text
    assert "[image:abc.jpg]" in text
    assert "[reply:9]" in text


def test_compute_backoff_grows_and_caps():
    assert compute_backoff(1, 3, jitter=0) == 3
    assert compute_backoff(2, 3, jitter=0) == 6
    assert compute_backoff(3, 3, jitter=0) == 12
    assert compute_backoff(10, 3, jitter=0) == 60

    for attempt in range(1, 8):
        value = compute_backoff(attempt, 3)
        assert value >= compute_backoff(attempt, 3, jitter=0)
        assert value <= compute_backoff(attempt, 3, jitter=0) * 1.2 + 1e-9


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"status": "ok", "retcode": 0}, True),
        ({"status": "async", "retcode": 0}, True),
        ({"status": "ok"}, True),
        ({"retcode": 0}, True),
        ({}, True),
        ({"status": "failed", "retcode": 100}, False),
        ({"status": "ok", "retcode": 1}, False),
        ({"retcode": "1"}, False),
        (None, False),
        ("ok", False),
    ],
)
def test_is_api_success(response, expected):
    assert is_api_success(response) is expected


def test_redact_url_hides_token():
    url = "ws://127.0.0.1:6700/?access_token=secret&x=1"
    redacted = _redact_url(url)
    assert "secret" not in redacted
    assert "access_token=%2A%2A%2A" in redacted
    assert "x=1" in redacted


def test_file_value(tmp_path):
    file_path = tmp_path / "测试 文件.pdf"
    file_path.write_bytes(b"x")

    uri = _file_value(str(file_path), use_uri=True)
    assert uri.startswith("file:///")
    assert "%20" in uri
    assert unquote(uri).endswith("测试 文件.pdf")

    plain = _file_value(str(file_path), use_uri=False)
    assert Path(plain).is_absolute()
    assert plain.endswith("测试 文件.pdf")

    assert (
        _file_value("https://example.com/a.pdf", use_uri=True)
        == "https://example.com/a.pdf"
    )
    assert (
        _file_value("base64://abc", use_uri=False) == "base64://abc"
    )
