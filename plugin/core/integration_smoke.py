"""真实项目端到端集成冒烟（技术架构文档 §28-5）。

为什么必须有它
--------------
`tests/test_actions_execute.py` 用「假 Painter」验证执行管线，但假 Painter 永远
回答不了唯一真问题：**官方 API 在这台机器的这个版本上，到底是不是这个签名**。
§28 第 5 条要求的是在真实 Painter 里跑一遍「计划 → 官方 API 执行 → 快照校验」，
并把结论留成可归档的证据。

两级设计
--------
* ``probe``  —— 只读。读 Painter / 官方 API 版本、跑全部能力探测、逐条核对工具
  声明的官方 API 路径是否真实可解析。不改任何 Painter 状态，任何时候都能跑。
* ``project`` —— 会临时新建一个工程（用 Painter 自带的测试网格），把核心计划真
  跑一遍，然后**关闭该工程且不保存**。

安全约束（不可协商）
--------------------
1. 当前工程有**未保存改动**时直接拒绝运行并说明原因——绝不静默丢弃用户工作。
2. 需要关闭一个已保存的工程时，必须先经过调用方传入的 ``confirm_close()``
   （UI 层弹确认框）；没有确认渠道就拒绝。
3. 冒烟自己创建的工程结束时一律 ``close()`` 不保存；``keep_project=True`` 才留下。

失败可定位
----------
整批失败不能只说一句「计划失败」。核心批次失败时本模块会自动退化为**单步隔离**：
为每个核心步骤单独跑一个「建临时 Fill Layer + 该步骤」的两动作计划，把责任精确
定位到某一个工具上。

报告
----
结果落盘为 JSON（``%LOCALAPPDATA%\\SP AI Assistant\\integration_smoke\\smoke-*.json``
与 ``latest.json``），``tools/check_smoke_report.py`` 据此做门禁。
"""

from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime

import substance_painter as sp

from core.painter_api import AdapterError, PainterUnavailable, default_api
from core.tools import declared_api_paths

__all__ = [
    "LEVEL_PROBE",
    "LEVEL_PROJECT",
    "REPORT_VERSION",
    "api_surface",
    "build_plan",
    "core_actions",
    "environment",
    "optional_actions",
    "report_directory",
    "resolve_mesh",
    "run",
    "summarize",
    "write_report",
]

REPORT_VERSION = 1
LEVEL_PROBE = "probe"
LEVEL_PROJECT = "project"

CORE = "core"
OPTIONAL = "optional"
ALL = "all"

SMOKE_LAYER_NAME = "SP AI Smoke Fill"
SMOKE_GROUP_NAME = "SP AI Smoke Group"
SMOKE_MATERIAL_NAME = "SP AI Smoke Material"
PROBE_LAYER_PREFIX = "SP AI Smoke Probe"

MESH_ENV_VAR = "SPAI_SMOKE_MESH"
#: Painter 自带自动化测试资源里的网格（运行时定位，不随仓库分发——那是 Adobe 的资产）
BUILTIN_MESHES = ("cubes_1_ts.fbx", "4cubes3udims.fbx", "cube_normal_map.glb")

#: 每个步骤在验证什么。报告里逐条带上，出问题时不用靠猜。
STEP_NOTES = {
    "create_fill_layer": "layerstack.insert_fill：真实创建一个 Fill Layer 并确认它进了图层树",
    "set_base_color": "SourceEditorMixin.set_source：BaseColor 语义化写入（§28 点名的核心操作）",
    "set_roughness": "SourceEditorMixin.set_source：Roughness 单通道写入",
    "set_metallic": "SourceEditorMixin.set_source：Metallic 单通道写入",
    "add_levels": "layerstack.insert_levels_effect + LevelsEffectNode.set_parameters（原声明指向不存在的 EffectNode）",
    "set_geometry_mask": "几何遮罩三调用映射（官方没有 set_geometry_mask 这个方法名）",
    "create_group": "layerstack.insert_group：分组容器",
    "save_smart_material": "layerstack.create_smart_material（官方没有 export_as_smart_material）",
}


