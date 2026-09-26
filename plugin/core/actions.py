from __future__ import annotations

import substance_painter as sp

ACTION_ALIASES = {
    "insert_fill_layer": "create_fill_layer",
    "insert_paint_layer": "create_paint_layer",
    "insert_group": "create_group",
    "insert_generator": "add_generator",
    "insert_filter": "add_filter",
    "insert_smart_mask": "add_smart_mask",
    "insert_smart_material": "add_smart_material",
    "set_fill_color": "set_uniform_color",
    "set_channel_color": "set_uniform_color",
}

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
    "set_fill_channel",
    "set_source_parameters",
    "set_effect_parameters",
    "verify_last_created_parameters",
    "add_generator",
    "add_filter",
    "add_smart_mask",
    "add_smart_material",
    "set_fill_material",
    "set_uniform_color",
    "set_source_resource",
    "set_source_preset",
    "set_source_output_mapping",
    "set_blending_mode",
    "set_visibility",
    "set_mask_enabled",
    "set_mask_background",
    "set_geometry_mask",
    "add_anchor_point",
    "add_color_selection",
    "add_compare_mask",
    "add_levels",
    "texture_stack_select",
    "texture_channel_add",
    "texture_channel_remove",
    "texture_channel_edit",
    "texture_set_resolution",
    "project_open",
    "project_save",
    "project_save_as",
    "project_save_copy",
    "project_reload_mesh",
    "display_environment",
    "display_color_lut",
    "display_tone_mapping",
    "resource_import_project",
    "resource_search",
    "resource_project_list",
    "bake_start",
    "bake_highpoly",
    "export_mesh",
    "save_smart_material",
    "save_smart_mask",
    "rename_selected",
    "delete_selected",
    "select_last_created",
    "export_textures",
    "apply_base_material",
    "auto_material_workflow",
    "ensure_texture_set_ready",
    "ensure_material_layer",
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



def _parse_color(value):
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) in (6, 8):
            try:
                parts = [int(text[i:i+2], 16) / 255.0 for i in range(0, len(text), 2)]
                if len(parts) == 3:
                    parts.append(1.0)
                return sp.colormanagement.Color(*parts)
            except ValueError:
                pass
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        vals = [float(v) for v in value]
        if max(vals) > 1.0:
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return sp.colormanagement.Color(*vals)
    if isinstance(value, dict):
        keys = ["r", "g", "b", "a"]
        if all(k in value for k in keys[:3]):
            vals = [float(value[k]) for k in keys if k in value]
            if max(vals) > 1.0:
                vals = [v / 255.0 for v in vals]
            if len(vals) == 3:
                vals.append(1.0)
            return sp.colormanagement.Color(*vals)
    raise ActionError("颜色必须是 #RRGGBB/#RRGGBBAA、RGB(A) 数组或颜色对象。")



def _channel_source_value(channel_name, value):
    if isinstance(value, dict) and value.get("resource"):
        matches = sp.resource.search(str(value["resource"]))
        if not matches:
            raise ActionError("找不到资源: " + str(value["resource"]))
        return matches[0].identifier()
    name = str(channel_name).casefold()
    if name in {"basecolor", "base_color", "color", "emissive"}:
        return _parse_color(value)
    if isinstance(value, (int, float)):
        v = max(0.0, min(1.0, float(value)))
        return sp.colormanagement.Color(v, v, v, 1.0)
    if isinstance(value, (list, tuple, dict)) or (isinstance(value, str) and value.strip().startswith("#")):
        return _parse_color(value)
    raise ActionError(f"{channel_name} 通道的值必须是颜色、数值或 resource。")

def _set_public_params(obj, values, allowed):
    if not isinstance(values, dict) or not values:
        raise ActionError("parameters 必须是非空对象。")
    unknown = [k for k in values if k not in allowed]
    if unknown:
        raise ActionError("不允许设置的参数: " + ", ".join(unknown))
    for key, value in values.items():
        setattr(obj, key, _normalize_parameter_value(value))

