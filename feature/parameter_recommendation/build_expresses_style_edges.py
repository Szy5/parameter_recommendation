#!/usr/bin/env python3
"""Build EXPRESSES_STYLE relationships from enriched single-pass results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        CAR_INSTANCE_LABEL,
        EXPRESSES_STYLE_LABEL,
        MAIN_STYLES,
        endpoint_snapshot,
        iter_jsonl,
        safe_slug,
        write_jsonl,
    )
else:
    from .common import (
        AC_LABEL,
        CAR_INSTANCE_LABEL,
        EXPRESSES_STYLE_LABEL,
        MAIN_STYLES,
        endpoint_snapshot,
        iter_jsonl,
        safe_slug,
        write_jsonl,
    )


def latest_ok(path: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("status") == "ok" and row.get("instance_id") is not None:
            result[str(row["instance_id"])] = row
    return result


def car_nodes(path: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("type") != "node" or CAR_INSTANCE_LABEL not in (row.get("labels") or []):
            continue
        identifier = str(row.get("id"))
        if identifier.startswith("car_"):
            result[identifier[len("car_") :]] = row
    return result


def style_nodes(path: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("type") != "node" or AC_LABEL not in (row.get("labels") or []):
            continue
        name = str((row.get("properties") or {}).get("name") or "")
        if name in MAIN_STYLES and str(row.get("id")).startswith("meixue_style_"):
            result[name] = row
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build graph-context EXPRESSES_STYLE edges")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--car-graph", type=Path, required=True)
    parser.add_argument("--aesthetic-graph", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-instances", type=int, default=800)
    args = parser.parse_args()

    judged = latest_ok(args.results)
    cars = car_nodes(args.car_graph)
    styles = style_nodes(args.aesthetic_graph)
    if len(judged) != args.expected_instances:
        raise SystemExit("Expected %d result instances, found %d" % (args.expected_instances, len(judged)))
    if len(cars) != args.expected_instances:
        raise SystemExit("Expected %d car instance nodes, found %d" % (args.expected_instances, len(cars)))
    if set(styles) != set(MAIN_STYLES):
        raise SystemExit("Aesthetic graph does not contain exactly the seven canonical style nodes")

    edges = []
    style_counts = Counter()
    missing_endpoints = 0
    for instance_id in sorted(judged, key=lambda value: int(value) if value.isdigit() else value):
        row = judged[instance_id]
        car = cars.get(instance_id)
        if car is None:
            missing_endpoints += 1
            continue
        for item in row.get("styles") or []:
            style = str(item.get("style"))
            style_node = styles.get(style)
            if style_node is None:
                missing_endpoints += 1
                continue
            parameters = item.get("parameters") or []
            if not isinstance(parameters, list) or not all(isinstance(name, str) for name in parameters):
                raise SystemExit("parameters must be a list of DesignParameter names")
            edges.append(
                {
                    "type": "relationship",
                    "id": "fusion_expresses_style_%s_%s"
                    % (instance_id, safe_slug(style)),
                    "label": EXPRESSES_STYLE_LABEL,
                    "properties": {
                        "style": style,
                        "score": item.get("score"),
                        "confidence": item.get("confidence"),
                        "evidence": item.get("evidence"),
                        "parameters": json.dumps(
                            parameters, ensure_ascii=False, separators=(",", ":")
                        ),
                        "parameter_source": item.get("parameter_source"),
                        "model": row.get("model"),
                        "prompt_version": row.get("prompt_version"),
                    },
                    "start": endpoint_snapshot(car),
                    "end": endpoint_snapshot(style_node),
                }
            )
            style_counts[style] += 1

    count = write_jsonl(args.output, edges)
    report = {
        "result_instances": len(judged),
        "car_instance_nodes": len(cars),
        "canonical_style_nodes": len(styles),
        "expresses_style_relationships": count,
        "style_counts": {style: style_counts[style] for style in MAIN_STYLES},
        "missing_endpoints": missing_endpoints,
        "parameters_storage": "JSON string containing DesignParameter name array",
        "output": str(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
