from __future__ import annotations

import substance_painter as sp


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
        result["layers"] = [_node_info(n) for n in sp.layerstack.get_root_layer_nodes(stack)]
    except Exception as exc:
        result["active_stack_error"] = f"{type(exc).__name__}: {exc}"
        result["layers"] = []
    return result


def prompt_context() -> str:
    import json
    return json.dumps(snapshot(), ensure_ascii=False, indent=2)
