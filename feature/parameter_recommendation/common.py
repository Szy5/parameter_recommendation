#!/usr/bin/env python3
"""Shared helpers for the parameter-recommendation pipeline.

The project runs on Python 3.8, so this module intentionally uses only the
standard library and typing forms supported by that version.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]

AC_LABEL = "AestheticConcept(美学概念)"
DP_LABEL = "DesignParameter(设计参数)"
CAR_CLASS_LABEL = "汽车级别"
CAR_INSTANCE_LABEL = "汽车实例"
BODY_LABEL = "车身"
GUIDES_LABEL = "Guides(指导)"
CONTAINS_LABEL = "包含"
EXPRESSES_STYLE_LABEL = "EXPRESSES_STYLE"

MAIN_STYLES = ["科技", "运动", "豪华", "硬派越野", "简约", "商务", "复古"]

BODY_PARAMETER_UNITS = {
    "长度(mm)": "mm",
    "宽度(mm)": "mm",
    "高度(mm)": "mm",
    "轴距(mm)": "mm",
    "A柱倾角": "°",
    "尾倾角": "°",
    "离地间隙": "mm",
    "接近角(°)": "°",
    "离去角(°)": "°",
}


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        for line_no, line in enumerate(file, 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d is not valid JSONL" % (path, line_no)) from exc


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")
            count += 1
    return count


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def parse_inner(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            texts = [str(item).strip() for item in value if str(item).strip()]
            if texts:
                return "; ".join(texts)
    return ""


def labels_contain(endpoint: Dict[str, Any], label: str) -> bool:
    return label in (endpoint.get("labels") or [])


def extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise
        result = json.loads(match.group(0))
    if not isinstance(result, dict):
        raise ValueError("LLM response is not a JSON object")
    return result


def clamp_float(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    number = float(value)
    return max(minimum, min(maximum, number))


def prompt_hash(system_prompt: str, user_prompt: str) -> str:
    payload = (system_prompt + "\n---\n" + user_prompt).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    return values


def load_llm_config(env_path: Path, model_override: Optional[str] = None) -> Tuple[str, str, str]:
    file_values = load_env_file(env_path)
    api_key = os.getenv("API_KEY", file_values.get("API_KEY", "")).strip()
    base_url = os.getenv("BASE_URL", file_values.get("BASE_URL", "")).strip().rstrip("/")
    model = (model_override or os.getenv("MODEL_NAME", file_values.get("MODEL_NAME", ""))).strip()
    if not api_key or not base_url or not model:
        raise ValueError(".env must provide API_KEY, BASE_URL and MODEL_NAME (or pass --model)")
    return api_key, base_url, model


def chat_completion(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_urls: Optional[Sequence[str]] = None,
    temperature: Optional[float] = 0.1,
    timeout: int = 180,
) -> Tuple[str, Dict[str, Any]]:
    content: Any = user_prompt
    if image_urls:
        blocks: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for url in image_urls:
            if isinstance(url, str) and url.strip():
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": url.strip(), "detail": "low"},
                    }
                )
        content = blocks

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    }
    if temperature is not None:
        payload["temperature"] = temperature

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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError("LLM HTTP %s: %s" % (exc.code, body)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("LLM connection failed: %s" % exc.reason) from exc

    try:
        text = result["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Unexpected LLM response shape") from exc
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return text, usage


def retry_delay(attempt: int) -> None:
    time.sleep(min(8.0, 1.5 * (attempt + 1)))


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
    return cleaned.strip("_") or "unknown"


def endpoint_snapshot(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node["id"],
        "labels": list(node.get("labels") or []),
        "properties": dict(node.get("properties") or {}),
    }

