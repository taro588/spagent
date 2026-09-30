"""MediaObject 与媒体管线（规格 §6）。

锁五件事：
1. MediaObject 字段与规格 §6.1 键集逐项对齐；
2. 下载缓存：落盘、local_path/width/height 回填、幂等命中不发请求；
3. 下载失败：明确错误 + 可重试（失败不阻断重试成功）；
4. Bing 图片搜索结果解析成结构化 MediaObject；
5. 搜索 + 缓存的完整回执：单张失败不阻断整体。
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from core import media  # noqa: E402


# ------------------------------------------------------------------ 结构
def test_mediaobject_keys_match_the_spec():
    """规格 §6.1 的 12 个键必须一个不少（local_path/error 是本地扩展）。"""
    keys = set(media.MediaObject().to_dict())
    spec_keys = {"id", "type", "title", "source_url", "image_url", "thumbnail_url",
                 "width", "height", "license", "provider", "search_query", "metadata"}
    assert spec_keys <= keys
    assert {"local_path", "error"} <= keys  # 本地管线扩展


def test_mediaobject_roundtrip():
    obj = media.MediaObject(id="x", title="旧铜", image_url="https://a/b.png",
                            provider="bing", search_query="旧铜", width=800, height=600)
    restored = media.MediaObject.from_dict(json.loads(json.dumps(obj.to_dict())))
    assert restored == obj
    # 未知键不炸（未来回执里带新字段也不至于断）
    assert media.MediaObject.from_dict({"title": "t", "future_key": 1}).title == "t"


# ------------------------------------------------------------------ 尺寸
def _png_bytes(width, height):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


def test_image_size_reads_png_jpeg_magic():
    assert media.image_size(_png_bytes(320, 200)) == (320, 200)
    # JPEG：SOI + APP0(段长 0x10 含自身 2 字节 → 内容 14 字节) + SOF0(精度/高/宽)
    jpeg = (b"\xff\xd8"
            b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xc0\x00\x11\x08\x00\x64\x00\xc8\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    assert media.image_size(jpeg) == (200, 100)
    assert media.image_size(b"not an image") == (None, None)


# ------------------------------------------------------------------ 下载
class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, limit=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_CACHE_ROOT", tmp_path / "media_cache")
    return tmp_path / "media_cache"


def test_download_caches_and_backfills(cache_dir, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(_png_bytes(640, 480))

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    item = media.MediaObject(image_url="https://example.com/copper.png")
    result = media.download(item)
    assert result.local_path and os.path.exists(result.local_path)
    assert result.local_path.endswith(".png")
    assert (result.width, result.height) == (640, 480)
    assert result.error == ""
    # 幂等：第二次下载命中缓存，不再发请求
    again = media.download(media.MediaObject(image_url="https://example.com/copper.png"))
    assert again.local_path == result.local_path
    assert len(calls) == 1


def test_download_failure_gives_clear_error_and_retry_recovers(cache_dir, monkeypatch):
    state = {"fail": True}

    def fake_urlopen(request, timeout=None):
        if state["fail"]:
            raise OSError("connection refused")
        return _FakeResponse(_png_bytes(10, 10))

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    item = media.MediaObject(image_url="https://example.com/x.png")
    with pytest.raises(media.MediaError) as excinfo:
        media.download(item)
    assert "下载失败" in str(excinfo.value)
    assert item.error and "connection refused" in item.error
    assert item.local_path == ""  # 失败不留半截缓存
    # 规格要求：下载失败必须可重试
    state["fail"] = False
    fixed = media.retry(item)
    assert fixed.local_path and fixed.error == ""


def test_download_rejects_missing_url(cache_dir):
    with pytest.raises(media.MediaError):
        media.download(media.MediaObject())


# ------------------------------------------------------------------ 搜索
_BING_HTML = (
    '<div class="img_cont"><a class="iusc" m="{&quot;murl&quot;:&quot;'
    'https://img.example.com/copper1.jpg&quot;,&quot;purl&quot;:&quot;'
    'https://www.example.com/gallery&quot;,&quot;t&quot;:&quot;旧铜腐蚀特写&quot;,'
    '&quot;turl&quot;:&quot;https://tse.example.com/th1.jpg&quot;,&quot;w&quot;:&quot;1200&quot;,'
    '&quot;h&quot;:&quot;800&quot;}"></a>'
    '<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/copper2.jpg&quot;,'
    '&quot;t&quot;:&quot;铜锈表面&quot;,&quot;w&quot;:&quot;640&quot;,&quot;h&quot;:&quot;480&quot;}"></a>'
    '<a class="iusc" m="{&quot;t&quot;:&quot;没有图址的结果应被跳过&quot;}"></a></div>'
)


def test_search_images_parses_bing_results(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_CACHE_ROOT", tmp_path)

    def fetcher(url):
        assert "images/async" in url and "copper" in url
        return _BING_HTML

    results = media.search_images("copper", max_results=5, fetcher=fetcher)
    assert len(results) == 2
    first = results[0]
    assert isinstance(first, media.MediaObject)
    assert first.image_url == "https://img.example.com/copper1.jpg"
    assert first.source_url == "https://www.example.com/gallery"
    assert first.title == "旧铜腐蚀特写"
    assert first.thumbnail_url == "https://tse.example.com/th1.jpg"
    assert (first.width, first.height) == (1200, 800)
    assert first.provider == "bing"
    assert first.search_query == "copper"
    assert first.id.startswith("bing-")
    # 规格要求：来源、许可信息、搜索关键词随对象保存
    assert "source_url" in first.to_dict() and "license" in first.to_dict()


def test_search_images_errors_are_user_readable():
    with pytest.raises(media.MediaError) as excinfo:
        media.search_images("", fetcher=lambda url: "")
    assert "query" in str(excinfo.value)
    with pytest.raises(media.MediaError) as excinfo:
        media.search_images("旧铜", fetcher=lambda url: "<html>empty</html>")
    assert "没有可用结果" in str(excinfo.value)


def test_search_images_cached_full_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_CACHE_ROOT", tmp_path / "media_cache")

    def fetcher(url):
        return _BING_HTML

    def downloader(m):
        if "copper2" in m.image_url:
            raise media.MediaError("下载失败：boom")
        m.local_path = tmp_path / "cached.png"
        return m

    receipt = media.search_images_cached("copper", max_results=5,
                                         fetcher=fetcher, downloader=downloader)
    assert receipt["query"] == "copper"
    assert receipt["cached"] == 1
    assert len(receipt["results"]) == 2
    assert receipt["failed"] == [
        {"title": "铜锈表面", "image_url": "https://img.example.com/copper2.jpg",
         "error": "下载失败：boom"}]
    # 成功那张：回执带本地路径，供 UI 卡片与 Vision 直接使用
    assert receipt["results"][0]["local_path"]


def test_search_images_cached_search_failure_is_a_receipt_not_a_crash():
    receipt = media.search_images_cached("旧铜", fetcher=lambda url: (_ for _ in ()).throw(
        OSError("network down")))
    assert receipt["error"] and "图片搜索失败" in receipt["error"]
    assert receipt["results"] == []


# ------------------------------------------------------------------ 工具回执
def test_run_image_search_empty_query_returns_error_receipt():
    from core import ai_client
    receipt = ai_client._run_image_search({})
    assert receipt["error"] and receipt["results"] == []
