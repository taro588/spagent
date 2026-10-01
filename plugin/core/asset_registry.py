"""Asset Registry / 资源生命周期（规格 §10）。

规格原文逐项落地：

* 状态机：GENERATED → DOWNLOADED → VALIDATED → REGISTERED →
  IMPORTED_TO_PAINTER → ASSIGNED → VERIFIED → EXPORTED。
  只允许沿状态机**顺次前进**（或显式失败标记），禁止跳步与回退。
* 每个资产拥有稳定 ID、来源、provider、输入、生成参数、文件路径、哈希和状态。
* 下载完成先验证文件是否真实、完整、格式正确，再导入 Painter
  —— `require_importable()` 在状态上强制 VALIDATED 前不得导入。
* 避免临时 URL 直接喂给 Painter：资产记录的是本地缓存路径。
* 缓存支持复用，降低重复生成成本：按 provider + route + 输入指纹查可复用资产，
  命中直接拿文件，不再发起生成。

持久化：%LOCALAPPDATA%\\SP AI Assistant\\asset_registry.json，
原子落盘（临时文件 + 改名），中断不会留下半截注册表。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# 规格 §10 状态机（原文顺序）。
ASSET_STATES: List[str] = [
    "GENERATED",
    "DOWNLOADED",
    "VALIDATED",
    "REGISTERED",
    "IMPORTED_TO_PAINTER",
    "ASSIGNED",
    "VERIFIED",
    "EXPORTED",
]

_STATE_INDEX = {name: i for i, name in enumerate(ASSET_STATES)}

# 合法迁移：只能前进到紧邻的下一个状态。
_TRANSITIONS = {name: [ASSET_STATES[i + 1]] for i, name in enumerate(ASSET_STATES[:-1])}


class RegistryError(Exception):
    """注册表错误：消息面向用户，必须能直接读懂。"""


def registry_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or "") / "SP AI Assistant"
    if not str(root).strip() or "LOCALAPPDATA" not in os.environ:
        # 测试环境：隔离目录，别碰用户真实注册表
        root = Path(tempfile.gettempdir()) / "spai_tests" / "asset_registry"
    root.mkdir(parents=True, exist_ok=True)
    return root / "asset_registry.json"


class Asset:
    """一个生成资产的完整记录（规格 §10 字段逐项对齐）。"""

    def __init__(self, asset_id: str = "", source: str = "", provider: str = "",
                 route: str = "", fingerprint: str = "", params: Optional[dict] = None,
                 files: Optional[Dict[str, str]] = None, state: str = "GENERATED"):
        self.asset_id = asset_id or uuid.uuid4().hex[:12]
        self.source = source                # 来源：用户 prompt / 参考图路径等
        self.provider = provider            # 例：patina
        self.route = route                  # §8.1 路线：text_to_pbr / image_to_pbr / extract
        self.fingerprint = fingerprint      # 输入指纹（pbr.input_fingerprint）
        self.params: Dict[str, Any] = dict(params or {})
        self.files: Dict[str, str] = dict(files or {})  # map_type → 本地缓存路径
        self.hashes: Dict[str, str] = {}    # map_type → sha256（VALIDATED 时填）
        self.state = state
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.error = ""

    # ---- 状态机 ----
    def can_transition(self, new_state: str) -> bool:
        return new_state in _TRANSITIONS.get(self.state, [])

    def transition(self, new_state: str) -> "Asset":
        if not self.can_transition(new_state):
            raise RegistryError(
                "非法状态迁移：%s → %s（状态机只允许顺次前进：%s）"
                % (self.state, new_state, " → ".join(ASSET_STATES)))
        self.state = new_state
        self.error = ""
        return self

    def fail(self, reason: str) -> "Asset":
        """显式失败标记：状态停在原地，错误原因可读可追溯。"""
        self.error = str(reason)
        return self

    def require_importable(self) -> None:
        """导入 Painter 前的状态闸（规格 §10：先验证再导入）。

        只有 REGISTERED / IMPORTED_TO_PAINTER 允许导入动作
        （IMPORTED_TO_PAINTER 允许是为了幂等重导入同一资产）。"""
        if self.state not in ("REGISTERED", "IMPORTED_TO_PAINTER"):
            raise RegistryError(
                "资产 %s 处于 %s 状态，尚未完成验证与登记，"
                "不允许导入 Painter（规格 §10：先验证、再导入）"
                % (self.asset_id, self.state))

    def compute_hashes(self) -> Dict[str, str]:
        """对每个通道文件算 sha256（VALIDATED 之前的真实性检查之一）。"""
        for map_type, path in self.files.items():
            try:
                self.hashes[map_type] = hashlib.sha256(
                    Path(path).read_bytes()).hexdigest()
            except OSError as exc:
                raise RegistryError("通道文件 %s 读取失败：%s" % (map_type, exc)) from exc
        return self.hashes

    def to_dict(self) -> dict:
        return {"asset_id": self.asset_id, "source": self.source,
                "provider": self.provider, "route": self.route,
                "fingerprint": self.fingerprint, "params": self.params,
                "files": self.files, "hashes": self.hashes, "state": self.state,
                "created_at": self.created_at, "error": self.error}

    @classmethod
    def from_dict(cls, data: dict) -> "Asset":
        asset = cls(asset_id=str(data.get("asset_id") or ""),
                    source=str(data.get("source") or ""),
                    provider=str(data.get("provider") or ""),
                    route=str(data.get("route") or ""),
                    fingerprint=str(data.get("fingerprint") or ""),
                    params=data.get("params") or {},
                    files=data.get("files") or {},
                    state=str(data.get("state") or "GENERATED"))
        asset.hashes = dict(data.get("hashes") or {})
        asset.created_at = str(data.get("created_at") or asset.created_at)
        asset.error = str(data.get("error") or "")
        return asset


class Registry:
    """资产注册表：持久化 + 状态推进 + 缓存复用查询。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else registry_path()
        self._assets: Dict[str, Asset] = {}
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        self._assets = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in (data or {}).get("assets", []):
                asset = Asset.from_dict(item)
                self._assets[asset.asset_id] = asset
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError("资产注册表损坏，无法读取：%s（%s）"
                                % (self.path, exc)) from exc

    def save(self) -> None:
        """原子落盘：临时文件 + 改名，中断不留半截文件。"""
        payload = {"assets": [a.to_dict() for a in self._assets.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   suffix=".json.part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---- 登记与查询 ----
    def register(self, asset: Asset) -> Asset:
        """新增资产并落盘。asset_id 冲突直接拒绝（稳定 ID 不可篡改）。"""
        if asset.asset_id in self._assets:
            raise RegistryError("资产 ID %s 已存在，不得重复登记" % asset.asset_id)
        self._assets[asset.asset_id] = asset
        self.save()
        return asset

    def get(self, asset_id: str) -> Asset:
        asset = self._assets.get(asset_id)
        if not asset:
            raise RegistryError("找不到资产 %s" % asset_id)
        return asset

    def advance(self, asset_id: str, new_state: str) -> Asset:
        asset = self.get(asset_id).transition(new_state)
        self.save()
        return asset

    def mark_failed(self, asset_id: str, reason: str) -> Asset:
        asset = self.get(asset_id).fail(reason)
        self.save()
        return asset

    def find_reusable(self, provider: str, route: str, fingerprint: str) -> Optional[Asset]:
        """缓存复用（规格 §10：降低重复生成成本）。

        命中条件：provider + route + 输入指纹完全一致，且资产已走完
        VALIDATED（文件已验证存在且完好）、没有失败标记。
        返回 None 表示必须重新生成。"""
        for asset in self._assets.values():
            if (asset.provider == provider and asset.route == route
                    and asset.fingerprint == fingerprint and not asset.error
                    and _STATE_INDEX.get(asset.state, -1) >= _STATE_INDEX["VALIDATED"]
                    and all(Path(p).exists() for p in asset.files.values())):
                return asset
        return None

    def all(self) -> List[Asset]:
        return list(self._assets.values())
