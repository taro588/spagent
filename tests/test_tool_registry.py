"""Tool Registry 契约测试（技术架构文档 §12 / §13 / §17 / §28）。

这些测试全部在 Painter 之外运行：registry 是纯数据，actions.py 通过一个
最小的 substance_painter 桩导入即可测试校验管线。CI 里不需要 Painter。

重点保证三件事：
  1. 白名单 / 模型 schema / 必填参数 只有一份来源（registry）；
  2. registry 与 core/actions.py 的真实执行分支一一对应（1:1）；
  3. 烘焙、导出这类独占执行域不能被隐式触发（§13、§25）。
"""

from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
ACTIONS_PATH = PLUGIN / "core" / "actions.py"

if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from core.tools import VERIFIERS, REGISTRY, painter_action_tool  # noqa: E402
from core.tools.domains import Permission  # noqa: E402
from core.tools.verifiers import verify_tool  # noqa: E402


# ----------------------------------------------------------------------
# registry 自身的一致性
# ----------------------------------------------------------------------
def test_every_tool_declares_domain_permission_and_summary():
    for spec in REGISTRY.specs:
        assert spec.domains, spec.name
        assert isinstance(spec.permission, Permission), spec.name
        assert spec.summary.strip(), spec.name
        assert spec.verifier in VERIFIERS, (
            f"{spec.name} 声明的校验器 {spec.verifier} 未在 verifiers.py 注册"
        )


def test_aliases_resolve_to_registered_tools():
    for alias, target in REGISTRY.aliases().items():
        assert target in REGISTRY.names(), alias
        assert alias not in REGISTRY.names(), alias


def test_high_impact_matches_permission_model():
    expected = {
        spec.name for spec in REGISTRY.specs
        if spec.permission in {Permission.EXPORT, Permission.DANGEROUS}
    }
    assert set(REGISTRY.high_impact) == expected
    assert {"delete_selected", "export_textures", "bake_start"} <= expected


def test_capability_matrix_covers_every_declared_domain():
    matrix = REGISTRY.capability_matrix()
    for spec in REGISTRY.specs:
        for domain in spec.domains:
            assert spec.name in matrix[domain.value], (spec.name, domain.value)


def test_spec_section_28_core_tools_exist():
    """§28 第 3、4 条：接手第一优先级点名的核心真实操作必须在册。"""
    for name in ("create_fill_layer", "set_base_color", "set_roughness",
                 "set_metallic", "set_height", "set_fill_material",
                 "resource_import_project"):
        spec = REGISTRY.get(name)
        assert spec is not None, f"§28 点名的核心工具缺失: {name}"
        assert spec.macro is False, name
        assert spec.api.startswith("substance_painter."), name
        assert spec.verifier in VERIFIERS, name


def test_named_channel_tools_carry_their_channel():
    """语义化单通道工具必须带 channel 声明，否则执行器与校验器只能靠猜。"""
    expected = {
        "set_base_color": "BaseColor",
        "set_roughness": "Roughness",
        "set_metallic": "Metallic",
        "set_height": "Height",
    }
    for name, channel in expected.items():
        assert REGISTRY.get(name).channel == channel, name


# ----------------------------------------------------------------------
# 1:1 对齐：registry ↔ actions.py 的真实执行分支
# ----------------------------------------------------------------------
def _compare_strings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return {
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    return set()


def _dispatch_tokens(source: str, function_names: set[str]) -> set[str]:
    """从源码里抽出 `kind == "x"` / `kind in {...}` 形式的真实分派名。

    这是从**实际代码**读出来的，不是再写一份清单；因此它既能发现
    「注册了却没有执行分支」，也能发现「有执行分支却没注册」。
    """
    tree = ast.parse(source)
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in function_names:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare):
                continue
            left = sub.left
            if not (isinstance(left, ast.Name) and left.id == "kind"):
                continue
            for operator, comparator in zip(sub.ops, sub.comparators):
                if isinstance(operator, (ast.Eq, ast.In)):
                    tokens |= _compare_strings(comparator)
    return tokens


