"""可复用的「假 Painter」（架构文档 §28-4 / §28-5 的离线测试基座）。

为什么需要它：有几类 bug 静态断言永远看不出来——调了不存在的官方接口、
参数顺序错、执行顺序错。只有把真正的执行管线（动作 → 适配层 → 官方 API）
跑一遍才会暴露。所以这里按**官方 0.3.4 声明文件里的真实成员名**造一个最小
Painter，让 `core.actions.execute_plan` 和 `core.integration_smoke.run` 能
在 CI 里真的跑起来。

放在独立模块里，是因为 `test_actions_execute.py`（执行管线）与
`test_integration_smoke.py`（工程生命周期冒烟）都要用同一份桩；两份桩必然漂移。
"""

from __future__ import annotations

import enum
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

# 本模块自己把 plugin/ 放进 sys.path，这样任何测试文件导入它都不必先做路径准备。
PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

# --------------------------------------------------------------- 节点与枚举
class FakeNode:
    """只实现官方声明里真实存在的成员（方法名逐一对照 0.3.4）。"""

    _next_uid = 1000

    def __init__(self, name="node"):
        FakeNode._next_uid += 1
        self._uid = FakeNode._next_uid
        self._name = name
        self.calls = []
        self.source_mode = None
        self._mask = None

    # 官方 Node API
    def uid(self):
        return self._uid

    def get_name(self):
        return self._name

    def set_name(self, value):
        self.calls.append(("set_name", value))
        self._name = value

    def set_visible(self, value):
        self.calls.append(("set_visible", value))

    def is_visible(self):
        return True

    def get_type(self):
        return "FakeLayer"

    def set_source(self, channel, source):
        self.calls.append(("set_source", channel, source))

    def set_material_source(self, resource_id):
        self.calls.append(("set_material_source", resource_id))
        return None

    # 官方 LayerNode API（几何遮罩就是这三个）
    def set_geometry_mask_type(self, value):
        self.calls.append(("set_geometry_mask_type", value))

    def set_geometry_mask_enabled_meshes(self, value):
        self.calls.append(("set_geometry_mask_enabled_meshes", list(value)))

    def set_geometry_mask_enabled_uv_tiles(self, value):
        self.calls.append(("set_geometry_mask_enabled_uv_tiles", list(value)))

    # 官方遮罩 API
    def has_mask(self):
        return self._mask is not None

    def add_mask(self, background):
        self._mask = background
        self.calls.append(("add_mask", background))

    def set_mask_background(self, background):
        self._mask = background
        self.calls.append(("set_mask_background", background))


class FakeGroup(FakeNode):
    """只有 Group 有 sub_layers（官方 GroupLayerNode）。"""

    def sub_layers(self):
        return []


class FakeEffect(FakeNode):
    """官方 LevelsEffectNode / CompareMaskEffectNode 这类效果节点的最小形状。"""

    def __init__(self, name="effect"):
        super().__init__(name)
        self._parameters = types.SimpleNamespace(contrast=0.0, brightness=0.0)

    def get_type(self):
        return "FakeEffect"

    def get_parameters(self):
        return self._parameters

    def set_parameters(self, parameters):
        self._parameters = parameters
        self.calls.append(("set_parameters", parameters))


class FakeScopedModification:
    def __init__(self, description):
        self.description = description

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GeometryMaskType(enum.Enum):
    """官方 layerstack.GeometryMaskType 是 Enum（本机实测 0.3.4）。"""

    Mesh = "mesh"
    UVTile = "uvtile"


class MaskBackground(enum.Enum):
    Black = "black"
    White = "white"


class NodeStack(enum.Enum):
    Content = "content"
    Mask = "mask"


class FakeResolution:
    def __init__(self, width=2048, height=2048):
        self.width = width
        self.height = height


class FakeMaterial:
    def __init__(self, name="TextureSet"):
        self.name = name
        self._resolution = FakeResolution()

    def get_resolution(self):
        return self._resolution

    def set_resolution(self, resolution):
        self._resolution = FakeResolution(*resolution)