# ----------------------------------------------------------------------
# 计划（模型侧同一份构造逻辑，测试直接断言它合法）
# ----------------------------------------------------------------------
def core_actions() -> list:
    """核心步骤：任一失败即整体失败。全部只用官方 0.3.x 确认存在的接口。

    顺序有讲究：``set_geometry_mask`` 必须排在 ``add_levels`` 之前——这些工具
    都作用于「最近创建的节点」，几何遮罩要落在 Fill Layer 上（效果节点是后来
    才插进来的）。
    """
    return [
        {"action": "create_fill_layer", "name": SMOKE_LAYER_NAME},
        {"action": "set_base_color", "value": [0.15, 0.45, 0.75, 1.0]},
        {"action": "set_roughness", "value": 0.35},
        {"action": "set_metallic", "value": 0.0},
        {"action": "set_geometry_mask", "parameters": {"type": "Mesh"}},
        {"action": "add_levels"},
    ]


def optional_actions() -> list:
    """进阶步骤：依赖工程内资源写入，失败只降级为 partial，不算冒烟失败。"""
    return [
        {"action": "create_group", "name": SMOKE_GROUP_NAME},
        {"action": "save_smart_material", "name": SMOKE_MATERIAL_NAME},
    ]


def build_plan(part: str = CORE) -> dict:
    """构造冒烟计划。

    ``operation_domain`` 显式声明为 ``material``（§13）：冒烟计划里没有烘焙、
    没有导出，所以它不该、也不能触发独占域。
    """
    if part == CORE:
        actions = core_actions()
    elif part == OPTIONAL:
        actions = optional_actions()
    elif part == ALL:
        actions = core_actions() + optional_actions()
    else:
        raise ValueError("未知的计划分段: %r" % (part,))
    return {"operation_domain": "material", "actions": actions}


# ----------------------------------------------------------------------
# 环境与 API 面（L0）
# ----------------------------------------------------------------------
def environment(plugin_version: str = "", host_version: str = "") -> dict:
    info = {
        "plugin_version": plugin_version,
        "host_version": host_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        info.update(default_api().runtime_info().as_dict())
    except (PainterUnavailable, AdapterError) as exc:
        info["api_error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def api_surface() -> dict:
    """L0：官方能力探测 + 工具声明路径的真实解析结果。"""
    surface = {
        "capabilities": {"total": 0, "supported": 0, "executed": 0, "unsupported": []},
        "declarations": {"tools": 0, "paths": 0, "ok": 0, "mismatched": [], "unknown": []},
    }
    api = default_api()

    try:
        report = api.capability_report()
        items = report.get("capabilities") or {}
        surface["verified_against"] = report.get("verified_against")
        surface["capabilities"]["total"] = len(items)
        surface["capabilities"]["supported"] = sum(
            1 for item in items.values() if item.get("supported"))
        # executed = 不只是探测到、而是在真机上完整执行过（painter_api 里
        # 标了 executed_on 的那些）。数字小于 supported 是正常的。
        surface["capabilities"]["executed"] = sum(
            1 for item in items.values() if item.get("executed_on"))
        surface["capabilities"]["unsupported"] = sorted(
            name for name, item in items.items() if not item.get("supported"))
    except (PainterUnavailable, AdapterError) as exc:
        surface["capabilities"]["error"] = "%s: %s" % (type(exc).__name__, exc)
        return surface

    declared = declared_api_paths()
    paths = sorted({path for entries in declared.values() for path in entries})
    surface["declarations"]["tools"] = len(declared)
    surface["declarations"]["paths"] = len(paths)
    try:
        results = api.verify_declared_paths(paths)
    except (PainterUnavailable, AdapterError) as exc:
        surface["declarations"]["error"] = "%s: %s" % (type(exc).__name__, exc)
        return surface
    for item in results:
        if item.get("ok"):
            surface["declarations"]["ok"] += 1
        else:
            surface["declarations"]["mismatched"].append(
                {"path": item.get("path"), "detail": item.get("detail")})
    return surface


# ----------------------------------------------------------------------
# 临时工程（L1）
# ----------------------------------------------------------------------
def mesh_candidates(explicit: str | None = None) -> list:
    candidates = []
    if explicit:
        candidates.append(("显式指定", explicit))
    env_path = os.environ.get(MESH_ENV_VAR)
    if env_path:
        candidates.append((MESH_ENV_VAR, env_path))
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(sp.__file__)))
        for name in BUILTIN_MESHES:
            candidates.append(("Painter 自带测试资源",
                               os.path.join(root, "automated_tests", "resources", name)))
    except Exception:
        pass
    return candidates


