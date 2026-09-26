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
    "set_fill_property",
    "set_source_parameters",
    "set_effect_parameters",
    "verify_last_created_parameters",
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

    # Prefer the starter-assets shelf, then all available resources.
    # Painter's search is fuzzy by default, so rank exact display/identifier
    # matches first instead of blindly taking the first result.
    resources = sp.resource.search(f"s:starterassets u:{usage} n:{name}")
    if not resources:
        resources = sp.resource.search(f"u:{usage} n:{name}")
    if not resources:
        raise ActionError(f"找不到 {usage} 资源: {name}")

    wanted = " ".join(name.casefold().split())
    exact = []
    for resource in resources:
        candidates = []
        try:
            candidates.append(resource.gui_name())
        except Exception:
            pass
        try:
            identifier = resource.identifier()
            candidates.append(getattr(identifier, "name", ""))
        except Exception:
            pass
        if any(" ".join(str(value).casefold().split()) == wanted for value in candidates):
            exact.append(resource)
    return exact[0] if exact else resources[0]


def _mask_position(node):
    if not node.has_mask():
        node.add_mask(sp.layerstack.MaskBackground.Black)
    return sp.layerstack.InsertPosition.inside_node(node, sp.layerstack.NodeStack.Mask)


def _content_position(node):
    return sp.layerstack.InsertPosition.inside_node(node, sp.layerstack.NodeStack.Content)



def _serializable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serializable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serializable(v) for k, v in value.items()}
    for attrs in (("r", "g", "b", "a"), ("x", "y", "z")):
        if all(hasattr(value, a) for a in attrs):
            return {a: float(getattr(value, a)) for a in attrs}
    return str(value)


def _value_close(actual, expected, tolerance=1e-4):
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _value_close(a, e, tolerance) for a, e in zip(actual, expected)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual) != set(expected):
            return False
        return all(_value_close(actual[k], expected[k], tolerance) for k in actual)
    # Painter commonly returns vector/color objects rather than JSON lists.
    actual_serialized = _serializable(actual)
    expected_serialized = _serializable(expected)
    if isinstance(actual_serialized, dict) and isinstance(expected_serialized, dict):
        if set(actual_serialized) != set(expected_serialized):
            return False
        return all(
            _value_close(actual_serialized[k], expected_serialized[k], tolerance)
            for k in actual_serialized
        )
    return actual_serialized == expected_serialized


def _material_source(node):
    if not hasattr(node, "get_material_source"):
        raise ActionError("目标节点不支持 Material source。")
    try:
        source = node.get_material_source()
    except Exception as exc:
        raise ActionError(
            "当前节点不在多通道 Material 模式，无法编辑 Substance 参数。"
        ) from exc
    if source is None or not hasattr(source, "get_parameters") or not hasattr(source, "set_parameters"):
        raise ActionError("当前 Material source 不支持 Substance 参数编辑。")
    return source


