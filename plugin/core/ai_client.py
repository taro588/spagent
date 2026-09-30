from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request

from core.tools import painter_action_tool


AI_CLIENT_BUILD = "0.6.0"

PROVIDERS = {
    "OpenAI": {
        "id": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.6"],
    },
    "Anthropic": {
        "id": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
    },
    "Google Gemini": {
        "id": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
    },
    "DeepSeek": {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "Kimi": {
        "id": "kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.5", "kimi-k2"],
    },
    "Qwen": {
        "id": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus"],
    },
    "GLM": {
        "id": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5-turbo", "glm-5"],
    },
    "MiniMax": {
        "id": "minimax",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3"],
    },
    "OpenAI Compatible": {
        "id": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "models": ["custom"],
    },
}

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
