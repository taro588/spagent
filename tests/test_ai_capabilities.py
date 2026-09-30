"""Provider 能力矩阵（规格 §5 Provider Adapter / §4.2 Capability Router）。

规格要求每个 Provider 声明 capabilities，路由层按能力选择而不是写死模型名。
这里锁三件事：
1. 矩阵完整且诚实 —— 声明的能力必须有 wire path 依据（tool_calling 已接到
   每条协议路径；web_search 原生或 Bing 兜底；vision 只在消息转换函数真能
   送图的 provider 上声明）；
2. 查询 API 的模型级覆盖机制可用（换入视觉模型自动放开）；
3. 视觉门在「有参考图 + 不支持视觉」时拦下并给出可换模型清单。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from core.ai_client import (  # noqa: E402
    CAPABILITY_KEYS,
    PROVIDERS,
    provider_capabilities,
    supports,
    vision_gate,
)


# --------------------------------------------------------------- 矩阵完整性
def test_every_provider_declares_capabilities_within_the_spec_keyset():
    for name, info in PROVIDERS.items():
        caps = info.get("capabilities")
        assert caps, "%s 没有声明 capabilities" % name
        assert set(caps) <= set(CAPABILITY_KEYS), (
            "%s 声明了规格外的能力键：%s" % (name, set(caps) - set(CAPABILITY_KEYS)))
        assert isinstance(info.get("model_capabilities"), dict), (
            "%s 缺少 model_capabilities（可以为空 dict，但键必须存在）" % name)


def test_wired_capabilities_are_declared_for_every_provider():
    """tool_calling / web_search 已接到全部协议路径，矩阵必须如实声明。"""
    for name in PROVIDERS:
        caps = provider_capabilities(name)
        assert "tool_calling" in caps, "%s：painter_actions 已接线却没声明" % name
        assert "web_search" in caps, "%s：搜索（原生或兜底）已接线却没声明" % name


def test_unwired_capabilities_are_never_declared():
    """图片搜索 / 图像生成还没有任何 wire path —— 任何 provider 都不得声明。"""
    for name in PROVIDERS:
        caps = provider_capabilities(name)
        assert "image_search" not in caps, (
            "%s：图片搜索未接线（§6 MediaObject 管线）却声明了" % name)
        assert "image_generation" not in caps, (
            "%s：图像生成未接线（§8 PBR 云端）却声明了" % name)


def test_vision_facts_match_the_message_conversion_wiring():
    """vision 只在真能把图片送进请求的 provider 上声明。

    OpenAI / Anthropic / Gemini 的消息转换都处理 image_url；本地兼容服务
    复用 OpenAI 转换（能否真用取决于本地模型，由用户自己判断）。
    其余 provider 的在列 chat 模型均为纯文本线。
    """
    vision_ok = {"OpenAI", "Anthropic", "Google Gemini", "OpenAI Compatible"}
    for name in PROVIDERS:
        assert ("vision" in provider_capabilities(name)) == (name in vision_ok), (
            "%s 的 vision 声明与消息转换接线不一致" % name)


# --------------------------------------------------------------- 模型级覆盖
def test_model_override_adds_vision_for_vl_model_names():
    assert supports("Qwen", "vision") is False
    assert supports("Qwen", "vision", "qwen-vl-max") is True
    assert supports("Kimi", "vision", "kimi-latest-vl") is True
    assert supports("GLM", "vision", "glm-4v-flash") is True
    # 覆盖只按模型名生效，不改变 provider 级集合
    assert "vision" not in provider_capabilities("Qwen")


def test_model_override_remove_and_unknown_provider():
    info = PROVIDERS["OpenAI"]
    info["model_capabilities"]["-text-only"] = {"remove": ["vision"]}
    try:
        assert supports("OpenAI", "vision", "gpt-5.6-text-only") is False
        assert supports("OpenAI", "vision", "gpt-5.6") is True
    finally:
        del info["model_capabilities"]["-text-only"]
    assert provider_capabilities("Nope") == frozenset()
    assert supports("Nope", "vision") is False


# --------------------------------------------------------------- 视觉门
def test_vision_gate_blocks_with_actionable_alternatives():
    message = vision_gate("DeepSeek", "deepseek-chat", True)
    assert message
    assert "不支持图片输入" in message
    assert "deepseek-chat" in message
    # 必须把可换的视觉模型列出来，而不是只报错
    assert "OpenAI" in message
    assert "参考图都已保留" in message


def test_vision_gate_passes_when_capable_or_no_images():
    assert vision_gate("OpenAI", "gpt-5.6", True) is None
    assert vision_gate("Anthropic", "claude-sonnet-4-6", True) is None
    assert vision_gate("Google Gemini", "gemini-2.5-pro", True) is None
    # 没有参考图时永远放行
    assert vision_gate("DeepSeek", "deepseek-chat", False) is None
    # provider 没选好时放行，交给 chat() 既有的「未知 AI 提供商」校验
    assert vision_gate("", "", True) is None
