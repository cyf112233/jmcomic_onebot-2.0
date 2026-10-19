# -*- coding: utf-8 -*-

from jm_bot import message as MSG


def test_text_and_combination():
    segment = MSG.text("hello")
    assert segment == {"type": "text", "data": {"text": "hello"}}

    message = MSG.message_of(MSG.text("a"), MSG.at(123))
    assert [item["type"] for item in message] == ["text", "at"]
    assert message[1]["data"]["qq"] == "123"


def test_image_keeps_remote_and_base64():
    assert MSG.image("https://example.com/a.jpg")["data"]["file"] == (
        "https://example.com/a.jpg"
    )
    assert MSG.image("base64://abc")["data"]["file"] == "base64://abc"


def test_image_local_path_becomes_uri(tmp_path):
    path = tmp_path / "a b.png"
    path.write_bytes(b"x")
    segment = MSG.image(str(path))
    assert segment["data"]["file"].startswith("file:///")


def test_face_reply_and_bool_conversion():
    assert MSG.face(1)["data"]["id"] == "1"
    assert MSG.reply(9)["data"]["id"] == "9"
    assert MSG.image("https://x/a.jpg", cache=False)["data"]["cache"] == 0
    assert MSG.image("https://x/a.jpg", cache=True)["data"]["cache"] == 1


def test_encode_file_to_base64_uri(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    assert MSG.encode_file_to_base64_uri(str(path)).startswith("base64://")