def resolve_mesh(explicit: str | None = None):
    """返回 ``(mesh_path|None, 尝试记录)``。找不到时把候选全列出来，便于排查。"""
    tried = []
    for source, path in mesh_candidates(explicit):
        exists = bool(path) and os.path.isfile(path)
        tried.append({"source": source, "path": path, "exists": exists})
        if exists:
            return path, tried
    return None, tried


def _wait_project_ready(timeout: float = 90.0):
    deadline = time.time() + timeout
    detail = "等待工程就绪超时"
    while time.time() < deadline:
        try:
            if sp.project.is_open() and not sp.project.is_busy():
                stack = sp.textureset.get_active_stack()
                if stack is not None:
                    return True, stack.name()
        except Exception as exc:
            detail = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(0.25)
    return False, detail


def _close_project(timeout: float = 60.0):
    try:
        sp.project.close()
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not sp.project.is_open():
                return True, ""
        except Exception:
            pass
        time.sleep(0.2)
    return False, "关闭工程超时"


def _summarize_verification(verification: dict) -> dict:
    items = verification.get("items") or []
    checks = [check for item in items for check in (item.get("checks") or [])]
    return {
        "verified": bool(verification.get("verified")),
        "checked": int(verification.get("checked") or 0),
        "failed": int(verification.get("failed") or 0),
        "checks": len(checks),
        "skipped_checks": sum(1 for check in checks if check.get("skipped")),
        "failed_checks": [
            {"tool": item.get("tool"), "check": check.get("check"),
             "detail": check.get("detail")}
            for item in items
            for check in (item.get("checks") or [])
            if not check.get("ok") and not check.get("skipped")
        ],
    }


def _execute_batch(plan: dict, label: str) -> dict:
    from core.actions import execute_plan

    entry = {
        "label": label,
        "actions": [action.get("action") for action in plan["actions"]],
        "notes": [STEP_NOTES.get(action.get("action"), "") for action in plan["actions"]],
        "ok": False,
        "error": "",
        "ms": 0,
        "results": [],
        "verification": {},
    }
    started = time.time()
    try:
        outcome = execute_plan(plan)
    except Exception as exc:
        entry["error"] = "%s: %s" % (type(exc).__name__, exc)
    else:
        entry["ok"] = True
        entry["scope_degraded"] = bool(outcome.get("scope_degraded"))
        entry["results"] = outcome.get("results") or []
        entry["verification"] = _summarize_verification(outcome.get("verification") or {})
    entry["ms"] = int((time.time() - started) * 1000)
    return entry