def _expand_workflow_actions(plan: dict) -> dict:
    """Expand high-level intent into deterministic official Painter API actions."""
    expanded = []
    for action in plan.get("actions", []):
        kind = ACTION_ALIASES.get(str(action.get("action") or "").strip(), action.get("action"))
        if kind == "apply_base_material":
            name = _name(action.get("name"), "AI Base Material")
            expanded.append({"action": "create_fill_layer", "name": name})
            expanded.append({"action": "set_active_channels", "channels": action.get("channels") or ["BaseColor", "Roughness", "Metallic", "Normal", "Height"]})
            if action.get("material") or action.get("resource"):
                expanded.append({"action": "set_fill_material", "name": action.get("material") or action.get("resource")})
            values = action.get("parameters") or {}
            aliases = {
                "basecolor": "BaseColor", "base_color": "BaseColor", "color": "BaseColor",
                "roughness": "Roughness", "metallic": "Metallic", "metalness": "Metallic",
                "height": "Height", "normal": "Normal", "emissive": "Emissive",
            }
            if action.get("material") or action.get("resource"):
                for key, value in values.items():
                    expanded.append({"action": "set_source_parameters", "parameters": {str(key): value}})
            else:
                for key, value in values.items():
                    expanded.append({"action": "set_fill_property", "property": aliases.get(str(key).casefold(), str(key)), "value": value})
            if action.get("smart_mask") or action.get("mask"):
                expanded.append({"action": "add_smart_mask", "name": action.get("smart_mask") or action.get("mask")})
            if action.get("generator"):
                expanded.append({"action": "add_generator", "name": action["generator"], "stack": "mask"})
            if action.get("filter"):
                expanded.append({"action": "add_filter", "name": action["filter"], "stack": "content"})
            expanded.append({"action": "select_last_created"})
            if values:
                expanded.append({"action": "verify_last_created_parameters", "parameters": values})
        elif kind == "auto_material_workflow":
            if action.get("bake", False):
                expanded.append({"action": "bake_start"})
            base = dict(action)
            base["action"] = "apply_base_material"
            expanded.extend(_expand_workflow_actions({"actions": [base]})["actions"])
            if action.get("export_path"):
                expanded.append({
                    "action": "export_textures",
                    "export_path": action["export_path"],
                    "preset": action.get("export_preset", "PBR Metallic Roughness"),
                })
        elif kind == "ensure_texture_set_ready":
            expanded.append({"action": "texture_stack_select", "stack": action.get("stack", "active")})
            if action.get("resolution"):
                expanded.append({"action": "texture_set_resolution", "resolution": action["resolution"]})
            if action.get("bake", False):
                expanded.append({"action": "bake_start"})
        elif kind == "ensure_material_layer":
            expanded.append({"action": "create_fill_layer", "name": _name(action.get("name"), "AI Material Layer")})
            material = action.get("material") or action.get("resource")
            if material:
                expanded.append({"action": "set_fill_material", "name": material})
            expanded.append({"action": "select_last_created"})
        else:
            expanded.append(action)
    return {"actions": expanded}


