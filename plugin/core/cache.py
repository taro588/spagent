"""统一缓存与成本策略（规格 §22）。

规格原文四条纪律，本模块逐条落地：

1. 「搜索结果、图片和 PBR 结果均可缓存」——三类产物统一进同一个索引
   （CATEGORIES），图片/PBR 的大文件留在各自目录，索引只存路径与元数据。
2. 「相同 prompt + reference + 参数 + provider + model 生成可命中缓存」
   ——这就是 make_key 的全部输入；任何一项不同都不是同一次生成，
   绝不命中别人的缓存（宁可多花一次请求，不可给错结果）。
3. 「先低成本/低分辨率验证，再在通过后生成最终分辨率」——两阶段编排
   在 resolution.py；本模块只负责记录 draft/final 各自的产物。
4. 「导出分辨率必须考虑 GPU 与项目限制」——同上，见 resolution.py。

纪律：本模块不 import substance_painter / Qt，必须能在 Painter 之外
（CI）导入并断言；网络访问为零（只落盘）。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

#: §22 三类可缓存产物。新增类别必须同时更新这里与 wire 测试。
CATEGORIES = ("search", "image", "pbr")

#: 默认 TTL：7 天。参考图与搜索结果会过期，PBR 资产另由 Registry 管生命周期。
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
#: 索引条目上限与字节上限（LRU 淘汰用）。
DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class CacheError(Exception):
    """缓存错误：面向用户可读（与 MediaError / PBRError 同风格）。"""


def cache_root() -> Path:
    """缓存根目录（%LOCALAPPDATA%\\SP AI Assistant\\cache）。

    CI 裸环境（无 LOCALAPPDATA）退回 temp 目录，保证测试可离线跑。
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / "SP AI Assistant" / "cache"
    else:  # pragma: no cover - CI 走这条
        root = Path(tempfile.gettempdir()) / "spai_tests" / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize(part: Any) -> Any:
    """把任意参数规整成可稳定序列化的形式（字典按键排序）。"""
    if isinstance(part, dict):
        return {str(k): _normalize(v) for k, v in sorted(part.items(), key=lambda kv: str(kv[0]))}
    if isinstance(part, (list, tuple)):
        return [_normalize(v) for v in part]
    if isinstance(part, Path):
        return str(part)
    if isinstance(part, (str, int, float, bool)) or part is None:
        return part
    return str(part)


