from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


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
    "OpenAI Compatible": {
        "id": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "models": ["custom"],
    },
}


class AIError(RuntimeError):
    pass


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


def _openai_responses(messages, model, api_key, base_url):
    data = _post(
        base_url.rstrip("/") + "/responses",
        {"model": model, "input": messages},
        {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    text = _extract_openai_responses_text(data)
    if not text:
        raise AIError("OpenAI 返回成功，但没有找到文本输出")
    return text


def _openai_compatible(messages, model, api_key, base_url):
    payload = {"model": model, "messages": messages}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = _post(
        base_url.rstrip("/") + "/chat/completions",
        payload,
        headers,
    )
    text = _extract_openai_compatible_content(data)
    if not text:
        raise AIError("兼容 OpenAI 的服务返回成功，但没有找到文本输出")
    return text

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
        "messages": user_messages,
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
                "parts": [{"text": content}],
            })
    payload = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    data = _post(
        base_url.rstrip("/") + f"/v1beta/models/{model}:generateContent",
        payload,
        {"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise AIError("Gemini 返回成功，但没有候选输出")
    parts = (candidates[0].get("content") or {}).get("parts") or []
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
    if not api_key and info["id"] != "openai_compatible":
        raise AIError("尚未设置 API Key")
    base = (base_url or info["base_url"]).strip()
    provider_id = info["id"]
    if provider_id == "openai":
        return _openai_responses(messages, model, api_key, base)
    if provider_id == "openai_compatible":
        return _openai_compatible(messages, model, api_key, base)
    if provider_id == "anthropic":
        return _anthropic(messages, model, api_key, base)
    if provider_id == "gemini":
        return _gemini(messages, model, api_key, base)
    raise AIError("未实现的 AI 提供商")
