from __future__ import annotations

import substance_painter as sp

SUPPORTED_ACTIONS = {
    "create_fill_layer",
    "create_paint_layer",
    "create_group",
    "add_mask",
    "set_opacity",
}


class ActionError(RuntimeError):
    pass


def _name(value, default):
    value = str(value or "").strip()
    if not value:
        return default
    return value[:120]


def _active_stack():
    if not sp.project.is_open():
        raise ActionError("没有打开 Painter 项目。")
    return sp.textureset.get_active_stack()


def execute_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise ActionError("执行计划格式无效。")
    if len(plan["actions"]) > 20:
        raise ActionError("单次最多执行 20 个动作。")

    created = []
    results = []
    with sp.layerstack.ScopedModification("SP AI Assistant"):
        for index, action in enumerate(plan["actions"]):
            if not isinstance(action, dict):
                raise ActionError(f"第 {index + 1} 个动作不是对象。")
            kind = action.get("action")
            if kind not in SUPPORTED_ACTIONS:
                raise ActionError(f"不允许执行动作: {kind}")

            if kind == "create_fill_layer":
                stack = _active_stack()
                pos = sp.layerstack.InsertPosition.from_textureset_stack(stack)
                node = sp.layerstack.insert_fill(pos)
                node.set_name(_name(action.get("name"), "AI Fill Layer"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "create_paint_layer":
                stack = _active_stack()
                pos = sp.layerstack.InsertPosition.from_textureset_stack(stack)
                node = sp.layerstack.insert_paint(pos)
                node.set_name(_name(action.get("name"), "AI Paint Layer"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "create_group":
                stack = _active_stack()
                pos = sp.layerstack.InsertPosition.from_textureset_stack(stack)
                node = sp.layerstack.insert_group(pos)
                node.set_name(_name(action.get("name"), "AI Group"))
                created.append(node)
                results.append({"action": kind, "name": node.get_name(), "uid": node.uid()})

            elif kind == "add_mask":
                target = action.get("target", "last_created")
                if target != "last_created" or not created:
                    raise ActionError("add_mask 当前只允许 target=last_created。")
                background = str(action.get("background", "black")).lower()
                bg = (
                    sp.layerstack.MaskBackground.White
                    if background == "white"
                    else sp.layerstack.MaskBackground.Black
                )
                node = created[-1]
                if not node.has_mask():
                    node.add_mask(bg)
                results.append({"action": kind, "target": node.get_name(), "background": background})

            elif kind == "set_opacity":
                target = action.get("target", "last_created")
                if target != "last_created" or not created:
                    raise ActionError("set_opacity 当前只允许 target=last_created。")
                opacity = float(action.get("opacity", 1.0))
                if not 0.0 <= opacity <= 1.0:
                    raise ActionError("opacity 必须在 0 到 1 之间。")
                node = created[-1]
                node.set_opacity(opacity)
                results.append({"action": kind, "target": node.get_name(), "opacity": opacity})

    return {"success": True, "results": results}
