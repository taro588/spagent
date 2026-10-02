from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

from core.tools import painter_action_tool


AI_CLIENT_BUILD = "0.6.0"

# ---------------------------------------------------------------------------
# Provider 能力矩阵（规格 §5 Provider Adapter / §4.2 Capability Router）
#
# 规格要求：每个 Provider 声明 capabilities，Master Agent 按能力路由而不是
# 写死模型名。能力名与规格一致：
#   vision            图片输入（参考图 / 材质分析）
#   web_search        联网搜索（原生工具或搜索 API 兜底）
#   image_search      图片搜索（返回可显示图片与来源；§6 MediaObject 管线接线后开放）
#   image_generation  图像生成（§8：PBR 生成走独立 PATINA provider + fal key，
#                      不经 chat provider 提供，因此矩阵对 chat provider 不声明；
#                      pbr_generate 工具已接到全部四条协议路径）
#   tool_calling      函数调用（painter_actions 已接到每条协议路径）
#
# 声明纪律：capability 只反映「本插件当前真能通过该 provider 走通的能力」，
# 不反映「该 provider 理论上还有什么」。wire path 没接的能力一律不声明，
# 否则按能力路由的上层会在没有实现的地方空转。
# ---------------------------------------------------------------------------
CAPABILITY_KEYS = ("vision", "web_search", "image_search", "image_generation",
                    "tool_calling")

# 给用户看的能力名（UI tooltip / 状态显示用）。
CAPABILITY_LABELS = {
    "vision": "图片输入",
    "web_search": "联网搜索",
    "image_search": "图片搜索",
    "image_generation": "图像生成",
    "tool_calling": "工具调用",
}

PROVIDERS = {
    "OpenAI": {
        "id": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.6"],
        "capabilities": {"vision", "web_search", "image_search", "tool_calling"},
        # 模型级覆盖：key 是模型名小写的子串，值为增量（add/remove）。
        "model_capabilities": {},
    },
    "Anthropic": {
        "id": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "capabilities": {"vision", "web_search", "image_search", "tool_calling"},
        "model_capabilities": {},
    },
    "Google Gemini": {
        "id": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "capabilities": {"vision", "web_search", "image_search", "tool_calling"},
        "model_capabilities": {},
    },
    "DeepSeek": {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        # 官方 chat/reasoner 均为纯文本模型；web_search 走 Bing 兜底函数。
        "capabilities": {"web_search", "image_search", "tool_calling"},
        "model_capabilities": {},
    },
    "Kimi": {
        "id": "kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.5", "kimi-k2"],
        # kimi-k2 系列为纯文本；moonshot 的视觉模型在 vl 线上（kimi-latest 等）。
        "capabilities": {"web_search", "image_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "Qwen": {
        "id": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus"],
        # qwen3.x chat 系列为纯文本；换入 qwen-vl-* 视觉模型时按子串匹配放开。
        "capabilities": {"web_search", "image_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "GLM": {
        "id": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5-turbo", "glm-5"],
        # glm-5 系列为纯文本；GLM 的视觉模型在 glm-4v 线上。
        "capabilities": {"web_search", "image_search", "tool_calling"},
        "model_capabilities": {"4v": {"add": ["vision"]}},
    },
    "MiniMax": {
        "id": "minimax",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3"],
        # M 系列为纯文本；MiniMax 的视觉模型走 vision 线（MiniMax-VL 等）。
        "capabilities": {"web_search", "image_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "OpenAI Compatible": {
        "id": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "models": ["custom"],
        # 本地服务的 wire path 支持图片（image_url content）；能否真用取决于
        # 用户本地装的是什么模型 —— 这由用户自己判断，插件不做阻断。
        "capabilities": {"vision", "web_search", "image_search", "tool_calling"},
        "model_capabilities": {},
    },
}

def provider_capabilities(provider_name: str, model: str = "") -> frozenset:
    """该 provider（可细化到模型）当前真实可用的能力集合（规格 §5）。

    模型级覆盖（model_capabilities）：key 是模型名小写的子串，命中即应用增量；
    多个命中按声明顺序叠加。DeepSeek 的 chat/reasoner、Kimi 的 k2 等
    provider 级就无视觉；Qwen/Kimi 等换入 *vl* 视觉模型时自动放开。
    """
    info = PROVIDERS.get(provider_name) or {}
    caps = set(info.get("capabilities") or ())
    model_key = str(model or "").lower()
    if model_key:
        for pattern, delta in (info.get("model_capabilities") or {}).items():
            if pattern in model_key:
                caps |= set(delta.get("add") or ())
                caps -= set(delta.get("remove") or ())
    return frozenset(caps)


def supports(provider_name: str, capability: str, model: str = "") -> bool:
    """按能力问询：当前选择（provider + 模型）能不能做这件事。"""
    return capability in provider_capabilities(provider_name, model)


def vision_gate(provider_name: str, model: str, has_images: bool):
    """参考图视觉门：当前模型不支持图片输入时返回要给用户看的说明，否则 None。

    为什么需要它：wire path 会把附件原样发给任何 provider，不支持视觉的模型
    只会回一个难懂的服务端错误。按能力矩阵提前拦下，把「能换哪些模型」直接
    告诉用户 —— 这是 §4.2 Capability Router 在单模型架构下今天就能兑现的部分。
    """
    if not has_images or provider_name not in PROVIDERS:
        return None
    if supports(provider_name, "vision", model):
        return None
    capable = [name for name in PROVIDERS
               if "vision" in provider_capabilities(name)]
    return (
        "当前模型 %s（%s）不支持图片输入，参考图不会被发送。\n"
        "支持视觉理解的模型：%s。\n"
        "请先切换模型再发送；你的输入和参考图都已保留。"
        % (model or "（未选择）", provider_name, "、".join(capable))
    )


WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索公开网页。用于获取当前、最新或需要外部资料的问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或完整问题"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 8}
            },
            "required": ["query"],
            "additionalProperties": False
        }
    }
}

