from __future__ import annotations

import substance_painter as sp


def _safe_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    for attrs in (("r", "g", "b", "a"), ("x", "y", "z")):
        if all(hasattr(value, a) for a in attrs):
            return {a: float(getattr(value, a)) for a in attrs}
    return str(value)


def _source_info(node):
    if not hasattr(node, "get_material_source"):
        return {}
    try:
        source = node.get_material_source()
        params = source.get_parameters()
        return {
            "source_type": type(source).__name__,
            "parameters": {str(k): _safe_value(v) for k, v in params.items()},
        }
    except Exception:
        return {}


def _effect_info(node):
    if not hasattr(node, "get_parameters"):
        return {}
    try:
        params = node.get_parameters()
        if hasattr(params, "__dict__"):
            values = {k: _safe_value(v) for k, v in vars(params).items()}
        else:
            values = _safe_value(params)
        return {"parameters": values}
    except Exception:
        return {}


def _node_info(node):
    try:
        node_type = node.get_type()
        node_type = getattr(node_type, "name", str(node_type))
    except Exception:
        node_type = "unknown"

    try:
        name = node.get_name()
    except Exception:
        name = ""

    item = {"uid": node.uid(), "name": name, "type": node_type}
    try:
        mode = getattr(node, "source_mode", None)
        item["source_mode"] = getattr(mode, "name", str(mode)) if mode is not None else None
    except Exception:
        item["source_mode"] = None
    try:
        channels = getattr(node, "active_channels", None)
        if channels is not None:
            item["active_channels"] = [getattr(ch, "name", str(ch)) for ch in channels]
    except Exception:
        item["active_channels"] = []
    item.update(_source_info(node))
    if not item.get("parameters"):
        item.update(_effect_info(node))
    if hasattr(node, "sub_layers"):
        try:
            item["children"] = [_node_info(child) for child in node.sub_layers()]
        except Exception:
            item["children"] = []
    return item


def snapshot() -> dict:
    result = {
        "painter_version": ".".join(map(str, sp.application.version_info())),
        "project_open": bool(sp.project.is_open()),
    }

    if not result["project_open"]:
        result["message"] = "当前没有打开 Substance 3D Painter 项目。"
        return result

    try:
        result["project_name"] = sp.project.name()
    except Exception:
        result["project_name"] = ""

    try:
        result["project_path"] = sp.project.file_path()
    except Exception:
        result["project_path"] = ""

    try:
        stack = sp.textureset.get_active_stack()
        result["active_stack"] = stack.name()
        material = stack.material()
        result["active_texture_set"] = material.name

        resolution = material.get_resolution()
        result["resolution"] = [resolution.width, resolution.height]
        try:
            result["available_channels"] = [
                getattr(channel, "name", str(channel))
                for channel in stack.all_channels().keys()
            ]
        except Exception:
            result["available_channels"] = []
        result["mesh_map_workflow"] = {
            "bake_api_available": hasattr(sp.baking, "bake_selected_textures_async"),
            "bake_is_async": True,
            "recommended_mesh_maps": ["Normal", "WorldSpaceNormal", "AO", "Curvature", "Position", "Thickness"],
        }

        result["layers"] = [
            _node_info(node)
            for node in sp.layerstack.get_root_layer_nodes(stack)
        ]

        try:
            result["selected_nodes"] = [
                _node_info(node)
                for node in sp.layerstack.get_selected_nodes(stack)
            ]
        except Exception:
            result["selected_nodes"] = []

        try:
            result["export_presets"] = [
                preset.name
                for preset in sp.export.list_predefined_export_presets()
            ]
        except Exception:
            result["export_presets"] = []

    except Exception as exc:
        result["active_stack_error"] = f"{type(exc).__name__}: {exc}"
        result["layers"] = []
        result["selected_nodes"] = []
        result["export_presets"] = []

    return result


def prompt_context() -> str:
    import json
    return json.dumps(snapshot(), ensure_ascii=False, indent=2)