class FakeStack:
    """官方 textureset.Stack 的最小形状。"""

    def __init__(self, name="TextureSet_Stack"):
        self._name = name
        self._material = FakeMaterial(name)
        self._channels = {}

    @classmethod
    def from_name(cls, name):
        return cls(name)

    def name(self):
        return self._name

    def material(self):
        return self._material

    def all_channels(self):
        return dict(self._channels)

    def add_channel(self, channel, fmt, label=None):
        self._channels[channel] = fmt

    def remove_channel(self, channel):
        self._channels.pop(channel, None)

    def edit_channel(self, channel, fmt, label=None):
        self._channels[channel] = fmt


class FakeProject:
    """工程生命周期。``create`` 会校验网格真的存在（真实 API 也这么做）。"""

    def __init__(self, open=False, needs_saving=False):
        self._open = bool(open)
        self._needs_saving = bool(needs_saving)
        self.created_meshes = []
        self.close_count = 0

    def is_open(self):
        return self._open

    def is_busy(self):
        return False

    def needs_saving(self):
        return self._needs_saving

    def name(self):
        return "smoke-project"

    def file_path(self):
        return ""

    def create(self, mesh_file_path=None, mesh_map_file_paths=None,
               template_file_path=None, settings=None):
        if self._open:
            raise RuntimeError("ProjectError: 已经有打开的工程")
        if not mesh_file_path or not os.path.isfile(mesh_file_path):
            raise ValueError("ValueError: 网格文件不存在: %r" % (mesh_file_path,))
        self.created_meshes.append(mesh_file_path)
        self._open = True

    def close(self):
        self._open = False
        self.close_count += 1


# --------------------------------------------------------------- 模块组装
def fake_color(r, g, b, color_space=None):
    """模拟官方 `colormanagement.Color(r, g, b, color_space=None)`。

    官方**没有 alpha**：多传的第 4 个位置参数会落到 `color_space` 上，随后在
    `_to_private_color_space()` 处以
    ``AttributeError: 'float' object has no attribute 'value'`` 失败。

    真机冒烟正是抓到这里：桩原先写成 `lambda r, g, b, a:` 收 4 个参数，
    把真机必然失败的调用放成了假绿灯。桩必须贴近真实签名，否则它只会骗自己。
    """
    if color_space is not None and not hasattr(color_space, "value"):
        raise AttributeError("'float' object has no attribute 'value'")
    return (r, g, b)


