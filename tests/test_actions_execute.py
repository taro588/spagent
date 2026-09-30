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


# ----------------------------------------------------------------------
# 真机冒烟抓到的回归（2026-09-30，Painter 11.0.0.4202 / 官方 API 0.3.4）
#
# 假 Painter 桩原来把 Color 写成 `lambda r, g, b, a:`（收 4 个参数），于是三个
# 核心单通道写入在单元测试里全绿、在真机上全炸：
#     AttributeError: 'float' object has no attribute 'value'
# 根因是官方 Color 只有 (r, g, b, color_space=None) —— 第 4 个分量落到 color_space
# 上，内部 _to_private_color_space() 随即取 .value 失败。
# 下面几条把「桩贴近真实签名 + 不许再传第 4 个分量」钉死。
# ----------------------------------------------------------------------


def test_parse_color_never_passes_more_than_three_components(painter):
    samples = [
        "#ff0000",            # #RRGGBB
        "#ff000080",          # #RRGGBBAA（alpha 必须被丢弃）
        [1.0, 0.0, 0.0],      # RGB 数组
        [1.0, 0.0, 0.0, 1.0],  # RGBA 数组
        [255, 0, 0],
        {"r": 1.0, "g": 0.0, "b": 0.0},
        {"r": 1.0, "g": 0.0, "b": 0.0, "a": 0.5},
    ]
    for raw in samples:
        color = painter.actions._parse_color(raw)
        assert len(color) == 3, "颜色写法 %r 传了 %d 个分量" % (raw, len(color))


def test_single_channel_numbers_use_three_component_color(painter):
    for value in (0.0, 0.35, 1.0):
        color = painter.actions._channel_source_value("Roughness", value)
        assert len(color) == 3, "数值 %r 传了 %d 个分量" % (value, len(color))


def test_three_core_channel_writes_pass_a_real_signature_stub(painter):
    """§28 点名的三个核心写入，在「按官方签名校验的桩」上必须全过。"""
    result = painter.actions.execute_plan({
        "operation_domain": "material",
        "actions": [
            {"action": "create_fill_layer", "name": "smoke"},
            {"action": "set_base_color", "value": [0.15, 0.45, 0.75, 1.0]},
            {"action": "set_roughness", "value": 0.35},
            {"action": "set_metallic", "value": 0.0},
        ],
    }, verify=False)
    assert result["success"] is True
    for kind in ("set_base_color", "set_roughness", "set_metallic"):
        assert _entry(result, kind)["api"], kind


def test_applied_receipt_counts_as_api_evidence(painter):
    """适配层对「一个动作拆成多个官方调用」回执 `applied`，校验器必须认它。

    真机冒烟暴露过这个契约不一致：set_geometry_mask / add_levels / create_group
    都执行成功，却因为结果里没有 `api` 字段被判 api_evidence 失败。
    """
    result = painter.actions.execute_plan({
        "operation_domain": "material",
        "actions": [
            {"action": "create_fill_layer", "name": "masked"},
            {"action": "set_geometry_mask", "parameters": {"type": "Mesh"}},
        ],
    })
    report = result["verification"]
    assert report["verified"] is True, report
    assert report["failed"] == 0, report


def test_effect_and_group_results_carry_api_evidence(painter):
    """add_levels / create_group 的结果也必须带官方入口（§18.1）。"""
    result = painter.actions.execute_plan({
        "operation_domain": "material",
        "actions": [
            {"action": "create_fill_layer", "name": "x"},
            {"action": "add_levels"},
            {"action": "create_group", "name": "g"},
        ],
    })
    assert _entry(result, "add_levels")["api"].endswith("insert_levels_effect")
    assert _entry(result, "create_group")["api"].endswith("insert_group")
    assert result["verification"]["failed"] == 0, result["verification"]
