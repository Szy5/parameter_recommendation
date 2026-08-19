#!/usr/bin/env python3
"""Run concept-fusion or EXPRESSES_STYLE LLM-as-Judge jobs.

The runner is resumable, stores raw model responses, and validates every result
before it can be used downstream. It never logs API credentials.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        BODY_PARAMETER_UNITS,
        MAIN_STYLES,
        append_jsonl,
        chat_completion,
        clamp_float,
        extract_json_object,
        load_llm_config,
        prompt_hash,
        read_jsonl,
        retry_delay,
    )
    from feature_v2.parameter_recommendation.prompts import (  # type: ignore
        CONCEPT_SYSTEM_PROMPT,
        PROMPT_VERSION,
        STYLE_SYSTEM_PROMPT,
        concept_user_prompt,
        prompt_markdown,
        style_user_prompt,
    )
else:
    from .common import (
        BODY_PARAMETER_UNITS,
        MAIN_STYLES,
        append_jsonl,
        chat_completion,
        clamp_float,
        extract_json_object,
        load_llm_config,
        prompt_hash,
        read_jsonl,
        retry_delay,
    )
    from .prompts import (
        CONCEPT_SYSTEM_PROMPT,
        PROMPT_VERSION,
        STYLE_SYSTEM_PROMPT,
        concept_user_prompt,
        prompt_markdown,
        style_user_prompt,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def task_id(task: str, record: Dict[str, Any]) -> str:
    return str(record["concept_id"] if task == "concept_fusion" else record["instance_id"])


def load_done_ids(path: Path, task: str) -> Set[str]:
    if not path.exists():
        return set()
    id_key = "concept_id" if task == "concept_fusion" else "instance_id"
    done: Set[str] = set()
    for row in read_jsonl(path):
        if row.get("status") == "ok" and row.get(id_key) is not None:
            done.add(str(row[id_key]))
    return done


def normalize_concept(raw: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    can_merge = raw.get("can_merge")
    if not isinstance(can_merge, bool):
        raise ValueError("can_merge must be boolean")
    target = raw.get("target_style")
    if target in ("", "null", "None"):
        target = None
    if can_merge and target not in MAIN_STYLES:
        raise ValueError("target_style must be one of the seven main styles")
    if not can_merge:
        target = None
    confidence = round(clamp_float(raw.get("confidence")), 3)
    if can_merge and confidence < 0.65:
        raise ValueError("can_merge=true requires confidence >= 0.65")
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason is empty")
    return {
        "concept_id": str(record["concept_id"]),
        "concept_name": record.get("name"),
        "can_merge": can_merge,
        "target_style": target,
        "confidence": confidence,
        "reason": reason,
        "pilot_stratum": record.get("pilot_stratum"),
    }


def numeric_or_original(value: Any) -> Any:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def normalize_styles(raw: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    styles_raw = raw.get("styles")
    if not isinstance(styles_raw, list):
        raise ValueError("styles must be a list")
    body = record.get("body_parameters") or {}
    seen: Set[str] = set()
    styles: List[Dict[str, Any]] = []
    for item in styles_raw:
        if not isinstance(item, dict):
            raise ValueError("each style must be an object")
        style = str(item.get("style") or "").strip()
        if style not in MAIN_STYLES:
            raise ValueError("unknown style: %s" % style)
        if style in seen:
            raise ValueError("duplicate style: %s" % style)
        seen.add(style)
        score = round(clamp_float(item.get("score")), 3)
        confidence = round(clamp_float(item.get("confidence")), 3)
        if score < 0.65 or confidence < 0.65:
            raise ValueError("returned styles require score and confidence >= 0.65")
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            raise ValueError("style evidence is empty")
        names_raw = item.get("parameter_names") or []
        if not isinstance(names_raw, list):
            raise ValueError("parameter_names must be a list")
        names: List[str] = []
        for name_value in names_raw:
            name = str(name_value).strip()
            if name not in body:
                raise ValueError("parameter is absent from source body node: %s" % name)
            if name not in BODY_PARAMETER_UNITS:
                raise ValueError("parameter is outside allowed schema: %s" % name)
            if name not in names:
                names.append(name)
        if len(names) > 5:
            raise ValueError("parameter_names exceeds maximum of 5")
        parameters = [
            {
                "name": name,
                "value": numeric_or_original(body[name]),
                "unit": BODY_PARAMETER_UNITS[name],
            }
            for name in names
        ]
        styles.append(
            {
                "style": style,
                "score": score,
                "confidence": confidence,
                "evidence": evidence,
                "parameters": parameters,
            }
        )
    return {
        "instance_id": str(record["instance_id"]),
        "model_name": record.get("model_name"),
        "car_class": record.get("car_class"),
        "styles": styles,
        "pilot_stratum": record.get("pilot_stratum"),
    }


def judge_one(
    task: str,
    record: Dict[str, Any],
    api_key: str,
    base_url: str,
    model: str,
    temperature: Optional[float],
    max_retries: int,
    timeout: int,
) -> Dict[str, Any]:
    if task == "concept_fusion":
        system_prompt = CONCEPT_SYSTEM_PROMPT
        user_prompt = concept_user_prompt(record)
        images = None
    else:
        system_prompt = STYLE_SYSTEM_PROMPT
        user_prompt = style_user_prompt(record)
        images = record.get("image_urls") or []

    raw_response = ""
    usage: Dict[str, Any] = {}
    last_error = ""
    attempt_history: List[Dict[str, Any]] = []
    active_user_prompt = user_prompt
    for attempt in range(max_retries + 1):
        try:
            raw_response, usage = chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=active_user_prompt,
                image_urls=images,
                temperature=temperature,
                timeout=timeout,
            )
            parsed = extract_json_object(raw_response)
            normalized = (
                normalize_concept(parsed, record)
                if task == "concept_fusion"
                else normalize_styles(parsed, record)
            )
            attempt_history.append(
                {
                    "attempt": attempt + 1,
                    "status": "ok",
                    "called_at": now_iso(),
                    "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                    "usage": usage,
                    "raw_response": raw_response,
                }
            )
            return {
                **normalized,
                "task": task,
                "status": "ok",
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                "judged_at": now_iso(),
                "attempt": attempt + 1,
                "usage": usage,
                "raw_response": raw_response,
                "attempt_history": attempt_history,
            }
        except Exception as exc:  # noqa: BLE001 - errors are persisted per record
            last_error = "%s: %s" % (type(exc).__name__, str(exc))
            attempt_history.append(
                {
                    "attempt": attempt + 1,
                    "status": "error",
                    "called_at": now_iso(),
                    "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                    "usage": usage,
                    "error": last_error,
                    "raw_response": raw_response,
                }
            )
            if attempt < max_retries:
                active_user_prompt = user_prompt + (
                    "\n\n上次响应未通过程序校验（%s）。"
                    "请从原始任务重新判断并只返回一个合法 JSON。"
                    "对概念融合，target_style 只能是单个主风格或 null；"
                    "若无法唯一归类则设 can_merge=false。" % last_error
                )
                retry_delay(attempt)

    id_key = "concept_id" if task == "concept_fusion" else "instance_id"
    return {
        id_key: task_id(task, record),
        "task": task,
        "status": "error",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
        "judged_at": now_iso(),
        "attempt": max_retries + 1,
        "error": last_error,
        "raw_response": raw_response,
        "attempt_history": attempt_history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run parameter-recommendation LLM judge")
    parser.add_argument("--task", choices=["concept_fusion", "expresses_style"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    api_key, base_url, model = load_llm_config(args.env, model_override=args.model)
    records = read_jsonl(args.input)
    if args.limit > 0:
        records = records[: args.limit]
    done = load_done_ids(args.output, args.task)
    pending = [record for record in records if task_id(args.task, record) not in done]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    (args.output.parent / "prompt_version.md").write_text(prompt_markdown(), encoding="utf-8")
    started = time.time()
    ok_count = 0
    error_count = 0
    lock = threading.Lock()

    print(
        "task=%s model=%s total=%d done=%d pending=%d workers=%d"
        % (args.task, model, len(records), len(done), len(pending), args.workers),
        flush=True,
    )

    def run(record: Dict[str, Any]) -> Dict[str, Any]:
        return judge_one(
            task=args.task,
            record=record,
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=args.temperature,
            max_retries=args.max_retries,
            timeout=args.timeout,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run, record) for record in pending]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            with lock:
                append_jsonl(args.output, result)
                if result.get("status") == "ok":
                    ok_count += 1
                else:
                    error_count += 1
            if index % 10 == 0 or index == len(futures):
                print("completed=%d/%d ok=%d error=%d" % (index, len(futures), ok_count, error_count), flush=True)

    meta = {
        "task": args.task,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "input": str(args.input),
        "output": str(args.output),
        "requested_records": len(records),
        "already_done": len(done),
        "new_ok": ok_count,
        "new_error": error_count,
        "workers": args.workers,
        "temperature": args.temperature,
        "max_retries": args.max_retries,
        "elapsed_seconds": round(time.time() - started, 3),
        "finished_at": now_iso(),
    }
    args.meta.parent.mkdir(parents=True, exist_ok=True)
    args.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if error_count:
        raise SystemExit("Judge completed with %d errors" % error_count)


if __name__ == "__main__":
    main()