def make_key(kind: str, *, prompt: str = "", reference: str = "",
             params: Optional[Dict[str, Any]] = None, provider: str = "",
             model: str = "", extra: str = "") -> str:
    """按规格 §22 生成缓存键：prompt + reference + 参数 + provider + model。

    规范化（去空白 / 键排序 / JSON 稳定序列化）后取 sha256 前 32 位，
    带 kind 前缀便于人工识别。同一组输入必然得到同一个键；任何一项
    不同（哪怕只是 provider 大小写）都会得到不同的键。
    """
    if kind not in CATEGORIES:
        raise CacheError("未知缓存类别 %r，允许：%s" % (kind, "、".join(CATEGORIES)))
    body = json.dumps({
        "kind": kind,
        "prompt": str(prompt or "").strip(),
        "reference": str(reference or "").strip(),
        "params": _normalize(params or {}),
        "provider": str(provider or "").strip().lower(),
        "model": str(model or "").strip(),
        "extra": str(extra or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "%s-%s" % (kind, hashlib.sha256(body.encode("utf-8")).hexdigest()[:32])


class Cache:
    """磁盘索引 + TTL + LRU 的统一缓存。

    索引是单个 JSON（原子写：先写 .part 再 replace），条目里只存产物
    **路径**与元数据，不存二进制——图片与 PBR 通道图仍由 media / registry
    自己的目录承载，避免同一份数据落两遍。
    """

    def __init__(self, root: Optional[Path] = None, ttl: float = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.root = Path(root) if root else cache_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.ttl = float(ttl)
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._data = self._load()

    # ---------------------------------------------------------------- 索引
    def _load(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                raw.setdefault("hits", 0)
                raw.setdefault("misses", 0)
                return raw
        except Exception:
            pass
        return {"entries": {}, "hits": 0, "misses": 0}

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_name(self.index_path.name + ".part")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.index_path)

    # ---------------------------------------------------------------- 读写
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """取缓存条目；缺失或已过期返回 None（并计入 miss）。"""
        entry = self._data["entries"].get(key)
        now = time.time()
        if not entry or self._expired(entry, now):
            if entry:  # 过期条目顺手清掉，别留在索引里骗人
                self._data["entries"].pop(key, None)
                self._save()
            self._data["misses"] = int(self._data.get("misses", 0)) + 1
            self._save()
            return None
        entry["last_used"] = now
        self._data["hits"] = int(self._data.get("hits", 0)) + 1
        self._save()
        return dict(entry)

    def put(self, key: str, *, kind: str = "", path: str = "",
            meta: Optional[Dict[str, Any]] = None, ttl: Optional[float] = None) -> Dict[str, Any]:
        """写入/更新一条缓存；返回落盘后的条目。"""
        if not key:
            raise CacheError("缓存键不能为空")
        if not kind:
            kind = str(key).split("-", 1)[0]
        if kind not in CATEGORIES:
            raise CacheError("未知缓存类别 %r，允许：%s" % (kind, "、".join(CATEGORIES)))
        now = time.time()
        entry = {
            "key": key,
            "kind": kind,
            "created": now,
            "last_used": now,
            "ttl": float(self.ttl if ttl is None else ttl),
            "path": str(path or ""),
            "bytes": self._size_of(path),
            "meta": dict(meta or {}),
        }
        self._data["entries"][key] = entry
        self.purge(now=now)
        self._save()
        return dict(entry)

    def touch(self, key: str) -> bool:
        """只更新 last_used（LRU 保命），不改变命中统计。"""
        entry = self._data["entries"].get(key)
        if not entry:
            return False
        entry["last_used"] = time.time()
        self._save()
        return True

    def drop(self, key: str) -> bool:
        """删除单条缓存；返回是否真的删掉了。"""
        if self._data["entries"].pop(key, None) is None:
            return False
        self._save()
        return True

    # ---------------------------------------------------------------- 维护
    @staticmethod
    def _expired(entry: Dict[str, Any], now: float) -> bool:
        ttl = float(entry.get("ttl") or 0)
        return ttl > 0 and (now - float(entry.get("created") or 0)) > ttl

    @staticmethod
    def _size_of(path: str) -> int:
        if not path:
            return 0
        try:
            return Path(path).stat().st_size
        except OSError:
            return 0

    def purge(self, *, now: Optional[float] = None,
              expired_only: bool = False) -> Dict[str, int]:
        """清理过期条目，再按 LRU 淘汰到容量以内。

        返回 {"expired": n, "evicted": m}。expired_only=True 时只清过期，
        不做 LRU 淘汰（手动清理场景）。被淘汰条目只从索引移除，
        **不删产物文件**——产物可能仍被 Registry / UI 卡片引用。
        """
        now = time.time() if now is None else now
        entries = self._data["entries"]
        expired = [k for k, e in entries.items() if self._expired(e, now)]
        for k in expired:
            entries.pop(k, None)
        evicted = 0
        if not expired_only:
            total = sum(int(e.get("bytes") or 0) for e in entries.values())
            if len(entries) > self.max_entries or total > self.max_bytes:
                order = sorted(entries.values(), key=lambda e: float(e.get("last_used") or 0))
                for entry in order:
                    if len(entries) <= self.max_entries and total <= self.max_bytes:
                        break
                    total -= int(entry.get("bytes") or 0)
                    entries.pop(entry.get("key"), None)
                    evicted += 1
        if expired or evicted:
            self._save()
        return {"expired": len(expired), "evicted": evicted}

    def clear(self) -> int:
        """清空索引（保留命中统计）。返回清掉的条目数。"""
        count = len(self._data["entries"])
        self._data["entries"] = {}
        self._save()
        return count

    # ---------------------------------------------------------------- 统计
    def stats(self) -> Dict[str, Any]:
        """命中率与「省下的请求数」（规格 §22 成本纪律的量化口径）。"""
        entries = self._data["entries"]
        hits = int(self._data.get("hits", 0))
        misses = int(self._data.get("misses", 0))
        total = hits + misses
        by_kind: Dict[str, int] = {}
        for entry in entries.values():
            kind = str(entry.get("kind") or "?")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "entries": len(entries),
            "bytes": sum(int(e.get("bytes") or 0) for e in entries.values()),
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
            "saved_requests": hits,   # 每次命中 = 少发一次网络请求
            "by_kind": by_kind,
            "root": str(self.root),
        }