def test_registry_and_executors_are_one_to_one():
    source = ACTIONS_PATH.read_text(encoding="utf-8")
    dispatch = _dispatch_tokens(source, {"execute_plan"})
    macros = _dispatch_tokens(source, {"_expand_workflow_actions"})
    # 直接调用 registry 的强断言：任何一个方向的差集都会抛 ToolContractError
    REGISTRY.assert_alignment(dispatch, macros)
    assert dispatch, "没有从 execute_plan 里解析到任何执行分支"
    assert macros == set(REGISTRY.macros()), sorted(macros)
    assert dispatch == set(REGISTRY.primitives()), sorted(dispatch)


def test_whitelist_and_schema_have_a_single_source():
    actions = ACTIONS_PATH.read_text(encoding="utf-8")
    ai_client = (PLUGIN / "core" / "ai_client.py").read_text(encoding="utf-8")
    dock = (PLUGIN / "ui" / "chat_dock.py").read_text(encoding="utf-8")
    # actions.py 只从 registry 取白名单 / 别名 / 必填参数
    assert "SUPPORTED_ACTIONS = set(tool_names())" in actions
    assert "ACTION_ALIASES = action_aliases()" in actions
    assert "required = required_params()" in actions
    assert '"insert_fill_layer"' not in actions  # 别名不再手写第二份
    assert '"export_textures": ("export_path",)' not in actions
    # ai_client 的模型 schema 由 registry 生成
    assert "PAINTER_ACTION_TOOL = painter_action_tool()" in ai_client
    assert '"set_uniform_color"' not in ai_client
    # chat_dock 的高影响清单也来自 registry
    assert "from core.actions import HIGH_IMPACT_ACTIONS" in dock
    assert 'HIGH_IMPACT_ACTIONS = {' not in dock


def test_model_schema_enumerates_registry_tools():
    tool = painter_action_tool()
    assert tool["function"]["name"] == "painter_actions"
    parameters = tool["function"]["parameters"]
    enum = parameters["properties"]["actions"]["items"]["properties"]["action"]["enum"]
    assert enum == list(REGISTRY.model_names())
    assert len(enum) == len(set(enum))
    assert parameters["required"] == ["actions"]
    # 执行域声明必须出现在模型可见参数里，否则 §13 的守卫无从判断
    assert "operation_domain" in parameters["properties"]
    assert "baking" in parameters["properties"]["operation_domain"]["enum"]
    assert "export" in parameters["properties"]["operation_domain"]["enum"]


# ----------------------------------------------------------------------
# 独占执行域守卫（§13）
# ----------------------------------------------------------------------
def test_domain_guard_rejects_implicit_baking_under_material_task():
    from core.tools import ToolContractError, check_plan_domains

    with pytest.raises(ToolContractError) as excinfo:
        check_plan_domains(
            [{"action": "create_fill_layer"}, {"action": "bake_start"}],
            "material",
        )
    assert "bake_start" in str(excinfo.value)
    assert "BAKING" in str(excinfo.value)


def test_domain_guard_allows_declared_baking_and_export():
    from core.tools import check_plan_domains

    assert check_plan_domains([{"action": "bake_start"}], "baking") == "baking"
    assert check_plan_domains([{"action": "export_textures"}], "export") == "export"
    # 未声明 ≠ 禁止非独占域；只是独占域需要显式声明
    assert check_plan_domains([{"action": "create_fill_layer"}], None) == "free"


def test_domain_guard_rejects_export_under_material_task():
    from core.tools import ToolContractError, check_plan_domains

    with pytest.raises(ToolContractError):
        check_plan_domains([{"action": "export_textures"}], "material")


def test_explicit_bake_flag_is_treated_as_declaration():
    from core.tools import check_plan_domains

    # 高阶动作上显式写了 bake: true → 展开出来的 bake_start 带 _explicit_bake
    assert check_plan_domains(
        [{"action": "bake_start", "_explicit_bake": True}], None
    ) == "free"


# ----------------------------------------------------------------------
# validate_plan 的真实行为（用 substance_painter 桩导入 actions）
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def actions():
    if "substance_painter" not in sys.modules:
        stub = types.ModuleType("substance_painter")
        sys.modules["substance_painter"] = stub
    module = importlib.import_module("core.actions")
    return importlib.reload(module)


