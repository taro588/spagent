"""适配层功能测试（架构文档 §11 / §14 / §16 / §28-2）。

用假 `substance_painter` 模块驱动，因此不需要 Painter 也能在 CI 里跑：
重点验证「缺失能力必须报清楚」而不是抛 AttributeError。
"""
from __future__ import annotations

import enum
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from core.painter_api import (  # noqa: E402
    AdapterError,
    CAPABILITIES,
    PainterAPI,
    PainterUnavailable,
    UnsupportedCapability,
)


# ---------------------------------------------------------------- fake API
class FakeNode:
    def __init__(self, name="node"):
        self.name = name
        self.calls = []
        self.source_mode = "Material"

    def set_name(self, value):
        self.calls.append(("set_name", value))
        self.name = value

    def get_name(self):
        return self.name

    def set_source(self, channel, source):
        self.calls.append(("set_source", channel, source))

    def set_material_source(self, resource_id):
        self.calls.append(("set_material_source", resource_id))

    def set_geometry_mask_type(self, value):
        self.calls.append(("set_geometry_mask_type", value))

    def set_geometry_mask_enabled_meshes(self, value):
        self.calls.append(("set_geometry_mask_enabled_meshes", value))

    def set_geometry_mask_enabled_uv_tiles(self, value):
        self.calls.append(("set_geometry_mask_enabled_uv_tiles", value))


class FakeGroup(FakeNode):
    """Group 才有 sub_layers（官方 GroupLayerNode）。"""

    def sub_layers(self):
        return []


class FakeGeometryMaskType(enum.Enum):
    Mesh = "mesh"
    UVTile = "uvtile"
    None_ = "none"


class FakeUsage(enum.Enum):
    BASE_MATERIAL = "base_material"
    TEXTURE = "texture"
    SMART_MATERIAL = "smart_material"


class FakeScoped:
    entered = 0

    def __init__(self, description):
        self.description = description

    def __enter__(self):
        FakeScoped.entered += 1
        return self

    def __exit__(self, *exc):
        return False


def make_module(painter_version=(11, 0, 0), api_version=(0, 3, 4), features=("all",)):
    """构造一个按需裁剪的假 substance_painter。"""
    everything = features == ("all",)
    sp = types.SimpleNamespace()
    sp.__version_info__ = api_version

    application = types.SimpleNamespace(version_info=lambda: painter_version)
    if everything or "suspend_engine" in features:
        application.disable_engine_computations = lambda: FakeScoped("engine")
    sp.application = application

    layerstack = types.SimpleNamespace(
        ScopedModification=FakeScoped,
        GeometryMaskType=FakeGeometryMaskType,
        InsertPosition=types.SimpleNamespace(from_textureset_stack=lambda stack: ("pos", stack)),
        # 探针要能解析这些类上的真实方法名（FillLayerNode/LayerNode 是
        # 官方声明里的类，方法来自 mixin，运行时可用 hasattr 探到）
        FillLayerNode=FakeNode,
        LayerNode=FakeNode,
        insert_fill=lambda position: FakeNode("fill"),
        insert_paint=lambda position: FakeNode("paint"),
        insert_group=lambda position: FakeNode("group"),
        insert_smart_material=lambda position, identifier: FakeNode("smart-material"),
        insert_smart_mask=lambda position, identifier: FakeNode("smart-mask"),
        get_node_by_uid=lambda uid: [FakeNode("by-uid")],
        set_selected_nodes=lambda nodes: None,
        create_smart_material=lambda group, name: {"identifier": name},
        create_smart_mask=lambda layer, name: {"identifier": name},
    )
    sp.layerstack = layerstack

    source = types.SimpleNamespace(
        SourceUniformColor=lambda color: ("uniform", color),
        SourceBitmap=lambda resource_id: ("bitmap", resource_id),
    )
    sp.source = source
    sp.colormanagement = types.SimpleNamespace(Color=lambda r, g, b, a: (r, g, b, a))

    textureset = types.SimpleNamespace(
        get_active_stack=lambda: "stack",
        TextureSet=types.SimpleNamespace(set_resolution=lambda *a: None,
                                         set_mesh_map_resource=lambda *a: None),
        Stack=types.SimpleNamespace(add_channel=lambda *a: None),
    )
    sp.textureset = textureset

    project = types.SimpleNamespace(is_open=lambda: True, is_busy=lambda: False,
                                    open=lambda *a, **kw: None, save=lambda: None,
                                    execute_when_not_busy=lambda cb: cb())
    sp.project = project

    sp.resource = types.SimpleNamespace(
        Usage=FakeUsage,
        import_project_resource=lambda path, usage, **kw: ("imported", path, usage.name),
        search=lambda query: [],
    )
    sp.export = types.SimpleNamespace(
        export_project_textures=lambda config: {"exported": config},
        export_mesh=lambda config: {"mesh": config},
    )
    sp.baking = types.SimpleNamespace(
        bake_selected_textures_async=lambda params, *a: ("bake", params))
    sp.display = types.SimpleNamespace(
        set_environment_resource=lambda r: None,
        set_color_lut_resource=lambda r: None,
        set_tone_mapping=lambda mode: None,
    )
    return sp


