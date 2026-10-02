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

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from core.ai_client import (  # noqa: E402
    CAPABILITY_KEYS,
    PROVIDERS,
    provider_capabilities,
    supports,
    vision_gate,
)


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """image_search 现在会写统一缓存（规格 §22），测试必须落到临时目录——
    否则会在本机真实的 %LOCALAPPDATA%\\SP AI Assistant\\cache 里留垃圾。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    yield


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
    """tool_calling / web_search / image_search 已接到全部协议路径，矩阵必须如实声明。"""
    for name in PROVIDERS:
        caps = provider_capabilities(name)
        assert "tool_calling" in caps, "%s：painter_actions 已接线却没声明" % name
        assert "web_search" in caps, "%s：搜索（原生或兜底）已接线却没声明" % name
        assert "image_search" in caps, (
            "%s：图片搜索已接线（§6 MediaObject 管线，四条协议路径）却没声明" % name)


def test_unwired_capabilities_are_never_declared():
    """图像生成还没有任何 wire path —— 任何 provider 都不得声明。"""
    for name in PROVIDERS:
        caps = provider_capabilities(name)
        assert "image_generation" not in caps, (
            "%s：图像生成未接线（§8 PBR 云端）却声明了" % name)


def test_image_search_wire_exists_on_every_protocol_path():
    """image_search 全员声明的前提是四条协议路径都有真实接线。

    证伪教训（2026-09-30）：剪断 Gemini 的 functionDeclaration 后
    165 项测试全绿 —— 矩阵声明与 wire 之间没有任何锁。这条测试把
    四条路径的工具清单逐个钉死：声明了 image_search，wire 就必须在。
    """
    from pathlib import Path
    client = (Path(ROOT) / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    assert '"name": "image_search"' in client  # IMAGE_SEARCH_TOOL schema 本体
    # OpenAI 兼容路径：function tools 清单
    assert "tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL, IMAGE_SEARCH_TOOL, PBR_GENERATE_TOOL]" in client
    # OpenAI Responses 路径：_function_tools() 必须声明 image_search
    responses = client.split("def _openai_responses(")[1].split("def _openai_compatible(")[0]
    assert '"name": "image_search"' in responses, "Responses 路径缺 image_search wire"
    assert "function_call_output" in responses, "Responses 路径缺工具回执回传"
    # Anthropic 路径：custom tool + tool_result 回传
    anthropic = client.split("def _anthropic(")[1].split("def _gemini(")[0]
    assert '"name": "image_search"' in anthropic, "Anthropic 路径缺 image_search wire"
    assert '"type": "tool_result"' in anthropic, "Anthropic 路径缺工具回执回传"
    # Gemini 路径：functionDeclarations + functionResponse 回传
    gemini = client.split("def _gemini(")[1].split("def chat(")[0]
    assert gemini.count('"name": "image_search"') >= 2, (
        "Gemini 路径缺 image_search wire（声明或回执）")


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


# --------------------------------------------------------------- 图片直显兜底（规格 §6）
# 0.7.2 用户实测：模型只把 local_path 当纯文本输出，对话框里只有路径没有图。
# 这里锁两条硬管线：回执带现成 Markdown；LAST_IMAGES 随 meta 进 UI 兜底显示。
_LOCAL_A = r"C:/Users/hu/AppData/Local/SP AI Assistant/media_cache/aa11.png"
_LOCAL_B = r"C:/Users/hu/AppData/Local/SP AI Assistant/media_cache/bb22.png"


def _fake_receipt():
    return {
        "query": "铜锈 材质",
        "results": [
            {"title": "铜锈特写", "local_path": _LOCAL_A},
            {"title": "铜锈地板", "local_path": _LOCAL_B},
            {"title": "失败的图", "local_path": "", "error": "下载失败"},
        ],
        "cached": 2,
        "failed": [{"title": "失败的图", "error": "下载失败"}],
    }


def test_image_search_receipt_carries_ready_markdown(monkeypatch):
    """回执必须带模型可整段复制的 markdown 片段 + hint，不许模型自己拼。"""
    from core import ai_client, media

    monkeypatch.setattr(media, "search_images_cached", lambda q, n: _fake_receipt())
    ai_client.LAST_IMAGES.clear()
    receipt = ai_client._run_image_search({"query": "铜锈", "max_results": 3})

    assert receipt["markdown"] == (
        "![铜锈特写](%s)\n![铜锈地板](%s)" % (_LOCAL_A, _LOCAL_B))
    assert "复制进你的回复" in receipt["hint"]
    # 只有缓存成功的图进 markdown，失败的不进
    assert "失败的图" not in receipt["markdown"]


def test_last_images_records_cached_paths_for_ui_fallback(monkeypatch):
    """工具命中的本地缓存图必须记进 LAST_IMAGES（UI 兜底显示的数据源），
    重复搜索同一张图不重复记。"""
    from core import ai_client, media

    monkeypatch.setattr(media, "search_images_cached", lambda q, n: _fake_receipt())
    ai_client.LAST_IMAGES.clear()

    ai_client._run_image_search({"query": "铜锈"})
    assert [img["url"] for img in ai_client.LAST_IMAGES] == [_LOCAL_A, _LOCAL_B]
    assert ai_client.LAST_IMAGES[0]["alt"] == "铜锈特写"

    ai_client._run_image_search({"query": "铜锈"})  # 同一轮再搜一次
    assert len(ai_client.LAST_IMAGES) == 2, "同一张图不得重复记入兜底清单"


def test_chat_resets_last_images_each_turn(monkeypatch):
    """每轮 chat() 开头必须清空 LAST_IMAGES——上一轮的图不能漏进下一轮。"""
    from core import ai_client

    ai_client.LAST_IMAGES.append({"url": _LOCAL_A, "alt": "旧图"})
    monkeypatch.setattr(ai_client, "_openai_responses", lambda *a, **k: "好的")
    provider = next(name for name, info in ai_client.PROVIDERS.items()
                    if info["id"] == "openai")
    model = next(iter(ai_client.PROVIDERS[provider].get("models") or ["gpt-x"]))
    ai_client.chat(provider, [{"role": "user", "content": "hi"}], model, "sk-test")

    assert ai_client.LAST_IMAGES == [], "chat() 必须在开头清空兜底图"


def test_empty_query_receipt_has_no_markdown_side_effects():
    from core import ai_client

    ai_client.LAST_IMAGES.clear()
    receipt = ai_client._run_image_search({"query": "  "})
    assert receipt["error"]
    assert "markdown" not in receipt
    assert ai_client.LAST_IMAGES == []


# --------------------------------------------------- §22 缓存接线（必须锁）
def test_image_search_hits_cache_on_second_identical_call(monkeypatch, tmp_path):
    """规格 §22：搜索结果可缓存。同 query+条数第二次必须命中，不再发请求。"""
    from core import ai_client, media

    live = tmp_path / "shot.png"
    live.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    calls = {"n": 0}

    def _fake(query, limit):
        calls["n"] += 1
        receipt = _fake_receipt()
        for item in receipt["results"]:
            if item.get("local_path"):
                item["local_path"] = str(live)
        return receipt

    monkeypatch.setattr(media, "search_images_cached", _fake)
    ai_client.LAST_IMAGES.clear()

    first = ai_client._run_image_search({"query": "铜锈", "max_results": 3})
    second = ai_client._run_image_search({"query": "铜锈", "max_results": 3})

    assert calls["n"] == 1, "第二次相同搜索不得再发网络请求（§22 缓存未生效）"
    assert first["cache"] == "miss" and second["cache"] == "hit"
    assert second["markdown"] == first["markdown"], "命中回执也要能直显图片"


def test_image_search_cache_never_fakes_a_hit(monkeypatch, tmp_path):
    """缓存命中必须诚实：本地缓存图不在了就当未命中重新搜，
    否则用户会看到一张读不出来的图（假命中比慢更糟）。"""
    from core import ai_client, media

    live = tmp_path / "shot.png"
    live.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    calls = {"n": 0}

    def _fake(query, limit):
        calls["n"] += 1
        receipt = _fake_receipt()
        for item in receipt["results"]:
            if item.get("local_path"):
                item["local_path"] = str(live)
        return receipt

    monkeypatch.setattr(media, "search_images_cached", _fake)
    ai_client.LAST_IMAGES.clear()
    ai_client._run_image_search({"query": "铜锈", "max_results": 3})
    live.unlink()                                  # 缓存图被清掉
    again = ai_client._run_image_search({"query": "铜锈", "max_results": 3})
    assert calls["n"] == 2, "文件已不在，缓存不得假命中"
    assert again["cache"] == "miss"


def test_image_search_cache_key_includes_query_and_count(monkeypatch, tmp_path):
    """不同 query 或不同条数是不同的缓存条目，不得互相命中。"""
    from core import ai_client, media

    seen = []

    def _fake(query, limit):
        seen.append((query, limit))
        receipt = _fake_receipt()
        receipt["results"] = [r for r in receipt["results"] if not r.get("local_path")]
        return receipt

    monkeypatch.setattr(media, "search_images_cached", _fake)
    ai_client._run_image_search({"query": "铜锈", "max_results": 3})
    ai_client._run_image_search({"query": "铁锈", "max_results": 3})
    ai_client._run_image_search({"query": "铜锈", "max_results": 5})
    assert seen == [("铜锈", 3), ("铁锈", 3), ("铜锈", 5)]


def test_pbr_receipt_carries_resolution_plan():
    """规格 §22：回执必须带分辨率决策（计划 + 理由），不许默默最高规格。"""
    from core import ai_client, pbr, resolution

    plan = resolution.plan("pc", requested=8192, texture_set=2048)
    assert plan["final"] == 2048 and plan["capped"] is True
    summary = resolution.describe(plan)
    assert "2048×2048" in summary and "硬约束" in summary

    # pbr_generate 的工具 schema 必须显式暴露 platform / resolution 两个开关
    props = ai_client.PBR_GENERATE_TOOL["function"]["parameters"]["properties"]
    assert "platform" in props and "resolution" in props
    assert pbr.PATINA_ROUTES  # 三条路线仍在，避免本测试变成空跑


def test_pbr_generate_wire_exists_on_every_protocol_path():
    """pbr_generate 四条路径 wire 锁（规格 §8）。

    证伪实录（2026-10-01）：剪断 Gemini 的 pbr_generate
    functionDeclaration 后 13 项能力测试全绿——wire 锁只锁了
    image_search，pbr_generate 的声明与接线之间同样裸奔。
    这条测试逐路径钉死：schema 在、四条清单在、回执回传在。
    """
    from pathlib import Path
    client = (Path(ROOT) / "plugin" / "core" / "ai_client.py").read_text(encoding="utf-8")
    assert '"name": "pbr_generate"' in client  # PBR_GENERATE_TOOL schema 本体
    assert "def _run_pbr_generate(" in client  # 执行器
    # OpenAI 兼容路径：function tools 清单（上方 image_search 锁同款）
    assert "PBR_GENERATE_TOOL" in client.split("def _openai_compatible(")[1].split("def _anthropic(")[0]
    # OpenAI Responses 路径：_function_tools() 声明
    responses = client.split("def _openai_responses(")[1].split("def _openai_compatible(")[0]
    assert '"name": "pbr_generate"' in responses, "Responses 路径缺 pbr_generate wire"
    # Anthropic 路径：custom tool 声明 + tool_result 回传
    anthropic = client.split("def _anthropic(")[1].split("def _gemini(")[0]
    assert '"name": "pbr_generate"' in anthropic, "Anthropic 路径缺 pbr_generate wire"
    # Gemini 路径：functionDeclarations 声明 + functionResponse 回传（各至少 1 次）
    gemini = client.split("def _gemini(")[1].split("def chat(")[0]
    assert gemini.count('"name": "pbr_generate"') >= 2, (
        "Gemini 路径缺 pbr_generate wire（声明或回执）")
