# -*- coding: utf-8 -*-
"""图片目录 -> PDF。

针对原实现的几个问题做了修正：

* 不再只处理 JPEG；
* 不再依赖“第一张图是不是 jpg”这种脆弱逻辑；
* 支持 PNG/WebP/BMP/TIFF 等格式，并处理透明通道与 EXIF 方向；
* 空目录、损坏图片会给出明确异常或告警；
* 输出文件名做了跨平台清理。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import List, Optional, Sequence, Union

from PIL import Image, ImageFile, ImageOps

from ..logging_utils import log_info, log_warn
from ..paths import IMAGE_SUFFIXES, has_image_files, natural_key

# 部分漫画图片尾部可能被截断，尽量读取而不是直接报错
ImageFile.LOAD_TRUNCATED_IMAGES = True


class PdfError(Exception):
    """PDF 生成失败。"""


def collect_images(
    chapter_dirs: Union[str, Path, Sequence[Union[str, Path]]],
) -> List[Path]:
    """按目录顺序收集图片，并在每个目录内做自然排序。"""
    if isinstance(chapter_dirs, (str, Path)):
        directories = [Path(chapter_dirs)]
    else:
        directories = [Path(item) for item in chapter_dirs]

    files: List[Path] = []
    for directory in directories:
        if not directory.is_dir():
            log_warn(f"章节目录不存在，已跳过：{directory}")
            continue
        try:
            entries = sorted(directory.iterdir(), key=natural_key)
        except OSError as exc:
            log_warn(f"无法读取章节目录 {directory}：{exc}")
            continue

        for entry in entries:
            if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES:
                files.append(entry)

    return files


def _to_pdf_mode(image: Image.Image) -> Image.Image:
    """把图片转换为 PDF 可保存的模式，透明背景填白。"""
    if image.mode in ("RGB", "L"):
        return image

    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background

    return image.convert("RGB")


_JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})


def _can_use_img2pdf(files: Sequence[Path], max_width: int) -> bool:
    """JPEG 且不需要缩放时，用 img2pdf 直接嵌入，避免整本解码。"""
    return (
        max_width <= 0
        and bool(files)
        and all(path.suffix.lower() in _JPEG_SUFFIXES for path in files)
    )


def _build_with_img2pdf(
    files: Sequence[Path],
    output: Path,
    title: Optional[str],
    resolution: float,
) -> None:
    """流式写入 PDF，内存占用远低于 Pillow 逐张解码。"""
    import img2pdf

    with output.open("wb") as stream:
        options = {
            "outputstream": stream,
            "dpi": float(resolution),
        }
        if title:
            options["title"] = str(title)
        img2pdf.convert(*[str(path) for path in files], **options)


def build_pdf(
    chapter_dirs: Union[str, Path, Sequence[Union[str, Path]]],
    output_path: Union[str, Path],
    title: Optional[str] = None,
    *,
    resolution: float = 150.0,
    max_width: int = 0,
) -> Path:
    """把多个章节目录中的图片合成为一个 PDF，返回输出路径。"""
    files = collect_images(chapter_dirs)
    if not files:
        raise PdfError("没有找到可用的图片，无法生成 PDF。")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    if _can_use_img2pdf(files, max_width):
        try:
            _build_with_img2pdf(files, output, title, resolution)
        except Exception as exc:
            log_warn(f"img2pdf 快速合成失败，回退 Pillow：{exc}")
        else:
            elapsed = time.monotonic() - started
            log_info(
                f"PDF 生成完成（img2pdf）：{output.name}（"
                f"{len(files)} 页，{elapsed:.1f} 秒）"
            )
            return output

    images: List[Image.Image] = []
    resources: List[Image.Image] = []

    try:
        for image_path in files:
            try:
                image = Image.open(image_path)
            except Exception as exc:
                log_warn(f"跳过无法读取的图片 {image_path.name}：{exc}")
                continue

            resources.append(image)

            try:
                # 处理手机拍照/扫描件的方向信息
                try:
                    orientation = image.getexif().get(0x0112)
                except Exception:
                    orientation = None
                if orientation in (2, 3, 4, 5, 6, 7, 8):
                    transposed = ImageOps.exif_transpose(image)
                    if transposed is not image:
                        resources.append(transposed)
                        image = transposed

                converted = _to_pdf_mode(image)
                if converted is not image:
                    resources.append(converted)
                image = converted

                if max_width > 0 and image.width > max_width:
                    ratio = max_width / float(image.width)
                    resized = image.resize(
                        (max_width, max(1, round(image.height * ratio))),
                        Image.LANCZOS,
                    )
                    resources.append(resized)
                    image = resized

                if image.width <= 0 or image.height <= 0:
                    log_warn(f"跳过尺寸异常的图片：{image_path.name}")
                    continue

                images.append(image)
            except Exception as exc:
                log_warn(f"跳过处理失败的图片 {image_path.name}：{exc}")
                continue

        if not images:
            raise PdfError("所有图片都无法读取，无法生成 PDF。")

        save_kwargs = {
            "save_all": True,
            "append_images": images[1:],
            "resolution": float(resolution),
        }
        if title:
            save_kwargs["title"] = str(title)

        try:
            images[0].save(output, "PDF", **save_kwargs)
        except Exception as exc:
            raise PdfError(f"写入 PDF 失败：{exc}") from exc
    finally:
        # 关闭所有打开/创建的图像对象
        for resource in resources:
            try:
                resource.close()
            except Exception:
                pass

    elapsed = time.monotonic() - started
    log_info(
        f"PDF 生成完成：{output.name}（{len(images)} 页，"
        f"{elapsed:.1f} 秒）"
    )
    return output


def all2PDF(input_folder: Union[str, Path], pdfpath: Union[str, Path], pdfname: str) -> str:
    """兼容旧版调用：把单个章节目录转换为 PDF。"""
    output = Path(pdfpath) / str(pdfname)
    if output.suffix.lower() != ".pdf":
        output = output.with_suffix(".pdf")

    result = build_pdf([input_folder], output, title=output.stem)
    return str(result)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jm_bot.jm_pdf",
        description="把漫画图片目录合成为 PDF",
    )
    parser.add_argument("input", help="章节目录，或包含章节目录的漫画目录")
    parser.add_argument("output", help="输出 PDF 路径")
    parser.add_argument(
        "--max-width",
        type=int,
        default=0,
        help="限制单页最大宽度（像素），0 表示不限制",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_dir():
        parser.error(f"输入目录不存在：{input_path}")

    directories: Sequence[Path]
    if has_image_files(input_path):
        directories = [input_path]
    else:
        try:
            directories = [
                entry
                for entry in sorted(input_path.iterdir(), key=natural_key)
                if entry.is_dir() and has_image_files(entry)
            ]
        except OSError as exc:
            parser.error(f"无法读取输入目录：{exc}")

    if not directories:
        parser.error("输入目录中没有找到图片")

    output = Path(args.output)
    result = build_pdf(
        directories,
        output,
        title=output.stem,
        max_width=max(0, args.max_width),
    )
    print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
