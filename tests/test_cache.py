"""统一缓存与成本策略（规格 §22）。

为什么钉这组测试：缓存是**唯一会「给错结果」的性能优化**——键算错一次，
用户拿到的就是别的 prompt 的图，而且看起来一切正常。四条硬锁：

1. **键的判别力**：prompt / reference / 参数 / provider / model 任意一项
   不同必须得到不同键（规格 §22 逐字列举的五个维度，一个都不能漏）。
2. **键的稳定性**：等价输入（键顺序、空白、provider 大小写）必须同键，
   否则缓存永远命不中，等于没有缓存。
3. **TTL 与 LRU**：过期不许命中；超容量按最久未用淘汰。
4. **统计诚实**：saved_requests 就是 hits，不许虚报省了多少请求。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from core import cache as cache_mod  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return cache_mod.Cache(root=tmp_path, ttl=3600, max_entries=5, max_bytes=1024)


# ------------------------------------------------------------------ 键
def test_key_inputs_match_the_spec_five_dimensions():
    """规格 §22：prompt + reference + 参数 + provider + model，五项各自可判别。"""
    base = cache_mod.make_key(
        "pbr", prompt="rusty copper", reference="ref.png",
        params={"steps": 20}, provider="fal", model="patina")
    variants = {
        "prompt": cache_mod.make_key(
            "pbr", prompt="rusty copper!", reference="ref.png",
            params={"steps": 20}, provider="fal", model="patina"),
        "reference": cache_mod.make_key(
            "pbr", prompt="rusty copper", reference="ref2.png",
            params={"steps": 20}, provider="fal", model="patina"),
        "params": cache_mod.make_key(
            "pbr", prompt="rusty copper", reference="ref.png",
            params={"steps": 30}, provider="fal", model="patina"),
        "provider": cache_mod.make_key(
            "pbr", prompt="rusty copper", reference="ref.png",
            params={"steps": 20}, provider="openai", model="patina"),
        "model": cache_mod.make_key(
            "pbr", prompt="rusty copper", reference="ref.png",
            params={"steps": 20}, provider="fal", model="patina-v2"),
    }
    for field, key in variants.items():
        assert key != base, "%s 不同却得到同一个缓存键（会命中别人的生成）" % field


def test_key_is_stable_for_equivalent_inputs():
    """等价输入必须同键：键顺序、空白、provider 大小写都不该改变结果。"""
    a = cache_mod.make_key("search", prompt="  old copper  ",
                           params={"b": 2, "a": 1}, provider="Bing")
    b = cache_mod.make_key("search", prompt="old copper",
                           params={"a": 1, "b": 2}, provider="bing")
    assert a == b
    assert a.startswith("search-")


def test_key_rejects_unknown_category():
    with pytest.raises(cache_mod.CacheError):
        cache_mod.make_key("video", prompt="x")


def test_all_three_spec_categories_are_supported():
    """规格 §22 原文：搜索结果、图片和 PBR 结果均可缓存。"""
    assert set(cache_mod.CATEGORIES) == {"search", "image", "pbr"}


# ------------------------------------------------------------------ 读写
def test_put_get_roundtrip(store):
    key = cache_mod.make_key("image", prompt="copper")
    store.put(key, kind="image", path="", meta={"title": "铜"})
    got = store.get(key)
    assert got is not None
    assert got["meta"]["title"] == "铜"
    assert got["kind"] == "image"


def test_miss_is_counted_and_returns_none(store):
    assert store.get("image-deadbeef") is None
    assert store.stats()["misses"] == 1
    assert store.stats()["hits"] == 0


def test_index_persists_across_instances(tmp_path):
    """索引必须落盘：重开插件（新 Cache 实例）仍能命中。"""
    key = cache_mod.make_key("pbr", prompt="copper")
    first = cache_mod.Cache(root=tmp_path)
    first.put(key, kind="pbr", meta={"route": "material"})
    second = cache_mod.Cache(root=tmp_path)
    assert second.get(key) is not None
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["entries"]


def test_expired_entry_is_not_served(tmp_path):
    """TTL 到期后不得命中——过期条目要顺手清掉，不能留在索引里骗人。"""
    store = cache_mod.Cache(root=tmp_path, ttl=1)
    key = cache_mod.make_key("search", prompt="copper")
    entry = store.put(key, kind="search", ttl=1)
    # 手工把时间往前拨（不 sleep，测试要快）
    store._data["entries"][key]["created"] = time.time() - 10
    assert store.get(key) is None
    assert store.stats()["entries"] == 0
    assert entry["ttl"] == 1


def test_touch_keeps_entry_alive_for_lru(store):
    a = cache_mod.make_key("search", prompt="a")
    b = cache_mod.make_key("search", prompt="b")
    store.put(a, kind="search")
    store.put(b, kind="search")
    store._data["entries"][a]["last_used"] = time.time() - 100
    assert store.touch(a) is True
    assert store.touch("search-nope") is False


# ------------------------------------------------------------------ 淘汰
def test_lru_eviction_by_entry_count(tmp_path):
    store = cache_mod.Cache(root=tmp_path, ttl=3600, max_entries=100)
    keys = [cache_mod.make_key("search", prompt="q%d" % i) for i in range(7)]
    for i, key in enumerate(keys):
        store.put(key, kind="search")
        store._data["entries"][key]["last_used"] = time.time() - (100 - i)
    assert store.stats()["entries"] == 7
    store.max_entries = 5          # 缩小容量后触发淘汰
    result = store.purge()
    assert result["evicted"] == 2
    assert store.stats()["entries"] == 5
    assert store.get(keys[0]) is None      # 最久未用，被淘汰
    assert store.get(keys[1]) is None      # 次久未用
    assert store.get(keys[6]) is not None  # 最近用过的留着


def test_lru_eviction_by_bytes(tmp_path):
    blob = tmp_path / "big.bin"
    blob.write_bytes(b"x" * 50)
    store = cache_mod.Cache(root=tmp_path, ttl=3600, max_entries=100, max_bytes=100)
    store.put(cache_mod.make_key("image", prompt="a"), kind="image", path=str(blob))
    assert store.stats()["bytes"] == 50
    store.max_bytes = 10          # 缩小字节上限后触发淘汰
    result = store.purge()
    assert result["evicted"] == 1
    # 淘汰只从索引移除，**产物文件不能删**（可能还被 UI 卡片引用）
    assert store.stats()["entries"] == 0
    assert blob.exists()


def test_purge_expired_only_skips_lru(tmp_path):
    store = cache_mod.Cache(root=tmp_path, ttl=3600, max_entries=100)
    for i in range(6):
        store.put(cache_mod.make_key("search", prompt="q%d" % i), kind="search")
    result = store.purge(expired_only=True)
    assert result["expired"] == 0 and result["evicted"] == 0
    store.max_entries = 3
    store.purge(expired_only=True)
    assert store.stats()["entries"] == 6   # expired_only 不做 LRU，允许超容量
    store.purge()
    assert store.stats()["entries"] == 3


def test_drop_and_clear(store):
    key = cache_mod.make_key("search", prompt="a")
    store.put(key, kind="search")
    assert store.drop(key) is True
    assert store.drop(key) is False
    store.put(key, kind="search")
    assert store.clear() == 1
    assert store.stats()["entries"] == 0


# ------------------------------------------------------------------ 统计
def test_stats_reports_honest_savings(store):
    """saved_requests 必须等于 hits——这是「省了多少次请求」的唯一口径。"""
    key = cache_mod.make_key("image", prompt="copper")
    store.put(key, kind="image")
    store.get(key)
    store.get(key)
    store.get("image-miss")
    stats = store.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["saved_requests"] == stats["hits"] == 2
    assert stats["hit_rate"] == round(2 / 3, 4)
    assert stats["by_kind"] == {"image": 1}


def test_put_rejects_unknown_kind(store):
    with pytest.raises(cache_mod.CacheError):
        store.put("image-x", kind="video")
    with pytest.raises(cache_mod.CacheError):
        store.put("")


def test_cache_root_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = cache_mod.cache_root()
    assert str(root).startswith(str(tmp_path))
    assert root.name == "cache"
