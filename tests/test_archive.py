# -*- coding: utf-8 -*-

import zipfile

import pytest

from jm_bot.archive import (
    ArchiveError,
    create_archive,
    generate_password,
)


def test_generate_password():
    password = generate_password(8)
    assert len(password) == 8
    assert password.isdigit()

    assert len(generate_password(1)) == 4
    assert len(generate_password(100)) == 32


def test_create_encrypted_archive(tmp_path):
    source = tmp_path / "漫画.pdf"
    source.write_bytes(b"%PDF-1.4 test content")

    result = create_archive(
        source,
        tmp_path,
        display_name="漫画/标题:测试",
        encrypt=True,
        password="123456",
    )

    assert result.password == "123456"
    assert result.path.is_file()
    assert "123456" not in result.path.name
    assert "/" not in result.path.name
    assert ":" not in result.path.name

    import pyzipper

    with pyzipper.AESZipFile(result.path) as archive:
        archive.setpassword(b"123456")
        assert archive.read("漫画.pdf") == b"%PDF-1.4 test content"


def test_create_plain_archive(tmp_path):
    source = tmp_path / "comic.pdf"
    source.write_bytes(b"plain")

    result = create_archive(source, tmp_path, encrypt=False)
    assert result.password is None
    assert result.path.suffix == ".zip"

    with zipfile.ZipFile(result.path) as archive:
        assert archive.read("comic.pdf") == b"plain"


def test_create_archive_missing_source(tmp_path):
    with pytest.raises(ArchiveError):
        create_archive(tmp_path / "missing.pdf", tmp_path)