def test_validate_plan_resolves_aliases_from_registry(actions):
    plan = actions.validate_plan(
        {"actions": [{"action": "insert_fill_layer", "name": "铜"}]}
    )
    assert plan["actions"][0]["action"] == "create_fill_layer"


def test_validate_plan_requires_registry_params(actions):
    with pytest.raises(actions.ActionError) as excinfo:
        actions.validate_plan({"operation_domain": "export",
                               "actions": [{"action": "export_textures"}]})
    assert "export_path" in str(excinfo.value)


def test_validate_plan_blocks_cross_domain_baking(actions):
    with pytest.raises(actions.ActionError) as excinfo:
        actions.validate_plan({
            "operation_domain": "material",
            "actions": [
                {"action": "create_fill_layer", "name": "旧铜"},
                {"action": "bake_start"},
            ],
        })
    assert "独占执行域" in str(excinfo.value)


def test_validate_plan_keeps_operation_domain_through_expansion(actions):
    plan = actions.validate_plan({
        "operation_domain": "material",
        "actions": [{"action": "apply_base_material", "name": "铜",
                     "parameters": {"BaseColor": "#885533"}}],
    })
    assert plan["operation_domain"] == "material"
    assert plan["actions"][0]["action"] == "create_fill_layer"
    assert any(a["action"] == "verify_last_created_parameters"
               for a in plan["actions"])


def test_workflow_bake_is_opt_in_and_explicit(actions):
    without = actions.validate_plan({"actions": [{"action": "auto_material_workflow"}]})
    assert all(a["action"] != "bake_start" for a in without["actions"])
    with_bake = actions.validate_plan(
        {"actions": [{"action": "auto_material_workflow", "bake": True}]}
    )
    bakes = [a for a in with_bake["actions"] if a["action"] == "bake_start"]
    assert bakes and bakes[0]["_explicit_bake"] is True


def test_execute_plan_exposes_high_impact_set(actions):
    from core.tools import HIGH_IMPACT_ACTIONS

    assert actions.HIGH_IMPACT_ACTIONS == HIGH_IMPACT_ACTIONS
    assert actions.SUPPORTED_ACTIONS == set(REGISTRY.names())
    assert actions.ACTION_ALIASES == REGISTRY.aliases()


def test_spec_section_20_old_copper_plan_validates(actions):
    """§20 的端到端示例（自动制作旧铜材质）必须能被校验管线接受。

    这里只跑 validate_plan（不碰 Painter），但足以证明 registry /
    别名 / 必填参数 / 域守卫 的组合对真实材质工作流是放行的。
    """
    plan = {
        "operation_domain": "material",
        "actions": [
            {"action": "create_fill_layer", "name": "Copper Base"},
            {"action": "set_base_color", "value": "#8A5A32"},
            {"action": "set_roughness", "value": 0.42},
            {"action": "set_metallic", "value": 0.9},
            {"action": "create_fill_layer", "name": "Oxide Edge"},
            {"action": "set_base_color", "color": "#3F6B4E"},   # color 写法
            {"action": "add_generator", "name": "Metal Edge Wear", "stack": "mask"},
            {"action": "set_effect_parameters",
             "parameters": {"scale": 4, "contrast": 0.6}},
            {"action": "verify_last_created_parameters",
             "parameters": {"Roughness": 0.42}},
        ],
    }
    validated = actions.validate_plan(plan)
    assert validated["operation_domain"] == "material"
    # color 写法被规范化成 value，供执行器与校验器统一读取
    assert validated["actions"][5]["value"] == "#3F6B4E"
    assert validated["actions"][1]["action"] == "set_base_color"


def test_spec_section_20_export_is_a_separate_declared_task(actions):
    plan = actions.validate_plan({
        "operation_domain": "export",
        "actions": [{"action": "export_textures", "export_path": "D:/sp_ai_export"}],
    })
    assert plan["operation_domain"] == "export"