def _normalize_parameter_value(value):
    # Painter SourceSubstance accepts tuples for vector-like values.
    # AI plans commonly arrive as JSON arrays, so normalize recursively.
    if isinstance(value, list):
        return tuple(_normalize_parameter_value(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _normalize_parameter_value(v) for k, v in value.items()}
    return value

def validate_plan(plan: dict) -> dict:
    """Validate an AI-generated plan before any Painter mutation occurs."""
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise ActionError("执行计划必须是包含 actions 数组的对象。")
    actions = plan["actions"]
    if not actions:
        raise ActionError("执行计划不能为空。")
    if len(actions) > 20:
        raise ActionError("单次最多执行 20 个动作。")

    required = {
        "set_opacity": ("opacity",),
        "set_active_channels": ("channels",),
        "set_projection_mode": ("mode",),
        "set_projection_scale": ("scale",),
        "set_fill_property": ("property", "value"),
        "set_source_parameters": ("parameters",),
        "set_effect_parameters": ("parameters",),
        "add_generator": ("name",),
        "add_filter": ("name",),
        "add_smart_mask": ("name",),
        "add_smart_material": ("name",),
        "set_fill_material": ("name",),
        "rename_selected": ("name",),
        "export_textures": ("export_path",),
    }
    for index, action in enumerate(actions, 1):
        if not isinstance(action, dict):
            raise ActionError(f"第 {index} 个动作必须是对象。")
        kind = action.get("action")
        if kind not in SUPPORTED_ACTIONS:
            raise ActionError(f"第 {index} 个动作不允许执行: {kind}")
        for field in required.get(kind, ()):
            if field not in action:
                raise ActionError(f"第 {index} 个 {kind} 缺少参数: {field}")

        if kind in {"set_source_parameters", "set_effect_parameters"}:
            if not isinstance(action.get("parameters"), dict) or not action["parameters"]:
                raise ActionError(f"第 {index} 个 {kind} 的 parameters 必须是非空对象。")
        if kind == "set_projection_scale":
            scale = action.get("scale")
            if not isinstance(scale, list) or len(scale) not in (2, 3):
                raise ActionError(f"第 {index} 个 set_projection_scale 的 scale 必须是 2 或 3 个数字。")
            if not all(isinstance(v, (int, float)) for v in scale):
                raise ActionError(f"第 {index} 个 set_projection_scale 的 scale 含非数字值。")
        if kind == "set_opacity":
            opacity = action.get("opacity")
            if not isinstance(opacity, (int, float)) or not 0 <= float(opacity) <= 1:
                raise ActionError(f"第 {index} 个 set_opacity 的 opacity 必须在 0 到 1 之间。")
        if kind == "export_textures":
            path = str(action.get("export_path") or "").strip()
            if not path or len(path) > 1000:
                raise ActionError(f"第 {index} 个 export_textures 的 export_path 无效。")
        if kind in {"rename_selected", "set_fill_material", "add_generator", "add_filter",
                    "add_smart_mask", "add_smart_material"}:
            if not str(action.get("name") or action.get("resource") or "").strip():
                raise ActionError(f"第 {index} 个 {kind} 缺少有效资源/名称。")
    return plan


def execute_plan(plan: dict) -> dict:
    plan = validate_plan(plan)

    created = []
    results = []
    pending_selection = None

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
                        channels.add(getattr(sp.textureset.ChannelType, str(name)))
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
                if not isinstance(scale, list) or len(scale) not in (2, 3):
                    raise ActionError("scale 必须是两个或三个数字组成的数组。")
                scale = [float(v) for v in scale]
                if hasattr(params, "projection_3d") and len(scale) == 3:
                    params.projection_3d.scale = scale
                elif hasattr(params, "uv_transformation"):
                    params.uv_transformation.scale = scale[:2]
                else:
                    raise ActionError("当前投影模式不支持 scale 参数。")
                node.set_projection_parameters(params)
                results.append({"action": kind, "target": node.get_name(), "scale": scale})

            elif kind == "set_fill_property":
                if not created:
                    raise ActionError("set_fill_property 没有可作用的最近节点。")
                node = created[-1]
                if not isinstance(node, (sp.layerstack.FillLayerNode, sp.layerstack.FillEffectNode)):
                    raise ActionError("set_fill_property 只能作用于 Fill Layer/Fill Effect。")
                source = _material_source(node)
                property_name = str(action.get("property") or "").strip()
                if not property_name:
                    raise ActionError("set_fill_property 需要 property。")
                value = _normalize_parameter_value(action.get("value"))
                available = source.get_parameters()
                if property_name not in available:
                    raise ActionError(f"当前材质不存在参数: {property_name}")
                source.set_parameters({property_name: value})
                results.append({"action": kind, "target": node.get_name(), "property": property_name, "value": value})

            elif kind == "set_source_parameters":
                if not created:
                    raise ActionError("set_source_parameters 没有可作用的最近节点。")
                node = created[-1]
                source = _material_source(node)
                values = action.get("parameters")
                if not isinstance(values, dict) or not values:
                    raise ActionError("parameters 必须是非空对象。")
                values = {
                    str(name): _normalize_parameter_value(value)
                    for name, value in values.items()
                }
                available = source.get_parameters()
                unknown = [name for name in values if name not in available]
                if unknown:
                    raise ActionError("未知参数: " + ", ".join(unknown))
                source.set_parameters(values)
                results.append({"action": kind, "target": node.get_name(), "parameters": values})

            elif kind == "set_effect_parameters":
                if not created:
                    raise ActionError("set_effect_parameters 没有可作用的最近节点。")
                node = created[-1]
                if not hasattr(node, "get_parameters") or not hasattr(node, "set_parameters"):
                    raise ActionError("目标 Effect 不支持参数编辑。")
                values = action.get("parameters")
                if not isinstance(values, dict) or not values:
                    raise ActionError("parameters 必须是非空对象。")
                current = node.get_parameters()
                unknown = [name for name in values if not hasattr(current, name)]
                if unknown:
                    raise ActionError("未知参数: " + ", ".join(unknown))
                values = {
                    str(name): _normalize_parameter_value(value)
                    for name, value in values.items()
                }
                for name, value in values.items():
                    try:
                        setattr(current, name, value)
                    except Exception as exc:
                        raise ActionError(f"无法设置 Effect 参数 {name}: {exc}") from exc
                node.set_parameters(current)
                results.append({"action": kind, "target": node.get_name(), "parameters": values})

            elif kind == "verify_last_created_parameters":
                if not created:
                    raise ActionError("verify_last_created_parameters 没有可验证的最近节点。")
                node = created[-1]
                expected = action.get("parameters")
                if not isinstance(expected, dict) or not expected:
                    raise ActionError("parameters 必须是非空对象。")
                if hasattr(node, "get_material_source"):
                    try:
                        source = node.get_material_source()
                    except Exception as exc:
                        raise ActionError(
                            "当前节点不在多通道 Material 模式，无法验证 Substance 参数。"
                        ) from exc
                    actual = source.get_parameters() if source else {}
                elif hasattr(node, "get_parameters"):
                    actual = node.get_parameters()
                else:
                    raise ActionError("目标节点不支持参数读取。")
                mismatches = {}
                for name, wanted in expected.items():
                    if name not in actual:
                        mismatches[name] = {"expected": wanted, "actual": None, "reason": "missing"}
                    elif not _value_close(actual[name], wanted):
                        mismatches[name] = {"expected": wanted, "actual": _serializable(actual[name]), "reason": "different"}
                results.append({
                    "action": kind,
                    "target": node.get_name(),
                    "verified": not mismatches,
                    "mismatches": mismatches,
                })

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
                pending_selection = created[-1]
                results.append({"action": kind, "uid": created[-1].uid()})

            elif kind == "export_textures":
                export_path = str(action.get("export_path") or "").strip()
                if not export_path:
                    raise ActionError("export_textures 需要 export_path。")
                preset_name = str(action.get("preset") or "PBR Metallic Roughness").strip()
                presets = sp.export.list_predefined_export_presets()
                preset = next((item for item in presets if item.name == preset_name), None)
                if preset is None:
                    raise ActionError(f"找不到预定义导出预设: {preset_name}")
                stack = _active_stack()
                if stack is None:
                    raise ActionError("当前没有可导出的 Texture Set。")
                config = {
                    "exportShaderParams": False,
                    "exportPath": export_path,
                    "defaultExportPreset": preset.url(),
                    "exportList": [{"rootPath": stack.name()}],
                    "exportParameters": [{
                        "parameters": {
                            "dithering": True,
                            "paddingAlgorithm": "infinite",
                        }
                    }],
                }
                try:
                    export_list = sp.export.list_project_textures(config)
                except Exception as exc:
                    raise ActionError(f"导出配置无效: {exc}") from exc
                if not export_list:
                    raise ActionError("当前配置没有可导出的贴图。")
                try:
                    export_result = sp.export.export_project_textures(config)
                except Exception as exc:
                    raise ActionError(f"纹理导出失败: {exc}") from exc
                status = getattr(export_result.status, "name", str(export_result.status))
                if status.lower() != "success":
                    raise ActionError(
                        getattr(export_result, "message", "") or f"导出状态: {status}"
                    )
                results.append({
                    "action": kind,
                    "status": status,
                    "message": getattr(export_result, "message", ""),
                    "textures": getattr(export_result, "textures", {}) or {},
                })

            elif kind == "set_fill_material":
                if not created:
                    raise ActionError("set_fill_material 没有可作用的最近创建节点。")
                node = created[-1]
                if not hasattr(node, "set_material_source"):
                    raise ActionError("最近节点不是支持材质源的 Fill Layer。")
                resource = _resource("substance", action.get("resource") or action.get("name"))
                source = node.set_material_source(resource.identifier())
                if source is None:
                    raise ActionError("Painter 没有返回可编辑的 Material source。")
                results.append({
                    "action": kind,
                    "resource": resource.gui_name(),
                    "target": node.get_name(),
                    "source_type": type(source).__name__,
                    "source_uid": source.uid() if hasattr(source, "uid") else None,
                })

    return {"success": True, "results": results}
