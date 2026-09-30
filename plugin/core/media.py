"""MediaObject 与媒体管线（规格 §6）。

搜索结果不只是文字 URL：每条图片结果是一个结构化 MediaObject，
同时供 Chat UI（卡片渲染）、Vision（本地缓存图）、后续生成服务使用。

纪律：本模块不 import substance_painter / Qt —— 必须能在 Painter 之外
（CI）导入并测试。网络访问全部走 urllib，与 ai_client 同风格。
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Painter 内用插件自带的 LOCALAPPDATA 约定；CI 裸环境退回 temp 目录。
    import os
    _CACHE_ROOT = Path(os.environ.get("LOCALAPPDATA") or "") / "SP AI Assistant" / "media_cache"
    if not os.environ.get("LOCALAPPDATA"):
        import tempfile
        _CACHE_ROOT = Path(tempfile.gettempdir()) / "spai_tests" / "media_cache"
except Exception:  # pragma: no cover - 理论上不可达
    import tempfile
    _CACHE_ROOT = Path(tempfile.gettempdir()) / "spai_tests" / "media_cache"


class MediaError(Exception):
    """媒体管线错误：消息面向用户，必须能直接读懂（规格 §6.1）。"""


@dataclass
class MediaObject:
    """一条结构化媒体对象（规格 §6.1 的字段名逐项对齐）。

    id / type / title / source_url / image_url / thumbnail_url / width /
    height / license / provider / search_query / metadata 是规格原文键；
    local_path / error 是本地管线扩展（缓存落地与失败可重试）。
    """

    id: str = ""
    type: str = "image"
    title: str = ""
    source_url: str = ""
    image_url: str = ""
    thumbnail_url: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    license: str = ""            # 规格 attribution/license：版权与许可信息
    provider: str = ""
    search_query: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    # —— 本地管线扩展 ——
    local_path: str = ""         # 缓存落地后的绝对路径；空表示尚未下载
    error: str = ""              # 最近一次下载失败的原因；成功后清空

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MediaObject":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def cache_root() -> Path:
    """本地工作缓存根目录（规格 §6.1：不直接依赖临时远程 URL）。"""
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _CACHE_ROOT


def _extension_for(data: bytes) -> str:
    """按文件头识别扩展名；识别不出就归到 .bin（不阻止缓存）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def image_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """从文件头解析宽高（PNG / JPEG / GIF / BMP / WebP）；失败返回 (None, None)。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return w, h
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:2] == b"BM" and len(data) >= 26:
            return int.from_bytes(data[18:22], "little"), int.from_bytes(data[22:26], "little")
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X" and len(data) >= 30:
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return w, h
            if data[12:16] == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if data[12:16] == b"VP8 " and len(data) >= 30:
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return w, h
        if data[:3] == b"\xff\xd8\xff":  # JPEG：逐段找 SOF0/1/2…
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker = data[offset + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[offset + 5:offset + 7], "big")
                    w = int.from_bytes(data[offset + 7:offset + 9], "big")
                    return w, h
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    offset += 2
                    continue
                seg_len = int.from_bytes(data[offset + 2:offset + 4], "big")
                if seg_len < 2:
                    break
                offset += 2 + seg_len
    except Exception:
        pass
    return None, None


def download(media: MediaObject, timeout: int = 20, overwrite: bool = False) -> MediaObject:
    """把 image_url 下载进本地工作缓存，回填 local_path / width / height。

    * 幂等：已缓存且不要求覆盖时直接回填，不发请求（这就是「重试」——
      失败后再调一次本函数即可，成功前不会留下半截文件）。
    * 失败必须带着明确原因抛 MediaError，同时记录在 media.error 上，
      供 UI 显示与下一次重试参考（规格 §6.1：下载失败必须可重试）。
    * 下载采用「临时文件 + 原子改名」，中断不会污染缓存。
    """
    url = (media.image_url or "").strip()
    if not url:
        media.error = "这条媒体对象没有 image_url，无法下载"
        raise MediaError(media.error)
    root = cache_root()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    # 先按 digest 找已有文件（任意扩展名）—— 缓存命中不必再猜扩展名。
    if not overwrite:
        for existing in root.glob(digest + ".*"):
            media.local_path = str(existing)
            media.error = ""
            w, h = image_size(existing.read_bytes()[:64])
            if media.width is None and w:
                media.width, media.height = w, h
            return media
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 SP-AI-Assistant/0.7",
            "Accept": "image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(12 * 1024 * 1024)  # 12MB 上限：参考图不需要更大
            if not data:
                raise MediaError("服务端返回了空文件")
    except MediaError:
        media.error = "服务端返回了空文件"
        raise
    except Exception as exc:
        media.error = "下载失败（%s）：%s" % (type(exc).__name__, exc)
        raise MediaError(media.error) from exc
    ext = _extension_for(data)
    final = root / (digest + ext)
    tmp = root / (digest + ext + ".part")
    tmp.write_bytes(data)
    tmp.replace(final)
    media.local_path = str(final)
    media.error = ""
    w, h = image_size(data)
    if w:
        media.width, media.height = w, h
    return media


def retry(media: MediaObject, timeout: int = 20) -> MediaObject:
    """显式重试入口：清掉失败标记再走一次下载。"""
    media.error = ""
    return download(media, timeout=timeout, overwrite=True)


def _bing_parse(raw: str, query: str, limit: int) -> List[MediaObject]:
    """解析 Bing 图片搜索结果页（async JSON 端点）。

    每个结果的 class="iusc" 元素带一个 m 属性，里面是 JSON：
    murl=原图、purl=来源页、t=标题、turl=缩略图。
    """
    results: List[MediaObject] = []
    for match in re.finditer(r'class="iusc"[^>]*\bm="([^"]+)"', raw):
        try:
            info = json.loads(match.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        except (json.JSONDecodeError, ValueError):
            continue
        image_url = str(info.get("murl") or "")
        if not image_url.startswith(("http://", "https://")):
            continue
        results.append(MediaObject(
            id="bing-" + hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12],
            title=str(info.get("t") or "图片参考")[:120],
            source_url=str(info.get("purl") or ""),
            image_url=image_url,
            thumbnail_url=str(info.get("turl") or ""),
            width=int(info["w"]) if str(info.get("w") or "").isdigit() else None,
            height=int(info["h"]) if str(info.get("h") or "").isdigit() else None,
            license="",  # Bing 聚合结果不带许可字段；来源页里才有
            provider="bing",
            search_query=query,
        ))
        if len(results) >= limit:
            break
    return results


def search_images(query: str, max_results: int = 8, fetcher=None) -> List[MediaObject]:
    """图片搜索：返回结构化 MediaObject 列表（本函数不下载）。

    fetcher 供测试注入（真实调用走 Bing 图片聚合端点）。
    """
    from urllib.parse import quote
    query = str(query or "").strip()
    if not query:
        raise MediaError("image_search 的 query 不能为空")
    limit = max(1, min(int(max_results or 8), 12))
    url = ("https://www.bing.com/images/async?q=" + quote(query)
           + "&first=1&count=%d&mmasync=1" % (limit * 2))
    try:
        if fetcher is not None:
            raw = fetcher(url)
        else:
            request = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 SP-AI-Assistant/0.7",
                "Accept": "text/html,*/*",
            })
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise MediaError("图片搜索失败：%s" % exc) from exc
    results = _bing_parse(raw, query, limit)
    if not results:
        raise MediaError('图片搜索「%s」没有可用结果，换个关键词试试' % query)
    return results


def search_images_cached(query: str, max_results: int = 8,
                         fetcher=None, downloader=None) -> dict:
    """搜索 + 逐张缓存（规格 §6 的完整闭环入口，工具回执用这个）。

    单张下载失败不阻断整体：失败的带 error 标记返回，UI 可逐张重试。
    回执里同时带本地路径与原始 URL —— Chat UI 用本地路径渲染卡片
    （不依赖临时远程 URL），Vision 直接读本地文件分析。
    """
    try:
        media_list = search_images(query, max_results, fetcher=fetcher)
    except MediaError as exc:
        return {"query": query, "error": str(exc), "results": []}
    dl = downloader or download
    cached, failed = [], []
    for media in media_list:
        try:
            dl(media)
        except MediaError as exc:
            # 真实 download 会把原因写进 media.error；注入的下载器抛错时
            # 兜底补上，保证回执里的失败项始终带可读原因。
            if not media.error:
                media.error = str(exc)
        (cached if media.local_path else failed).append(media)
    return {
        "query": query,
        "results": [m.to_dict() for m in media_list],
        "cached": len(cached),
        "failed": [{"title": m.title, "image_url": m.image_url, "error": m.error}
                   for m in failed],
    }
