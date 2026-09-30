"""集成冒烟测试（架构文档 §28-5）。

这里验证三件事：
1. 冒烟用的计划本身**合法**——直接过真实的 `validate_plan`，而不是断言字符串；
2. 工程生命周期的安全约束真的成立——未保存的工程必须被拒绝、临时工程必须被关闭；
3. 报告聚合与门禁脚本的判断规则正确（pass / partial / fail 的边界）。

真正「官方 API 在这个 Painter 版本上对不对」只能由 Painter 里的那次运行回答，
所以这些测试不冒充集成测试；它们保证的是**跑之前逻辑正确、跑之后结论可信**。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from fake_painter import build_stub, install, painter_no_project  # noqa: E402,F401


@pytest.fixture
def probe_env(painter_no_project):
    """无工程环境（冒烟的默认起点）。"""
    return painter_no_project


@pytest.fixture
def clean_env(painter_no_project, monkeypatch):
    """声明核对能通过的环境。

    注入桩只实现了官方 API 的一小块，若用真实的 63 条声明去核对必然满屏
    mismatch，那样测出来的结论规则就不是规则本身了。所以这里把「声明的官方
    路径」缩到桩确实能解析的两条，专门测结论聚合。
    """
    monkeypatch.setattr(painter_no_project.smoke, "declared_api_paths", lambda: {
        "create_fill_layer": ("substance_painter.layerstack.insert_fill",),
        "add_levels": ("substance_painter.layerstack.insert_levels_effect",),
    })
    return painter_no_project


def _mesh(name="smoke.fbx"):
    path = Path(tempfile.mkdtemp(prefix="spai-smoke-")) / name
    path.write_bytes(b"fake-mesh")
    return str(path)


# --------------------------------------------------------------- 计划本身
def test_core_plan_passes_the_real_validator(probe_env):
    plan = probe_env.actions.validate_plan(probe_env.smoke.build_plan(probe_env.smoke.CORE))
    assert plan["operation_domain"] == "material"
    assert plan["actions"][0]["action"] == "create_fill_layer"


def test_every_smoke_action_is_a_registered_tool(probe_env):
    from core.tools import get_tool

    for action in probe_env.smoke.build_plan(probe_env.smoke.ALL)["actions"]:
        assert get_tool(action["action"]) is not None, action


def test_smoke_plan_never_touches_an_exclusive_domain(probe_env):
    """§13：冒烟必须待在自己声明的任务域里，绝不能碰烘焙/导出这类独占域。

    这里断言的是真实规则（独占域），不是「所有域都必须等于 material」——
    例如 `save_smart_material` 顺带声明了 RESOURCE，那是允许的跨域协作，
    而 BAKING/EXPORT 不是。
    """
    from core.tools import EXCLUSIVE_DOMAINS, get_tool

    plan = probe_env.smoke.build_plan(probe_env.smoke.ALL)
    assert plan["operation_domain"] == "material"
    for action in plan["actions"]:
        exclusive = set(get_tool(action["action"]).domains) & set(EXCLUSIVE_DOMAINS)
        assert not exclusive, (action["action"], sorted(domain.value for domain in exclusive))


def test_core_plan_covers_the_call_sites_repaired_in_0_7_0(probe_env):
    """核心/进阶批次必须覆盖三个「原来对着不存在的接口写」的调用点。"""
    names = [action["action"]
             for action in probe_env.smoke.build_plan(probe_env.smoke.ALL)["actions"]]
    assert "set_geometry_mask" in names          # 官方没有 set_geometry_mask
    assert "save_smart_material" in names        # 官方没有 export_as_smart_material
    assert {"set_base_color", "set_roughness", "set_metallic"} <= set(names)


def test_geometry_mask_step_runs_before_the_effect_step(probe_env):
    """顺序有语义：几何遮罩要落在 Fill Layer 上，效果节点是后来才插进来的。"""
    names = [action["action"]
             for action in probe_env.smoke.build_plan(probe_env.smoke.CORE)["actions"]]
    assert names.index("set_geometry_mask") < names.index("add_levels")


# --------------------------------------------------------------- 只读探测
def test_probe_level_changes_nothing(probe_env):
    report = probe_env.smoke.run(level=probe_env.smoke.LEVEL_PROBE,
                                 plugin_version="0.7.0-test")

    assert probe_env.project.created_meshes == []
    assert probe_env.project.close_count == 0
    assert report["project"]["ran"] is False
    assert report["environment"]["painter_version"] == "11.0.0"
    assert report["environment"]["python_api_version"] == "0.3.4"
    assert report["api_surface"]["capabilities"]["total"] > 0
    assert report["outcome"] in {"pass", "fail"}


def test_probe_level_accounts_for_every_declared_path(probe_env):
    surface = probe_env.smoke.api_surface()
    assert surface["capabilities"]["supported"] > 0
    assert surface["declarations"]["tools"] > 0
    assert surface["declarations"]["ok"] + len(surface["declarations"]["mismatched"]) \
        == surface["declarations"]["paths"]


def test_probe_level_passes_when_every_declaration_resolves(clean_env):
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROBE)
    assert report["api_surface"]["declarations"]["mismatched"] == []
    assert report["outcome"] == "pass"


# --------------------------------------------------------------- 临时工程
def test_project_level_creates_runs_and_closes_a_temp_project(clean_env):
    mesh = _mesh()
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT,
                                 plugin_version="0.7.0-test", mesh_path=mesh)

    project = report["project"]
    assert project["ran"] is True and project["ready"] is True
    assert project["closed"] is True
    assert clean_env.project.created_meshes == [mesh]
    assert clean_env.project.close_count == 1

    assert [batch["label"] for batch in report["batches"]] == ["核心批次", "进阶批次"]
    assert project["core_ok"] is True
    assert project["optional_ok"] is True
    assert report["outcome"] == "pass"


def test_project_level_reports_the_real_official_calls(clean_env):
    """跑完之后，节点上留下的调用必须是官方真实方法名。"""
    clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT, mesh_path=_mesh())
    called = [call[0] for node in clean_env.created for call in node.calls]

    assert "set_geometry_mask_type" in called     # 适配层拆出来的真调用
    assert "set_source" in called                  # set_base_color / set_roughness
    assert "set_geometry_mask" not in called       # 官方根本没这个方法
    assert any(node.get_type() == "FakeEffect" for node in clean_env.created), \
        "add_levels 应该真的插进一个效果节点"


def test_project_level_results_cover_every_planned_step(clean_env):
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT,
                                 mesh_path=_mesh())
    core = [batch for batch in report["batches"] if batch["label"] == "核心批次"][0]
    executed = [item["action"] for item in core["results"]]
    for action in clean_env.smoke.build_plan(clean_env.smoke.CORE)["actions"]:
        assert action["action"] in executed, action


def test_project_level_refuses_an_unsaved_open_project(monkeypatch):
    import core.painter_api as painter_api

    try:
        env = install(monkeypatch, project_open=True, needs_saving=True)
        report = env.smoke.run(level=env.smoke.LEVEL_PROJECT)
    finally:
        painter_api.set_default_api(None)

    assert report["project"]["reason"] == "unsaved_project"
    assert report["project"]["ran"] is False
    assert env.project.close_count == 0            # 绝不替用户关掉未保存的工程
    assert any("未保存" in note for note in report["notes"])


def test_project_level_needs_a_confirmation_before_closing_a_saved_project(monkeypatch):
    import core.painter_api as painter_api

    try:
        env = install(monkeypatch, project_open=True, needs_saving=False)
        report = env.smoke.run(level=env.smoke.LEVEL_PROJECT,
                               confirm_close=lambda: False)
    finally:
        painter_api.set_default_api(None)

    assert report["project"]["reason"] == "declined"
    assert env.project.close_count == 0


def test_project_level_accepts_the_confirmed_close(monkeypatch):
    import core.painter_api as painter_api

    try:
        env = install(monkeypatch, project_open=True, needs_saving=False)
        report = env.smoke.run(level=env.smoke.LEVEL_PROJECT,
                               mesh_path=_mesh("saved.fbx"),
                               confirm_close=lambda: True)
    finally:
        painter_api.set_default_api(None)

    assert report["project"]["closed_previous"] is True
    assert env.project.close_count == 2  # 关旧的 + 关冒烟自己建的


def test_project_level_without_a_mesh_is_skipped_not_failed(clean_env):
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT)
    assert report["project"]["reason"] == "no_mesh"
    assert report["project"]["mesh"] is None
    assert report["project"]["mesh_tried"]          # 候选全列出来，便于排查
    assert report["outcome"] == "partial"
    assert clean_env.project.created_meshes == []


def test_project_level_failure_isolates_the_single_step(clean_env, monkeypatch):
    """整批失败时必须自动退化为单步隔离，把责任钉到具体工具上。"""
    def boom(_plan):
        raise RuntimeError("ActionError: 第 3 个动作不允许执行: set_roughness")

    import core.actions as actions_module

    monkeypatch.setattr(actions_module, "execute_plan", boom)
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT, mesh_path=_mesh())

    assert report["project"]["core_ok"] is False
    assert report["outcome"] == "fail"
    isolated = [batch for batch in report["batches"] if batch.get("isolated_tool")]
    assert [batch["isolated_tool"] for batch in isolated] == \
        [action["action"] for action in clean_env.smoke.core_actions()]
    assert all(not batch["ok"] for batch in isolated)
    assert "set_roughness" in isolated[0]["error"] or isolated[0]["error"]


def test_project_level_keeps_the_project_when_asked(clean_env):
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT,
                                 mesh_path=_mesh(), keep_project=True)
    assert report["project"]["closed"] is False
    assert clean_env.project.is_open() is True
    assert clean_env.project.close_count == 0


# --------------------------------------------------------------- 结论与报告
def test_mismatched_declaration_forces_a_fail(probe_env, monkeypatch):
    monkeypatch.setattr(probe_env.smoke, "declared_api_paths",
                        lambda: {"bogus": ("substance_painter.nope.nope",)})
    report = probe_env.smoke.run(level=probe_env.smoke.LEVEL_PROBE)
    assert report["outcome"] == "fail"
    assert report["api_surface"]["declarations"]["mismatched"]
    assert "substance_painter.nope.nope" in probe_env.smoke.summarize(report)


def test_optional_failure_degrades_to_partial(clean_env, monkeypatch):
    """进阶批次失败只降级，不该把「核心能力可用」判成不可用。"""
    import core.actions as actions_module

    real = actions_module.execute_plan
    calls = {"n": 0}

    def flaky(plan, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二次调用 = 进阶批次
            raise RuntimeError("ActionError: 第 2 个动作 create_smart_material 失败")
        return real(plan, *args, **kwargs)

    monkeypatch.setattr(actions_module, "execute_plan", flaky)
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT, mesh_path=_mesh())

    assert report["project"]["core_ok"] is True
    assert report["project"]["optional_ok"] is False
    assert report["outcome"] == "partial"


def test_report_is_written_with_a_latest_copy(probe_env, tmp_path):
    report = probe_env.smoke.run(level=probe_env.smoke.LEVEL_PROBE)
    path = probe_env.smoke.write_report(report, directory=str(tmp_path))

    assert Path(path).is_file()
    assert (tmp_path / "latest.json").is_file()
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert written["outcome"] == report["outcome"]
    assert written["report_version"] == 1


def test_summarize_is_human_readable(probe_env):
    report = probe_env.smoke.run(level=probe_env.smoke.LEVEL_PROBE,
                                 plugin_version="0.7.0")
    text = probe_env.smoke.summarize(report)
    assert "结论：" in text
    assert "11.0.0" in text
    assert "能力：" in text


# --------------------------------------------------------------- 门禁脚本
def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "check_smoke_report", ROOT / "tools" / "check_smoke_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _passing_report(level="project"):
    return {
        "outcome": "pass",
        "level": level,
        "duration_seconds": 1.0,
        "api_surface": {"declarations": {"mismatched": [], "ok": 2, "paths": 2}},
        "project": {"ran": True, "ready": True, "closed": True},
    }


def test_gate_accepts_a_healthy_project_report():
    gate = _load_gate()
    assert gate.evaluate(_passing_report(), min_level="probe") == []
    assert gate.evaluate(_passing_report(), min_level="project") == []


def test_gate_rejects_a_probe_report_when_a_project_run_is_required():
    gate = _load_gate()
    problems = gate.evaluate(_passing_report("probe"), min_level="project")
    assert problems and "级别" in problems[0]


def test_gate_rejects_a_mismatched_declaration():
    gate = _load_gate()
    report = _passing_report()
    report["api_surface"]["declarations"]["mismatched"] = [
        {"path": "substance_painter.nope", "detail": "不存在"}]
    problems = gate.evaluate(report, min_level="project")
    assert any("nope" in problem for problem in problems)


def test_gate_rejects_a_report_whose_temp_project_was_left_open():
    gate = _load_gate()
    report = _passing_report()
    report["project"]["closed"] = False
    problems = gate.evaluate(report, min_level="project")
    assert any("没有被关闭" in problem for problem in problems)


def test_gate_exit_codes(tmp_path, capsys):
    gate = _load_gate()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_passing_report()), encoding="utf-8")
    assert gate.main([str(good)]) == 0

    bad = tmp_path / "bad.json"
    payload = _passing_report()
    payload["outcome"] = "partial"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    assert gate.main([str(bad)]) == 1

    missing = tmp_path / "missing.json"
    assert gate.main([str(missing)]) == 2
    assert gate.main([str(missing), "--allow-missing"]) == 0
    capsys.readouterr()


def test_gate_rejects_a_report_that_is_not_a_smoke_report(tmp_path, capsys):
    gate = _load_gate()
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    assert gate.main([str(bogus)]) == 2
    capsys.readouterr()


def test_gate_reads_a_report_written_by_the_smoke(clean_env, tmp_path):
    """端到端：冒烟写出的报告，门禁真的能读并给出正确判定。"""
    gate = _load_gate()
    report = clean_env.smoke.run(level=clean_env.smoke.LEVEL_PROJECT, mesh_path=_mesh())
    path = clean_env.smoke.write_report(report, directory=str(tmp_path))
    assert gate.main([path, "--min-level", "project"]) == 0


def test_stub_rejects_a_mesh_that_does_not_exist():
    """假 Painter 也照真实行为校验网格存在性，避免测试给出假绿灯。"""
    sp, _created = build_stub()
    with pytest.raises(ValueError):
        sp.project.create(mesh_file_path="C:/nope/not-here.fbx")