# ----------------------------------------------------------------------
# 校验器（§18.1）——纯快照驱动，可在 Painter 之外跑
# ----------------------------------------------------------------------
def _spec(name):
    spec = REGISTRY.get(name)
    assert spec is not None
    return spec


def test_layer_created_verifier_confirms_real_state_and_flags_absence():
    before = {"layers": []}
    after = {"layers": [{"uid": "u1", "name": "旧铜", "type": "FillLayerNode"}]}
    ok = verify_tool(_spec("create_fill_layer"), {"action": "create_fill_layer",
                                                 "name": "旧铜"},
                     {"action": "create_fill_layer", "uid": "u1",
                      "api": "substance_painter.layerstack.insert_fill"},
                     before, after)
    assert ok["verified"] is True

    missing = verify_tool(_spec("create_fill_layer"),
                          {"action": "create_fill_layer", "name": "旧铜"},
                          {"action": "create_fill_layer", "uid": "u1",
                           "api": "substance_painter.layerstack.insert_fill"},
                          before, {"layers": []})
    assert missing["verified"] is False
    assert any(c["check"] == "layer_present" and not c["ok"]
               for c in missing["checks"])


def test_channel_source_verifier_marks_unreadable_state_as_skipped():
    report = verify_tool(
        _spec("set_uniform_color"),
        {"action": "set_uniform_color", "channel": "BaseColor", "color": "#ff0000"},
        {"action": "set_uniform_color", "target": "铜",
         "api": "substance_painter.layerstack.FillLayerNode.set_source"},
        {},
        {"layers": [{"uid": "u1", "name": "铜", "type": "FillLayerNode"}]},
    )
    value_check = next(c for c in report["checks"] if c["check"] == "value_applied")
    assert value_check["skipped"] is True
    # 读不到就不算已验证，但目标存在与 API 证据仍然要过
    assert report["verified"] is True


def test_channel_source_verifier_detects_mismatch_when_readable():
    report = verify_tool(
        _spec("set_uniform_color"),
        {"action": "set_uniform_color", "channel": "BaseColor", "color": "#ff0000"},
        {"action": "set_uniform_color", "target": "铜",
         "api": "substance_painter.layerstack.FillLayerNode.set_source"},
        {},
        {"layers": [{"uid": "u1", "name": "铜",
                     "parameters": {"BaseColor": "0f0f0f"}}]},
    )
    assert report["verified"] is False


def test_material_source_verifier_requires_material_mode():
    spec = _spec("set_fill_material")
    good = verify_tool(spec, {"action": "set_fill_material", "name": "Copper"},
                       {"action": "set_fill_material", "source_mode": "Material",
                        "source_type": "SourceSubstance",
                        "api": "substance_painter.layerstack.FillLayerNode.set_material_source"},
                       {}, {})
    assert good["verified"] is True
    bad = verify_tool(spec, {"action": "set_fill_material", "name": "Copper"},
                      {"action": "set_fill_material", "source_mode": "Split",
                       "source_type": "SourceSubstance",
                       "api": "x"}, {}, {})
    assert bad["verified"] is False


def test_missing_api_evidence_fails_verification():
    report = verify_tool(_spec("create_fill_layer"),
                         {"action": "create_fill_layer", "name": "x"},
                         {"action": "create_fill_layer"},
                         {"layers": []},
                         {"layers": [{"uid": "1", "name": "x"}]})
    assert report["verified"] is False
    assert any(c["check"] == "api_evidence" and not c["ok"]
               for c in report["checks"])


def test_verify_plan_summarises_failures():
    from core.tools import verify_plan

    specs = [_spec("create_fill_layer"), _spec("create_fill_layer")]
    actions = [{"action": "create_fill_layer", "name": "a"},
               {"action": "create_fill_layer", "name": "b"}]
    results = [{"action": "create_fill_layer", "uid": "1", "api": "x"},
               {"action": "create_fill_layer", "uid": "2", "api": "x"}]
    report = verify_plan(specs, actions, results, {"layers": []},
                         {"layers": [{"uid": "1", "name": "a"}]})
    assert report["checked"] == 2
    assert report["failed"] == 1
    assert report["verified"] is False
