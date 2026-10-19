# -*- coding: utf-8 -*-

from pathlib import Path

from jm_bot.paths import (
    cleanup_job_dirs,
    format_bytes,
    is_safe_work_dir,
    natural_key,
    order_chapter_dirs,
    sanitize_filename,
    strip_cq_codes,
    truncate_text,
)


def _touch_image(directory: Path, name: str = "1.jpg") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"not-a-real-image")
    return path


def test_sanitize_filename_removes_illegal_characters():
    result = sanitize_filename('  a/b:c*?"<>|  ')
    assert "/" not in result
    assert ":" not in result
    assert "*" not in result
    assert "?" not in result
    assert result.startswith("a_b")


def test_sanitize_filename_reserved_and_empty():
    assert sanitize_filename("") == "jm"
    assert sanitize_filename("CON") == "_CON"
    assert len(sanitize_filename("x" * 500, max_length=20)) == 20


def test_natural_key_sorts_numbers():
    names = ["10.jpg", "2.jpg", "1.jpg"]
    assert sorted(names, key=natural_key) == ["1.jpg", "2.jpg", "10.jpg"]


def test_order_chapter_dirs_follows_metadata_order(tmp_path):
    album = tmp_path / "album"
    _touch_image(album / "100")
    _touch_image(album / "99")

    ordered = order_chapter_dirs(album, ["100", "99"])
    assert [path.name for path in ordered] == ["100", "99"]

    fallback = order_chapter_dirs(album, [])
    assert [path.name for path in fallback] == ["99", "100"]


def test_order_chapter_dirs_handles_flat_album(tmp_path):
    album = tmp_path / "album"
    _touch_image(album)
    assert [path.name for path in order_chapter_dirs(album)] == ["album"]


def test_cleanup_job_dirs_only_removes_matching_directories(tmp_path):
    work = tmp_path / "work"
    job = work / "123456-1700000000-abcdef"
    unrelated = work / "keep-me"
    job.mkdir(parents=True)
    unrelated.mkdir()
    (job / "x.jpg").write_bytes(b"x")

    assert cleanup_job_dirs(work) == 1
    assert not job.exists()
    assert unrelated.exists()


def test_is_safe_work_dir(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    assert is_safe_work_dir(project / "data" / "work", project)[0]
    assert not is_safe_work_dir(project, project)[0]
    assert not is_safe_work_dir(project.parent, project)[0]
    assert not is_safe_work_dir(Path("/"), project)[0]


def test_text_helpers():
    assert strip_cq_codes("[CQ:at,qq=1] /jm 123") == " /jm 123"
    assert truncate_text("a\nb\tc", 10) == "a b c"
    assert truncate_text("abcdef", 4) == "abc…"
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
