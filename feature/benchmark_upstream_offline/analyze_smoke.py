#!/usr/bin/env python3
"""Summarize model runs and compare labels on identical smoke samples."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from .constants import MODEL_PRICES_USD_PER_MILLION, ROUTE_BY_CAR_TYPE
from .io_utils import iter_jsonl, write_json


def usage_tokens(usage: Dict[str, Any]) -> Tuple[int, int, int]:
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0) if isinstance(details, dict) else 0
    return input_tokens, output_tokens, cached_tokens


def estimated_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int) -> Optional[float]:
    prices = MODEL_PRICES_USD_PER_MILLION.get(model)
    if not prices:
        return None
    ordinary_input = max(0, input_tokens - cached_tokens)
    value = (
        ordinary_input * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000.0
    return round(value, 6)


def latest_rows(path: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not path.exists():
        return rows
    for row in iter_jsonl(path):
        key = (str(row.get("task") or path.name.split("_", 1)[0]), str(row.get("node_id")))
        # A later success must not be replaced by a later historical failure, but a success replaces failure.
        if row.get("status") == "ok" or key not in rows:
            rows[key] = row
    return rows


def model_rows(model_dir: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    combined: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for task in ("style", "type"):
        combined.update(latest_rows(model_dir / (task + "_results.jsonl")))
    return combined


def audit_rows(model_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for task in ("style", "type"):
        path = model_dir / (task + "_results.jsonl")
        if path.exists():
            rows.extend(iter_jsonl(path))
    return rows


def labels(row: Dict[str, Any]) -> Tuple[str, ...]:
    key = "styles" if row.get("task") == "style" else "types"
    return tuple(sorted(str(item["name"]) for item in row.get(key, []) if isinstance(item, dict) and item.get("name")))


def summarize_model(
    model: str,
    rows: Dict[Tuple[str, str], Dict[str, Any]],
    all_audit_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    by_task: Dict[str, Any] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    for task in ("style", "type"):
        task_rows = [row for (row_task, _), row in rows.items() if row_task == task]
        ok_rows = [row for row in task_rows if row.get("status") == "ok"]
        label_counts: Counter = Counter()
        input_tokens = output_tokens = cached_tokens = 0
        retries = 0
        edge_count = 0
        label_count_histogram: Counter = Counter()
        cross_route_nonempty = 0
        for row in task_rows:
            if row.get("status") == "ok":
                current_labels = labels(row)
                edge_count += len(current_labels)
                label_counts.update(current_labels)
                label_count_histogram[len(current_labels)] += 1
                if task == "type" and current_labels:
                    routes = {ROUTE_BY_CAR_TYPE[name] for name in current_labels}
                    cross_route_nonempty += len(routes) > 1
        task_audit_rows = [row for row in all_audit_rows if row.get("task") == task]
        api_call_count = 0
        for row in task_audit_rows:
            history = row.get("attempt_history") or []
            usages = [attempt.get("usage") or {} for attempt in history if isinstance(attempt, dict)]
            if not usages:
                usages = [row.get("usage") or {}]
            api_call_count += len(usages)
            retries += max(0, len(usages) - 1)
            for usage in usages:
                current_input, current_output, current_cached = usage_tokens(usage)
                input_tokens += current_input
                output_tokens += current_output
                cached_tokens += current_cached
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["cached_input_tokens"] += cached_tokens
        nonempty_count = sum(bool(labels(row)) for row in ok_rows)
        by_task[task] = {
            "task_keys": len(task_rows),
            "ok": len(ok_rows),
            "error": len(task_rows) - len(ok_rows),
            "success_rate": round(len(ok_rows) / len(task_rows), 4) if task_rows else None,
            "empty_rate_among_ok": round(sum(not labels(row) for row in ok_rows) / len(ok_rows), 4) if ok_rows else None,
            "edge_count": edge_count,
            "mean_edges_per_ok": round(edge_count / len(ok_rows), 4) if ok_rows else None,
            "mean_edges_per_nonempty": round(edge_count / nonempty_count, 4) if nonempty_count else None,
            "multi_label_rate_among_ok": round(
                sum(len(labels(row)) > 1 for row in ok_rows) / len(ok_rows), 4
            ) if ok_rows else None,
            "label_count_histogram": {str(key): value for key, value in sorted(label_count_histogram.items())},
            "cross_route_rate_among_nonempty": (
                round(cross_route_nonempty / nonempty_count, 4) if task == "type" and nonempty_count else None
            ),
            "label_counts": dict(sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))),
            "audit_record_count": len(task_audit_rows),
            "api_call_count": api_call_count,
            "retry_count": retries,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_tokens,
            "estimated_cost_usd": estimated_cost(model, input_tokens, output_tokens, cached_tokens),
        }
    return {
        "model": model,
        "pricing_usd_per_million_tokens": MODEL_PRICES_USD_PER_MILLION.get(model),
        "pricing_as_of": "2026-08-17",
        "tasks": by_task,
        **totals,
        "estimated_cost_usd": estimated_cost(
            model, totals["input_tokens"], totals["output_tokens"], totals["cached_input_tokens"]
        ),
    }


def compare_models(
    left_model: str,
    left: Dict[Tuple[str, str], Dict[str, Any]],
    right_model: str,
    right: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"models": [left_model, right_model], "tasks": {}}
    for task in ("style", "type"):
        keys = sorted(
            key for key in set(left).intersection(right)
            if key[0] == task and left[key].get("status") == "ok" and right[key].get("status") == "ok"
        )
        exact = 0
        jaccards: List[float] = []
        empty_disagreements = 0
        disagreements: List[Dict[str, Any]] = []
        for key in keys:
            left_set, right_set = set(labels(left[key])), set(labels(right[key]))
            exact += left_set == right_set
            union = left_set | right_set
            jaccards.append(len(left_set & right_set) / len(union) if union else 1.0)
            empty_disagreements += bool(left_set) != bool(right_set)
            if left_set != right_set and len(disagreements) < 50:
                disagreements.append({
                    "node_id": key[1], left_model: sorted(left_set), right_model: sorted(right_set),
                })
        result["tasks"][task] = {
            "comparable_ok_pairs": len(keys),
            "exact_label_set_agreement": round(exact / len(keys), 4) if keys else None,
            "mean_jaccard": round(sum(jaccards) / len(jaccards), 4) if keys else None,
            "empty_nonempty_disagreements": empty_disagreements,
            "first_50_label_disagreements": disagreements,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = {model: model_rows(args.input_root / model) for model in args.models}
    audits = {model: audit_rows(args.input_root / model) for model in args.models}
    report: Dict[str, Any] = {
        "note": "Agreement is a quality proxy, not accuracy; this smoke set has no human gold labels.",
        "models": {model: summarize_model(model, rows[model], audits[model]) for model in args.models},
    }
    if len(args.models) == 2:
        report["comparison"] = compare_models(args.models[0], rows[args.models[0]], args.models[1], rows[args.models[1]])
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