# 模型可见的工具 schema 由 Tool Registry 生成（架构文档 §12：
# 再手写一份 enum 就是 bug —— 见 §25「AI 调用工具与执行白名单不一致」）。
PAINTER_ACTION_TOOL = painter_action_tool()

# 图片搜索工具（规格 §6）：执行端是 core.media 的 MediaObject 管线
# （结构化结果 + 本地缓存 + 失败可重试），不是 Painter 操作，不进 Registry。
IMAGE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "image_search",
        "description": (
            "搜索参考图片并返回结构化结果（标题/来源/尺寸/本地缓存路径）。"
            "适用场景：用户要材质参考、风格参考、贴图灵感、看图分析。"
            "结果里的 local_path 是已缓存到本地的图片文件，必须用 Markdown "
            "图片语法 ![标题](local_path) 把图片嵌入回复（回执 markdown 字段 "
            "是现成的，整段复制即可），禁止只把路径当纯文本输出——那样用户 "
            "在对话框里看不到图。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "图片搜索关键词，如「旧铜材质 腐蚀 特写」"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 12}
            },
            "required": ["query"],
            "additionalProperties": False
        }
    }
}


# PBR 生成工具（规格 §8/§9/§10）：执行端是 core.pbr（PATINA 客户端 +
# 质量门）与 core.asset_registry（资产状态机），不进 Painter Registry。
PBR_GENERATE_TOOL = {
    "type": "function",
    "function": {
        "name": "pbr_generate",
        "description": (
            "生成一套 PBR 材质贴图（basecolor/normal/roughness/metalness/height，"
            "无缝平铺）。三条路线：text_to_pbr 用文字描述生成；image_to_pbr 从"
            "已有贴图/成品图预测 PBR 通道（需给 image）；extract 从照片中提取"
            "目标材质并无缝化（需给 image）。结果自动过质量门并登记资产，"
            "回执 markdown 字段是现成的展示片段，整段复制进回复即可。"
            "需要配好 fal key 才能用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "材质描述，如「风化旧铜，绿色铜锈，中等氧化，高粗糙度变化」"},
                "route": {"type": "string", "enum": ["text_to_pbr", "image_to_pbr", "extract"],
                          "description": "生成路线，默认 text_to_pbr"},
                "image": {"type": "string", "description": "参考图/照片的本地路径（image_to_pbr 与 extract 必填）"},
                "platform": {"type": "string",
                             "enum": ["mobile", "game", "pc", "console", "film", "vfx", "archviz"],
                             "description": "目标平台（规格 §22：分辨率按平台与工程决定，"
                                            "不无条件生成最高规格）。默认 game"},
                "resolution": {"type": "integer", "enum": [512, 1024, 2048, 4096, 8192],
                               "description": "期望分辨率（长边像素）。只是期望值，"
                                              "会被平台/工程/GPU 硬约束下调，回执 resolution_plan "
                                              "里说明实际决策与理由"},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
}


def _run_pbr_generate(args: dict) -> dict:
    """执行 pbr_generate 工具调用并返回回执 dict（规格 §8–§10 管线入口）。

    完整闭环：缓存复用检查（§10）→ PATINA 生成（§8）→ 通道图下载 →
    质量门（§9）→ 资产登记（GENERATED→DOWNLOADED→VALIDATED→REGISTERED）。
    生成的 basecolor 图记进 LAST_IMAGES——就算模型不写 Markdown，
    结果图也要显示在对话框里（0.7.3 的直显纪律同样适用）。"""
    from core import pbr as _pbr
    from core import asset_registry as _registry
    from core import resolution as _resolution

    prompt = str((args or {}).get("prompt") or "").strip()
    route = str((args or {}).get("route") or "text_to_pbr")
    image = str((args or {}).get("image") or "").strip()
    if not prompt:
        return {"error": "pbr_generate 的 prompt 不能为空"}
    if route not in _pbr.PATINA_ROUTES:
        return {"error": "未知路线 %r，可用：%s" % (route, ", ".join(_pbr.PATINA_ROUTES))}
    if route in ("image_to_pbr", "extract") and not image:
        return {"error": "路线 %s 需要提供 image（本地图片路径）" % route}
    if image and not Path(image).exists():
        return {"error": "参考图不存在：%s" % image}

    # 规格 §22：分辨率由平台/期望/工程/GPU 硬约束共同决定，绝不无条件最高。
    # 计划只做决策与说明（PATINA 当前按服务端默认分辨率出图），回执里
    # 给模型一份可读的 resolution_plan，UI/用户能看到成本决策理由。
    platform = str((args or {}).get("platform") or "game").strip().lower()
    requested = (args or {}).get("resolution")
    try:
        requested_px = int(requested) if requested else None
    except (TypeError, ValueError):
        requested_px = None
    plan = _resolution.plan(platform, requested=requested_px)

    fingerprint = _pbr.input_fingerprint(route, prompt, image)
    registry = _registry.Registry()
    reused = registry.find_reusable("patina", route, fingerprint)
    if reused is not None:
        receipt = _pbr_receipt(reused, reused=True)
        receipt["resolution_plan"] = plan
        _remember_pbr_preview(receipt)
        return receipt

    # fal 只收公网 URL 或 data URI；本地参考图在这里转成 data URI
    image_url = image
    if image:
        try:
            with open(image, "rb") as handle:
                raw = handle.read(12 * 1024 * 1024)
        except OSError as exc:
            return {"error": "参考图读取失败：%s" % exc}
        mime = mimetypes.guess_type(image)[0] or "image/png"
        image_url = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))

    try:
        generated = _pbr.patina_generate(route, prompt, image_url=image_url)
    except _pbr.PBRError as exc:
        return {"error": str(exc), "prompt": prompt, "route": route}

    asset = _registry.Asset(source=prompt, provider="patina", route=route,
                            fingerprint=fingerprint,
                            params={"request_id": generated.get("request_id"),
                                    "model": generated.get("model")})
    paths = _pbr.download_pbr_maps(generated)
    if not paths:
        asset.files = {}
        registry.register(asset)
        asset.transition("DOWNLOADED")
        registry.mark_failed(asset.asset_id, "全部通道图下载失败")
        return {"error": "生成成功但通道图下载失败（网络问题），可用相同参数重试",
                "asset_id": asset.asset_id, "prompt": prompt, "route": route}
    asset.files = paths
    registry.register(asset)
    registry.advance(asset.asset_id, "DOWNLOADED")

    report = _pbr.validate_pbr_set(paths)
    # 规格 §22：draft/final 两阶段纪律——质量门结论直接换算成「值不值得
    # 升级」，回执里给模型明确的 promote_final 判定与理由。
    promote_ok, promote_reason = _resolution.should_promote({
        "passed": bool(report.get("ok")) and not report.get("failed"),
        "failed": ["%s：%s" % (item.get("check"), item.get("error"))
                   for item in report.get("failed") or []],
    })
    if not report.get("ok"):
        registry.mark_failed(asset.asset_id, "质量门未通过：%s"
                             % "; ".join(item.get("error", "") for item in report["failed"]))
        receipt = {"asset_id": asset.asset_id, "state": asset.state,
                   "prompt": prompt, "route": route,
                   "quality": report,
                   "resolution_plan": plan,
                   "promote_final": {"ok": promote_ok, "reason": promote_reason},
                   "error": "质量门未通过，详见 quality 字段；已保留资产可重试"}
        return receipt
    asset.compute_hashes()
    registry.advance(asset.asset_id, "VALIDATED")
    registry.advance(asset.asset_id, "REGISTERED")
    receipt = _pbr_receipt(registry.get(asset.asset_id), reused=False)
    receipt["resolution_plan"] = plan
    receipt["promote_final"] = {"ok": promote_ok, "reason": promote_reason}
    _remember_pbr_preview(receipt)
    return receipt


