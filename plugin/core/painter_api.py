"""Painter 官方 API 适配层（架构文档 §11 / §14 / §16 / §28-2）。

为什么要这一层：`actions.py` 里散着 60 多处直接 `sp.xxx.yyy()` 调用，一旦
Painter 官方 API 换名（本机实测 0.3.4 / Painter 11.0.0.4202 就已经和部分
声明对不上），错误只能在运行时以 AttributeError 冒出来，模型与用户都读不懂。
适配层做四件事：

  1. **读版本**：`runtime_info()` 同时给出 Painter 版本与官方 Python API 版本
     （§28-2 要求「读取当前 Painter 官方 API 版本」）；
  2. **探能力**：`CAPABILITIES` 是显式清单，每项声明探测路径与「我们验证过的
     版本」，`supports()/require()` 让工具在动手之前就报清楚「这个版本没这个
     能力」，而不是执行到一半炸掉；
  3. **收差异**：命名差异、缺失接口、多态分发都在这一层吸收
     （例如几何遮罩官方是 `set_geometry_mask_type` + `..._enabled_meshes` +
     `..._enabled_uv_tiles`，并没有 `set_geometry_mask`）；
  4. **可校验**：`verify_declared_paths()` 用运行时 hasattr 逐条核对 Tool
     Registry 里声明的官方 API 路径（§12「工具必须映射到明确的官方 API」）。

本模块**不导入 Qt**，也**不在导入期**碰 `substance_painter`，所以 CI 里可以
用假模块直接做功能测试（见 tests/test_painter_api.py）。
"""
from __future__ import annotations

import contextlib
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

# manifest.json 的 min_painter_version；低于它直接判定为不受支持。
PAINTER_MIN_VERSION: Tuple[int, int, int] = (7, 2, 0)
# 本文件里零散接口的探测结果确认于该官方 Python API 版本（Painter 11.0.0）。
VERIFIED_AGAINST = "0.3.4"
# 2026-09-30 真机集成冒烟（Painter 11.0.0.4202）实际**执行**过对应官方入口的
# 能力 —— 不只是 hasattr 探测。证据 = integration_smoke/latest.json 里的
# 执行回执（api / applied）与确定性调用链。每个版本跑完冒烟后把新执行过的
# 能力补进来，让「哪些能力在哪个版本真的能用」越跑越准（roadmap 下一步 6）。
EXECUTED_AGAINST = "0.3.4"


class AdapterError(RuntimeError):
    """适配层错误基类：消息面向用户，不暴露内部调用栈。"""


class PainterUnavailable(AdapterError):
    """Painter 的 Python API 无法导入（不在 Painter 里运行）。"""


class UnsupportedCapability(AdapterError):
    """当前 Painter 版本不提供该能力。"""

    def __init__(self, capability: str, *, required: Optional[Tuple[int, int, int]] = None,
                 current: Tuple[int, int, int] = (0, 0, 0), detail: str = ""):
        self.capability = capability
        self.required = required
        self.current = current
        message = "当前 Painter 不提供 %s 能力" % capability
        if required:
            message += "（需要 Painter >= %s，当前 %s）" % (
                ".".join(map(str, required)), ".".join(map(str, current)))
        if detail:
            message += "：" + detail
        super().__init__(message)


@dataclass(frozen=True)
class Capability:
    """一条官方 API 能力声明。

    probe: 运行时探测路径（`substance_painter.` 之后的部分），用 hasattr 走。
    verified_on: 真机上探测确认存在的官方 API 版本；None 表示尚未实测。
    executed_on: 真机上**真实执行过**（完整调用成功）的官方 API 版本；
        None 表示只探测过、还没在真实计划里跑过。两者必须分开记：
        「符号存在」和「签名契约真的对」是两件事（Color 契约缺陷就是探测
        全绿、一执行才炸的反例）。
    min_painter: 已知的最低 Painter 版本；None 表示未确认，不做版本硬判定。
    """

    name: str
    probe: str
    note: str
    min_painter: Optional[Tuple[int, int, int]] = None
    verified_on: Optional[str] = None
    executed_on: Optional[str] = None


