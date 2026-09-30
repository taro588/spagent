"""全部工具的声明清单 —— AI 可调用能力的唯一事实源（架构文档 §12）。

这里**只**描述「有哪些工具、属于哪个域、需要什么权限、哪些参数必填、
用什么 API、执行后怎么校验」。真正的执行代码在 core/actions.py，
执行分支通过 `@handler("工具名")` 标记，测试会强制两边一一对应。

新增一个工具的正确顺序：
    1. 在本文件加一条 ToolSpec；
    2. 在 core/actions.py 加一个 `@handler("<name>")` 执行分支；
    3. 如果需要更强的执行后校验，在 core/tools/verifiers.py 加校验器，
       并把 spec 的 verifier 指向它。
tests/test_tool_registry.py 会同时校验 1 和 2 是否对齐。
"""

from __future__ import annotations

from .domains import OperationDomain as D
from .domains import Permission as P
from .spec import ToolSpec

__all__ = ["CATALOG"]


def _spec(*args, **kwargs) -> ToolSpec:  # 便于目视对齐的极薄包装
    return ToolSpec(*args, **kwargs)


CATALOG: tuple[ToolSpec, ...] = (
    # ------------------------------------------------------------------
    # PROJECT（§12 PROJECT：get_project、state、save、save_copy）
    # ------------------------------------------------------------------
    _spec(
        "project_open",
        (D.PROJECT,),
        P.WRITE,
        "打开指定路径的 Painter 项目。",
        required=("path",),
        api="substance_painter.project.open",
        verifier="project_state",
    ),
    _spec(
        "project_save",
        (D.PROJECT,),
        P.WRITE,
        "保存当前项目。",
        api="substance_painter.project.save",
        verifier="project_state",
    ),
    _spec(
        "project_save_as",
        (D.PROJECT,),
        P.WRITE,
        "另存当前项目到指定路径。",
        required=("path",),
        api="substance_painter.project.save_as",
        verifier="project_state",
    ),
    _spec(
        "project_save_copy",
        (D.PROJECT,),
        P.WRITE,
        "另存一份项目副本，不影响当前打开的项目。",
        required=("path",),
        api="substance_painter.project.save_as_copy",
        verifier="project_state",
    ),
    _spec(
        "project_reload_mesh",
        (D.PROJECT,),
        P.WRITE,
        "重新载入项目网格（可选保留笔触）。",
        required=("path",),
        api="substance_painter.project.reload_mesh",
        verifier="project_state",
    ),
    _spec(
        "display_environment",
        (D.PROJECT,),
        P.WRITE,
        "设置视口环境贴图。",
        required=("resource",),
        api="substance_painter.display.set_environment_resource",
        verifier="executor_result",
    ),
    _spec(
        "display_color_lut",
        (D.PROJECT,),
        P.WRITE,
        "设置视口颜色 LUT。",
        required=("resource",),
        api="substance_painter.display.set_color_lut_resource",
        verifier="executor_result",
    ),
    _spec(
        "display_tone_mapping",
        (D.PROJECT,),
        P.WRITE,
        "设置视口色调映射函数。",
        required=("mode",),
        api="substance_painter.display.set_tone_mapping",
        verifier="executor_result",
    ),
    # ------------------------------------------------------------------
    # TEXTURE_SET（§12 TEXTURE_SET：list、active、channels、resolution）
    # ------------------------------------------------------------------
    _spec(
        "texture_stack_select",
        (D.TEXTURE_SET,),
        P.WRITE,
        "切换当前活跃 Texture Set。",
        required=("stack",),
        api="substance_painter.textureset.set_active_stack",
        verifier="texture_set_state",
    ),
    _spec(
        "texture_channel_add",
        (D.TEXTURE_SET,),
        P.WRITE,
        "为 Texture Set 新增通道。",
        required=("channel", "format"),
        api="substance_painter.textureset.Stack.add_channel",
        verifier="texture_set_state",
    ),
    _spec(
        "texture_channel_remove",
        (D.TEXTURE_SET,),
        P.WRITE,
        "从 Texture Set 移除通道。",
        required=("channel",),
        api="substance_painter.textureset.Stack.remove_channel",
        verifier="texture_set_state",
    ),
    _spec(
        "texture_channel_edit",
        (D.TEXTURE_SET,),
        P.WRITE,
        "修改 Texture Set 通道格式或标签。",
        required=("channel", "format"),
        api="substance_painter.textureset.Stack.edit_channel",
        verifier="texture_set_state",
    ),
    _spec(
        "texture_set_resolution",
        (D.TEXTURE_SET,),
        P.WRITE,
        "设置 Texture Set 分辨率。",
        required=("resolution",),
        api="substance_painter.textureset.TextureSet.set_resolution",
        verifier="texture_set_state",
    ),
    _spec(
        "ensure_texture_set_ready",
        (D.TEXTURE_SET,),
        P.WRITE,
        "高层工作流：选中 Texture Set、必要时设置分辨率，可选烘焙。",
        macro=True,
        verifier="executor_result",
    ),
    # ------------------------------------------------------------------
    # LAYER（§12 LAYER：create_group、create_fill_layer、delete、rename…）
    # ------------------------------------------------------------------
    _spec(
        "create_fill_layer",
        (D.LAYER,),
        P.WRITE,
        "创建 Fill Layer（材质/填充图层）。",
        aliases=("insert_fill_layer",),
        api="substance_painter.layerstack.insert_fill",
        verifier="layer_created",
    ),
    _spec(
        "create_paint_layer",
        (D.LAYER,),
        P.WRITE,
        "创建 Paint Layer（手绘图层）。",
        aliases=("insert_paint_layer",),
        api="substance_painter.layerstack.insert_paint",
        verifier="layer_created",
    ),
    _spec(
        "create_group",
        (D.LAYER,),
        P.WRITE,
        "创建 Group Layer（图层组）。",
        aliases=("insert_group",),
        api="substance_painter.layerstack.insert_group",
        verifier="layer_created",
    ),
    _spec(
        "add_mask",
        (D.MASK,),
        P.WRITE,
        "为最近创建的图层添加遮罩。",
        api="substance_painter.layerstack.LayerNode.add_mask",
        verifier="executor_result",
    ),
    _spec(
        "set_opacity",
        (D.LAYER,),
        P.WRITE,
        "设置图层不透明度（0-1）。",
        required=("opacity",),
        api="substance_painter.layerstack.Node.set_opacity",
        verifier="executor_result",
    ),
    _spec(
        "set_active_channels",
        (D.MATERIAL,),
        P.WRITE,
        "设置图层生效的通道集合。",
        required=("channels",),
        api="substance_painter.source.ActiveChannelsMixin.active_channels",
        verifier="executor_result",
    ),
    _spec(
        "set_projection_mode",
        (D.LAYER,),
        P.WRITE,
        "设置投影模式（UV / Triplanar / Planar）。",
        required=("mode",),
        api="substance_painter.layerstack.FillParamsEditorMixin.set_projection_mode",
        verifier="executor_result",
    ),
    _spec(
        "set_projection_scale",
        (D.LAYER,),
        P.WRITE,
        "设置投影缩放。",
        required=("scale",),
        api="substance_painter.layerstack.FillParamsEditorMixin.set_projection_parameters",
        verifier="executor_result",
    ),
    _spec(
        "set_blending_mode",
        (D.LAYER,),
        P.WRITE,
        "设置图层混合模式。",
        required=("mode",),
        api="substance_painter.layerstack.Node.set_blending_mode",
        verifier="executor_result",
    ),
    _spec(
        "set_visibility",
        (D.LAYER,),
        P.WRITE,
        "显示/隐藏图层。",
        required=("visible",),
        api="substance_painter.layerstack.Node.set_visible",
        verifier="executor_result",
    ),
    _spec(
        "rename_selected",
        (D.LAYER,),
        P.WRITE,
        "重命名当前选中的节点。",
        required=("name",),
        api="substance_painter.layerstack.Node.set_name",
        verifier="executor_result",
    ),
    _spec(
        "delete_selected",
        (D.LAYER,),
        P.DANGEROUS,
        "删除当前选中的节点（单次上限 10 个）。",
        api="substance_painter.layerstack.delete_node",
        verifier="layer_deleted",
    ),
    _spec(
        "select_last_created",
        (D.LAYER,),
        P.READ,
        "选中最近创建的节点。",
        api="substance_painter.layerstack.set_selected_nodes",
        verifier="executor_result",
    ),
    # ------------------------------------------------------------------
    # MATERIAL（§12 MATERIAL：set_base_color、set_roughness、set_metallic…）
    # ------------------------------------------------------------------
    _spec(
        "set_uniform_color",
        (D.MATERIAL,),
        P.WRITE,
        "把某个通道设为纯色。",
        required=("channel", "color"),
        aliases=("set_fill_color", "set_channel_color"),
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_fill_channel",
        (D.MATERIAL,),
        P.WRITE,
        "设置 Fill 图层某个通道的来源值。",
        required=("channel", "value"),
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    # §28 点名的核心真实操作：语义化的单通道写入工具。
    # 与 set_fill_property 的区别只是「把通道名写进工具名」，
    # 让模型少一次参数拼装、也让日志与校验报告可读。
    _spec(
        "set_base_color",
        (D.MATERIAL,),
        P.WRITE,
        "设置 BaseColor 通道（#RRGGBB / RGB(A) 数组 / 0-1 灰度）。",
        required=("value",),
        channel="BaseColor",
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_roughness",
        (D.MATERIAL,),
        P.WRITE,
        "设置 Roughness 通道（0-1 或颜色）。",
        required=("value",),
        channel="Roughness",
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_metallic",
        (D.MATERIAL,),
        P.WRITE,
        "设置 Metallic 通道（0-1 或颜色）。",
        required=("value",),
        channel="Metallic",
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_height",
        (D.MATERIAL,),
        P.WRITE,
        "设置 Height 通道（0-1 或颜色）。",
        required=("value",),
        channel="Height",
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_fill_property",
        (D.MATERIAL,),
        P.WRITE,
        "按通道名或材质参数名设置 Fill 图层的值。",
        required=("property", "value"),
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "set_source_parameters",
        (D.MATERIAL,),
        P.WRITE,
        "设置 Substance 材质参数（需先切到 Material 模式）。",
        required=("parameters",),
        api="substance_painter.source.SourceSubstance.set_parameters",
        verifier="material_parameters",
    ),
    _spec(
        "set_fill_material",
        (D.MATERIAL,),
        P.WRITE,
        "把 Substance 材质灌入 Fill Layer（切换到多通道 Material 模式）。",
        required=("name",),
        api="substance_painter.source.SourceEditorMixin.set_material_source",
        verifier="material_source",
    ),
    _spec(
        "set_source_preset",
        (D.MATERIAL,),
        P.WRITE,
        "对当前材质套用预设。",
        required=("preset",),
        api="substance_painter.source.SourceSubstance.apply_preset",
        verifier="executor_result",
    ),
    _spec(
        "set_source_output_mapping",
        (D.MATERIAL,),
        P.WRITE,
        "设置材质输出到通道的映射。",
        required=("mapping",),
        api="substance_painter.source.SourceSubstance.output_mapping",
        verifier="executor_result",
    ),
    _spec(
        "set_source_resource",
        (D.RESOURCE, D.MATERIAL),
        P.WRITE,
        "把某个 Painter 资源挂到指定通道。",
        required=("channel", "resource"),
        api="substance_painter.source.SourceEditorMixin.set_source",
        verifier="channel_source",
    ),
    _spec(
        "add_smart_material",
        (D.MATERIAL,),
        P.WRITE,
        "把 Smart Material 资源作为新图层插入 Layer Stack。",
        required=("name",),
        aliases=("insert_smart_material",),
        api="substance_painter.layerstack.insert_smart_material",
        verifier="layer_created",
    ),
    _spec(
        "apply_base_material",
        (D.LAYER, D.MATERIAL),
        P.WRITE,
        "高层工作流：创建 Fill Layer 并一次性写入基础材质参数。",
        macro=True,
        verifier="executor_result",
    ),
    _spec(
        "auto_material_workflow",
        (D.LAYER, D.MATERIAL),
        P.WRITE,
        "高层工作流：材质制作全流程（可选烘焙与导出，需显式开启）。",
        macro=True,
        schema_note="烘焙与导出必须显式声明 operation_domain。",
        verifier="executor_result",
    ),
    _spec(
        "ensure_material_layer",
        (D.LAYER, D.MATERIAL),
        P.WRITE,
        "高层工作流：确保当前有一个可编辑的材质图层。",
        macro=True,
        verifier="executor_result",
    ),
    # ------------------------------------------------------------------
    # MASK / EFFECT（§12 MASK：create_mask、add_generator、add_filter…）
    # ------------------------------------------------------------------
    _spec(
        "add_smart_mask",
        (D.MASK,),
        P.WRITE,
        "为最近创建的图层添加 Smart Mask。",
        required=("name",),
        aliases=("insert_smart_mask",),
        api="substance_painter.layerstack.insert_smart_mask",
        verifier="executor_result",
    ),
    _spec(
        "add_generator",
        (D.MASK,),
        P.WRITE,
        "添加 Generator 效果（如金属边缘磨损）。",
        required=("name",),
        aliases=("insert_generator",),
        api="substance_painter.layerstack.insert_generator_effect",
        verifier="effect_added",
    ),
    _spec(
        "add_filter",
        (D.MASK, D.EFFECT),
        P.WRITE,
        "添加 Filter 效果。",
        required=("name",),
        aliases=("insert_filter",),
        api="substance_painter.layerstack.insert_filter_effect",
        verifier="effect_added",
    ),
    _spec(
        "set_effect_parameters",
        (D.EFFECT,),
        P.WRITE,
        "设置 Effect（Generator/Filter）参数。",
        required=("parameters",),
        api="substance_painter.layerstack.LevelsEffectNode.set_parameters",
        api_alternatives=(
            "substance_painter.layerstack.CompareMaskEffectNode.set_parameters",
            "substance_painter.layerstack.ColorSelectionEffectNode.set_parameters",
        ),
        verifier="effect_parameters",
    ),
    _spec(
        "set_mask_enabled",
        (D.MASK,),
        P.WRITE,
        "启用/禁用图层遮罩。",
        required=("enabled",),
        api="substance_painter.layerstack.LayerNode.enable_mask",
        verifier="executor_result",
    ),
    _spec(
        "set_mask_background",
        (D.MASK,),
        P.WRITE,
        "设置遮罩底色（黑/白）。",
        required=("background",),
        api="substance_painter.layerstack.LayerNode.set_mask_background",
        verifier="executor_result",
    ),
    _spec(
        "set_geometry_mask",
        (D.MASK,),
        P.WRITE,
        "设置几何遮罩参数。",
        required=("parameters",),
        api="substance_painter.layerstack.LayerNode.set_geometry_mask_type",
        api_alternatives=(
            "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_meshes",
            "substance_painter.layerstack.LayerNode.set_geometry_mask_enabled_uv_tiles",
        ),
        verifier="executor_result",
    ),
    _spec(
        "add_anchor_point",
        (D.MASK,),
        P.WRITE,
        "添加 Anchor Point（供后续图层复用上游结果）。",
        api="substance_painter.layerstack.insert_anchor_point_effect",
        verifier="effect_added",
    ),
    _spec(
        "add_color_selection",
        (D.MASK,),
        P.WRITE,
        "添加 Color Selection 效果。",
        api="substance_painter.layerstack.insert_color_selection_effect",
        verifier="effect_added",
    ),
    _spec(
        "add_compare_mask",
        (D.MASK,),
        P.WRITE,
        "添加 Compare Mask 效果。",
        api="substance_painter.layerstack.insert_compare_mask_effect",
        verifier="effect_added",
    ),
    _spec(
        "add_levels",
        (D.MASK,),
        P.WRITE,
        "添加 Levels 效果。",
        api="substance_painter.layerstack.insert_levels_effect",
        verifier="effect_added",
    ),
    # ------------------------------------------------------------------
    # RESOURCE（§12 RESOURCE：search、import、get、assign）
    # ------------------------------------------------------------------
    _spec(
        "resource_search",
        (D.RESOURCE,),
        P.READ,
        "在 Painter 资源库中搜索资源。",
        required=("query",),
        api="substance_painter.resource.search",
        verifier="read_only",
    ),
    _spec(
        "resource_project_list",
        (D.RESOURCE,),
        P.READ,
        "列出当前项目内的资源。",
        api="substance_painter.resource.list_project_resources",
        verifier="read_only",
    ),
    _spec(
        "resource_import_project",
        (D.RESOURCE,),
        P.WRITE,
        "把磁盘上的贴图/材质文件导入当前项目。",
        required=("path", "usage"),
        api="substance_painter.resource.import_project_resource",
        verifier="resource_imported",
    ),
    _spec(
        "save_smart_material",
        (D.RESOURCE,),
        P.WRITE,
        "把 Group Layer 导出为 Smart Material。",
        required=("name",),
        api="substance_painter.layerstack.create_smart_material",
        verifier="executor_result",
    ),
    _spec(
        "save_smart_mask",
        (D.RESOURCE,),
        P.WRITE,
        "把 Group Layer 导出为 Smart Mask。",
        required=("name",),
        api="substance_painter.layerstack.create_smart_mask",
        verifier="executor_result",
    ),
    # ------------------------------------------------------------------
    # BAKING（§13 独占域：必须显式声明任务域）
    # ------------------------------------------------------------------
    _spec(
        "bake_start",
        (D.BAKING,),
        P.DANGEROUS,
        "开始烘焙选中 Texture Set 的 Mesh Maps。",
        api="substance_painter.baking.bake_selected_textures_async",
        verifier="bake_requested",
        schema_note="烘焙属于独占执行域，计划必须声明 operation_domain=baking。",
    ),
    _spec(
        "bake_highpoly",
        (D.BAKING,),
        P.DANGEROUS,
        "设置烘焙用的 High Poly 网格。",
        required=("path",),
        api="substance_painter.baking.BakingParameters.set",
        verifier="bake_requested",
    ),
    # ------------------------------------------------------------------
    # EXPORT（§13 独占域）
    # ------------------------------------------------------------------
    _spec(
        "export_textures",
        (D.EXPORT,),
        P.EXPORT,
        "按导出预设导出贴图。",
        required=("export_path",),
        api="substance_painter.export.export_project_textures",
        verifier="export_result",
        schema_note="导出属于独占执行域，计划必须声明 operation_domain=export。",
    ),
    _spec(
        "export_mesh",
        (D.EXPORT,),
        P.EXPORT,
        "导出项目网格。",
        required=("path",),
        api="substance_painter.export.export_mesh",
        verifier="export_result",
    ),
    # ------------------------------------------------------------------
    # VERIFY（§12 VERIFY：inspect_*、snapshot、diff、verify）
    # ------------------------------------------------------------------
    _spec(
        "verify_last_created_parameters",
        (D.VERIFY,),
        P.READ,
        "对比最近创建节点上实际生效的参数与期望值（API 校验，§18.1）。",
        required=("parameters",),
        verifier="verify_report",
        api="substance_painter.source.SourceSubstance.get_parameters",
    ),
)

# 目视自检：catlog 里不允许出现重名（registry 还会再查一次别名的唯一性）
_names = [spec.name for spec in CATALOG]
if len(_names) != len(set(_names)):
    from .spec import ToolContractError

    duplicates = sorted({name for name in _names if _names.count(name) > 1})
    raise ToolContractError("catalog 存在重名工具: " + ", ".join(duplicates))