def _isolate_failures(batches: list) -> list:
    """整批失败 → 每个核心步骤单独跑一遍，把责任定位到具体工具。

    单步计划都自带一个临时 Fill Layer，这样 ``created[-1]`` 的上下文是成立的，
    报出来的失败就是该工具自己的失败。
    """
    isolated = []
    for index, action in enumerate(core_actions(), 1):
        name = "%s %d" % (PROBE_LAYER_PREFIX, index)
        probe = {
            "operation_domain": "material",
            "actions": [{"action": "create_fill_layer", "name": name}, dict(action)],
        }
        entry = _execute_batch(probe, "隔离 · %s" % action["action"])
        entry["isolated_tool"] = action["action"]
        isolated.append(entry)
    batches.extend(isolated)
    return isolated


def _run_project_level(report: dict, mesh_path, confirm_close, keep_project: bool) -> dict:
    project = {"ran": False, "mesh": mesh_path, "mesh_tried": [], "closed": False}

    # 顺序是刻意的：先做安全判定（不碰任何东西），再解析网格，最后才关旧建新。
    # 这样「没有网格可用」绝不会先把用户的工程关掉——任何一步失败都不留下半个动作。
    if sp.project.is_open():
        try:
            needs_saving = bool(sp.project.needs_saving())
        except Exception as exc:
            project["reason"] = "project_state_unreadable"
            project["error"] = "%s: %s" % (type(exc).__name__, exc)
            return project
        if needs_saving:
            project["reason"] = "unsaved_project"
            report["notes"].append(
                "当前工程有未保存改动，冒烟拒绝运行——请先保存或关闭工程再试。")
            return project
        if confirm_close is None:
            project["reason"] = "close_not_confirmed"
            report["notes"].append("需要先关闭已打开的工程，但没有拿到确认渠道。")
            return project
        if not confirm_close():
            project["reason"] = "declined"
            return project

    resolved, tried = resolve_mesh(mesh_path)
    project["mesh"] = resolved
    project["mesh_tried"] = tried
    if not resolved:
        project["reason"] = "no_mesh"
        report["notes"].append(
            "找不到可用的测试网格：设 %s 指向任意 .fbx/.obj 后重跑。" % MESH_ENV_VAR)
        return project

    if sp.project.is_open():
        ok, error = _close_project()
        project["closed_previous"] = ok
        if not ok:
            project["reason"] = "close_failed"
            project["error"] = error
            return project

    try:
        sp.project.create(mesh_file_path=resolved)
    except Exception as exc:
        project["reason"] = "create_failed"
        project["error"] = "%s: %s" % (type(exc).__name__, exc)
        return project

    ready, detail = _wait_project_ready()
    project["ran"] = True
    project["ready"] = ready
    if not ready:
        project["reason"] = "not_ready"
        project["error"] = detail
        _finish_project(project, keep_project)
        return project

    core = _execute_batch(build_plan(CORE), "核心批次")
    report["batches"].append(core)
    project["core_ok"] = core["ok"]
    if not core["ok"]:
        _isolate_failures(report["batches"])

    optional = _execute_batch(build_plan(OPTIONAL), "进阶批次")
    report["batches"].append(optional)
    project["optional_ok"] = optional["ok"]

    _finish_project(project, keep_project)
    return project


def _finish_project(project: dict, keep_project: bool) -> None:
    if keep_project:
        project["reason"] = project.get("reason") or "kept"
        return
    ok, error = _close_project()
    project["closed"] = ok
    if not ok:
        project["close_error"] = error


# ----------------------------------------------------------------------
# 结论与报告
# ----------------------------------------------------------------------
def _outcome(report: dict) -> str:
    declarations = report["api_surface"]["declarations"]
    capabilities = report["api_surface"]["capabilities"]
    if capabilities.get("error") or declarations.get("error"):
        return "fail"
    if declarations.get("mismatched"):
        return "fail"
    if report["level"] == LEVEL_PROBE:
        return "pass"

    project = report["project"]
    if not project.get("ran") or not project.get("ready"):
        return "partial" if not project.get("error") else "fail"
    if not project.get("core_ok"):
        return "fail"
    if not project.get("optional_ok"):
        return "partial"
    return "pass"