def build_stub(with_scope=True, with_bake=True, project_open=False,
               needs_saving=False, painter_root=None):
    """装配一个 substance_painter 假模块。

    Args:
        with_scope: 是否提供 `layerstack.ScopedModification`（缺省时验证降级路径）。
        with_bake: 是否提供 `baking.bake_selected_textures_async`。
        project_open / needs_saving: 初始工程状态。
        painter_root: 伪装 Painter 安装根目录（用于 `sp.__file__`，冒烟据此找自带网格）。
    """
    sp = types.ModuleType("substance_painter")
    sp.__version_info__ = (0, 3, 4)
    root = painter_root or "/nonexistent/painter"
    sp.__file__ = os.path.join(root, "resources", "python", "modules",
                               "substance_painter", "__init__.py")

    sp.application = types.SimpleNamespace(
        version_info=lambda: (11, 0, 0),
        disable_engine_computations=lambda: FakeScopedModification("engine"))

    created = []

    def _insert(kind):
        def _make(position):
            node = FakeGroup(kind) if kind == "group" else FakeNode(kind)
            created.append(node)
            return node
        return _make

    layerstack = types.SimpleNamespace(
        GeometryMaskType=GeometryMaskType,
        MaskBackground=MaskBackground,
        NodeStack=NodeStack,
        # 探针路径 substance_painter.layerstack.LayerNode.set_geometry_mask_* 要在
        # 类上解析得到（真实 API 里方法来自 Node/LayerNode 类）
        LayerNode=FakeNode,
        FillLayerNode=FakeNode,
        FillEffectNode=FakeEffect,
        GroupLayerNode=FakeGroup,
        InsertPosition=types.SimpleNamespace(
            from_textureset_stack=lambda stack: ("stack", stack),
            inside_node=lambda node, stack: ("inside", node, stack)),
        insert_fill=_insert("fill"),
        insert_paint=_insert("paint"),
        insert_group=_insert("group"),
        insert_levels_effect=lambda position: _push(created, FakeEffect("levels")),
        insert_anchor_point_effect=lambda position, name: _push(
            created, FakeEffect(name)),
        insert_color_selection_effect=lambda position: _push(
            created, FakeEffect("color-selection")),
        insert_compare_mask_effect=lambda position: _push(
            created, FakeEffect("compare-mask")),
        get_selected_nodes=lambda stack: [],
        get_root_layer_nodes=lambda stack: list(created),
        set_selected_nodes=lambda nodes: None,
        get_node_by_uid=lambda uid: [node for node in created if node.uid() == uid],
        create_smart_material=lambda node, name: types.SimpleNamespace(
            identifier="project://smart-material/" + name,
            gui_name=lambda: name),
        create_smart_mask=lambda node, name: types.SimpleNamespace(
            identifier="project://smart-mask/" + name,
            gui_name=lambda: name),
    )
    if with_scope:
        layerstack.ScopedModification = FakeScopedModification
    sp.layerstack = layerstack

    sp.source = types.SimpleNamespace(
        SourceUniformColor=lambda color: ("uniform", color),
        SourceBitmap=lambda resource_id: ("bitmap", resource_id))

    sp.colormanagement = types.SimpleNamespace(Color=fake_color)

    stack = FakeStack()
    sp.textureset = types.SimpleNamespace(
        get_active_stack=lambda: stack,
        set_active_stack=lambda value: None,
        Stack=FakeStack,
        TextureSet=FakeMaterial,
        ChannelType=enum.Enum("ChannelType", "BaseColor Roughness Metallic Height Normal"),
        ChannelFormat=enum.Enum("ChannelFormat", "L8 RGB8 RGBA8"),
        Material=FakeMaterial)

    sp.project = FakeProject(open=project_open, needs_saving=needs_saving)

    class Usage(enum.Enum):
        BASE_MATERIAL = "base"
        TEXTURE = "tex"

    sp.resource = types.SimpleNamespace(
        Usage=Usage,
        search=lambda query: [types.SimpleNamespace(
            identifier=lambda: "project://" + str(query),
            gui_name=lambda: str(query))],
        import_project_resource=lambda path, usage, **kw: types.SimpleNamespace(
            gui_name=lambda: "imported", identifier="project://" + path))

    sp.export = types.SimpleNamespace(
        export_mesh=lambda *a: None,
        export_project_textures=lambda *a: None,
        list_predefined_export_presets=lambda: [])

    sp.baking = types.SimpleNamespace()
    if with_bake:
        sp.baking.bake_selected_textures_async = lambda *a: ("baked",)

    sp.display = types.SimpleNamespace()
    return sp, created


def _push(node_list, node):
    node_list.append(node)
    return node


# --------------------------------------------------------------- 安装与夹具
def install(monkeypatch, **kwargs):
    """把假 Painter 装进 sys.modules，重建 core 模块，返回环境对象。"""
    monkeypatch.delenv("SPAI_SMOKE_MESH", raising=False)
    stub, created = build_stub(**kwargs)
    monkeypatch.setitem(sys.modules, "substance_painter", stub)

    import core.painter_api as painter_api

    painter_api.set_default_api(painter_api.PainterAPI(stub))
    actions = importlib.reload(importlib.import_module("core.actions"))
    import core.painter_context as painter_context

    importlib.reload(painter_context)
    integration_smoke = importlib.reload(
        importlib.import_module("core.integration_smoke"))

    return types.SimpleNamespace(
        sp=stub, created=created, api=painter_api, actions=actions,
        context=painter_context, smoke=integration_smoke,
        project=stub.project)


@pytest.fixture
def painter(monkeypatch):
    """默认（已打开工程）假 Painter —— 既有执行管线测试用的就是它。"""
    import core.painter_api as painter_api

    try:
        yield install(monkeypatch, project_open=True)
    finally:
        painter_api.set_default_api(None)


@pytest.fixture
def painter_no_project(monkeypatch):
    """没有打开任何工程的假 Painter —— 集成冒烟的默认起点。"""
    import core.painter_api as painter_api

    try:
        yield install(monkeypatch, project_open=False)
    finally:
        painter_api.set_default_api(None)


@pytest.fixture
def painter_no_scope(monkeypatch):
    """缺 ScopedModification 的假 Painter（验证 §16 事务降级路径）。"""
    import core.painter_api as painter_api

    try:
        yield install(monkeypatch, project_open=True, with_scope=False)
    finally:
        painter_api.set_default_api(None)
