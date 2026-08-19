#!/usr/bin/env python3
"""Attach graph-derived DesignParameter names to style Judge results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import MAIN_STYLES, read_jsonl, write_jsonl  # type: ignore
else:
    from .common import MAIN_STYLES, read_jsonl, write_jsonl


def latest_ok(path: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(path):
        if row.get("status") == "ok" and row.get("instance_id") is not None:
            result[str(row["instance_id"])] = row
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich style results with graph DesignParameter names")
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-instances", type=int, default=800)
    args = parser.parse_args()

    criteria = json.loads(args.criteria.read_text(encoding="utf-8"))
    parameter_names = {
        style: [str(item["name"]) for item in (criteria.get("styles") or {}).get(style, [])]
        for style in MAIN_STYLES
    }
    judged = latest_ok(args.judge_output)
    if len(judged) != args.expected_instances:
        raise SystemExit(
            "Expected %d successful instances, found %d"
            % (args.expected_instances, len(judged))
        )

    output = []
    style_counts = Counter()
    for instance_id in sorted(
        judged, key=lambda value: int(value) if value.isdigit() else value
    ):
        row = dict(judged[instance_id])
        enriched_styles = []
        for item in row.get("styles") or []:
            enriched = dict(item)
            style = str(enriched.get("style"))
            enriched["parameters"] = list(parameter_names.get(style, []))
            enriched["parameter_source"] = "AestheticConcept-[Guides]->DesignParameter"
            style_counts[style] += 1
            enriched_styles.append(enriched)
        row["styles"] = enriched_styles
        row["parameters_enriched_by_program"] = True
        output.append(row)

    count = write_jsonl(args.output, output)
    report = {
        "successful_judged_instances": len(judged),
        "output_instances": count,
        "style_counts": {style: style_counts[style] for style in MAIN_STYLES},
        "empty_style_instances": sum(1 for row in output if not row.get("styles")),
        "parameter_counts_by_style": {
            style: len(parameter_names[style]) for style in MAIN_STYLES
        },
        "parameters_are_graph_derived": True,
        "parameters_contain_vehicle_values": False,
        "output": str(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
