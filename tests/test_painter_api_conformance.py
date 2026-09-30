"""Tool Catalog ↔ 官方 API 一致性测试（架构文档 §12 / §28-2）。

`tools/painter_api_census.py` 会去 Painter 安装目录解析随包发布的 API 声明
（`resources/python/modules/substance_painter/*.py`），逐条核对 catalog 里
声明的官方入口是否真实存在。装了 Painter 的机器上必须全绿；CI 上没有
Painter 时自动跳过。

这条测试的价值来自实证：2026-09-30 首次运行时抓出 20 条声明对不上官方
API 0.3.4，其中三处是真会崩的调用——
    set_geometry_mask          → 官方只有 set_geometry_mask_type / ..._enabled_*
    save_smart_material        → 官方只有 create_smart_material（没有 export_as_*）
    save_smart_mask            → 同上
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from core.tools import REGISTRY  # noqa: E402


def _census_module():
    spec = importlib.util.spec_from_file_location(
        "painter_api_census", ROOT / "tools" / "painter_api_census.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def census():
    module = _census_module()
    api_dir = module.find_api_dir()
    if not api_dir:
        pytest.skip("本机没有 Painter 官方 API 声明目录，跳过一致性校验")
    return module.Census.from_dir(api_dir)


def test_every_declared_api_path_exists_in_the_installed_api(census):
    """每条 api / api_alternatives 至少有一条能在官方 API 里解析成功。"""
    failures = []
    for spec in REGISTRY.specs:
        paths = spec.api_paths()
        if not paths:
            continue
        results = [census.resolve(path) for path in paths]
        if not any(result["status"] in {"ok", "ok_alias"} for result in results):
            failures.append((spec.name, [(r["path"], r["detail"]) for r in results]))
    assert failures == [], "工具声明的官方 API 路径不存在：%r" % (failures,)


def test_declarations_use_canonical_module_paths(census):
    """别名解析（定义在别的子模块）不允许出现在主入口上。

    例如 layerstack.SourceSubstance 虽然能解析（被 import 进来），但规范
    路径是 source.SourceSubstance；主入口必须是规范路径，备选入口也要求
    规范化，避免文档跟着漂移。
    """
    aliases = []
    for spec in REGISTRY.specs:
        for path in spec.api_paths():
            result = census.resolve(path)
            if result["status"] == "ok_alias":
                aliases.append((spec.name, path, result["detail"]))
    assert aliases == [], "以下声明应写成规范路径：%r" % (aliases,)


def test_capability_probes_are_declared_with_verification_evidence(census):
    """能力表里的每条探测路径都必须能在官方 API 里解析，且注明验证版本。"""
    from core.painter_api import CAPABILITIES

    unresolved = []
    for cap in CAPABILITIES:
        result = census.resolve("substance_painter." + cap.probe)
        if result["status"] not in {"ok", "ok_alias"}:
            unresolved.append((cap.name, cap.probe, result["detail"]))
        assert cap.verified_on, cap.name
    assert unresolved == [], "能力探测路径对不上官方 API：%r" % (unresolved,)


def test_catalog_has_no_orphan_api_paths_registry_wide():
    """每个工具都必须声明官方入口（宏工具除外）。"""
    missing = [spec.name for spec in REGISTRY.specs
               if not spec.api_paths() and not spec.macro]
    assert missing == [], missing
