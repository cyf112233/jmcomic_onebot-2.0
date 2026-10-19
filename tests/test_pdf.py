# -*- coding: utf-8 -*-

import re
from pathlib import Path

import pytest
from PIL import Image

from jm_bot.jm_pdf import PdfError, all2PDF, build_pdf, collect_images


def _make_image(path: Path, mode: str, color) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new(mode, (80, 120), color)
    image.save(path)
    image.close()


def _page_count(pdf: Path) -> int:
    data = pdf.read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?![s])", data))


def test_collect_images_natural_order(tmp_path):
    chapter = tmp_path / "chapter"
    for name in ("10.jpg", "2.jpg", "1.png"):
        _make_image(chapter / name, "RGB", (1, 2, 3))

    names = [path.name for path in collect_images([chapter])]
    assert names == ["1.png", "2.jpg", "10.jpg"]


def test_build_pdf_supports_multiple_formats(tmp_path):
    album = tmp_path / "album"
    chapters = [album / "100", album / "99"]
    _make_image(chapters[0] / "1.jpg", "RGB", (255, 0, 0))
    _make_image(chapters[0] / "2.png", "RGBA", (0, 255, 0, 128))
    _make_image(chapters[1] / "3.webp", "P", 3)
    _make_image(chapters[1] / "4.bmp", "RGB", (0, 0, 255))

    output = tmp_path / "out.pdf"
    result = build_pdf(chapters, output, title="测试", resolution=120)

    assert result == output
    assert output.read_bytes().startswith(b"%PDF")
    assert _page_count(output) == 4


def test_build_pdf_skips_corrupt_image(tmp_path):
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    (chapter / "1.jpg").write_text("not an image", encoding="utf-8")
    _make_image(chapter / "2.png", "RGB", (10, 20, 30))

    output = build_pdf([chapter], tmp_path / "out.pdf")
    assert _page_count(output) == 1


def test_build_pdf_empty_directory(tmp_path):
    chapter = tmp_path / "empty"
    chapter.mkdir()
    with pytest.raises(PdfError):
        build_pdf([chapter], tmp_path / "out.pdf")


def test_build_pdf_all_corrupt(tmp_path):
    chapter = tmp_path / "chapter"
    chapter.mkdir()
    (chapter / "1.jpg").write_text("bad", encoding="utf-8")
    with pytest.raises(PdfError):
        build_pdf([chapter], tmp_path / "out.pdf")


def test_all2pdf_compatibility_wrapper(tmp_path):
    chapter = tmp_path / "chapter"
    _make_image(chapter / "1.jpg", "RGB", (5, 6, 7))

    result = Path(all2PDF(chapter, tmp_path, "comic"))
    assert result.name == "comic.pdf"
    assert result.is_file()


def test_max_width_resize(tmp_path):
    chapter = tmp_path / "chapter"
    _make_image(chapter / "1.jpg", "RGB", (5, 6, 7))

    output = build_pdf([chapter], tmp_path / "out.pdf", max_width=40)
    assert output.is_file()
    assert _page_count(output) == 1
