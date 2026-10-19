# -*- coding: utf-8 -*-

from pathlib import Path

import pytest
import yaml

from jm_bot.archive import ArchiveResult
from jm_bot.jmcomic_api import (
    DIR_RULE,
    AlbumMeta,
    JmConfigError,
    prepare_option_file,
)
from jm_bot.texts import (
    format_multi_chapter_rejection,
    format_success_info,
)


def test_single_chapter_album_is_not_multi():
    meta = AlbumMeta.from_episodes(
        "123", "单章", 20, [("111", "1", "第1话")]
    )
    assert meta.chapter_count == 1
    assert meta.is_multi_chapter is False


def test_multi_chapter_album_is_detected():
    meta = AlbumMeta.from_episodes(
        "123",
        "多章",
        40,
        [("111", "1", "第1话"), ("222", "2", "第2话")],
    )
    assert meta.chapter_count == 2
    assert meta.is_multi_chapter is True
    assert [chapter.photo_id for chapter in meta.chapters] == ["111", "222"]


def test_episode_formats_and_deduplication():
    episodes = [
        {"photo_id": "2", "index": 2, "title": "B"},
        {"id": "1", "sort": 1, "name": "A"},
        ("2", "2", "B duplicate"),
    ]
    meta = AlbumMeta.from_episodes("9", "x", 0, episodes)
    assert [chapter.photo_id for chapter in meta.chapters] == ["1", "2"]


class _Episode:
    def __init__(self, photo_id, index, title):
        self.photo_id = photo_id
        self.index = index
        self.title = title


def test_episode_object_format():
    meta = AlbumMeta.from_episodes(
        "9", "x", 0, [_Episode("8", 1, "A"), _Episode("7", 2, "B")]
    )
    assert [chapter.photo_id for chapter in meta.chapters] == ["8", "7"]


def test_episode_plain_id_formats():
    strings = AlbumMeta.from_episodes("9", "x", 0, ["111", "222"])
    assert strings.is_multi_chapter is True
    assert [chapter.photo_id for chapter in strings.chapters] == [
        "111",
        "222",
    ]

    numbers = AlbumMeta.from_episodes("9", "x", 0, [111, 222])
    assert numbers.is_multi_chapter is True
    assert [chapter.photo_id for chapter in numbers.chapters] == [
        "111",
        "222",
    ]


def test_empty_episode_list_synthesizes_single_chapter():
    meta = AlbumMeta.from_episodes("42", "单行本", 12, [])
    assert meta.chapter_count == 1
    assert meta.chapters[0].photo_id == "42"
    assert meta.is_multi_chapter is False

    empty = AlbumMeta.from_episodes(
        "42", "单行本", 12, [], synthesize_single=False
    )
    assert empty.chapter_count == 0


def test_prepare_option_file_forces_rule_and_base_dir(tmp_path):
    source = tmp_path / "source.yml"
    source.write_text(
        "version: '2.0'\n"
        "download:\n"
        "  cache: false\n"
        "dir_rule:\n"
        "  rule: Bd_Atitle\n"
        "  base_dir: old\n",
        encoding="utf-8",
    )
    work = tmp_path / "work"

    option = prepare_option_file(source, work)
    data = yaml.safe_load(option.read_text(encoding="utf-8"))

    assert data["version"] == "2.0"
    assert data["download"]["cache"] is False
    assert data["dir_rule"]["rule"] == DIR_RULE
    assert data["dir_rule"]["base_dir"] == str(work)
    assert option.parent == work


def test_prepare_option_file_missing_source(tmp_path):
    with pytest.raises(JmConfigError):
        prepare_option_file(tmp_path / "nope.yml", tmp_path / "work")


def test_multi_chapter_rejection_text():
    meta = AlbumMeta.from_episodes(
        "422866",
        "一个很长的多章节标题" * 10,
        100,
        [("1", "1", "第一话"), ("2", "2", "第二话"), ("3", "3", "第三话")],
    )
    text = format_multi_chapter_rejection(meta)
    assert "多章节" in text
    assert "422866" in text
    assert "章节数：3" in text
    assert "章节示例" in text
    assert "第一话" in text

    post_check = format_multi_chapter_rejection(meta, actual_count=5)
    assert "章节数：5" in post_check


def test_success_info_contains_password_and_size():
    result = ArchiveResult(
        Path("/tmp/不存在的文件（密码123456）.zip"), password="123456"
    )
    info = format_success_info(None, "123", result)
    assert "123456" in info
    assert "123" in info
    assert "不存在的文件" in info
