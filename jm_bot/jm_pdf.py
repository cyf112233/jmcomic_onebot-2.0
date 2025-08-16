# -*- coding: utf-8 -*-
"""
JM 漫画下载与转 PDF 兼容模块（独立文件，内置漫画获取）

功能概述：
- 兼容读取示例教程中的 YAML 配置（jm库教程/jm漫画转pdf/config.yml 同结构）
- 使用 jmcomic 库按配置下载漫画（可选）
- 将下载目录的章节图片按顺序合成为单个 PDF 文件
- 提供批量转换、跳过已存在 PDF、基础的错误与日志输出

依赖：
  - jmcomic            （漫画下载）
  - pillow (PIL)       （图像处理与保存 PDF）
  - pyyaml             （解析 YAML 配置）
安装示例：
  pip install jmcomic pillow pyyaml

注意：
- 本模块不依赖 OneBot，供主逻辑调用。
- 可直接作为脚本运行以快速验证转换逻辑：python -m jm_bot.jm_pdf
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
import os
import time
import pathlib
import yaml

from PIL import Image

try:
    import jmcomic  # type: ignore
except Exception:  # pragma: no cover
    jmcomic = None  # 允许环境未安装 jmcomic 时仅使用 PDF 转换能力


# -------------------------
# 配置模型（兼容教程 YAML）
# -------------------------
@dataclass
class JMComicConfig:
    base_dir: str
    rule: str = "Bd_Atitle_Pindex"
    domains: List[str] = None
    cache: bool = True
    image_decode: bool = True
    image_suffix: str = ".jpg"
    batch_count: int = 8

    def ensure_dirs(self) -> None:
        pathlib.Path(self.base_dir).mkdir(parents=True, exist_ok=True)


def _as_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    if isinstance(v, (int, float)):
        return bool(v)
    return default


def load_jmcomic_yaml_config(path: str) -> JMComicConfig:
    """
    兼容读取教程中的 YAML 配置结构：
      dir_rule.base_dir
      dir_rule.rule
      client.domain (list)
      download.cache
      download.image.decode
      download.image.suffix
      download.threading.batch_count
    """
    cfg_path = pathlib.Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"JM 配置文件不存在：{cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    dir_rule = (raw.get("dir_rule") or {})
    download = (raw.get("download") or {})
    image_cfg = (download.get("image") or {})
    threading_cfg = (download.get("threading") or {})
    client = (raw.get("client") or {})
    domains = client.get("domain") or []

    cfg = JMComicConfig(
        base_dir=str(dir_rule.get("base_dir") or "."),
        rule=str(dir_rule.get("rule") or "Bd_Atitle_Pindex"),
        domains=list(domains) if isinstance(domains, list) else [],
        cache=_as_bool(download.get("cache"), True),
        image_decode=_as_bool(image_cfg.get("decode"), True),
        image_suffix=str(image_cfg.get("suffix") or ".jpg"),
        batch_count=int(threading_cfg.get("batch_count") or 8),
    )
    cfg.ensure_dirs()
    return cfg


def build_jm_option_from_yaml(path: str):
    """
    基于 YAML 路径构造 jmcomic 的 JmOption。
    若未安装 jmcomic，则返回 None。
    """
    if jmcomic is None:
        return None
    # jmcomic 官方推荐的从文件加载方式
    return jmcomic.JmOption.from_file(path)


# -------------------------
# 下载相关（可选）
# -------------------------
def download_albums(album_ids: Sequence[str], jm_option: Any) -> None:
    """
    使用 jmcomic 下载多个本子/专辑。
    jm_option 建议使用 build_jm_option_from_yaml() 构造。
    """
    if jmcomic is None:
        raise RuntimeError("未安装 jmcomic，无法执行下载。请先 pip install jmcomic")
    if not album_ids:
        return
    for aid in album_ids:
        jmcomic.download_album(str(aid), jm_option)


# -------------------------
# PDF 合成
# -------------------------
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _list_numeric_subdirs(root: pathlib.Path) -> List[pathlib.Path]:
    """
    返回 root 下按名称转 int 排序的子目录列表（仅数值名）。
    """
    items: List[Tuple[int, pathlib.Path]] = []
    if not root.exists():
        return []
    for entry in root.iterdir():
        if entry.is_dir():
            try:
                idx = int(entry.name)
                items.append((idx, entry))
            except Exception:
                # 忽略非纯数字目录
                continue
    items.sort(key=lambda x: x[0])
    return [p for _, p in items]


def _list_images_in_dir(d: pathlib.Path) -> List[pathlib.Path]:
    files: List[pathlib.Path] = []
    if not d.exists():
        return files
    for entry in d.iterdir():
        if entry.is_file():
            if entry.suffix.lower() in _IMAGE_EXTS:
                files.append(entry)
    # 按文件名自然排序
    files.sort(key=lambda p: p.name)
    return files


def _open_image_rgb(path: pathlib.Path) -> Image.Image:
    img = Image.open(str(path))
    # 有些格式是 RGBA/P 等，统一转 RGB 便于保存 PDF
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def convert_album_dir_to_pdf(input_folder: str, output_dir: str, pdf_name: Optional[str] = None) -> str:
    """
    将一个漫画目录转换为单个 PDF。
    目录结构假设为：
      input_folder/
        1/   *.jpg
        2/   *.jpg
        ...
    返回最终 PDF 路径。
    """
    start = time.time()
    in_dir = pathlib.Path(input_folder)
    if not in_dir.exists():
        raise FileNotFoundError(f"输入目录不存在：{in_dir}")

    chapter_dirs = _list_numeric_subdirs(in_dir)
    if not chapter_dirs:
        # 兼容：若没有数字子目录，尝试直接把当前目录下图片合并
        chapter_dirs = [in_dir]

    images: List[pathlib.Path] = []
    for ch in chapter_dirs:
        images.extend(_list_images_in_dir(ch))

    if not images:
        raise ValueError(f"未找到可用图片：{in_dir}")

    head = _open_image_rgb(images[0])
    rest = [_open_image_rgb(p) for p in images[1:]]

    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_file = out_dir / ((pdf_name or in_dir.name) + ("" if (pdf_name or in_dir.name).lower().endswith(".pdf") else ".pdf"))
    head.save(str(pdf_file), "PDF", save_all=True, append_images=rest)

    dur = time.time() - start
    print(f"[PDF] 生成完成：{pdf_file} （{dur:.2f}s）")
    return str(pdf_file)


def all2PDF(input_folder: str, pdfpath: str, pdfname: Optional[str] = None) -> str:
    """
    Compatibility wrapper for legacy signature all2PDF(input_folder, pdfpath, pdfname).
    Delegates to convert_album_dir_to_pdf.
    """
    return convert_album_dir_to_pdf(input_folder, pdfpath, pdfname)


def convert_all_albums_to_pdf(base_dir: str, skip_existing: bool = True) -> List[str]:
    """
    扫描 base_dir 下的每个子目录，将其各自合成 PDF。
    默认跳过已经存在的同名 PDF。
    返回所有生成的 PDF 路径列表（跳过的不返回）。
    """
    base = pathlib.Path(base_dir)
    if not base.exists():
        raise FileNotFoundError(f"base_dir 不存在：{base}")
    out_list: List[str] = []

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        target_pdf = base / (entry.name + ".pdf")
        if skip_existing and target_pdf.exists():
            print(f"[Skip] 已存在：{target_pdf}")
            continue
        try:
            pdf_path = convert_album_dir_to_pdf(str(entry), str(base), entry.name)
            out_list.append(pdf_path)
        except Exception as e:
            print(f"[Warn] 转换失败：{entry} -> {e!r}")
    return out_list


# -------------------------
# 快速 CLI 验证
# -------------------------
def _demo_main() -> None:
    """
    用法示例（请根据实际路径修改）：
      python -m jm_bot.jm_pdf

    尝试读取默认配置：jm_bot/jm_pdf/config.yml
    - 若环境已安装 jmcomic，可在此处调用下载
    - 随后将 base_dir 下所有子目录转换为 PDF
    """
    default_yaml = "jm_bot/jm_pdf/config.yml"
    try:
        cfg = load_jmcomic_yaml_config(default_yaml)
    except Exception as e:
        print(f"[ERR ] 加载默认 YAML 失败：{e!r}")
        return

    print(f"[INFO] base_dir={cfg.base_dir} rule={cfg.rule} domains={cfg.domains}")
    print(f"[INFO] cache={cfg.cache} decode={cfg.image_decode} suffix={cfg.image_suffix} batch={cfg.batch_count}")

    # 如需下载，请取消注释以下代码，并填写 album_ids
    # if jmcomic is not None:
    #     jm_opt = build_jm_option_from_yaml(default_yaml)
    #     download_albums(['146417'], jm_opt)

    # 批量转换
    convert_all_albums_to_pdf(cfg.base_dir, skip_existing=True)


if __name__ == "__main__":
    _demo_main()
