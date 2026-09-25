from __future__ import annotations

import substance_painter as sp

SUPPORTED_ACTIONS = {
    "create_fill_layer",
    "create_paint_layer",
    "create_group",
    "add_mask",
    "set_opacity",
    "set_active_channels",
    "set_projection_mode",
    "set_projection_scale",
    "add_generator",
    "add_filter",
    "add_smart_mask",
    "add_smart_material",
    "set_fill_material",
    "rename_selected",
    "delete_selected",
    "select_last_created",
    "export_textures",
}


class ActionError(RuntimeError):
    pass


def _name(value, default):
    value = str(value or "").strip()
    return value[:120] if value else default


def _active_stack():
    if not sp.project.is_open():
        raise ActionError("没有打开 Painter 项目。")
    return sp.textureset.get_active_stack()


def _resource(usage: str, name: str):
    name = str(name or "").strip()
    if not name:
        raise ActionError(f"{usage} 资源名称不能为空。")
    resources = sp.resource.search(f"s:starterassets u:{usage} n:{name}")
    if not resources:
        resources = sp.resource.search(f"u:{usage} n:{name}")
    if not resources:
        raise ActionError(f"找不到 {usage} 资源: {name}")
    return resources[0]


def _mask_position(node):
    if not node.has_mask():
        node.add_mask(sp.layerstack.MaskBackground.Black)
    return sp.layerstack.InsertPosition.inside_node(node, sp.layerstack.NodeStack.Mask)


def _content_position(node):
    return sp.layerstack.InsertPosition.inside_node(node, sp.layerstack.NodeStack.Content)


