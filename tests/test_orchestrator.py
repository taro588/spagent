# -*- coding: utf-8 -*-
"""Master Agent 编排层测试（规格 §4.1 / §4.3 / §21）。

纯逻辑层离线测试：错误标准化、重路由决策、能力路由备选、任务拆解。
全部不碰网络、不碰 Painter、不碰 Qt。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin"))

from core import orchestrator as orch  # noqa: E402


# --------------------------------------------------------------- §21 错误标准化

@pytest.mark.parametrize("text,expected", [
    ("HTTP 401: invalid API key provided", "auth"),
    ("403 forbidden for this account", "auth"),
    ("无效的密钥", "auth"),
    ("HTTP 402: billing quota exceeded", "quota"),
    ("余额不足，请充值", "quota"),
    ("HTTP 429: rate limit reached, too many requests", "rate_limit"),
    ("请求被限流", "rate_limit"),
    ("urlopen error: connection timed out", "network"),  # timeout 规则先于 server
    ("DNS lookup failed", "network"),
    ("HTTP 503: service unavailable, server overloaded", "server"),
    ("内容安全策略拦截：敏感内容", "content_policy"),
    ("OpenAI 返回成功，但没有 choices 文本输出", "parse"),
    ("something totally unexpected", "unknown"),
])
def test_classify_error_maps_provider_text_to_standard_categories(text, expected):
    assert orch.classify_error(text) == expected


def test_classify_error_auth_beats_rate_limit_when_both_present():
    """报错同时含鉴权与限流关键词时必须归 auth——换家解决不了 Key 问题
    就绝不能归成可以原地重试的类别。"""
    assert orch.classify_error("401 unauthorized, rate limit also mentioned") == "auth"


def test_classify_error_is_case_insensitive_and_tolerates_empty():
    assert orch.classify_error("INVALID API KEY") == "auth"
    assert orch.classify_error("") == "unknown"
    assert orch.classify_error(None) == "unknown"


# --------------------------------------------------------------- §4.1 重路由决策

def test_auth_and_quota_switch_provider_immediately():
    """Key/余额错误同家重试没有意义：第一次就要换家。"""
    assert orch.reroute_decision("auth", 1)["action"] == "switch_provider"
    assert orch.reroute_decision("quota", 1)["action"] == "switch_provider"


def test_transient_errors_retry_once_then_switch():
    """网络/服务端/解析类瞬时故障：先同家重试一次，再失败才换家。"""
    for category in ("network", "server", "rate_limit", "parse"):
        assert orch.reroute_decision(category, 1)["action"] == "retry_same", category
        assert orch.reroute_decision(category, 2)["action"] == "switch_provider", category


def test_content_policy_never_switches():
    """内容安全拦截换家只会撞别家的安全策略：必须 degrade 不是换家。"""
    assert orch.reroute_decision("content_policy", 1)["action"] == "degrade"
    assert orch.reroute_decision("content_policy", 2)["action"] == "abort"


def test_reroute_aborts_instead_of_blind_repeat():
    """超过上限必须 abort——规格 §4.1「不盲目重复相同调用」。"""
    for category in ("auth", "quota", "network", "server", "rate_limit", "parse"):
        assert orch.reroute_decision(category, 3)["action"] == "abort", category
    assert orch.reroute_decision("unknown", 1)["action"] == "abort"
    # attempt 超出策略表也一律 abort
    assert orch.reroute_decision("network", 9)["action"] == "abort"


def test_reroute_decision_reports_category_and_reason():
    decision = orch.reroute_decision("auth", 1)
    assert decision["category"] == "auth"
    assert decision["reason"]  # 每个决策必须带给人看的理由
    assert orch.reroute_decision("bogus_category", 1)["category"] == "unknown"


def test_max_reroutes_caps_total_attempts():
    """MAX_REROUTES 硬顶：无论策略表写得多宽松，超过即 abort。"""
    assert orch.MAX_REROUTES == 2
    assert orch.reroute_decision("network", orch.MAX_REROUTES + 1)["action"] == "abort"


# --------------------------------------------------------------- §4.1/§4.2 能力路由

def test_pick_fallback_skips_current_and_capability_less():
    """备选不能是当前家；不能选能力矩阵不支持 tool_calling 的家。"""
    fallback = orch.pick_fallback_provider("openai", "tool_calling", config_fn=None)
    assert fallback is not None
    assert fallback != "openai"


def test_pick_fallback_requires_configured_key_when_config_fn_given():
    """config_fn 注入时：没配 Key / 没选模型的家不算「已配置的备选」。"""
    def only_openai_configured(provider_id):
        if provider_id == "openai":
            return {"api_key": "sk-x", "model": "gpt-x"}
        return {"api_key": "", "model": ""}

    # 返回 PROVIDERS 显示名（UI 下拉框直接可用），id 走 PROVIDERS[name]["id"]
    assert orch.pick_fallback_provider("gemini", "tool_calling",
                                       config_fn=only_openai_configured) == "OpenAI"
    # 全家都没配 Key：返回 None，调用方如实告知，不悄悄退化成原地重试
    def nobody_configured(provider_id):
        return {"api_key": "", "model": ""}
    assert orch.pick_fallback_provider("openai", "tool_calling",
                                       config_fn=nobody_configured) is None

    def model_without_key(provider_id):
        # 配了模型没配 Key 的家也不算备选——换过去必然再吃一次 auth 错误。
        # openai_compatible 豁免 Key 检查（兼容端点 Key 可选），排除出桩。
        if provider_id in ("openai", "openai_compatible"):
            return {}
        return {"api_key": "", "model": "some-model"}

    assert orch.pick_fallback_provider("openai", "tool_calling",
                                       config_fn=model_without_key) is None


def test_pick_fallback_respects_capability_filter():
    """要求 vision 时必须选矩阵声明 vision 的家。"""
    fallback = orch.pick_fallback_provider("openai", "vision", config_fn=None)
    from core.ai_client import provider_capabilities
    if fallback is not None:
        assert "vision" in provider_capabilities(fallback)


# --------------------------------------------------------------- §4.3 角色清单

def test_specialists_cover_all_seven_roles():
    """§4.3 的七个 Specialist 一个都不能少。"""
    assert set(orch.SPECIALISTS) == {
        "search", "vision", "reasoning", "material_pbr",
        "sp_automation", "verification", "corrector",
    }
    for role, info in orch.SPECIALISTS.items():
        assert info["label"], role
        assert info["description"], role
        assert isinstance(info["capabilities"], tuple), role


def test_specialist_capability_keys_match_capability_matrix():
    """角色声明的能力键必须存在于能力矩阵键名集合里（§4.2 同一套键）。"""
    from core.ai_client import CAPABILITY_LABELS
    for info in orch.SPECIALISTS.values():
        for capability in info["capabilities"]:
            assert capability in CAPABILITY_LABELS, capability


# --------------------------------------------------------------- §4.1 任务拆解

def test_plan_steps_expands_material_goal_to_e2e_flow():
    """材质类目标必须展开成 §20 端到端流程，每步带角色/并行组/验证标准。"""
    steps = orch.plan_steps("在当前 Texture Set 创建一个旧铜材质")
    keys = [step["key"] for step in steps]
    assert "search_refs" in keys and "pbr_generate" in keys
    assert "verify_result" in keys and "correct_if_failed" in keys
    for step in steps:
        assert step["role"] in orch.SPECIALISTS, step["key"]
        assert step["verify"], "每个子任务都必须有可验证标准：%s" % step["key"]
        assert step["parallel_group"], step["key"]
    # §4.1「协调并行搜索/视觉分析」：搜索与视觉在同一个并行组
    groups = {step["key"]: step["parallel_group"] for step in steps}
    assert groups["search_refs"] == groups["vision_analysis"]


def test_plan_steps_plain_question_returns_single_reasoning_step():
    """非材质目标不过度拆解：单步 reasoning，拆不出来的步骤宁可不拆。"""
    steps = orch.plan_steps("帮我解释一下什么是 roughness")
    assert len(steps) == 1
    assert steps[0]["role"] == "reasoning"


def test_steps_summary_renders_specialist_labels():
    summary = orch.steps_summary(orch.plan_steps("做个铁锈材质"))
    assert "Search Agent" in summary
    assert "Material/PBR Agent" in summary


def test_plan_steps_returns_copies_not_shared_template():
    """两次 plan_steps 的结果互不影响（模板不能被调用方污染）。"""
    first = orch.plan_steps("旧铜材质")
    first[0]["title"] = "污染测试"
    second = orch.plan_steps("旧铜材质")
    assert second[0]["title"] != "污染测试"


# --------------------------------------------------------------- 纯度检查

def test_orchestrator_never_imports_painter_or_qt():
    """编排层纯度（§3：Orchestration 不直接写 Painter 细节）：
    不得 import substance_painter 或任何 Qt 模块。"""
    import core.orchestrator as module
    source = open(module.__file__, encoding="utf-8").read()
    assert "import substance_painter" not in source
    assert "from substance_painter" not in source
    assert "PySide" not in source
    assert "QtWidgets" not in source