@pytest.fixture(autouse=True)
def _reset_scoped():
    FakeScoped.entered = 0


# ---------------------------------------------------------------- version
def test_runtime_info_reads_both_versions():
    api = PainterAPI(make_module())
    info = api.runtime_info()
    assert info.painter_version == (11, 0, 0)
    assert info.painter_version_text == "11.0.0"
    assert info.python_api_version == (0, 3, 4)
    assert info.supported is True
    assert info.as_dict()["python_api_version"] == "0.3.4"


def test_runtime_info_flags_unsupported_painter():
    info = PainterAPI(make_module(painter_version=(6, 2, 0))).runtime_info()
    assert info.supported is False
    assert any("最低要求" in note for note in info.notes)


def test_missing_module_reports_painter_unavailable(monkeypatch):
    import importlib

    def explode(name):
        raise ImportError("no substance_painter here")

    monkeypatch.setattr(importlib, "import_module", explode)
    with pytest.raises(PainterUnavailable):
        PainterAPI().sp  # 延迟导入才会触发


# ---------------------------------------------------------------- capability
def test_require_raises_named_capability_with_probe():
    sp = make_module()
    del sp.layerstack.set_selected_nodes
    api = PainterAPI(sp)
    assert api.supports("layerstack.insert_fill") is True
    assert api.supports("layerstack.selection") is False
    with pytest.raises(UnsupportedCapability) as err:
        api.require("layerstack.selection")
    assert "layerstack.selection" in str(err.value)
    assert "layerstack.set_selected_nodes" in str(err.value)


def test_every_capability_probe_resolves_on_a_full_fake():
    api = PainterAPI(make_module())
    report = api.capability_report()
    missing = [name for name, item in report["capabilities"].items() if not item["supported"]]
    # 假模块覆盖了全部能力，逐个都必须探得到，否则说明探测路径写错
    assert missing == [], missing
    assert report["verified_against"] == "0.3.4"


def test_unknown_capability_name_is_a_programming_error():
    api = PainterAPI(make_module())
    with pytest.raises(AdapterError):
        api.supports("nope.nope")


# ---------------------------------------------------------------- §16 事务
def test_scoped_modification_uses_official_manager():
    api = PainterAPI(make_module())
    with api.scoped_modification("test") as scope:
        assert scope == {"scoped": True, "degraded": False}
    assert FakeScoped.entered == 1


def test_scoped_modification_degrades_loudly_when_absent():
    sp = make_module()
    del sp.layerstack.ScopedModification
    api = PainterAPI(sp)
    with api.scoped_modification("test") as scope:
        assert scope["degraded"] is True
    assert FakeScoped.entered == 0


