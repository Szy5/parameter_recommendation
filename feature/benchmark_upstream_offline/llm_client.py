"""Minimal OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def chat_completion(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float],
    timeout: int,
    use_env_proxy: bool = False,
    prompt_cache_key: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], str]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener() if use_env_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            response_request_id = response.headers.get("x-request-id", "")
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:3000]
        raise RuntimeError("LLM HTTP %s: %s" % (exc.code, error_body)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("LLM connection failed: %s" % exc.reason) from exc
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("unexpected LLM response shape") from exc
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    request_id = str(body.get("id") or response_request_id or "")
    return text, usage, request_id