def execute_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise ActionError("执行计划格式无效。")
    if len(plan["actions"]) > 20:
        raise ActionError("单次最多执行 20 个动作。")

    created = []
    results = []

    def selected_nodes():
        nodes = sp.layerstack.get_selected_nodes(_active_stack())
        if not nodes:
            raise ActionError("当前 Texture Set 没有选中的节点。")
        return list(nodes)

    with sp.layerstack.ScopedModification("SP AI Assistant"):
        for index, action in enumerate(plan["actions"]):
            if not isinstance(action, dict):
                raise ActionError(f"第 {index + 1} 个动作不是对象。")

            kind = action.get("action")
            if kind not in SUPPORTED_ACTIONS:
                raise ActionError(f"不允许执行动作: {kind}")

            if kind == "create_fill_layer":
                node = sp.layerstack.insert_fill(
                    sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())
                )
                node.set_name(_name(action.get("name"), "AI Fill Layer"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "create_paint_layer":
                node = sp.layerstack.insert_paint(
                    sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())
                )
                node.set_name(_name(action.get("name"), "AI Paint Layer"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "create_group":
                node = sp.layerstack.insert_group(
                    sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())
                )
                node.set_name(_name(action.get("name"), "AI Group"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "add_mask":
                if not created:
                    raise ActionError("add_mask 没有可作用的最近创建图层。")
                node = created[-1]
                background = str(action.get("background", "black")).lower()
                bg = sp.layerstack.MaskBackground.White if background == "white" else sp.layerstack.MaskBackground.Black
                if not node.has_mask():
                    node.add_mask(bg)
                results.append({"action": kind, "target": node.get_name(), "background": background})

            elif kind == "set_active_channels":
                if not created:
                    raise ActionError("set_active_channels 没有可作用的最近创建节点。")
                node = created[-1]
                if not hasattr(node, "active_channels"):
                    raise ActionError("目标节点不支持 active_channels。")
                names = action.get("channels") or ["BaseColor", "Roughness", "Metallic", "Normal"]
                channels = set()
                for name in names:
                    try:
                        channels.add(getattr(sp.layerstack.ChannelType, str(name)))
                    except AttributeError:
                        raise ActionError(f"未知通道: {name}")
                node.active_channels = channels
                results.append({"action": kind, "target": node.get_name(), "channels": [c.name for c in channels]})

            elif kind == "set_projection_mode":
                if not created:
                    raise ActionError("set_projection_mode 没有可作用的最近创建节点。")
                node = created[-1]
                mode = str(action.get("mode", "UV")).strip()
                try:
                    projection = getattr(sp.layerstack.ProjectionMode, mode)
                except AttributeError:
                    raise ActionError(f"未知投影模式: {mode}")
                node.set_projection_mode(projection)
                results.append({"action": kind, "target": node.get_name(), "mode": mode})

            elif kind == "set_projection_scale":
                if not created:
                    raise ActionError("set_projection_scale 没有可作用的最近创建节点。")
                node = created[-1]
                if not hasattr(node, "get_projection_parameters"):
                    raise ActionError("目标节点不支持投影参数。")
                params = node.get_projection_parameters()
                scale = action.get("scale")
                if not isinstance(scale, list) or len(scale) != 3:
                    raise ActionError("scale 必须是三个数字组成的数组。")
                params.projection_3d.scale = [float(v) for v in scale]
                node.set_projection_parameters(params)
                results.append({"action": kind, "target": node.get_name(), "scale": params.projection_3d.scale})

            elif kind == "set_opacity":
                if not created:
                    raise ActionError("set_opacity 没有可作用的最近创建节点。")
                opacity = float(action.get("opacity", 1.0))
                if not 0.0 <= opacity <= 1.0:
                    raise ActionError("opacity 必须在 0 到 1 之间。")
                node = created[-1]
                node.set_opacity(opacity)
                results.append({"action": kind, "target": node.get_name(), "opacity": opacity})

            elif kind == "add_generator":
                if not created:
                    raise ActionError("add_generator 没有可作用的最近创建图层。")
                node = created[-1]
                resource = _resource("generator", action.get("resource") or action.get("name"))
                pos = _mask_position(node) if action.get("stack", "mask") == "mask" else _content_position(node)
                effect = sp.layerstack.insert_generator_effect(pos, resource.identifier())
                if action.get("name"):
                    effect.set_name(_name(action.get("name"), "AI Generator"))
                results.append({"action": kind, "resource": resource.gui_name(), "target": node.get_name()})

            elif kind == "add_filter":
                if not created:
                    raise ActionError("add_filter 没有可作用的最近创建图层。")
                node = created[-1]
                resource = _resource("filter", action.get("resource") or action.get("name"))
                pos = _mask_position(node) if action.get("stack", "content") == "mask" else _content_position(node)
                effect = sp.layerstack.insert_filter_effect(pos, resource.identifier())
                if action.get("name"):
                    effect.set_name(_name(action.get("name"), "AI Filter"))
                results.append({"action": kind, "resource": resource.gui_name(), "target": node.get_name()})

            elif kind == "add_smart_mask":
                if not created:
                    raise ActionError("add_smart_mask 没有可作用的最近创建图层。")
                node = created[-1]
                resource = _resource("smartmask", action.get("resource") or action.get("name"))
                pos = _mask_position(node)
                inserted = sp.layerstack.insert_smart_mask(pos, resource.identifier())
                results.append({"action": kind, "resource": resource.gui_name(), "inserted": len(inserted)})

            elif kind == "add_smart_material":
                resource = _resource("smartmaterial", action.get("resource") or action.get("name"))
                pos = sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())
                node = sp.layerstack.insert_smart_material(pos, resource.identifier())
                created.append(node)
                results.append({"action": kind, "resource": resource.gui_name(), "name": node.get_name(), "uid": node.uid()})

            elif kind == "rename_selected":
                nodes = selected_nodes()
                name = _name(action.get("name"), "")
                if not name:
                    raise ActionError("rename_selected 需要 name。")
                for node in nodes:
                    node.set_name(name)
                results.append({"action": kind, "count": len(nodes), "name": name})

            elif kind == "delete_selected":
                nodes = selected_nodes()
                if len(nodes) > 10:
                    raise ActionError("单次最多删除 10 个选中节点。")
                for node in nodes:
                    sp.layerstack.delete_node(node)
                results.append({"action": kind, "count": len(nodes)})

            elif kind == "select_last_created":
                if not created:
                    raise ActionError("没有最近创建的节点。")
                sp.layerstack.set_selected_nodes([created[-1]])
                results.append({"action": kind, "uid": created[-1].uid()})

            elif kind == "export_textures":
                export_path = str(action.get("export_path") or "").strip()
                if not export_path:
                    raise ActionError("export_textures 需要 export_path。")
                preset_name = str(action.get("preset") or "PBR Metallic Roughness").strip()
                preset_id = sp.resource.ResourceID(context="starter_assets", name=preset_name)
                config = {
                    "exportShaderParams": False,
                    "exportPath": export_path,
                    "defaultExportPreset": preset_id.url(),
                    "exportList": [{"rootPath": _active_stack().name()}],
                    "exportParameters": [{
                        "parameters": {
                            "dithering": True,
                            "paddingAlgorithm": "infinite",
                        }
                    }],
                }
                export_list = sp.export.list_project_textures(config)
                if not export_list:
                    raise ActionError("当前配置没有可导出的贴图。")
                export_result = sp.export.export_project_textures(config)
                results.append({
                    "action": kind,
                    "status": getattr(export_result.status, "name", str(export_result.status)),
                    "message": export_result.message,
                    "textures": export_result.textures,
                })

            elif kind == "set_fill_material":
                if not created:
                    raise ActionError("set_fill_material 没有可作用的最近创建节点。")
                node = created[-1]
                if not hasattr(node, "set_material_source"):
                    raise ActionError("最近节点不是支持材质源的 Fill Layer。")
                resource = _resource("substance", action.get("resource") or action.get("name"))
                node.set_material_source(resource.identifier())
                results.append({"action": kind, "resource": resource.gui_name(), "target": node.get_name()})

    return {"success": True, "results": results}
