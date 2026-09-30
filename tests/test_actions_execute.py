"""execute_plan 端到端测试（架构文档 §11 / §14 / §25 / §28-4）。

用「假 Painter」把真正的执行管线跑一遍：动作 → 适配层 → 官方 API 调用。
之所以必须有这层测试：几何遮罩、Smart Material 这几处原来调的是**不存在的
官方接口**（`set_geometry_mask` / `export_as_smart_material`），静态断言看不
出来，只有真的走一遍调用才会暴露。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

# 假 Painter 与夹具在 tests/fake_painter.py：与集成冒烟测试共用同一份桩，
# 否则「执行管线」和「工程生命周期」两个测试会各自漂移。
from fake_painter import painter, painter_no_scope  # noqa: E402,F401


# --------------------------------------------------------------- helper


def _entry(result, action):
    """从执行结果里取指定动作的那一条（尾部还有 select_last_created）。"""
    for item in result["results"]:
        if item.get("action") == action:
            return item
    raise AssertionError("结果里没有 %s：%r" % (action, result["results"]))


def test_geometry_mask_plan_uses_the_three_real_official_calls(painter):
    result = painter.actions.execute_plan({
        "operation_domain": "material",
        "actions": [
            {"action": "create_fill_layer", "name": "旧铜"},
            {"action": "set_geometry_mask",
             "parameters": {"type": "Mesh", "meshes": ["body"]}},
        ],
    }, verify=False)
    assert result["success"] is True
    node = painter.created[-1]
    kinds = [call[0] for call in node.calls]
    assert "set_geometry_mask_type" in kinds
    assert "set_geometry_mask_enabled_meshes" in kinds
    applied = _entry(result, "set_geometry_mask")["applied"]
    assert [item["api"].rsplit(".", 1)[-1] for item in applied] == [
        "set_geometry_mask_type", "set_geometry_mask_enabled_meshes"]


def test_geometry_mask_bad_parameters_is_a_clear_error(painter):
    with pytest.raises(painter.actions.AdapterError) as err:
        painter.actions.execute_plan({
            "operation_domain": "material",
            "actions": [
                {"action": "create_fill_layer"},
                {"action": "set_geometry_mask", "parameters": {"nope": 1}},
            ],
        }, verify=False)
    assert "几何遮罩参数无法识别" in str(err.value)


def test_save_smart_material_reports_unsupported_file_path(painter):
    result = painter.actions.execute_plan({
        "operation_domain": "material",
        "actions": [
            {"action": "create_group", "name": "铜"},
            {"action": "save_smart_material", "name": "Old Copper",
             "path": "C:/tmp/old-copper.sbsar"},
        ],
    }, verify=False)
    entry = _entry(result, "save_smart_material")
    assert entry["api"] == "substance_painter.layerstack.create_smart_material"
    assert "path_unsupported" in entry
    # 旧实现调用的是不存在的 export_as_smart_material —— 现在必须一次都不碰
    assert all("export_as_smart_material" not in str(call)
               for node in painter.created for call in node.calls)


def test_save_smart_material_requires_a_group(painter):
    with pytest.raises(painter.actions.AdapterError) as err:
        painter.actions.execute_plan({
            "operation_domain": "material",
            "actions": [
                {"action": "create_fill_layer", "name": "不是组"},
                {"action": "save_smart_material", "name": "X"},
            ],
        }, verify=False)
    assert "Group" in str(err.value)


def test_plan_runs_without_scoped_modification_but_flags_it(painter_no_scope):
    result = painter_no_scope.actions.execute_plan({
        "operation_domain": "material",
        "actions": [{"action": "create_fill_layer", "name": "x"}],
    }, verify=False)
    assert result["success"] is True
    assert result["scope_degraded"] is True


def test_missing_bake_capability_fails_before_touching_painter(painter):
    """§28-2：能力缺失必须在动手之前报清楚，而不是 AttributeError。"""
    del painter.sp.baking.bake_selected_textures_async
    with pytest.raises(painter.api.UnsupportedCapability) as err:
        painter.actions.execute_plan({
            "operation_domain": "bake",
            "actions": [{"action": "bake_start"}],
        }, verify=False)
    assert "baking.bake_selected_textures" in str(err.value)


def test_resource_import_accepts_human_usage_names(painter):
    result = painter.actions.execute_plan({
        "operation_domain": "resource",
        "actions": [{"action": "resource_import_project",
                     "path": "C:/tex/copper.png", "usage": "BASE_MATERIAL"}],
    }, verify=False)
    assert _entry(result, "resource_import_project")["resource"] == "imported"
