#!/usr/bin/env python3
"""Run resumable Style and Type judges over extracted feature nodes."""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import PROMPT_VERSION
from .constants import CAR_TYPES, STYLES
from .io_utils import (
    append_jsonl, extract_json_object, load_llm_config, prompt_hash, read_jsonl, write_json,
)
from .llm_client import chat_completion
from .prompts import prompt_cache_key as build_prompt_cache_key
from .prompts import prompt_manifest, system_prompt, user_prompt


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_temperature(value: str) -> Optional[float]:
    if value.lower() in ("none", "null", "omit"):
        return None
    return float(value)


def normalize_result(task: str, raw: Dict[str, Any], feature: Dict[str, Any]) -> Dict[str, Any]:
    expected_id = str(feature["node_id"])
    if str(raw.get("node_id")) != expected_id:
        raise ValueError("node_id mismatch: expected %s" % expected_id)
    key = "styles" if task == "style" else "types"
    allowed = set(STYLES if task == "style" else CAR_TYPES)
    values = raw.get(key)
    if not isinstance(values, list):
        raise ValueError("%s must be a list" % key)
    normalized: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise ValueError("%s[%d] must be an object" % (key, index))
        name = str(item.get("name") or "").strip()
        if name not in allowed:
            raise ValueError("unknown %s label: %s" % (task, name))
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is empty for %s" % name)
        # The model is instructed not to emit weak edges. Defense in depth drops them.
        if confidence < 0.65 or name in seen:
            continue
        seen.add(name)
        normalized.append({"name": name, "confidence": round(confidence, 3), "reason": reason})
    return {"node_id": expected_id, key: normalized}


def successful_ids(path: Path, task: str, model: str) -> Set[str]:
    done: Set[str] = set()
    if not path.exists():
        return done
    for row in read_jsonl(path):
        if (
            row.get("status") == "ok" and row.get("task") == task and row.get("model") == model
            and row.get("prompt_version") == PROMPT_VERSION
        ):
            done.add(str(row.get("node_id")))
    return done


def run_one(
    task: str,
    feature: Dict[str, Any],
    rubric: Dict[str, Any],
    api_key: str,
    base_url: str,
    model: str,
    temperature: Optional[float],
    timeout: int,
    max_retries: int,
    use_env_proxy: bool = False,
) -> Dict[str, Any]:
    system = system_prompt(task, rubric)
    original_user = user_prompt(task, feature)
    cache_key = build_prompt_cache_key(model, task, rubric)
    active_user = original_user
    history: List[Dict[str, Any]] = []
    final_raw = ""
    final_usage: Dict[str, Any] = {}
    last_error = ""
    started = time.monotonic()
    for attempt in range(max_retries + 1):
        call_started = time.monotonic()
        try:
            final_raw, final_usage, request_id = chat_completion(
                api_key, base_url, model, system, active_user, temperature, timeout,
                use_env_proxy, cache_key,
            )
            parsed = extract_json_object(final_raw)
            normalized = normalize_result(task, parsed, feature)
            history.append({
                "attempt": attempt + 1,
                "status": "ok",
                "called_at": now_iso(),
                "elapsed_seconds": round(time.monotonic() - call_started, 3),
                "prompt_hash": prompt_hash(system, active_user),
                "request_id": request_id,
                "usage": final_usage,
                "raw_response": final_raw,
            })
            return {
                **normalized,
                "task": task,
                "status": "ok",
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "prompt_cache_key": cache_key,
                "prompt_hash": prompt_hash(system, original_user),
                "source_label": feature.get("sample_stratum") or (feature.get("labels") or [""])[0],
                "judged_at": now_iso(),
                "attempt": attempt + 1,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": final_usage,
                "raw_response": final_raw,
                "attempt_history": history,
            }
        except Exception as exc:  # Persist every record-level failure for audit/recovery.
            last_error = "%s: %s" % (type(exc).__name__, str(exc))
            history.append({
                "attempt": attempt + 1,
                "status": "error",
                "called_at": now_iso(),
                "elapsed_seconds": round(time.monotonic() - call_started, 3),
                "prompt_hash": prompt_hash(system, active_user),
                "usage": final_usage,
                "error": last_error,
                "raw_response": final_raw,
            })
            if attempt < max_retries:
                active_user = original_user + (
                    "\n\n上次响应未通过程序校验（%s）。请从原任务重新判断，只返回合法 JSON；"
                    "node_id 必须原样返回，标签必须来自封闭词表，低于 0.65 的关系不要输出。" % last_error
                )
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
    return {
        "node_id": str(feature["node_id"]),
        "task": task,
        "status": "error",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_cache_key": cache_key,
        "prompt_hash": prompt_hash(system, original_user),
        "source_label": feature.get("sample_stratum") or (feature.get("labels") or [""])[0],
        "judged_at": now_iso(),
        "attempt": max_retries + 1,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "usage": final_usage,
        "error": last_error,
        "raw_response": final_raw,
        "attempt_history": history,
    }