def _pbr_receipt(asset, reused: bool) -> dict:
    """把资产变成模型可读回执：状态、文件、质量报告、现成 markdown 片段。"""
    from core import pbr as _pbr
    base = asset.files.get("basecolor") or ""
    markdown = ("![%s](%s)" % (asset.source[:40], base)) if base else ""
    return {
        "asset_id": asset.asset_id,
        "state": asset.state,
        "route": asset.route,
        "prompt": asset.source,
        "files": asset.files,
        "reused": reused,
        "markdown": markdown,
        "hint": ("缓存命中，直接复用已验证资产。" if reused else
                 "质量门已通过（确定性层）。把 markdown 字段整段复制进回复展示"
                 "生成结果；quality.needs_vision 是仍需目视确认的语义检查项。"),
        "quality": {"maps": asset.files, "hashes": asset.hashes},
    }


def _remember_pbr_preview(receipt: dict) -> None:
    """生成结果图进 LAST_IMAGES（0.7.3 直显纪律：不依赖模型写 Markdown）。"""
    for title, path_key in (("PBR BaseColor " + receipt.get("asset_id", ""), "basecolor"),):
        path = (receipt.get("files") or {}).get(path_key)
        if path and not any(item.get("url") == path for item in LAST_IMAGES):
            LAST_IMAGES.append({"url": path, "alt": title[:80]})


