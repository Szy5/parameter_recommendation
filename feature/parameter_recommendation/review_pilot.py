#!/usr/bin/env python3
"""Produce structural and consistency QA reports for pilot Judge runs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import MAIN_STYLES, read_jsonl  # type: ignore
else:
    from .common import MAIN_STYLES, read_jsonl


def latest_ok(path: Path, key: str) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return output
    for row in read_jsonl(path):
        if row.get("status") == "ok" and row.get(key) is not None:
            output[str(row[key])] = row
    return output


def error_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for row in read_jsonl(path) if row.get("status") != "ok")


def concept_report(input_path: Path, output_path: Path, repeat_path: Optional[Path]) -> Dict[str, Any]:
    inputs = {str(row["concept_id"]): row for row in read_jsonl(input_path)}
    outputs = latest_ok(output_path, "concept_id")
    target_counts = Counter(row.get("target_style") or "KEEP_ORIGINAL" for row in outputs.values())
    low_conf_merges = [
        concept_id
        for concept_id, row in outputs.items()
        if row.get("can_merge") and float(row.get("confidence", 0)) < 0.75
    ]
    forced_control_merges = [
        concept_id
        for concept_id, row in outputs.items()
        if inputs.get(concept_id, {}).get("pilot_stratum") == "unaligned_control" and row.get("can_merge")
    ]
    report: Dict[str, Any] = {
        "input_count": len(inputs),
        "ok_count": len(outputs),
        "error_count": error_count(output_path),
        "coverage_rate": round(len(outputs) / len(inputs), 4) if inputs else 0,
        "merge_count": sum(1 for row in outputs.values() if row.get("can_merge")),
        "keep_original_count": sum(1 for row in outputs.values() if not row.get("can_merge")),
        "target_counts": dict(target_counts),
        "low_confidence_merge_ids": low_conf_merges,
        "unaligned_control_merge_ids": forced_control_merges,
    }
    if repeat_path:
        repeated = latest_ok(repeat_path, "concept_id")
        shared = sorted(set(outputs) & set(repeated))
        exact = 0
        for concept_id in shared:
            left = outputs[concept_id]
            right = repeated[concept_id]
            if (
                bool(left.get("can_merge")) == bool(right.get("can_merge"))
                and left.get("target_style") == right.get("target_style")
            ):
                exact += 1
        report["repeat_shared_count"] = len(shared)
        report["repeat_exact_decision_rate"] = round(exact / len(shared), 4) if shared else None
    return report


def style_names(row: Dict[str, Any], threshold: float = 0.65) -> Set[str]:
    return {
        str(item.get("style"))
        for item in row.get("styles") or []
        if float(item.get("score", 0)) >= threshold and float(item.get("confidence", 0)) >= threshold
    }


def style_report(input_path: Path, output_path: Path, repeat_path: Optional[Path]) -> Dict[str, Any]:
    inputs = {str(row["instance_id"]): row for row in read_jsonl(input_path)}
    outputs = latest_ok(output_path, "instance_id")
    target_counts: Counter = Counter()
    empty = []
    empty_parameters = []
    for instance_id, row in outputs.items():
        styles = row.get("styles") or []
        if not styles:
            empty.append(instance_id)
        for item in styles:
            target_counts[item.get("style")] += 1
            if not item.get("parameters"):
                empty_parameters.append({"instance_id": instance_id, "style": item.get("style")})

    report: Dict[str, Any] = {
        "input_count": len(inputs),
        "ok_count": len(outputs),
        "error_count": error_count(output_path),
        "coverage_rate": round(len(outputs) / len(inputs), 4) if inputs else 0,
        "style_counts": dict(target_counts),
        "empty_style_instance_ids": empty,
        "empty_parameter_style_rows": empty_parameters,
        "multi_label_instance_count": sum(1 for row in outputs.values() if len(row.get("styles") or []) > 1),
    }
    if repeat_path:
        repeated = latest_ok(repeat_path, "instance_id")
        shared = sorted(set(outputs) & set(repeated))
        exact = 0
        jaccards: List[float] = []
        for instance_id in shared:
            left = style_names(outputs[instance_id])
            right = style_names(repeated[instance_id])
            exact += left == right
            union = left | right
            jaccards.append(len(left & right) / len(union) if union else 1.0)
        report["repeat_shared_count"] = len(shared)
        report["repeat_exact_label_rate"] = round(exact / len(shared), 4) if shared else None
        report["repeat_mean_jaccard"] = round(sum(jaccards) / len(jaccards), 4) if jaccards else None
    return report


def markdown_report(report: Dict[str, Any]) -> str:
    concept = report["concept_fusion"]
    style = report["expresses_style"]
    return """# Parameter Recommendation Pilot Review

## Concept fusion

- Inputs: {ci}
- Valid outputs: {co}
- Errors: {ce}
- Merge / keep original: {cm} / {ck}
- Target counts: `{ct}`
- Unaligned controls incorrectly merged (manual review required): `{cu}`
- Repeat exact decision rate: `{cr}`

## EXPRESSES_STYLE

- Inputs: {si}
- Valid outputs: {so}
- Errors: {se}
- Style counts: `{st}`
- Empty style instances: `{sx}`
- Style rows without direct geometric parameter evidence (allowed): `{sp}`
- Repeat exact label rate: `{sr}`
- Repeat mean Jaccard: `{sj}`

## Automated gate

Automated checks only validate structure and repeat consistency. Semantic accuracy must still be manually reviewed before any full run.
""".format(
        ci=concept.get("input_count"),
        co=concept.get("ok_count"),
        ce=concept.get("error_count"),
        cm=concept.get("merge_count"),
        ck=concept.get("keep_original_count"),
        ct=concept.get("target_counts"),
        cu=concept.get("unaligned_control_merge_ids"),
        cr=concept.get("repeat_exact_decision_rate", "not run"),
        si=style.get("input_count"),
        so=style.get("ok_count"),
        se=style.get("error_count"),
        st=style.get("style_counts"),
        sx=style.get("empty_style_instance_ids"),
        sp=style.get("empty_parameter_style_rows"),
        sr=style.get("repeat_exact_label_rate", "not run"),
        sj=style.get("repeat_mean_jaccard", "not run"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Review parameter-recommendation pilot")
    parser.add_argument("--concept-input", type=Path, required=True)
    parser.add_argument("--concept-output", type=Path, required=True)
    parser.add_argument("--style-input", type=Path, required=True)
    parser.add_argument("--style-output", type=Path, required=True)
    parser.add_argument("--concept-repeat", type=Path, default=None)
    parser.add_argument("--style-repeat", type=Path, default=None)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    report = {
        "concept_fusion": concept_report(args.concept_input, args.concept_output, args.concept_repeat),
        "expresses_style": style_report(args.style_input, args.style_output, args.style_repeat),
        "allowed_styles": MAIN_STYLES,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
