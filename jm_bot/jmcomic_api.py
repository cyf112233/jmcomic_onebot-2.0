# -*- coding: utf-8 -*-
"""jmcomic 元数据读取与下载适配层。

这里刻意把 ``jmcomic`` 的导入放在函数内部：

* 主进程启动时可以先做依赖检查，再导入重量级依赖；
* ``ProcessPoolExecutor`` 的子进程只需导入本模块，不会在父进程产生副作用。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple, Union

import yaml

from .logging_utils import log_warn

# 机器人依赖的目录规则：<base_dir>/<漫画ID>/<章节ID>/图片
# 章节 ID 是纯数字，便于按元数据顺序稳定排序。
DIR_RULE = "Bd_Aid_Pid"
OPTION_FILENAME = "jmcomic_option.yml"

# 每个线程复用自己的元数据 client
_thread_local = threading.local()


class JmConfigError(Exception):
    """jmcomic 配置或元数据读取失败。"""


@dataclass(frozen=True)
class ChapterMeta:
    """一个章节（jmcomic 中的 photo）。"""

    photo_id: str
    index: int = 0
    title: str = ""


@dataclass(frozen=True)
class AlbumMeta:
    """一本漫画（jmcomic 中的 album）。"""

    album_id: str
    name: str
    page_count: int
    chapters: Tuple[ChapterMeta, ...] = ()

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def is_multi_chapter(self) -> bool:
        """是否包含一个以上章节。

        jmcomic 对没有章节信息的单行本会自动合成一个章节，因此
        正常的单章节漫画 ``chapter_count`` 为 1。
        """
        return len(self.chapters) > 1

    @classmethod
    def from_episodes(
        cls,
        album_id: Any,
        name: Any,
        page_count: Any = 0,
        episodes: Iterable[Any] = (),
        *,
        synthesize_single: bool = True,
    ) -> "AlbumMeta":
        """从 jmcomic 的 ``episode_list`` 构造元数据。

        ``episode_list`` 的元素在不同版本中可能是四元组、字典或
        ``JmPhotoDetail`` 对象，这里都做了兼容。
        """
        chapters = []
        seen = set()

        for position, episode in enumerate(episodes or ()):
            chapter = _chapter_from_episode(episode, position)
            if chapter is None or chapter.photo_id in seen:
                continue
            seen.add(chapter.photo_id)
            chapters.append(chapter)

        if not chapters and synthesize_single:
            chapters = [
                ChapterMeta(
                    photo_id=str(album_id),
                    index=1,
                    title=str(name or ""),
                )
            ]

        chapters.sort(key=lambda item: (item.index, item.photo_id))
        return cls(
            album_id=str(album_id or ""),
            name=str(name or "").strip(),
            page_count=_safe_int(page_count),
            chapters=tuple(chapters),
        )

    @classmethod
    def from_album(cls, album: Any) -> "AlbumMeta":
        """从 ``JmAlbumDetail`` 构造元数据。"""
        album_id = _get_field(album, "album_id", "id")
        name = _get_field(album, "name", "title")
        page_count = _get_field(album, "page_count")
        episodes = _get_field(album, "episode_list") or ()
        return cls.from_episodes(album_id, name, page_count, episodes)


def _get_field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                value = obj[name]
            else:
                continue
        else:
            value = getattr(obj, name, None)
            if value is None:
                continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _chapter_from_episode(episode: Any, position: int) -> Optional[ChapterMeta]:
    if isinstance(episode, dict):
        photo_id = episode.get("photo_id") or episode.get("id")
        index = episode.get("index") or episode.get("sort") or position + 1
        title = episode.get("title") or episode.get("name") or ""
    elif isinstance(episode, str):
        # 少数版本/自定义客户端可能只返回 photo_id 列表
        photo_id = episode
        index = position + 1
        title = ""
    elif isinstance(episode, int):
        photo_id = episode
        index = position + 1
        title = ""
    elif isinstance(episode, (tuple, list)):
        photo_id = episode[0] if len(episode) > 0 else None
        index = episode[1] if len(episode) > 1 else position + 1
        title = episode[2] if len(episode) > 2 else ""
    else:
        photo_id = getattr(episode, "photo_id", None) or getattr(
            episode, "id", None
        )
        index = (
            getattr(episode, "index", None)
            or getattr(episode, "sort", None)
            or position + 1
        )
        title = getattr(episode, "title", None) or getattr(
            episode, "name", None
        ) or ""

    if photo_id is None or str(photo_id).strip() == "":
        return None

    chapter_index = _safe_int(index)
    if chapter_index <= 0:
        chapter_index = position + 1

    return ChapterMeta(
        photo_id=str(photo_id).strip(),
        index=chapter_index,
        title=str(title or "").strip(),
    )


def prepare_option_file(
    source: Union[str, Path], work_dir: Union[str, Path]
) -> Path:
    """基于用户配置生成本次任务专用的 jmcomic 配置。

    强制使用 ``Bd_Aid_Pid`` 目录规则，并把 ``base_dir`` 指到任务目录。
    """
    source_path = Path(source)
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    if not source_path.is_file():
        raise JmConfigError(f"jmcomic 配置文件不存在：{source_path}")

    try:
        data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise JmConfigError(
            f"读取 jmcomic 配置失败：{source_path}（{exc}）"
        ) from exc

    if not isinstance(data, dict):
        raise JmConfigError(f"jmcomic 配置内容必须是 YAML 映射：{source_path}")

    data.setdefault("version", "2.0")

    dir_rule = data.get("dir_rule")
    if not isinstance(dir_rule, dict):
        dir_rule = {}
        data["dir_rule"] = dir_rule

    previous_rule = dir_rule.get("rule")
    if previous_rule and previous_rule != DIR_RULE:
        log_warn(
            "jmcomic 配置中的 dir_rule.rule 已被强制覆盖为 "
            f"{DIR_RULE}（原值：{previous_rule}）"
        )

    dir_rule["base_dir"] = str(work_path)
    dir_rule["rule"] = DIR_RULE

    option_path = work_path / OPTION_FILENAME
    option_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return option_path


def fetch_album_meta(
    album_id: str, option_file: Union[str, Path]
) -> AlbumMeta:
    """读取漫画元数据（网络请求，调用方应放到线程中执行）。

    每个线程缓存一个 jmcomic client，避免每次查询都重新做域名/设置
    请求；不同线程之间互不共享 client。
    """
    import jmcomic

    key = str(Path(option_file).resolve())
    clients = getattr(_thread_local, "clients", None)
    if clients is None:
        clients = {}
        _thread_local.clients = clients

    client = clients.get(key)
    if client is None:
        option = jmcomic.JmOption.from_file(str(option_file))
        client = option.new_jm_client()
        clients[key] = client

    album = client.get_album_detail(str(album_id))
    return AlbumMeta.from_album(album)


def download_album_sync(
    album_id: str, option_file: Union[str, Path]
) -> None:
    """下载整本漫画（阻塞函数，供进程池调用）。"""
    import jmcomic

    option = jmcomic.JmOption.from_file(str(option_file))
    jmcomic.download_album(str(album_id), option)


__all__ = [
    "AlbumMeta",
    "ChapterMeta",
    "DIR_RULE",
    "JmConfigError",
    "download_album_sync",
    "fetch_album_meta",
    "prepare_option_file",
]
