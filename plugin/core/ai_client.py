from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request

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
#   image_generation  图像生成（§8 PBR 云端生成接线后开放）
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
        "capabilities": {"vision", "web_search", "tool_calling"},
        # 模型级覆盖：key 是模型名小写的子串，值为增量（add/remove）。
        "model_capabilities": {},
    },
    "Anthropic": {
        "id": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "capabilities": {"vision", "web_search", "tool_calling"},
        "model_capabilities": {},
    },
    "Google Gemini": {
        "id": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "capabilities": {"vision", "web_search", "tool_calling"},
        "model_capabilities": {},
    },
    "DeepSeek": {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        # 官方 chat/reasoner 均为纯文本模型；web_search 走 Bing 兜底函数。
        "capabilities": {"web_search", "tool_calling"},
        "model_capabilities": {},
    },
    "Kimi": {
        "id": "kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.5", "kimi-k2"],
        # kimi-k2 系列为纯文本；moonshot 的视觉模型在 vl 线上（kimi-latest 等）。
        "capabilities": {"web_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "Qwen": {
        "id": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus"],
        # qwen3.x chat 系列为纯文本；换入 qwen-vl-* 视觉模型时按子串匹配放开。
        "capabilities": {"web_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "GLM": {
        "id": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5-turbo", "glm-5"],
        # glm-5 系列为纯文本；GLM 的视觉模型在 glm-4v 线上。
        "capabilities": {"web_search", "tool_calling"},
        "model_capabilities": {"4v": {"add": ["vision"]}},
    },
    "MiniMax": {
        "id": "minimax",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3"],
        # M 系列为纯文本；MiniMax 的视觉模型走 vision 线（MiniMax-VL 等）。
        "capabilities": {"web_search", "tool_calling"},
        "model_capabilities": {"vl": {"add": ["vision"]}},
    },
    "OpenAI Compatible": {
        "id": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "models": ["custom"],
        # 本地服务的 wire path 支持图片（image_url content）；能否真用取决于
        # 用户本地装的是什么模型 —— 这由用户自己判断，插件不做阻断。
        "capabilities": {"vision", "web_search", "tool_calling"},
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


class AIError(RuntimeError):
    pass


# Metadata of the most recent chat() call: token usage reported by the
# provider (normalized keys), plus the model that actually answered.
LAST_USAGE = {}


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
    response_tool = {
        "type": "function",
        "name": "painter_actions",
        "description": PAINTER_ACTION_TOOL["function"]["description"],
        "parameters": PAINTER_ACTION_TOOL["function"]["parameters"],
        "strict": False,
    }
    payload = {
        "model": model,
        "input": [
            {
                "role": m.get("role"),
                "content": _openai_message_content(m.get("content"), response_api=True),
            }
            for m in messages
        ],
        "tools": [response_tool, {"type": "web_search"}],
        "tool_choice": "auto",
    }
    data = _post(
        base_url.rstrip("/") + "/responses",
        payload,
        {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    _record_usage(data)

    # Responses API returns a function_call item when the model decides to
    # operate Painter. The plugin executes that allow-listed plan locally
    # through Adobe's official Painter Python API.
    for item in data.get("output", []) or []:
        if item.get("type") == "function_call" and item.get("name") == "painter_actions":
            arguments = item.get("arguments") or "{}"
            try:
                plan = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise AIError("OpenAI 返回的 Painter 工具参数不是有效 JSON") from exc
            if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
                return json.dumps(plan, ensure_ascii=False)

    text = _extract_openai_responses_text(data)
    if not text:
        raise AIError("OpenAI 返回成功，但没有文本输出或 Painter 工具调用")
    return text


def _openai_compatible(messages, model, api_key, base_url):
    normalized_messages = [{"role": m.get("role"), "content": _openai_message_content(m.get("content"))} for m in messages]
    tools = [PAINTER_ACTION_TOOL, WEB_SEARCH_TOOL]
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
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": m.get("role"), "content": _anthropic_content(m.get("content"))} for m in user_messages],
        "tools": [
            {
                # Custom tool keeps the full official function-calling surface so
                # Claude can still drive Painter exactly like the OpenAI path.
                "type": "custom",
                "name": "painter_actions",
                "description": PAINTER_ACTION_TOOL["function"]["description"],
                "input_schema": PAINTER_ACTION_TOOL["function"]["parameters"],
            },
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
        ],
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
    for block in data.get("content", []) or []:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "painter_actions"
            and isinstance(block.get("input"), dict)
        ):
            # Claude decided to operate Painter: return the allow-listed plan
            # so the plugin can validate and execute it via the official API.
            return json.dumps(block["input"], ensure_ascii=False)
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
    payload = {
        "contents": contents,
        # google_search keeps the model's native web capability; the function
        # declaration lets Gemini drive Painter through the same plan pipeline.
        "tools": [
            {"google_search": {}},
            {
                "functionDeclarations": [
                    {
                        "name": "painter_actions",
                        "description": PAINTER_ACTION_TOOL["function"]["description"],
                        "parameters": PAINTER_ACTION_TOOL["function"]["parameters"],
                    }
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
    for part in parts:
        if not isinstance(part, dict):
            continue
        call = part.get("functionCall")
        if (
            isinstance(call, dict)
            and call.get("name") == "painter_actions"
            and isinstance(call.get("args"), dict)
        ):
            # Gemini decided to operate Painter: return the allow-listed plan
            # for local validation and execution via the official API.
            return json.dumps(call["args"], ensure_ascii=False)
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
