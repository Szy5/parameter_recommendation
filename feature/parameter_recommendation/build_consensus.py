#!/usr/bin/env python3
"""Build conservative two-pass Judge consensus artifacts.

Concept disagreements are kept as original entities.  EXPRESSES_STYLE labels
and their parameter evidence must occur in both runs before graph insertion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import read_jsonl, write_jsonl  # type: ignore
else:
    from .common import read_jsonl, write_jsonl


def latest_ok(path: Path, key: str) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(path):
        if row.get("status") == "ok" and row.get(key) is not None:
            rows[str(row[key])] = row
    return rows


def concept_decision(row: Dict[str, Any]) -> str:
    return str(row.get("target_style")) if row.get("can_merge") else "KEEP_ORIGINAL"


def concept_consensus(first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
    left = concept_decision(first)
    right = concept_decision(second)
    agreed = left == right
    can_merge = agreed and left != "KEEP_ORIGINAL"
    return {
        "concept_id": str(first["concept_id"]),
        "concept_name": first.get("concept_name"),
        "can_merge": can_merge,
        "target_style": left if can_merge else None,
        "confidence": min(float(first.get("confidence", 0)), float(second.get("confidence", 0))),
        "reason": "PASS1: %s\nPASS2: %s" % (first.get("reason", ""), second.get("reason", "")),
        "consensus_status": "agreed" if agreed else "disagreement_keep_original",
        "requires_review": not agreed,
        "source_decisions": [left, right],
        "task": "concept_fusion",
        "status": "ok",
        "model": "%s + %s" % (first.get("model"), second.get("model")),
        "prompt_version": first.get("prompt_version"),
        "source_prompt_versions": [first.get("prompt_version"), second.get("prompt_version")],
    }


def parameter_names(style: Dict[str, Any]) -> Set[str]:
    return {str(item.get("name")) for item in style.get("parameters") or []}


def style_consensus(first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
    left = {str(item["style"]): item for item in first.get("styles") or []}
    right = {str(item["style"]): item for item in second.get("styles") or []}
    styles: List[Dict[str, Any]] = []
    for style_name in sorted(set(left) & set(right)):
        left_item = left[style_name]
        right_item = right[style_name]
        stable_names = parameter_names(left_item) & parameter_names(right_item)
        source_parameters = {
            str(item.get("name")): item for item in left_item.get("parameters") or []
        }
        styles.append(
            {
                "style": style_name,
                "score": min(float(left_item.get("score", 0)), float(right_item.get("score", 0))),
                "confidence": min(
                    float(left_item.get("confidence", 0)), float(right_item.get("confidence", 0))
                ),
                "evidence": "PASS1: %s\nPASS2: %s"
                % (left_item.get("evidence", ""), right_item.get("evidence", "")),
                "parameters": [source_parameters[name] for name in sorted(stable_names)],
                "parameter_consensus": "intersection",
            }
        )
    left_labels = sorted(left)
    right_labels = sorted(right)
    return {
        "instance_id": str(first["instance_id"]),
        "model_name": first.get("model_name"),
        "car_class": first.get("car_class"),
        "styles": styles,
        "consensus_status": "agreed" if left_labels == right_labels else "label_intersection",
        "requires_review": left_labels != right_labels,
        "source_style_labels": [left_labels, right_labels],
        "task": "expresses_style",
        "status": "ok",
        "model": "%s + %s" % (first.get("model"), second.get("model")),
        "prompt_version": first.get("prompt_version"),
        "source_prompt_versions": [first.get("prompt_version"), second.get("prompt_version")],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build conservative two-pass Judge consensus")
    parser.add_argument("--task", choices=["concept_fusion", "expresses_style"], required=True)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    key = "concept_id" if args.task == "concept_fusion" else "instance_id"
    first = latest_ok(args.first, key)
    second = latest_ok(args.second, key)
    shared = sorted(set(first) & set(second), key=lambda value: int(value) if value.isdigit() else value)
    if set(first) != set(second):
        raise SystemExit("Consensus inputs must have identical successful record IDs")
    if args.task == "concept_fusion":
        output = [concept_consensus(first[item], second[item]) for item in shared]
    else:
        output = [style_consensus(first[item], second[item]) for item in shared]

    write_jsonl(args.output, output)
    report = {
        "task": args.task,
        "records": len(output),
        "agreed_records": sum(1 for row in output if not row.get("requires_review")),
        "review_records": sum(1 for row in output if row.get("requires_review")),
        "first": str(args.first),
        "second": str(args.second),
        "output": str(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
