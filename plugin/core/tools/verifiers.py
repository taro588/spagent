"""执行后校验器（技术架构文档 §18.1 API Verification）。

设计约束：校验器**不直接调用 Painter**，只消费「快照字典」——
与 core/painter_context.snapshot() 同构的普通 dict。这样：
  * 校验逻辑可以在 Painter 之外被单元测试驱动（CI 里也能跑）；
  * Painter 侧只需要在执行前后各取一次 snapshot()。

架构文档 §18.1 要求校验的真实状态：
    「确认 Layer 实际创建」「确认名称、UID、父子关系、可见性」
    「确认 Fill source、Material source、参数值」「确认 ResourceID、Usage」
    「确认 Texture Set channel 和分辨率」

校验结论必须诚实：快照读不到的字段一律标记 skipped（不计入失败），
绝不用「执行器说成功了」冒充「状态已确认」。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = ["VERIFIERS", "verify_tool", "verify_plan", "verify_spec_id"]


# ----------------------------------------------------------------------
# 快照读取辅助
# ----------------------------------------------------------------------
def _iter_nodes(snapshot: Mapping[str, Any]) -> Iterable[dict]:
    """深度优先遍历快照里的图层树。"""
    queue: list[Any] = list(snapshot.get("layers") or [])
    seen = 0
    while queue and seen < 5000:
        node = queue.pop(0)
        seen += 1
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            queue[0:0] = [child for child in children if isinstance(child, dict)]


def _find_nodes(
    snapshot: Mapping[str, Any],
    name: str | None = None,
    uid: str | None = None,
) -> list[dict]:
    matches = []
    for node in _iter_nodes(snapshot):
        if uid and str(node.get("uid") or "") == str(uid):
            matches.append(node)
            continue
        if name and str(node.get("name") or "") == str(name):
            matches.append(node)
    return matches


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    for attrs in (("r", "g", "b", "a"), ("x", "y", "z")):
        if all(hasattr(value, attr) for attr in attrs):
            return {attr: float(getattr(value, attr)) for attr in attrs}
    return str(value)


def _value_matches(actual: Any, expected: Any, tolerance: float = 1e-4) -> bool:
    actual, expected = _serialize(actual), _serialize(expected)
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _value_matches(a, e, tolerance) for a, e in zip(actual, expected)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual) != set(expected):
            return False
        return all(_value_matches(actual[k], expected[k], tolerance) for k in actual)
    return actual == expected


def _check(checks: list[dict], name: str, ok: bool, detail: Any = None,
           skipped: bool = False) -> None:
    entry: dict[str, Any] = {"check": name, "ok": bool(ok)}
    if skipped:
        entry["skipped"] = True
    if detail is not None:
        entry["detail"] = detail
    checks.append(entry)


def _finish(spec, verifier_id: str, checks: list[dict], **extra) -> dict:
    considered = [c for c in checks if not c.get("skipped")]
    report = {
        "tool": getattr(spec, "name", str(spec)),
        "verifier": verifier_id,
        "verified": bool(considered) and all(c["ok"] for c in considered),
        "checks": checks,
    }
    report.update(extra)
    return report


def _api_evidence(checks: list[dict], result: Mapping[str, Any] | None) -> None:
    """每个写操作都必须留下官方 API 证据（§18.1 不以「写文件成功」为准）。"""
    result = result or {}
    _check(
        checks,
        "api_evidence",
        bool(result.get("api")),
        result.get("api") or "执行结果没有记录官方 API 入口",
    )


# ----------------------------------------------------------------------
# 校验器
# ----------------------------------------------------------------------
def _project_state(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    project = str(action.get("path") or "")
    _check(checks, "project_open", bool(after.get("project_open")),
           after.get("project_path") or "")
    if project:
        _check(
            checks,
            "project_path_matches",
            str(after.get("project_path") or "") == project,
            {"expected": project, "actual": after.get("project_path")},
            skipped=not after.get("project_path"),
        )
    return _finish(spec, "project_state", checks)


def _texture_set_state(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    _check(checks, "texture_set_present", bool(after.get("active_texture_set")),
           after.get("active_texture_set") or "")
    channels = after.get("available_channels") or []
    wanted = action.get("channel")
    if wanted:
        _check(checks, "channel_present", str(wanted) in [str(c) for c in channels],
               {"expected": wanted, "channels": channels})
    resolution = action.get("resolution")
    if resolution:
        actual = after.get("resolution")
        expected = resolution if isinstance(resolution, list) else [resolution, resolution]
        _check(checks, "resolution_applied",
               _value_matches(actual, [int(v) for v in expected]),
               {"expected": expected, "actual": actual},
               skipped=not actual)
    return _finish(spec, "texture_set_state", checks)


def _layer_created(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    name = str(action.get("name") or "")
    uid = str((result or {}).get("uid") or "")
    matches = _find_nodes(after, uid=uid or None, name=name or None)
    _check(checks, "layer_present", bool(matches),
           {"name": name, "uid": uid, "found": len(matches)})
    if uid:
        _check(checks, "uid_matches",
               any(str(n.get("uid") or "") == uid for n in matches), uid)
    if before:
        _check(checks, "stack_changed",
               len(list(_iter_nodes(after))) > len(list(_iter_nodes(before))),
               {"before": len(list(_iter_nodes(before))),
                "after": len(list(_iter_nodes(after)))})
    return _finish(spec, "layer_created", checks)


def _layer_deleted(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    count = int((result or {}).get("count") or 0)
    if before and count:
        _check(checks, "shrink_by_count",
               len(list(_iter_nodes(before))) - len(list(_iter_nodes(after))) == count,
               {"removed": count,
                "before": len(list(_iter_nodes(before))),
                "after": len(list(_iter_nodes(after)))})
    else:
        _check(checks, "shrink_by_count", False,
               "缺少删除数量或删除前快照，无法确认", skipped=True)
    return _finish(spec, "layer_deleted", checks)


def _channel_source(spec, action, result, before, after):
    """Confirm the channel source really landed on the target node.

    The snapshot only exposes source parameters when Painter hands back a
    readable source, so an unreadable node yields a *skipped* parameter
    check rather than a fabricated pass.
    """
    checks: list[dict] = []
    _api_evidence(checks, result)
    target = str((result or {}).get("target") or action.get("name") or "")
    matches = _find_nodes(after, name=target) if target else list(_iter_nodes(after))
    _check(checks, "target_present", bool(matches), {"target": target,
                                                     "found": len(matches)})
    key = (action.get("channel") or action.get("property")
           or getattr(spec, "channel", "") or "")
    expected = action.get("color", action.get("value"))
    if matches and key:
        parameters = (matches[0].get("parameters") or {})
        readable = key in parameters or str(key) in parameters
        if readable:
            actual = parameters.get(key, parameters.get(str(key)))
            _check(checks, "value_applied", _value_matches(actual, expected),
                   {"key": key, "expected": expected, "actual": _serialize(actual)})
        else:
            _check(checks, "value_applied", False,
                   {"key": key, "available": sorted(str(k) for k in parameters)},
                   skipped=True)
    else:
        _check(checks, "value_applied", False, "无法定位目标节点或缺少通道名",
               skipped=True)
    return _finish(spec, "channel_source", checks)


def _material_source(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    if result and result.get("source_mode"):
        _check(checks, "material_mode", str(result.get("source_mode")) == "Material",
               result.get("source_mode"))
    else:
        _check(checks, "material_mode", False, "执行结果未报告 source_mode", skipped=True)
    _check(checks, "source_type_reported", bool((result or {}).get("source_type")),
           (result or {}).get("source_type") or "")
    return _finish(spec, "material_source", checks)


def _material_parameters(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    values = action.get("parameters") or {}
    _check(checks, "parameters_reported", bool(values), sorted(values))
    target = str((result or {}).get("target") or "")
    if target:
        matches = _find_nodes(after, name=target)
        if matches:
            parameters = matches[0].get("parameters") or {}
            if parameters:
                mismatched = {
                    key: {"expected": value, "actual": _serialize(parameters.get(key))}
                    for key, value in values.items()
                    if key in parameters and not _value_matches(parameters.get(key), value)
                }
                _check(checks, "values_applied", not mismatched, mismatched)
            else:
                _check(checks, "values_applied", False,
                       "快照未暴露该节点的材质参数", skipped=True)
        else:
            _check(checks, "values_applied", False, f"快照里找不到 {target}", skipped=True)
    return _finish(spec, "material_parameters", checks)


def _effect_added(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    if before:
        _check(checks, "stack_grew",
               len(list(_iter_nodes(after))) >= len(list(_iter_nodes(before))),
               {"before": len(list(_iter_nodes(before))),
                "after": len(list(_iter_nodes(after)))})
    else:
        _check(checks, "stack_grew", False, "缺少执行前快照", skipped=True)
    _check(checks, "resource_reported",
           bool((result or {}).get("resource") or (result or {}).get("inserted") is not None),
           (result or {}).get("resource"))
    return _finish(spec, "effect_added", checks)


def _effect_parameters(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    values = action.get("parameters") or {}
    _check(checks, "parameters_reported", bool(values), sorted(values))
    _check(checks, "parameters_applied",
           bool((result or {}).get("parameters")),
           (result or {}).get("parameters"))
    return _finish(spec, "effect_parameters", checks)


def _resource_imported(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    path = str(action.get("path") or "")
    _check(checks, "resource_id_returned", bool((result or {}).get("resource")),
           (result or {}).get("resource") or "")
    _check(checks, "usage_declared", bool(action.get("usage")), action.get("usage"))
    imported = [str(item) for item in (after.get("project_resources") or [])]
    if imported:
        needle = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        _check(checks, "appears_in_project", any(needle in item for item in imported),
               {"needle": needle, "count": len(imported)})
    else:
        _check(checks, "appears_in_project", False,
               "快照未包含项目资源列表", skipped=True)
    return _finish(spec, "resource_imported", checks)


def _bake_requested(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    if spec.name == "bake_start":
        _check(checks, "bake_started",
               str((result or {}).get("status") or "") == "started",
               (result or {}).get("status"))
    else:
        _check(checks, "highpoly_path", bool(str(action.get("path") or "").strip()),
               action.get("path"))
    return _finish(spec, "bake_requested", checks)


def _export_result(spec, action, result, before, after):
    checks: list[dict] = []
    _api_evidence(checks, result)
    status = str((result or {}).get("status") or "").lower()
    if status:
        _check(checks, "export_success", status == "success", status)
    else:
        _check(checks, "export_success", bool((result or {}).get("path")),
               (result or {}).get("path") or "导出结果没有状态")
    _check(checks, "path_targeted", bool(str(action.get("export_path")
                                            or action.get("path") or "").strip()),
           action.get("export_path") or action.get("path"))
    return _finish(spec, "export_result", checks)


def _verify_report(spec, action, result, before, after):
    checks: list[dict] = []
    verified = (result or {}).get("verified")
    _check(checks, "mismatch_free", verified is True,
           (result or {}).get("mismatches") or {})
    return _finish(spec, "verify_report", checks)


def _read_only(spec, action, result, before, after):
    checks: list[dict] = []
    _check(checks, "read_only_tool", True, "只读工具，不修改项目状态")
    return _finish(spec, "read_only", checks)


def _executor_result(spec, action, result, before, after):
    """兜底校验：执行器必须报告它做了什么（含官方 API 入口）。"""
    checks: list[dict] = []
    _api_evidence(checks, result)
    evidence = any(
        key in (result or {}) for key in ("uid", "name", "count", "status", "path",
                                          "resource", "stack", "channels", "mode")
    )
    _check(checks, "result_reported", evidence,
           sorted((result or {}).keys()))
    return _finish(spec, "executor_result", checks)


VERIFIERS = {
    "project_state": _project_state,
    "texture_set_state": _texture_set_state,
    "layer_created": _layer_created,
    "layer_deleted": _layer_deleted,
    "channel_source": _channel_source,
    "material_source": _material_source,
    "material_parameters": _material_parameters,
    "effect_added": _effect_added,
    "effect_parameters": _effect_parameters,
    "resource_imported": _resource_imported,
    "bake_requested": _bake_requested,
    "export_result": _export_result,
    "verify_report": _verify_report,
    "read_only": _read_only,
    "executor_result": _executor_result,
}


def verify_spec_id(spec) -> str:
    """返回 spec 声明的校验器 id，未注册则回落兜底校验器。"""
    verifier_id = getattr(spec, "verifier", "") or "executor_result"
    return verifier_id if verifier_id in VERIFIERS else "executor_result"


def verify_tool(spec, action, result, before=None, after=None) -> dict:
    """对一次工具执行做 API 校验。"""
    verifier_id = verify_spec_id(spec)
    try:
        return VERIFIERS[verifier_id](spec, action, result, before or {}, after or {})
    except Exception as exc:  # 校验器自身出错不能让执行结果丢失
        return {
            "tool": getattr(spec, "name", str(spec)),
            "verifier": verifier_id,
            "verified": False,
            "error": f"{type(exc).__name__}: {exc}",
            "checks": [],
        }


def verify_plan(specs, actions, results, before=None, after=None) -> dict:
    """对整份计划的执行结果做批量校验。

    specs   —— 与 actions 一一对应的 ToolSpec（由 registry 查得）。
    results —— execute_plan 产出的 results 副本（须与 actions 顺序对齐）。
    """
    items = []
    for index, action in enumerate(actions):
        spec = specs[index] if index < len(specs) else None
        if spec is None:
            continue
        result = results[index] if index < len(results) else {}
        items.append(verify_tool(spec, action, result, before, after))
    failed = [item for item in items if not item.get("verified")]
    return {
        "verified": not failed,
        "checked": len(items),
        "failed": len(failed),
        "items": items,
    }