def validate_plan(plan: dict) -> dict:
    """Validate an AI-generated plan before any Painter mutation occurs."""
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise ActionError("执行计划必须是包含 actions 数组的对象。")
    plan = _expand_workflow_actions(plan)
    actions = plan["actions"]
    if not actions:
        raise ActionError("执行计划不能为空。")
    if len(actions) > 20:
        raise ActionError("单次最多执行 20 个动作。")

    required = {
        "set_uniform_color": ("channel", "color"),
        "set_source_resource": ("channel", "resource"),
        "set_source_preset": ("preset",),
        "set_source_output_mapping": ("mapping",),
        "set_blending_mode": ("mode",),
        "set_visibility": ("visible",),
        "set_mask_enabled": ("enabled",),
        "set_mask_background": ("background",),
        "set_geometry_mask": ("parameters",),
        "add_anchor_point": (),
        "add_color_selection": (),
        "add_compare_mask": (),
        "add_levels": (),
        "texture_stack_select": ("stack",),
        "texture_channel_add": ("channel", "format"),
        "texture_channel_remove": ("channel",),
        "texture_channel_edit": ("channel", "format"),
        "texture_set_resolution": ("resolution",),
        "project_open": ("path",),
        "project_save": (),
        "project_save_as": ("path",),
        "project_save_copy": ("path",),
        "project_reload_mesh": ("path",),
        "display_environment": ("resource",),
        "display_color_lut": ("resource",),
        "display_tone_mapping": ("mode",),
        "resource_import_project": ("path", "usage"),
        "resource_search": ("query",),
        "resource_project_list": (),
        "bake_start": (),
        "bake_highpoly": ("path",),
        "export_mesh": ("path",),
        "save_smart_material": ("path", "name"),
        "save_smart_mask": ("path", "name"),
        "set_opacity": ("opacity",),
        "set_active_channels": ("channels",),
        "set_projection_mode": ("mode",),
        "set_projection_scale": ("scale",),
        "set_fill_property": ("property", "value"),
        "set_fill_channel": ("channel", "value"),
        "set_source_parameters": ("parameters",),
        "set_effect_parameters": ("parameters",),
        "add_generator": ("name",),
        "add_filter": ("name",),
        "add_smart_mask": ("name",),
        "add_smart_material": ("name",),
        "set_fill_material": ("name",),
        "rename_selected": ("name",),
        "export_textures": ("export_path",),
        "apply_base_material": (),
        "auto_material_workflow": (),
        "ensure_texture_set_ready": (),
        "ensure_material_layer": (),
    }
    for index, action in enumerate(actions, 1):
        if not isinstance(action, dict):
            raise ActionError(f"第 {index} 个动作必须是对象。")
        kind = ACTION_ALIASES.get(str(action.get("action") or "").strip(), action.get("action"))
        if kind != action.get("action"):
            action["action"] = kind
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

            kind = ACTION_ALIASES.get(str(action.get("action") or "").strip(), action.get("action"))
            if kind != action.get("action"):
                action["action"] = kind
            if kind not in SUPPORTED_ACTIONS:
                raise ActionError(f"不允许执行动作: {kind}")

            if kind == "create_fill_layer":
                node = sp.layerstack.insert_fill(
                    sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())
                )
                node.set_name(_name(action.get("name"), "AI Fill Layer"))
                created.append(node)
                results.append({
                    "action": kind,
                    "name": node.get_name(),
                    "uid": node.uid(),
                    "api": "substance_painter.layerstack.insert_fill",
                })

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
                node = created[-1] if created else selected_nodes()[0]
                if not isinstance(node, (sp.layerstack.FillLayerNode, sp.layerstack.FillEffectNode)):
                    raise ActionError("set_fill_property 只能作用于 Fill Layer/Fill Effect。")
                property_name = str(action.get("property") or "").strip()
                if not property_name:
                    raise ActionError("set_fill_property 需要 property。")
                value = action.get("value")
                channel_aliases = {
                    "basecolor": "BaseColor", "base_color": "BaseColor", "color": "BaseColor",
                    "roughness": "Roughness", "metallic": "Metallic", "metalness": "Metallic",
                    "height": "Height", "normal": "Normal", "opacity": "Opacity",
                    "emissive": "Emissive", "ao": "AmbientOcclusion", "ambientocclusion": "AmbientOcclusion",
                }
                channel_name = channel_aliases.get(property_name.casefold(), property_name)
                try:
                    channel = getattr(sp.textureset.ChannelType, channel_name)
                except AttributeError:
                    channel = None
                source_mode = getattr(node, "source_mode", None)
                if channel is not None and source_mode is not None:
                    node.set_source(channel, _channel_source_value(channel_name, value))
                    results.append({"action": kind, "target": node.get_name(), "property": property_name,
                                    "channel": channel_name, "value": value,
                                    "source_mode": getattr(source_mode, "name", str(source_mode)),
                                    "api": "substance_painter.layerstack.FillLayerNode.set_source"})
                else:
                    source = _material_source(node)
                    value = _normalize_parameter_value(value)
                    available = source.get_parameters()
                    if property_name not in available:
                        raise ActionError(f"当前材质不存在参数: {property_name}")
                    source.set_parameters({property_name: value})
                    results.append({"action": kind, "target": node.get_name(), "property": property_name, "value": value,
                                    "source_mode": getattr(source_mode, "name", str(source_mode))})

            elif kind == "set_fill_channel":
                node = created[-1] if created else selected_nodes()[0]
                if not isinstance(node, (sp.layerstack.FillLayerNode, sp.layerstack.FillEffectNode)):
                    raise ActionError("set_fill_channel 只能作用于 Fill Layer/Fill Effect。")
                channel = getattr(sp.textureset.ChannelType, str(action["channel"]))
                channel_arg = channel if getattr(node, "source_mode", None) is not None else None
                value = action.get("value")
                source_value = _channel_source_value(str(action["channel"]), value)
                node.set_source(channel_arg, source_value)
                results.append({"action": kind, "target": node.get_name(), "channel": str(action["channel"]),
                                "value": value, "api": "substance_painter.layerstack.FillLayerNode.set_source"})

            elif kind == "set_source_parameters":
                node = created[-1] if created else selected_nodes()[0]
                if getattr(node, "source_mode", None) is not None:
                    mode_name = getattr(getattr(node, "source_mode", None), "name", str(getattr(node, "source_mode", None)))
                    if mode_name != "Material":
                        raise ActionError("当前 Fill 是 Split 模式；Substance 参数必须先调用 set_fill_material 切换到 Material 模式，再调用 set_source_parameters。")
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
                node = created[-1] if created else selected_nodes()[0]
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
                    except Exception:
                        source = None
                    if source is None and hasattr(node, "get_source"):
                        channel_name = str(action.get("channel") or "BaseColor")
                        channel = getattr(sp.textureset.ChannelType, channel_name)
                        source = node.get_source(channel if getattr(node, "source_mode", None) is not None else None)
                    if source is None or not hasattr(source, "get_parameters"):
                        raise ActionError("当前节点没有可验证的参数源。")
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

            elif kind == "set_uniform_color":
                node = created[-1] if created else selected_nodes()[0]
                channel = getattr(sp.textureset.ChannelType, str(action["channel"]))
                node.set_source(channel, _parse_color(action["color"]))
                results.append({"action": kind, "target": node.get_name(), "channel": str(action["channel"]), "color": action["color"]})

            elif kind == "set_source_resource":
                node = created[-1] if created else selected_nodes()[0]
                channel = getattr(sp.textureset.ChannelType, str(action["channel"]))
                matches = sp.resource.search(str(action["resource"]))
                if not matches:
                    raise ActionError("找不到资源: " + str(action["resource"]))
                resource = matches[0]
                node.set_source(channel, resource.identifier())
                results.append({"action": kind, "target": node.get_name(), "channel": str(action["channel"]), "resource": resource.gui_name()})

            elif kind == "set_source_preset":
                node = created[-1] if created else selected_nodes()[0]
                source = _material_source(node)
                source.apply_preset(str(action["preset"]))
                results.append({"action": kind, "target": node.get_name(), "preset": action["preset"]})

            elif kind == "set_source_output_mapping":
                node = created[-1] if created else selected_nodes()[0]
                source = _material_source(node)
                mapping = {}
                for channel, output in dict(action["mapping"]).items():
                    mapping[getattr(sp.textureset.ChannelType, str(channel))] = str(output)
                source.output_mapping = mapping
                results.append({"action": kind, "target": node.get_name(), "mapping": action["mapping"]})

            elif kind == "set_blending_mode":
                node = created[-1] if created else selected_nodes()[0]
                if not node.has_blending():
                    raise ActionError("目标节点没有 Blending Mode。")
                mode = getattr(sp.layerstack.BlendingMode, str(action["mode"]))
                channel_name = action.get("channel")
                channel = getattr(sp.textureset.ChannelType, str(channel_name)) if channel_name else None
                if not node.is_in_mask_stack() and channel is None:
                    raise ActionError("普通图层设置 Blending Mode 时必须提供 channel。")
                node.set_blending_mode(mode, channel)
                results.append({"action": kind, "target": node.get_name(), "mode": str(action["mode"]), "channel": channel_name})

            elif kind == "set_visibility":
                node = created[-1] if created else selected_nodes()[0]
                node.set_visible(bool(action["visible"]))
                results.append({"action": kind, "target": node.get_name(), "visible": bool(action["visible"])})

            elif kind == "set_mask_enabled":
                node = created[-1] if created else selected_nodes()[0]
                node.enable_mask(bool(action["enabled"]))
                results.append({"action": kind, "target": node.get_name(), "enabled": bool(action["enabled"])})

            elif kind == "set_mask_background":
                node = created[-1] if created else selected_nodes()[0]
                if not node.has_mask():
                    node.add_mask(sp.layerstack.MaskBackground.Black)
                bg = getattr(sp.layerstack.MaskBackground, str(action["background"]).capitalize())
                node.set_mask_background(bg)
                results.append({"action": kind, "target": node.get_name(), "background": str(action["background"])})

            elif kind == "set_geometry_mask":
                node = created[-1] if created else selected_nodes()[0]
                params = action["parameters"]
                if not isinstance(params, dict):
                    raise ActionError("geometry mask parameters 必须是对象。")
                node.set_geometry_mask(**params)
                results.append({"action": kind, "target": node.get_name(), "parameters": params})

            elif kind in {"add_anchor_point", "add_color_selection", "add_compare_mask", "add_levels"}:
                node = created[-1] if created else selected_nodes()[0]
                pos = _content_position(node)
                if kind == "add_anchor_point":
                    effect = sp.layerstack.insert_anchor_point_effect(pos, _name(action.get("name"), "AI Anchor Point"))
                else:
                    pos = _mask_position(node)
                    fn = {
                        "add_color_selection": sp.layerstack.insert_color_selection_effect,
                        "add_compare_mask": sp.layerstack.insert_compare_mask_effect,
                        "add_levels": sp.layerstack.insert_levels_effect,
                    }[kind]
                    effect = fn(pos)
                if action.get("name"):
                    effect.set_name(_name(action["name"], kind))
                if action.get("parameters"):
                    current = effect.get_parameters()
                    _set_public_params(current, action["parameters"], set(action["parameters"].keys()))
                    effect.set_parameters(current)
                created.append(effect)
                results.append({"action": kind, "target": node.get_name(), "uid": effect.uid()})

            elif kind == "texture_stack_select":
                requested = str(action["stack"]).strip()
                if requested.casefold() in {"active", "current"}:
                    stack = _active_stack()
                else:
                    stack = sp.textureset.Stack.from_name(requested)
                    sp.textureset.set_active_stack(stack)
                results.append({"action": kind, "stack": stack.name()})

            elif kind in {"texture_channel_add", "texture_channel_remove", "texture_channel_edit"}:
                stack = _active_stack()
                channel = getattr(sp.textureset.ChannelType, str(action["channel"]))
                if kind == "texture_channel_add":
                    fmt = getattr(sp.textureset.ChannelFormat, str(action["format"]))
                    stack.add_channel(channel, fmt, action.get("label"))
                elif kind == "texture_channel_remove":
                    stack.remove_channel(channel)
                else:
                    fmt = getattr(sp.textureset.ChannelFormat, str(action["format"]))
                    stack.edit_channel(channel, fmt, action.get("label"))
                results.append({"action": kind, "channel": str(action["channel"])})

            elif kind == "texture_set_resolution":
                material = _active_stack().material()
                resolution = action["resolution"]
                if isinstance(resolution, int):
                    value = sp.textureset.Resolution(resolution, resolution)
                else:
                    value = sp.textureset.Resolution(int(resolution[0]), int(resolution[1]))
                material.set_resolution(value)
                results.append({"action": kind, "resolution": [value.width, value.height]})

            elif kind == "project_open":
                sp.project.open(str(action["path"]))
                results.append({"action": kind, "path": str(action["path"])})

            elif kind == "project_save":
                sp.project.save()
                results.append({"action": kind, "path": sp.project.file_path()})

            elif kind == "project_save_as":
                sp.project.save_as(str(action["path"]))
                results.append({"action": kind, "path": str(action["path"])})

            elif kind == "project_save_copy":
                sp.project.save_as_copy(str(action["path"]))
                results.append({"action": kind, "path": str(action["path"])})

            elif kind == "project_reload_mesh":
                path = str(action["path"])
                settings = sp.project.MeshReloadingSettings(import_cameras=True, preserve_strokes=True)
                callback = lambda status: None
                sp.project.reload_mesh(path, settings, callback)
                results.append({"action": kind, "path": path, "status": "started"})

            elif kind in {"display_environment", "display_color_lut"}:
                resource = sp.resource.ResourceID.from_url(str(action["resource"])) if "://" in str(action["resource"]) else sp.resource.search(str(action["resource"]))[0].identifier()
                if kind == "display_environment":
                    sp.display.set_environment_resource(resource)
                else:
                    sp.display.set_color_lut_resource(resource)
                results.append({"action": kind, "resource": str(action["resource"])})

            elif kind == "display_tone_mapping":
                mode = getattr(sp.display.ToneMappingFunction, str(action["mode"]))
                sp.display.set_tone_mapping(mode)
                results.append({"action": kind, "mode": str(action["mode"])})

            elif kind == "resource_import_project":
                usage = getattr(sp.resource.Usage, str(action["usage"]))
                resource = sp.resource.import_project_resource(str(action["path"]), usage, action.get("name"), action.get("group"))
                results.append({"action": kind, "resource": resource.gui_name()})

            elif kind == "resource_search":
                resources = sp.resource.search(str(action["query"]))
                results.append({"action": kind, "count": len(resources), "resources": [
                    {"name": r.gui_name(), "id": r.identifier().url()} for r in resources[:50]
                ]})

            elif kind == "resource_project_list":
                resources = sp.resource.list_project_resources()
                results.append({"action": kind, "count": len(resources), "resources": [
                    {"name": r.gui_name(), "id": r.identifier().url()} for r in resources[:100]
                ]})

            elif kind == "bake_highpoly":
                from core.qt_compat import qt_modules
                QtCore, _QtGui, _QtWidgets = qt_modules()
                highpoly = QtCore.QUrl.fromLocalFile(str(action["path"])).toString()
                params = sp.baking.BakingParameters.from_texture_set(_active_stack().material())
                common = params.common()
                sp.baking.BakingParameters.set({common["HipolyMesh"]: highpoly})
                results.append({"action": kind, "path": str(action["path"])})

            elif kind == "bake_start":
                sp.baking.bake_selected_textures_async()
                results.append({"action": kind, "status": "started"})

            elif kind == "export_mesh":
                option_name = str(action.get("option") or "BaseMesh")
                option = getattr(sp.export.MeshExportOption, option_name)
                result = sp.export.export_mesh(str(action["path"]), option)
                results.append({"action": kind, "path": str(action["path"]), "option": option_name, "result": _serializable(result)})

            elif kind == "save_smart_material":
                node = created[-1] if created else selected_nodes()[0]
                if not isinstance(node, sp.layerstack.GroupLayerNode):
                    raise ActionError("save_smart_material 需要 Group Layer。")
                resource = sp.layerstack.create_smart_material(node, str(action["name"]))
                path = str(action["path"])
                sp.layerstack.export_as_smart_material(node, str(action["name"]), path)
                results.append({"action": kind, "resource": resource.identifier().url(), "path": path})

            elif kind == "save_smart_mask":
                node = created[-1] if created else selected_nodes()[0]
                if not isinstance(node, sp.layerstack.GroupLayerNode):
                    raise ActionError("save_smart_mask 需要 Group Layer。")
                resource = sp.layerstack.create_smart_mask(node, str(action["name"]))
                path = str(action["path"])
                sp.layerstack.export_as_smart_mask(node, str(action["name"]), path)
                results.append({"action": kind, "resource": resource.identifier().url(), "path": path})

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
                    "source_mode": getattr(getattr(node, "source_mode", None), "name", str(getattr(node, "source_mode", None))),
                })

    # Select created nodes after the modification scope so the result is
    # visible immediately in Painter's Layer Stack.
    if created:
        try:
            sp.layerstack.set_selected_nodes(created)
            results.append({
                "action": "select_last_created",
                "selected_uids": [_serializable(node.uid()) for node in created],
                "api": "substance_painter.layerstack.set_selected_nodes",
            })
        except Exception as exc:
            results.append({"action": "select_last_created", "warning": str(exc)})
    return {"success": True, "results": results}