def run(level: str = LEVEL_PROBE, plugin_version: str = "", host_version: str = "",
        mesh_path: str | None = None, confirm_close=None, keep_project: bool = False) -> dict:
    """跑一次冒烟并返回报告字典（不落盘；落盘用 :func:`write_report`）。

    Args:
        level: ``probe``（只读）或 ``project``（临时工程）。
        confirm_close: 需要关闭用户已打开（且已保存）的工程时调用的确认回调，
            返回 ``True`` 才继续。``probe`` 级别不会用到。
        keep_project: 冒烟结束后保留它创建的工程（默认关闭且不保存）。
    """
    started = datetime.now()
    report = {
        "report_version": REPORT_VERSION,
        "level": level,
        "started_at": started.isoformat(timespec="seconds"),
        "environment": environment(plugin_version, host_version),
        "api_surface": api_surface(),
        "project": {"ran": False, "reason": "未运行"},
        "batches": [],
        "notes": [],
    }

    if level == LEVEL_PROJECT:
        report["project"] = _run_project_level(report, mesh_path, confirm_close, keep_project)

    finished = datetime.now()
    report["finished_at"] = finished.isoformat(timespec="seconds")
    report["duration_seconds"] = round((finished - started).total_seconds(), 2)
    report["outcome"] = _outcome(report)
    return report


def summarize(report: dict) -> str:
    """给用户看的多行摘要（消息框 / 控制台通用）。"""
    env = report.get("environment") or {}
    surface = report.get("api_surface") or {}
    caps = surface.get("capabilities") or {}
    decl = surface.get("declarations") or {}
    lines = [
        "结论：%s（%s 级，耗时 %ss）" % (
            {"pass": "通过", "partial": "部分通过", "fail": "未通过"}.get(
                report.get("outcome"), report.get("outcome")),
            report.get("level"), report.get("duration_seconds")),
        "Painter %s / 官方 API %s / 插件 %s" % (
            env.get("painter_version", "?"), env.get("python_api_version", "?"),
            env.get("plugin_version") or "?"),
        "能力：%s/%s 可用（其中 %s 条在真机执行过，非仅探测）" % (
            caps.get("supported", 0), caps.get("total", 0), caps.get("executed", 0)),
        "声明核对：%s/%s 条工具声明路径可解析" % (
            decl.get("ok", 0), decl.get("paths", 0)),
    ]
    if caps.get("unsupported"):
        lines.append("缺失能力：%s" % "、".join(caps["unsupported"][:6]))
    for item in (decl.get("mismatched") or [])[:4]:
        lines.append("声明对不上：%s（%s）" % (item.get("path"), item.get("detail")))

    project = report.get("project") or {}
    if report.get("level") == LEVEL_PROJECT:
        lines.append("临时工程：%s" % ("已运行" if project.get("ran") else
                                  "未运行（%s）" % project.get("reason", "?")))
        for batch in report.get("batches") or []:
            if batch.get("isolated_tool") or batch.get("label") == "核心批次" \
                    or batch.get("label") == "进阶批次":
                lines.append("  · %s：%s%s" % (
                    batch.get("label"), "通过" if batch.get("ok") else "失败",
                    "" if batch.get("ok") else " —— " + str(batch.get("error"))))
    for note in report.get("notes") or []:
        lines.append("提示：" + note)
    return "\n".join(lines)


def report_directory() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(root, "SP AI Assistant", "integration_smoke")


def write_report(report: dict, directory: str | None = None) -> str:
    """把报告写进磁盘，并同步一份 ``latest.json``。返回主报告路径。"""
    target_dir = directory or report_directory()
    os.makedirs(target_dir, exist_ok=True)
    stamp = str(report.get("started_at") or "").replace(":", "").replace("-", "")
    path = os.path.join(target_dir, "smoke-%s.json" % (stamp or "unknown"))
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    latest = os.path.join(target_dir, "latest.json")
    with open(latest, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return path