def task_output(output_dir: Path, task: str) -> Path:
    return output_dir / (task + "_results.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("style", "type", "both"), default="both")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rubric-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path("feature/.env"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--temperature", type=parse_temperature, default=0.1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--use-env-proxy", action="store_true",
        help="Honor HTTP(S)_PROXY variables. Default is direct access to the explicit BASE_URL.",
    )
    parser.add_argument(
        "--no-warmup", action="store_true",
        help="Skip the default one synchronous cache-warmup request per selected task.",
    )
    args = parser.parse_args()

    api_key, base_url, model = load_llm_config(args.env, args.model)
    features = read_jsonl(args.input)
    if args.limit > 0:
        features = features[: args.limit]
    tasks = ["style", "type"] if args.task == "both" else [args.task]
    rubrics = {
        "style": json.loads((args.rubric_dir / "style_rubric.json").read_text(encoding="utf-8")),
        "type": json.loads((args.rubric_dir / "type_rubric.json").read_text(encoding="utf-8")),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "prompt_manifest.json", prompt_manifest(rubrics))
    lock = threading.Lock()
    pending: List[Tuple[str, Dict[str, Any]]] = []
    existing_done: Dict[str, int] = {}
    for task in tasks:
        done = successful_ids(task_output(args.output_dir, task), task, model)
        existing_done[task] = len(done.intersection(str(row["node_id"]) for row in features))
        pending.extend((task, row) for row in features if str(row["node_id"]) not in done)

    print(
        "model=%s tasks=%s features=%d pending=%d workers=%d temperature=%s"
        % (model, ",".join(tasks), len(features), len(pending), args.workers, args.temperature),
        flush=True,
    )
    counters = {task: {"ok": 0, "error": 0} for task in tasks}
    started = time.monotonic()

    def call(item: Tuple[str, Dict[str, Any]]) -> Dict[str, Any]:
        task, feature = item
        return run_one(
            task, feature, rubrics[task], api_key, base_url, model,
            args.temperature, args.timeout, args.max_retries, args.use_env_proxy,
        )

    pending_total = len(pending)
    warmup_items: List[Tuple[str, Dict[str, Any]]] = []
    concurrent_items: List[Tuple[str, Dict[str, Any]]] = []
    warmed_tasks: Set[str] = set()
    for item in pending:
        item_task = item[0]
        if not args.no_warmup and item_task not in warmed_tasks:
            warmup_items.append(item)
            warmed_tasks.add(item_task)
        else:
            concurrent_items.append(item)

    completed_before_pool = 0
    for item in warmup_items:
        print("warming prompt cache for task=%s node_id=%s" % (item[0], item[1]["node_id"]), flush=True)
        result = call(item)
        result_task = str(result["task"])
        append_jsonl(task_output(args.output_dir, result_task), result)
        counters[result_task][str(result["status"])] += 1
        completed_before_pool += 1
        cached = ((result.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        print(
            "warmup completed=%d/%d status=%s cached_tokens=%s"
            % (completed_before_pool, pending_total, result["status"], cached),
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(call, item) for item in concurrent_items]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            task = str(result["task"])
            with lock:
                append_jsonl(task_output(args.output_dir, task), result)
                counters[task][str(result["status"])] += 1
            total_completed = completed_before_pool + index
            if total_completed % 20 == 0 or index == len(futures):
                print("completed=%d/%d counters=%s" % (total_completed, pending_total, counters), flush=True)

    meta = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "input": str(args.input),
        "rubric_dir": str(args.rubric_dir),
        "tasks": tasks,
        "requested_features": len(features),
        "requested_task_count": len(features) * len(tasks),
        "already_done": existing_done,
        "new_results": counters,
        "workers": args.workers,
        "temperature": args.temperature,
        "max_retries": args.max_retries,
        "use_env_proxy": args.use_env_proxy,
        "warmup_enabled": not args.no_warmup,
        "warmup_task_count": len(warmup_items),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "finished_at": now_iso(),
    }
    # Keep the familiar latest-run file and task-specific/history files so a
    # later Type-only resume cannot erase evidence of an earlier Style run.
    write_json(args.output_dir / "run_meta.json", meta)
    write_json(args.output_dir / ("run_meta_" + "_".join(tasks) + ".json"), meta)
    append_jsonl(args.output_dir / "run_history.jsonl", meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if any(values["error"] for values in counters.values()):
        raise SystemExit("Judge completed with errors; repeat the same command to retry failed task keys")


if __name__ == "__main__":
    main()