# ---------------------------------------------------------------- §14 遮罩
def test_set_geometry_mask_maps_to_three_real_calls():
    api = PainterAPI(make_module())
    node = FakeNode()
    result = api.set_geometry_mask(node, {"type": "Mesh", "meshes": ["body"]})
    kinds = [call[0] for call in node.calls]
    assert kinds == ["set_geometry_mask_type", "set_geometry_mask_enabled_meshes"]
    assert node.calls[0][1].name == "Mesh"
    assert [item["api"].rsplit(".", 1)[-1] for item in result["applied"]] == kinds


def test_set_geometry_mask_accepts_aliases_and_rejects_garbage():
    api = PainterAPI(make_module())
    node = FakeNode()
    api.set_geometry_mask(node, {"geometry_mask_type": "UVTile", "enabled_meshes": ["head"]})
    assert [call[0] for call in node.calls] == [
        "set_geometry_mask_type", "set_geometry_mask_enabled_meshes"]
    with pytest.raises(AdapterError):
        api.set_geometry_mask(node, {"bogus": 1})
    with pytest.raises(AdapterError):
        api.set_geometry_mask(node, {})


def test_set_geometry_mask_reports_unknown_enum_value():
    api = PainterAPI(make_module())
    with pytest.raises(AdapterError) as err:
        api.set_geometry_mask(FakeNode(), {"type": "Squiggly"})
    assert "可用" in str(err.value)


# ------------------------------------------------------- smart material/mask
def test_save_smart_material_refuses_non_group_and_reports_path():
    api = PainterAPI(make_module())
    with pytest.raises(AdapterError):
        api.save_smart_material(FakeNode(), "Copper")
    result = api.save_smart_material(FakeGroup(), "Copper", path="C:/tmp/copper.sbsar")
    assert result["api"].endswith("create_smart_material")
    # 官方 0.3.4 没有「写出文件」接口 —— 必须如实说明，不能假装写了文件
    assert "path_unsupported" in result
    assert "path" not in result


def test_save_smart_mask_requires_name():
    api = PainterAPI(make_module())
    with pytest.raises(AdapterError):
        api.save_smart_mask(FakeNode(), "   ")


# ---------------------------------------------------------------- 其它包装
def test_source_wrappers_call_official_constructors():
    api = PainterAPI(make_module())
    node = FakeNode()
    api.set_source(node, "baseColor", api.uniform_color_source([1, 0, 0, 1]))
    assert node.calls[0][0] == "set_source"
    api.set_material_source(node, "resource-id")
    assert node.calls[-1] == ("set_material_source", "resource-id")


def test_import_project_resource_accepts_usage_case_insensitively():
    api = PainterAPI(make_module())
    result = api.import_project_resource("C:/tex/copper.png", "BASE_MATERIAL", name="copper")
    assert result[1] == "C:/tex/copper.png"
    with pytest.raises(AdapterError):
        api.import_project_resource("C:/tex/copper.png", "nonsense")


def test_execute_when_not_busy_falls_back_to_direct_call():
    sp = make_module()
    del sp.project.execute_when_not_busy
    assert PainterAPI(sp).execute_when_not_busy(lambda: "ran") == "ran"


def test_verify_declared_paths_flags_missing_and_foreign():
    api = PainterAPI(make_module())
    rows = api.verify_declared_paths([
        "substance_painter.layerstack.insert_fill",
        "substance_painter.layerstack.EffectNode.set_parameters",
        "layerstack.insert_fill",
    ])
    by_path = {row["path"]: row for row in rows}
    assert by_path["substance_painter.layerstack.insert_fill"]["ok"] is True
    assert by_path["substance_painter.layerstack.EffectNode.set_parameters"]["ok"] is False
    assert by_path["layerstack.insert_fill"]["ok"] is False


def test_capabilities_are_unique_and_have_notes():
    names = [cap.name for cap in CAPABILITIES]
    assert len(names) == len(set(names))
    assert all(cap.note for cap in CAPABILITIES)
    assert all(cap.verified_on for cap in CAPABILITIES)