def _run_image_search(args: dict) -> dict:
    """执行 image_search 工具调用并返回回执 dict（规格 §6 管线入口）。

    三件与显示/成本强相关的副作用：
    - **缓存**（规格 §22）：同一 query+条数直接命中上次搜索的回执，
      省一次网络请求；命中要求本地缓存图全部还在，缺一张就当未命中
      重新搜——缓存不许给「图已经不在了」的假命中。
    - 回执附 markdown 字段：每张缓存成功的图一行现成的 ![标题](local_path)，
      模型整段复制进回复，图片就会显示在对话框里。
    - 把命中的本地缓存图记进 LAST_IMAGES，Chat UI 兜底附加显示——
      就算模型不听话只贴路径文本，图也得出现在对话里。
    """
    from core import media as _media
    from core import cache as _cache
    query = (args or {}).get("query")
    max_results = (args or {}).get("max_results", 8)
    if not str(query or "").strip():
        return {"query": "", "error": "image_search 的 query 不能为空", "results": []}
    limit = int(max_results or 8)

    key = _cache.make_key("search", prompt=str(query),
                          params={"max_results": limit}, provider="bing")
    store = _cache.Cache()
    receipt = None
    hit = store.get(key)
    cached_receipt = ((hit or {}).get("meta") or {}).get("receipt")
    if cached_receipt and _search_receipt_files_alive(cached_receipt):
        receipt = dict(cached_receipt)
        receipt["cache"] = "hit"
    if receipt is None:
        try:
            receipt = _media.search_images_cached(str(query), limit)
        except Exception as exc:  # 兜底：回执里给模型可读的错误而不是中断会话
            return {"query": str(query), "error": str(exc), "results": []}
        receipt["cache"] = "miss"
        if not receipt.get("error"):
            clean = {k: v for k, v in receipt.items() if k != "cache"}
            store.put(key, kind="search", meta={"query": str(query), "receipt": clean})

    cached = [(m.get("title") or "参考图", m.get("local_path") or "")
              for m in receipt.get("results") or [] if m.get("local_path")]
    if cached:
        receipt["markdown"] = "\n".join(
            "![%s](%s)" % (title.replace("]", "").replace("[", ""), path)
            for title, path in cached)
        receipt["hint"] = (
            "把 markdown 字段整段复制进你的回复，图片就会显示在对话框里；"
            "不要只输出路径文本。"
        )
        for title, path in cached:
            if not any(item.get("url") == path for item in LAST_IMAGES):
                LAST_IMAGES.append({"url": path, "alt": title})
    return receipt


