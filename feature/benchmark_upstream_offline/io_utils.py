"""Small, Python 3.8-compatible JSON and environment helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d is not valid JSONL" % (path, line_number)) from exc
            if not isinstance(value, dict):
                raise ValueError("%s:%d is not a JSON object" % (path, line_number))
            yield value


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    temporary.replace(path)
    return count


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


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


def clean_value(value: Any, max_list: int = 30) -> Any:
    if isinstance(value, dict):
        return {str(key): clean_value(item, max_list) for key, item in value.items() if item not in (None, "")}
    if isinstance(value, list):
        return [clean_value(item, max_list) for item in value[:max_list]]
    return value


def labels_include(endpoint: Dict[str, Any], label: str) -> bool:
    return label in (endpoint.get("labels") or [])


def first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            joined = "; ".join(str(item).strip() for item in value if str(item).strip())
            if joined:
                return joined
    return ""


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def prompt_hash(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256((system_prompt + "\n---\n" + user_prompt).encode("utf-8")).hexdigest()


def load_env(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"").strip("'")
    return values


def load_llm_config(path: Path, model_override: Optional[str]) -> Tuple[str, str, str]:
    values = load_env(path)
    api_key = os.getenv("API_KEY", values.get("API_KEY", "")).strip()
    base_url = os.getenv("BASE_URL", values.get("BASE_URL", "")).strip().rstrip("/")
    model = (model_override or os.getenv("MODEL_NAME", values.get("MODEL_NAME", ""))).strip()
    if not api_key or not base_url or not model:
        raise ValueError("API_KEY, BASE_URL and MODEL_NAME (or --model) are required")
    return api_key, base_url, model