CAPABILITIES: Tuple[Capability, ...] = (
    Capability("application.version", "application.version_info",
               "读取 Painter 版本", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("application.suspend_engine", "application.disable_engine_computations",
               "临时关闭引擎计算，批量提交时减少重复计算", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.scoped_modification", "layerstack.ScopedModification",
               "§16 批量修改合并为一次提交", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.insert_fill", "layerstack.insert_fill",
               "创建 Fill Layer", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.insert_paint", "layerstack.insert_paint",
               "创建 Paint Layer", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.insert_group", "layerstack.insert_group",
               "创建 Group", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.insert_position", "layerstack.InsertPosition.from_textureset_stack",
               "按 Texture Set 栈定位插入点", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.uid_lookup", "layerstack.get_node_by_uid",
               "§15 用 UID 定位图层（不依赖图层名）", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.selection", "layerstack.set_selected_nodes",
               "设置选中节点", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("source.set_source", "layerstack.FillLayerNode.set_source",
               "写入通道来源（颜色/资源/锚点）", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("source.material_source", "layerstack.FillLayerNode.set_material_source",
               "§14 多通道 Material 模式", verified_on=VERIFIED_AGAINST),
    Capability("source.uniform_color", "source.SourceUniformColor",
               "通道写入纯色", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("source.bitmap", "source.SourceBitmap",
               "通道写入位图资源", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.geometry_mask_type", "layerstack.LayerNode.set_geometry_mask_type",
               "几何遮罩类型", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.geometry_mask_meshes", "layerstack.LayerNode.set_geometry_mask_enabled_meshes",
               "几何遮罩按网格启用", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.geometry_mask_uv_tiles", "layerstack.LayerNode.set_geometry_mask_enabled_uv_tiles",
               "几何遮罩按 UV Tile 启用（需 UV Tile 工作流）", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.smart_material_create", "layerstack.create_smart_material",
               "把 Group 存为工程内 Smart Material 资源", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("layerstack.smart_mask_create", "layerstack.create_smart_mask",
               "把图层存为工程内 Smart Mask 资源", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.smart_material_insert", "layerstack.insert_smart_material",
               "插入 Smart Material", verified_on=VERIFIED_AGAINST),
    Capability("layerstack.smart_mask_insert", "layerstack.insert_smart_mask",
               "插入 Smart Mask", verified_on=VERIFIED_AGAINST),
    Capability("textureset.active_stack", "textureset.get_active_stack",
               "读取当前 Texture Set 栈", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
    Capability("textureset.resolution", "textureset.TextureSet.set_resolution",
               "设置 Texture Set 分辨率", verified_on=VERIFIED_AGAINST),
    Capability("textureset.channel_add", "textureset.Stack.add_channel",
               "新增通道", verified_on=VERIFIED_AGAINST),
    Capability("textureset.mesh_map", "textureset.TextureSet.set_mesh_map_resource",
               "把烘焙贴图挂回 Texture Set", verified_on=VERIFIED_AGAINST),
    Capability("resource.import_project", "resource.import_project_resource",
               "§11.2 导入资源到工程", verified_on=VERIFIED_AGAINST),
    Capability("resource.search", "resource.search",
               "搜索本地资源（材质/遮罩/滤镜）", verified_on=VERIFIED_AGAINST),
    Capability("resource.usage", "resource.Usage",
               "资源用途枚举", verified_on=VERIFIED_AGAINST),
    Capability("project.open", "project.open", "打开工程", verified_on=VERIFIED_AGAINST),
    Capability("project.is_busy", "project.is_busy", "查询工程是否忙", verified_on=VERIFIED_AGAINST),
    Capability("project.execute_when_not_busy", "project.execute_when_not_busy",
               "§11.1 在非忙状态下执行", verified_on=VERIFIED_AGAINST),
    Capability("project.save", "project.save", "保存工程", verified_on=VERIFIED_AGAINST),
    Capability("export.textures", "export.export_project_textures",
               "§11.6 导出贴图", verified_on=VERIFIED_AGAINST),
    Capability("export.mesh", "export.export_mesh", "导出网格", verified_on=VERIFIED_AGAINST),
    Capability("baking.bake_selected_textures", "baking.bake_selected_textures_async",
               "烘焙选中纹理（异步）", verified_on=VERIFIED_AGAINST),
    Capability("display.environment", "display.set_environment_resource",
               "设置环境贴图", verified_on=VERIFIED_AGAINST),
    Capability("display.color_lut", "display.set_color_lut_resource",
               "设置颜色 LUT", verified_on=VERIFIED_AGAINST),
    Capability("display.tone_mapping", "display.set_tone_mapping",
               "设置色调映射", verified_on=VERIFIED_AGAINST),
    Capability("colormanagement.color", "colormanagement.Color",
               "构造颜色对象", verified_on=VERIFIED_AGAINST,
               executed_on=EXECUTED_AGAINST),
)

CAPABILITY_BY_NAME = {cap.name: cap for cap in CAPABILITIES}


@dataclass(frozen=True)
class RuntimeInfo:
    painter_version: Tuple[int, int, int]
    painter_version_text: str
    python_api_version: Tuple[int, int, int]
    python_api_version_text: str
    supported: bool
    notes: Tuple[str, ...] = field(default=())

    def as_dict(self) -> dict:
        return {
            "painter_version": self.painter_version_text,
            "python_api_version": self.python_api_version_text,
            "supported": self.supported,
            "notes": list(self.notes),
        }


def _as_version(value: Any) -> Tuple[int, int, int]:
    try:
        parts = [int(part) for part in tuple(value)[:3]]
    except Exception:
        return (0, 0, 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


class PainterAPI:
    """官方 API 的唯一出入口。所有包装方法都注明对应的官方符号。"""

    def __init__(self, module: Any = None):
        self._module = module
        self._runtime: Optional[RuntimeInfo] = None

    # ---------- module / version ----------
    @property
    def sp(self):
        """底层 `substance_painter` 模块（延迟导入）。"""
        if self._module is None:
            try:
                self._module = importlib.import_module("substance_painter")
            except Exception as exc:  # pragma: no cover - 只在非 Painter 环境触发
                raise PainterUnavailable(
                    "没有找到 substance_painter 模块：本插件必须在 Substance 3D "
                    "Painter 内部运行（%s）" % exc) from exc
        return self._module

    def _getattr_path(self, dotted: str):
        target = self.sp
        for part in dotted.split("."):
            try:
                target = getattr(target, part)
            except AttributeError as exc:
                raise UnsupportedCapability(
                    dotted, current=self.runtime_info().painter_version,
                    detail="官方 API 中不存在 %s" % dotted) from exc
        return target

    def runtime_info(self) -> RuntimeInfo:
        if self._runtime is not None:
            return self._runtime
        notes = []
        painter = (0, 0, 0)
        try:
            painter = _as_version(self.sp.application.version_info())
        except Exception as exc:
            notes.append("读取 Painter 版本失败：%s" % exc)
        try:
            api = _as_version(getattr(self.sp, "__version_info__", (0, 0, 0)))
        except Exception:
            api = (0, 0, 0)
        if not api[0]:
            notes.append("官方未暴露 Python API 版本（substance_painter.__version_info__）")
        supported = painter >= PAINTER_MIN_VERSION
        if painter != (0, 0, 0) and not supported:
            notes.append("低于插件最低要求 Painter %s" % ".".join(map(str, PAINTER_MIN_VERSION)))
        self._runtime = RuntimeInfo(
            painter_version=painter,
            painter_version_text=".".join(map(str, painter)),
            python_api_version=api,
            python_api_version_text=".".join(map(str, api)),
            supported=supported,
            notes=tuple(notes),
        )
        return self._runtime

    # ---------- capabilities ----------
    def supports(self, name: str) -> bool:
        cap = CAPABILITY_BY_NAME.get(name)
        if cap is None:
            raise AdapterError("未知能力名：%s" % name)
        try:
            self._getattr_path(cap.probe)
            return True
        except (UnsupportedCapability, PainterUnavailable):
            return False

    def require(self, name: str) -> Capability:
        cap = CAPABILITY_BY_NAME.get(name)
        if cap is None:
            raise AdapterError("未知能力名：%s" % name)
        try:
            self._getattr_path(cap.probe)
        except PainterUnavailable:
            raise
        except UnsupportedCapability as exc:
            raise UnsupportedCapability(
                name, required=cap.min_painter,
                current=self.runtime_info().painter_version,
                detail="缺少官方符号 %s" % cap.probe) from exc
        return cap

    def capability_report(self) -> dict:
        report = {"runtime": self.runtime_info().as_dict(), "verified_against": VERIFIED_AGAINST,
                  "capabilities": {}}
        for cap in CAPABILITIES:
            report["capabilities"][cap.name] = {
                "supported": self.supports(cap.name),
                "probe": cap.probe,
                "verified_on": cap.verified_on,
                "executed_on": cap.executed_on,
                "note": cap.note,
            }
        return report

    def verify_declared_paths(self, paths: Iterable[str]) -> list:
        """§12：逐条核对工具声明的官方 API 路径是否真实存在。"""
        results = []
        for path in paths:
            if not path.startswith("substance_painter."):
                results.append({"path": path, "ok": False,
                                "detail": "不是 substance_painter 官方路径"})
                continue
            try:
                self._getattr_path(path[len("substance_painter."):])
                results.append({"path": path, "ok": True, "detail": ""})
            except (UnsupportedCapability, PainterUnavailable) as exc:
                results.append({"path": path, "ok": False, "detail": str(exc)})
        return results

    # ---------- §16 事务 ----------
    @contextlib.contextmanager
    def scoped_modification(self, description: str = "SP AI Assistant"):
        """官方 `layerstack.ScopedModification`；缺失时退化为空上下文。

        退化时结果里会带 `"degraded": True`，让上层知道「没有合并提交」，
        而不是假装批次语义生效了。
        """
        try:
            manager = self._getattr_path("layerstack.ScopedModification")(description)
        except (UnsupportedCapability, PainterUnavailable):
            yield {"scoped": False, "degraded": True}
            return
        with manager:
            yield {"scoped": True, "degraded": False}

    @contextlib.contextmanager
    def suspend_engine(self):
        """官方 `application.disable_engine_computations`（缺失时空上下文）。"""
        try:
            manager = self._getattr_path("application.disable_engine_computations")()
        except (UnsupportedCapability, PainterUnavailable):
            yield {"suspended": False, "degraded": True}
            return
        with manager:
            yield {"suspended": True, "degraded": False}

    # ---------- §11.3 图层与来源 ----------
    def active_stack(self):
        """官方 `textureset.get_active_stack()`。"""
        self.require("textureset.active_stack")
        if not self.sp.project.is_open():
            raise AdapterError("没有打开 Painter 项目。")
        return self.sp.textureset.get_active_stack()

    def insert_position(self, stack=None):
        """官方 `layerstack.InsertPosition.from_textureset_stack(stack)`。"""
        self.require("layerstack.insert_position")
        return self.sp.layerstack.InsertPosition.from_textureset_stack(
            stack if stack is not None else self.active_stack())

    def insert_fill(self, name: str = "", position=None, stack=None):
        """官方 `layerstack.insert_fill(position)`（命名另走 `set_name`）。"""
        self.require("layerstack.insert_fill")
        node = self.sp.layerstack.insert_fill(
            position if position is not None else self.insert_position(stack))
        if name:
            node.set_name(name)
        return node

    def insert_paint(self, name: str = "", position=None, stack=None):
        """官方 `layerstack.insert_paint(position)`。"""
        self.require("layerstack.insert_paint")
        node = self.sp.layerstack.insert_paint(
            position if position is not None else self.insert_position(stack))
        if name:
            node.set_name(name)
        return node

    def insert_group(self, name: str = "", position=None, stack=None):
        """官方 `layerstack.insert_group(position)`。"""
        self.require("layerstack.insert_group")
        node = self.sp.layerstack.insert_group(
            position if position is not None else self.insert_position(stack))
        if name:
            node.set_name(name)
        return node

    def color(self, rgb: Sequence[float]):
        """官方 `colormanagement.Color(r, g, b, color_space=None)` —— **没有 alpha**。

        真机冒烟实测的教训：把第 4 个分量当 alpha 传进去，它会落到 `color_space`
        上，官方内部 `_to_private_color_space()` 随即以
        ``AttributeError: 'float' object has no attribute 'value'`` 失败。
        所以这里只取前三个分量。
        """
        values = [float(v) for v in rgb][:3]
        values += [0.0] * (3 - len(values))
        try:
            return self._getattr_path("colormanagement.Color")(*values)
        except UnsupportedCapability:
            return tuple(values)

    def uniform_color_source(self, rgb: Sequence[float]):
        """官方 `source.SourceUniformColor(colormanagement.Color)`。"""
        self.require("source.uniform_color")
        return self.sp.source.SourceUniformColor(
            rgb if hasattr(rgb, "value_raw") else self.color(rgb))

    def bitmap_source(self, resource_id):
        """官方 `source.SourceBitmap(resource_id)`。"""
        self.require("source.bitmap")
        return self.sp.source.SourceBitmap(resource_id)

    def set_source(self, node, channel, source):
        """官方 `source.SourceEditorMixin.set_source(channel, source)`。

        单通道 Fill 的 `source_mode` 为空，官方此时只接受 `set_source(source)`；
        这里按调用签名自适应，避免把「通道参数」猜错成一次 AttributeError。
        """
        self.require("source.set_source")
        try:
            return node.set_source(channel, source)
        except TypeError:
            return node.set_source(source)

    def set_material_source(self, node, resource_id):
        """官方 `source.SourceEditorMixin.set_material_source(resource_id)`（§14）。"""
        self.require("source.material_source")
        return node.set_material_source(resource_id)

    def source_mode_name(self, node) -> str:
        mode = getattr(node, "source_mode", None)
        return getattr(mode, "name", str(mode)) if mode is not None else ""

    # ---------- §14 / §25 几何遮罩 ----------
    GEOMETRY_MASK_KEYS = {
        "type": ("type", "geometry_mask_type", "mode", "kind"),
        "meshes": ("meshes", "enabled_meshes", "mesh_names"),
        "uv_tiles": ("uv_tiles", "enabled_uv_tiles", "uvtiles"),
    }

    def set_geometry_mask(self, node, parameters: Mapping[str, Any]) -> dict:
        """几何遮罩：官方没有 `set_geometry_mask`，必须拆成三个真实调用。

        * `LayerNode.set_geometry_mask_type(GeometryMaskType)`
        * `LayerNode.set_geometry_mask_enabled_meshes(list[str])`
        * `LayerNode.set_geometry_mask_enabled_uv_tiles(list[UVTile])`

        返回实际调用了哪些接口，供 §18.1 校验与用户回执使用。
        """
        if not isinstance(parameters, Mapping):
            raise AdapterError("几何遮罩参数必须是对象。")
        normalized = {}
        for canonical, aliases in self.GEOMETRY_MASK_KEYS.items():
            for alias in aliases:
                if alias in parameters:
                    normalized[canonical] = parameters[alias]
                    break
        if not normalized:
            raise AdapterError(
                "几何遮罩参数无法识别，支持键：type / meshes / uv_tiles（及常见别名）。")

        applied = []
        mask_type = normalized.get("type")
        if mask_type not in (None, ""):
            self.require("layerstack.geometry_mask_type")
            enum_type = self._geometry_mask_type(mask_type)
            node.set_geometry_mask_type(enum_type)
            applied.append({"api": "substance_painter.layerstack.LayerNode.set_geometry_mask_type",
                            "value": getattr(enum_type, "name", str(enum_type))})
        meshes = normalized.get("meshes")
        if meshes:
            self.require("layerstack.geometry_mask_meshes")
            node.set_geometry_mask_enabled_meshes([str(item) for item in meshes])
            applied.append({"api": "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_meshes",
                            "value": [str(item) for item in meshes]})
        uv_tiles = normalized.get("uv_tiles")
        if uv_tiles:
            self.require("layerstack.geometry_mask_uv_tiles")
            node.set_geometry_mask_enabled_uv_tiles(list(uv_tiles))
            applied.append({"api": "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_uv_tiles",
                            "value": [str(item) for item in uv_tiles]})
        if not applied:
            raise AdapterError("几何遮罩参数为空，未执行任何官方调用。")
        return {"parameters": {k: v for k, v in normalized.items()}, "applied": applied}

    def _geometry_mask_type(self, value):
        """把用户/模型给的遮罩类型归一成官方枚举成员。

        官方 `GeometryMaskType` 是 Enum（Mesh / UVTile / None…），但不同
        Painter 版本暴露方式略有差异，因此既支持传枚举成员、也支持传字符串
        （大小写不敏感），并且不假设它一定可迭代。
        """
        enum = self._getattr_path("layerstack.GeometryMaskType")
        if isinstance(enum, type) and isinstance(value, enum):
            return value
        text = str(value).strip()
        direct = getattr(enum, text, None)
        if direct is not None and not isinstance(direct, type):
            return direct
        members = []
        try:
            members = list(enum)  # Enum 类可迭代
        except TypeError:
            members = []
        candidates = {}
        for member in members:
            candidates[getattr(member, "name", str(member)).casefold()] = member
        for name in dir(enum):
            attribute = getattr(enum, name, None)
            if attribute is None or isinstance(attribute, type) or callable(attribute):
                continue
            candidates.setdefault(name.casefold(), attribute)
        if text.casefold() in candidates:
            return candidates[text.casefold()]
        raise AdapterError("未知几何遮罩类型 %r，可用：%s"
                           % (value, ", ".join(sorted(candidates))))

    # ---------- Smart Material / Smart Mask ----------
    def save_smart_material(self, node, name: str, path: str = "") -> dict:
        """把 Group 存为工程内 Smart Material 资源（官方 `create_smart_material`）。

        官方 API 0.3.4 **没有**把 Smart Material 导出为文件的接口，
        因此 `path` 只能作为回执里的 `path_unsupported` 如实报告，不能假装写了文件。
        """
        self.require("layerstack.smart_material_create")
        if not str(name or "").strip():
            raise AdapterError("Smart Material 名称不能为空。")
        if not hasattr(node, "sub_layers"):
            raise AdapterError("只有 Group 图层可以存为 Smart Material。")
        resource = self.sp.layerstack.create_smart_material(node, str(name))
        result = {
            "api": "substance_painter.layerstack.create_smart_material",
            "name": str(name),
            "resource": _resource_identity(resource),
        }
        if path:
            result["path_unsupported"] = (
                "官方 Python API %s 未提供把 Smart Material 写入文件的接口，已改为"
                "存为工程内资源。" % VERIFIED_AGAINST)
        return result

    def save_smart_mask(self, node, name: str, path: str = "") -> dict:
        """把图层存为工程内 Smart Mask 资源（官方 `create_smart_mask`）。"""
        self.require("layerstack.smart_mask_create")
        if not str(name or "").strip():
            raise AdapterError("Smart Mask 名称不能为空。")
        resource = self.sp.layerstack.create_smart_mask(node, str(name))
        result = {
            "api": "substance_painter.layerstack.create_smart_mask",
            "name": str(name),
            "resource": _resource_identity(resource),
        }
        if path:
            result["path_unsupported"] = (
                "官方 Python API %s 未提供把 Smart Mask 写入文件的接口，已改为"
                "存为工程内资源。" % VERIFIED_AGAINST)
        return result

    # ---------- §11.1 / §11.2 / §11.6 -----------------
    def execute_when_not_busy(self, callback: Callable[[], Any]) -> Any:
        """官方 `project.execute_when_not_busy(callback)`；不可用时直接执行。"""
        try:
            return self._getattr_path("project.execute_when_not_busy")(callback)
        except UnsupportedCapability:
            return callback()

    def import_project_resource(self, path: str, usage: str, name: str = "", group: str = ""):
        """官方 `resource.import_project_resource(file_path, resource_usage, ...)`。"""
        self.require("resource.import_project")
        usage_enum = self.resource_usage(usage)
        kwargs = {}
        if name:
            kwargs["name"] = str(name)
        if group:
            kwargs["group"] = str(group)
        return self.sp.resource.import_project_resource(str(path), usage_enum, **kwargs)

    # 官方 `resource.Usage` 的成员名是大写下划线（BASE_MATERIAL / SMART_MASK…），
    # 但模型和用户更常写 "material" / "smart material"；这里做一层别名归一，
    # 否则 getattr 会直接 AttributeError（§25「参数校验缺失」类问题）。
    USAGE_ALIASES = {
        "material": "BASE_MATERIAL",
        "base material": "BASE_MATERIAL",
        "texture": "TEXTURE",
        "bitmap": "TEXTURE",
        "alpha": "ALPHA",
        "brush": "BRUSH",
        "generator": "GENERATOR",
        "filter": "FILTER",
        "smart material": "SMART_MATERIAL",
        "smartmaterial": "SMART_MATERIAL",
        "smart mask": "SMART_MASK",
        "smartmask": "SMART_MASK",
        "color lut": "COLOR_LUT",
        "lut": "COLOR_LUT",
        "environment": "ENVIRONMENT",
        "font": "FONT",
        "procedural": "PROCEDURAL",
        "shader": "SHADER",
        "tool": "TOOL",
        "export": "EXPORT",
        "emitter": "EMITTER",
        "particle": "PARTICLE",
        "receiver": "RECEIVER",
    }

    def resource_usage(self, usage: str):
        """官方 `resource.Usage.<NAME>`，支持常见别名与大小写不敏感。"""
        self.require("resource.usage")
        enum = self.sp.resource.Usage
        candidates = {member.name.casefold(): member for member in enum}
        text = str(usage or "").strip()
        folded = text.casefold()
        alias = self.USAGE_ALIASES.get(folded)
        if alias and alias.casefold() in candidates:
            return candidates[alias.casefold()]
        if folded in candidates:
            return candidates[folded]
        raise AdapterError("未知资源用途 %r，可用：%s"
                           % (usage, ", ".join(sorted(candidates))))

    def export_textures(self, configuration: dict):
        """官方 `export.export_project_textures(json_config)`（§11.6）。"""
        self.require("export.textures")
        return self.sp.export.export_project_textures(configuration)

    def export_mesh(self, configuration: dict):
        """官方 `export.export_mesh(configuration)`。"""
        self.require("export.mesh")
        return self.sp.export.export_mesh(configuration)

    def bake_selected_textures(self, parameters, stop_source=None):
        """官方 `baking.bake_selected_textures_async(...)`。"""
        self.require("baking.bake_selected_textures")
        if stop_source is None:
            return self.sp.baking.bake_selected_textures_async(parameters)
        return self.sp.baking.bake_selected_textures_async(parameters, stop_source)

    def mesh_map_report(self) -> dict:
        """烘焙相关能力快照（供 prompt_context / 诊断使用）。"""
        return {
            "bake_api_available": self.supports("baking.bake_selected_textures"),
            "bake_is_async": True,
            "mesh_map_resource_api": self.supports("textureset.mesh_map"),
        }


def _resource_identity(resource) -> dict:
    """把官方 Resource / ResourceID 收敛成可序列化身份（§15 稳定标识）。"""
    identity = {"type": type(resource).__name__}
    for attribute in ("identifier", "gui_name", "url"):
        try:
            value = getattr(resource, attribute)
        except Exception:
            continue
        if callable(value):  # gui_name() 是方法，identifier 是属性
            try:
                value = value()
            except Exception:
                continue
        identity[attribute] = value if isinstance(value, (str, int)) else str(value)
    return identity


_DEFAULT_API: Optional[PainterAPI] = None


def default_api() -> PainterAPI:
    """进程内共享的适配层实例（Painter 里始终复用一个模块对象）。"""
    global _DEFAULT_API
    if _DEFAULT_API is None:
        _DEFAULT_API = PainterAPI()
    return _DEFAULT_API


def set_default_api(api: Optional[PainterAPI]) -> None:
    """测试或诊断用：替换/重置全局实例。"""
    global _DEFAULT_API
    _DEFAULT_API = api


__all__ = [
    "AdapterError", "PainterUnavailable", "UnsupportedCapability", "Capability",
    "CAPABILITIES", "CAPABILITY_BY_NAME", "RuntimeInfo", "PainterAPI",
    "default_api", "set_default_api", "PAINTER_MIN_VERSION", "VERIFIED_AGAINST",
]
