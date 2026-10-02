"""分辨率与成本策略（规格 §22）。

为什么钉这组测试：分辨率决策直接等于钱与显存。三条规格原文各自需要
一组反例来锁：

1. 「先低成本/低分辨率验证」——高分辨率必须有两阶段；draft 不通过
   就绝不能升级（should_promote 只认明确通过）。
2. 「根据 Texture Set 和目标平台决定」——两者取小；显式要求不得放大。
3. 「导出必须考虑 GPU 与项目限制；不要无条件生成最高规格」——导出收紧
   且 capped 如实标记，UI 才能显示「你要的比能给的高」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from core import resolution as res  # noqa: E402


# ------------------------------------------------------------------ 平台表
def test_platform_targets_are_conservative_for_unknown():
    """未知平台不许猜高——猜高就是替用户烧钱。"""
    assert res.platform_target("unknown") == 2048
    assert res.platform_target("") == 2048
    assert res.platform_target("something-new") == 2048
    assert res.platform_target("film") == 8192
    assert res.platform_target("MOBILE") == res.platform_target("mobile")


def test_allowed_sizes_are_powers_of_two_ladder():
    assert res.ALLOWED_SIZES == (512, 1024, 2048, 4096, 8192)
    assert res._floor_size(3000) == 2048   # 只向下取，绝不向上超预算
    assert res._floor_size(100) == 512


# ------------------------------------------------------------------ 两阶段
def test_high_resolution_gets_two_stages():
    """规格 §22：先低分辨率验证，再出最终分辨率。"""
    plan = res.plan("pc")          # 4096
    assert plan["final"] == 4096
    assert plan["draft"] == res.DRAFT_LONG_EDGE == 1024
    assert plan["stages"] == 2
    assert any("draft" in r for r in plan["reasons"])


def test_low_resolution_needs_no_draft():
    """目标本身就不高于 draft 档位时，不要多跑一轮。"""
    plan = res.plan("mobile", texture_set=1024)
    assert plan["final"] == 1024
    assert plan["draft"] is None
    assert plan["stages"] == 1


@pytest.mark.parametrize("report,expected", [
    (None, False),
    ({}, False),
    ({"passed": False}, False),
    ({"passed": False, "failed": ["seam_score"]}, False),
    ({"passed": True}, True),
])
def test_should_promote_only_on_explicit_pass(report, expected):
    ok, reason = res.should_promote(report)
    assert ok is expected
    assert reason           # 两种结论都必须给得出理由


def test_should_promote_mentions_failed_checks():
    ok, reason = res.should_promote({"passed": False, "failed": ["channels", "resolution"]})
    assert ok is False
    assert "channels" in reason


# ------------------------------------------------------------------ 平台/工程取小
def test_texture_set_lowers_the_platform_target():
    """目标平台 4K 但工程只有 2K 贴图集：生成 4K 是纯浪费。"""
    plan = res.plan("pc", texture_set=2048)
    assert plan["final"] == 2048
    assert plan["capped"] is True
    assert any("Texture Set" in r for r in plan["reasons"])


def test_explicit_request_cannot_upgrade_beyond_platform():
    plan = res.plan("game", requested=8192)
    assert plan["final"] == 2048          # game → 2048，显式 8K 无效
    assert plan["capped"] is True


def test_explicit_request_can_lower():
    plan = res.plan("film", requested=2048)
    assert plan["final"] == 2048          # 用户主动要小，尊重
    assert plan["capped"] is False


def test_never_auto_max_out_spec_case():
    """规格原文「不要无条件生成最高规格」的直译用例。"""
    plan = res.plan("film", texture_set=2048, requested=8192)
    assert plan["final"] == 2048
    assert plan["capped"] is True


# ------------------------------------------------------------------ 导出与 GPU
def test_export_tightens_and_explains():
    plan = res.plan("pc", export=True)
    assert plan["final"] == 2048           # 4096 × 0.5 = 2048
    assert plan["export"] is True
    assert plan["stages"] == 2             # 2048 > draft 档，仍走两阶段
    assert any("导出" in r for r in plan["reasons"])


def test_gpu_limit_caps():
    plan = res.plan("film", gpu_limit=2048)
    assert plan["final"] == 2048
    assert plan["capped"] is True
    assert any("GPU" in r for r in plan["reasons"])


def test_gpu_limit_is_never_exceeded():
    for platform in ("mobile", "game", "pc", "film", "archviz"):
        plan = res.plan(platform, gpu_limit=1024)
        assert plan["final"] <= 1024


# ------------------------------------------------------------------ 描述
def test_describe_is_human_readable():
    text = res.describe(res.plan("pc", texture_set=2048))
    assert "draft 1024" in text
    assert "2048×2048" in text
    assert "硬约束" in text
    assert res.describe({}) == "未规划分辨率"