def _search_receipt_files_alive(receipt: dict) -> bool:
    """缓存命中的诚实性检查：所有 local_path 指到的文件必须还在盘上。"""
    results = (receipt or {}).get("results") or []
    paths = [m.get("local_path") for m in results if m.get("local_path")]
    if not paths:
        return False
    return all(Path(p).exists() for p in paths)


class AIError(RuntimeError):
    pass


# Metadata of the most recent chat() call: token usage reported by the
# provider (normalized keys), plus the model that actually answered.
LAST_USAGE = {}

# 本轮 chat() 里 image_search 真正缓存到本地的图片 [{url, alt}]
# （规格 §6）：Chat UI 拿它做兜底显示——即使模型没把 local_path 写成
# Markdown，图也要显示在对话框里，不能退化成一行路径文本。
LAST_IMAGES = []


def _record_usage(data):
    """Normalize provider-specific usage payloads into LAST_USAGE."""
    usage = data.get("usage") or data.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        return
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("promptTokenCount")))
    completion = usage.get(
        "completion_tokens",
        usage.get("output_tokens", usage.get("candidatesTokenCount")),
    )
    total = usage.get("total_tokens", usage.get("totalTokenCount"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if prompt is not None:
        LAST_USAGE["prompt_tokens"] = int(prompt)
    if completion is not None:
        LAST_USAGE["completion_tokens"] = int(completion)
    if total is not None:
        LAST_USAGE["total_tokens"] = int(total)


def web_search(query, max_results=5):
    return _web_search(query, max_results)


def _web_search(query, max_results=5):
    """Provider-independent lightweight web search for non-OpenAI providers."""
    import html
    import re
    from urllib.parse import quote
    query = str(query or "").strip()
    if not query:
        raise AIError("web_search 的 query 不能为空")
    limit = max(1, min(int(max_results or 5), 8))
    url = "https://www.bing.com/search?q=" + quote(query) + "&format=rss"
    request = urllib.request.Request(url, headers={"User-Agent": "SP-AI-Assistant/0.4"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise AIError(f"联网搜索失败: {exc}") from exc
    items = re.findall(r"<item>(.*?)</item>", raw, flags=re.S | re.I)
    results = []
    for item in items[:limit]:
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", item, flags=re.S | re.I)
            return html.unescape(re.sub(r"<.*?>", "", m.group(1))).strip() if m else ""
        title, link, desc = tag("title"), tag("link"), tag("description")
        if title and link:
            results.append({"title": title, "url": link, "snippet": desc})
    return {"query": query, "results": results}

def _post(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    max_attempts = 3
    last_error = None
    for attempt in range(max_attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AIError("模型返回了无法解析的 JSON") from exc
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            code = int(exc.code)
            if code not in {408, 425, 429} and not 500 <= code <= 599:
                raise AIError(f"HTTP {code}: {body[:1000]}") from exc
            last_error = AIError(f"HTTP {code}: {body[:1000]}")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = AIError(f"网络请求失败: {exc}")
        except AIError:
            raise
        except Exception as exc:
            last_error = AIError(str(exc))
        if attempt < max_attempts - 1:
            time.sleep(0.8 * (2 ** attempt))
    raise last_error or AIError("AI 请求失败")


def _extract_openai_compatible_content(data):
    choices = data.get("choices") or []
    if not choices:
        raise AIError("服务返回成功，但没有 choices 文本输出")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                if item.get("text"):
                    parts.append(str(item["text"]))
        return "\n".join(parts)
    return ""


def _extract_openai_responses_text(data):
    parts = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()



def _openai_message_content(content, response_api=False):
    """Normalize chat content for OpenAI Responses and Chat Completions APIs."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append({
                "type": "input_text" if response_api else "text",
                "text": str(item.get("text", "")),
            })
        elif kind in {"image_url", "input_image"}:
            url = item.get("data_url") or item.get("url")
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url") or url
            if url:
                if response_api:
                    parts.append({"type": "input_image", "image_url": url})
                else:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _anthropic_content(content):
    if isinstance(content, str):
        return content
    parts = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append({"type": "text", "text": str(item.get("text", ""))})
        elif item.get("type") == "image_url":
            data_url = item.get("data_url") or item.get("url")
            if isinstance(data_url, str) and data_url.startswith("data:"):
                header, encoded = data_url.split(",", 1)
                media_type = header.split(";", 1)[0].replace("data:", "")
                parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": encoded,
                    },
                })
    return parts


def _gemini_parts(content):
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append({"text": str(item.get("text", ""))})
        elif item.get("type") == "image_url":
            data_url = item.get("data_url") or item.get("url")
            if isinstance(data_url, str) and data_url.startswith("data:"):
                header, encoded = data_url.split(",", 1)
                mime = header.split(";", 1)[0].replace("data:", "")
                parts.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": encoded,
                    }
                })
    return parts

def _openai_responses(messages, model, api_key, base_url):
    def _function_tools():
        return [
            {
                "type": "function",
                "name": "painter_actions",
                "description": PAINTER_ACTION_TOOL["function"]["description"],
                "parameters": PAINTER_ACTION_TOOL["function"]["parameters"],
                "strict": False,
            },
            {
                "type": "function",
                "name": "image_search",
                "description": IMAGE_SEARCH_TOOL["function"]["description"],
                "parameters": IMAGE_SEARCH_TOOL["function"]["parameters"],
                "strict": False,
            },
            {
                "type": "function",
                "name": "pbr_generate",
                "description": PBR_GENERATE_TOOL["function"]["description"],
                "parameters": PBR_GENERATE_TOOL["function"]["parameters"],
                "strict": False,
            },
        ]

    input_items = [
        {
            "role": m.get("role"),
            "content": _openai_message_content(m.get("content"), response_api=True),
        }
        for m in messages
    ]
    # 最多 3 轮：image_search 的回执要回传给模型继续生成（规格 §6.2
    # 「视觉模型收到图片后再进行分析」）；painter_actions 仍然终止返回。
    for _round in range(3):
        payload = {
            "model": model,
            "input": input_items,
            "tools": _function_tools() + [{"type": "web_search"}],
            "tool_choice": "auto",
        }
        data = _post(
            base_url.rstrip("/") + "/responses",
            payload,
            {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        )
        _record_usage(data)
        image_call = None
        pbr_call = None
        for item in data.get("output", []) or []:
            if item.get("type") == "function_call" and item.get("name") == "painter_actions":
                arguments = item.get("arguments") or "{}"
                try:
                    plan = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise AIError("OpenAI 返回的 Painter 工具参数不是有效 JSON") from exc
                if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
                    return json.dumps(plan, ensure_ascii=False)
            if item.get("type") == "function_call" and item.get("name") == "image_search":
                image_call = item
            if item.get("type") == "function_call" and item.get("name") == "pbr_generate":
                pbr_call = item
        if pbr_call is not None:
            try:
                pbr_args = json.loads(pbr_call.get("arguments") or "{}")
            except json.JSONDecodeError:
                pbr_args = {}
            pbr_result = _run_pbr_generate(pbr_args)
            input_items = input_items + [
                pbr_call,
                {
                    "type": "function_call_output",
                    "call_id": pbr_call.get("call_id") or pbr_call.get("id"),
                    "output": json.dumps(pbr_result, ensure_ascii=False),
                },
            ]
            continue
        if image_call is not None:
            try:
                args = json.loads(image_call.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_image_search(args)
            input_items = input_items + [
                image_call,
                {
                    "type": "function_call_output",
                    "call_id": image_call.get("call_id") or image_call.get("id"),
                    "output": json.dumps(result, ensure_ascii=False),
                },
            ]
            continue
        break

    text = _extract_openai_responses_text(data)
    if not text:
        raise AIError("OpenAI 返回成功，但没有文本输出或 Painter 工具调用")
    return text


def _openai_compatible(messages, model, api_key, base_url):
    normalized_messages = [{"role": m.get("role"), "content": _openai_message_content(m.get("content"))} for m in messages]
    tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL, IMAGE_SEARCH_TOOL, PBR_GENERATE_TOOL]
    for _round in range(6):
        payload = {"model": model, "messages": normalized_messages, "tools": tools, "tool_choice": "auto"}
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        data = _post(base_url.rstrip("/") + "/chat/completions", payload, headers)
        _record_usage(data)
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            text = _extract_openai_compatible_content(data)
            if text: return text
            raise AIError("兼容 OpenAI 的服务返回成功，但没有文本输出")
        normalized_messages.append(message)
        painter_call = None
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            try: args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError: args = {}
            if name == "painter_actions":
                painter_call = args
                break
            if name == "web_search":
                try: result = _web_search(args.get("query"), args.get("max_results", 5))
                except Exception as exc: result = {"error": str(exc)}
                normalized_messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
            elif name == "image_search":
                # 规格 §6：图片搜索走 MediaObject 管线，回执带本地缓存路径
                result = _run_image_search(args)
                normalized_messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
            elif name == "pbr_generate":
                # 规格 §8–§10：PBR 生成走 PATINA + 质量门 + 资产登记
                result = _run_pbr_generate(args)
                normalized_messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
        if painter_call is not None:
            return json.dumps(painter_call, ensure_ascii=False)
    raise AIError("工具调用超过最大连续轮次，请重新尝试。")

def _anthropic(messages, model, api_key, base_url):
    system_parts = []
    user_messages = []
    for message in messages:
        if message.get("role") == "system":
            system_parts.append(message.get("content") or "")
        else:
            user_messages.append(message)
    tools = [
        {
            # Custom tool keeps the full official function-calling surface so
            # Claude can still drive Painter exactly like the OpenAI path.
            "type": "custom",
            "name": "painter_actions",
            "description": PAINTER_ACTION_TOOL["function"]["description"],
            "input_schema": PAINTER_ACTION_TOOL["function"]["parameters"],
        },
        {
            "type": "custom",
            "name": "image_search",
            "description": IMAGE_SEARCH_TOOL["function"]["description"],
            "input_schema": IMAGE_SEARCH_TOOL["function"]["parameters"],
        },
        {
            "type": "custom",
            "name": "pbr_generate",
            "description": PBR_GENERATE_TOOL["function"]["description"],
            "input_schema": PBR_GENERATE_TOOL["function"]["parameters"],
        },
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
    ]
    payload_messages = [{"role": m.get("role"), "content": _anthropic_content(m.get("content"))} for m in user_messages]
    # 最多 3 轮：image_search 的回执要回传给模型继续分析（规格 §6.2）；
    # painter_actions 仍然终止返回。
    for _round in range(3):
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": payload_messages,
            "tools": tools,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts).strip()
        data = _post(
            base_url.rstrip("/") + "/v1/messages",
            payload,
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        _record_usage(data)
        blocks = data.get("content", []) or []
        painter_plan = None
        image_uses = [
            block for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == "image_search"
        ]
        pbr_uses = [
            block for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
            and block.get("name") == "pbr_generate"
        ]
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "painter_actions"
                and isinstance(block.get("input"), dict)
            ):
                # Claude decided to operate Painter: return the allow-listed plan
                # so the plugin can validate and execute it via the official API.
                painter_plan = json.dumps(block["input"], ensure_ascii=False)
                break
        if painter_plan is not None:
            return painter_plan
        if pbr_uses:
            tool_results = []
            for block in pbr_uses:
                result = _run_pbr_generate(block.get("input") or {})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                })
            payload_messages = payload_messages + [
                {"role": "assistant", "content": blocks},
                {"role": "user", "content": tool_results},
            ]
            continue
        if image_uses:
            tool_results = []
            for block in image_uses:
                result = _run_image_search(block.get("input") or {})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                })
            payload_messages = payload_messages + [
                {"role": "assistant", "content": blocks},
                {"role": "user", "content": tool_results},
            ]
            continue
        break
    text = "\n".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()
    if not text:
        raise AIError("Anthropic 返回成功，但没有找到文本输出")
    return text


def _gemini(messages, model, api_key, base_url):
    contents = []
    system_parts = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            system_parts.append(content)
        else:
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": _gemini_parts(content),
            })
    # 最多 3 轮：image_search 的回执以 functionResponse 回传（规格 §6.2）；
    # painter_actions 仍然终止返回。
    data = None
    for _round in range(3):
        payload = {
            "contents": contents,
            # google_search keeps the model's native web capability; the function
            # declarations let Gemini drive Painter through the same plan pipeline.
            "tools": [
                {"google_search": {}},
                {
                    "functionDeclarations": [
                        {
                            "name": "painter_actions",
                            "description": PAINTER_ACTION_TOOL["function"]["description"],
                            "parameters": PAINTER_ACTION_TOOL["function"]["parameters"],
                        },
                        {
                            "name": "image_search",
                            "description": IMAGE_SEARCH_TOOL["function"]["description"],
                            "parameters": IMAGE_SEARCH_TOOL["function"]["parameters"],
                        },
                        {
                            "name": "pbr_generate",
                            "description": PBR_GENERATE_TOOL["function"]["description"],
                            "parameters": PBR_GENERATE_TOOL["function"]["parameters"],
                        },
                    ]
                },
            ],
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        data = _post(
            base_url.rstrip("/") + f"/v1beta/models/{model}:generateContent",
            payload,
            {"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )
        _record_usage(data)
        candidates = data.get("candidates") or []
        if not candidates:
            raise AIError("Gemini 返回成功，但没有候选输出")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        painter_plan = None
        image_calls = []
        pbr_calls = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall")
            if not isinstance(call, dict):
                continue
            if call.get("name") == "painter_actions" and isinstance(call.get("args"), dict):
                # Gemini decided to operate Painter: return the allow-listed plan
                # for local validation and execution via the official API.
                painter_plan = json.dumps(call["args"], ensure_ascii=False)
                break
            if call.get("name") == "image_search":
                image_calls.append(call)
            if call.get("name") == "pbr_generate":
                pbr_calls.append(call)
        if painter_plan is not None:
            return painter_plan
        if pbr_calls:
            contents = contents + [
                {"role": "model", "parts": [{"functionCall": call} for call in pbr_calls]},
                {"role": "user", "parts": [
                    {
                        "functionResponse": {
                            "name": "pbr_generate",
                            "response": _run_pbr_generate(call.get("args") or {}),
                        }
                    }
                    for call in pbr_calls
                ]},
            ]
            continue
        if image_calls:
            contents = contents + [
                {"role": "model", "parts": [{"functionCall": call} for call in image_calls]},
                {"role": "user", "parts": [
                    {
                        "functionResponse": {
                            "name": "image_search",
                            "response": _run_image_search(call.get("args") or {}),
                        }
                    }
                    for call in image_calls
                ]},
            ]
            continue
        break
    text = "\n".join(
        str(p.get("text", "")) for p in parts
        if isinstance(p, dict) and p.get("text")
    ).strip()
    if not text:
        raise AIError("Gemini 返回成功，但没有找到文本输出")
    return text


def chat(provider_name: str, messages: list[dict], model: str, api_key: str, base_url: str = "") -> str:
    info = PROVIDERS.get(provider_name)
    if not info:
        raise AIError("未知 AI 提供商")
    if not model:
        raise AIError("尚未设置模型")
    if not api_key:
        raise AIError("尚未设置 API Key")
    LAST_USAGE.clear()
    LAST_USAGE["model"] = model
    LAST_USAGE["provider"] = provider_name
    LAST_IMAGES.clear()  # 每轮对话重置兜底图（规格 §6）
    base = (base_url or info["base_url"]).strip()
    provider_id = info["id"]
    if provider_id == "openai":
        return _openai_responses(messages, model, api_key, base)
    if provider_id in {"openai_compatible", "deepseek", "kimi", "qwen", "glm", "minimax"}:
        return _openai_compatible(messages, model, api_key, base)
    if provider_id == "anthropic":
        return _anthropic(messages, model, api_key, base)
    if provider_id == "gemini":
        return _gemini(messages, model, api_key, base)
    raise AIError("未实现的 AI 提供商")
