#!/usr/bin/env python3
"""Merge the fused aesthetic graph, complete car subgraph, and style edges."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        EXPRESSES_STYLE_LABEL,
        iter_jsonl,
        write_jsonl,
    )
else:
    from .common import EXPRESSES_STYLE_LABEL, iter_jsonl, write_jsonl


EXPRESSES_STYLE_PROPERTY_ALLOWLIST = {
    "style",
    "score",
    "confidence",
    "parameters",
}


def is_car_record(record: Dict[str, Any]) -> bool:
    return str(record.get("id") or "").startswith("car_")


def compact_style_edge(record: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(record)
    properties = record.get("properties") or {}
    copied["properties"] = {
        key: properties[key]
        for key in ("style", "score", "confidence", "parameters")
        if key in properties
    }
    unexpected = set(copied["properties"]) - EXPRESSES_STYLE_PROPERTY_ALLOWLIST
    if unexpected:
        raise ValueError("Unexpected EXPRESSES_STYLE properties: %s" % sorted(unexpected))
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Build unified automobile+aesthetic graph")
    parser.add_argument("--aesthetic-graph", type=Path, required=True)
    parser.add_argument("--car-graph", type=Path, required=True)
    parser.add_argument("--style-relationships", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    aesthetic = list(iter_jsonl(args.aesthetic_graph))
    cars = [row for row in iter_jsonl(args.car_graph) if is_car_record(row)]
    style_edges = []
    for row in iter_jsonl(args.style_relationships):
        if row.get("type") != "relationship" or row.get("label") != EXPRESSES_STYLE_LABEL:
            raise SystemExit("Style relationship input contains a non-EXPRESSES_STYLE record")
        style_edges.append(compact_style_edge(row))

    records = aesthetic + cars + style_edges
    node_ids: Set[str] = set()
    relationship_ids: Set[str] = set()
    duplicate_node_ids: List[str] = []
    duplicate_relationship_ids: List[str] = []
    nodes = 0
    relationships = 0
    for row in records:
        identifier = str(row.get("id"))
        if row.get("type") == "node":
            nodes += 1
            if identifier in node_ids:
                duplicate_node_ids.append(identifier)
            node_ids.add(identifier)
        elif row.get("type") == "relationship":
            relationships += 1
            if identifier in relationship_ids:
                duplicate_relationship_ids.append(identifier)
            relationship_ids.add(identifier)

    missing_endpoints = []
    relation_types = Counter()
    for row in records:
        if row.get("type") != "relationship":
            continue
        relation_types[str(row.get("label"))] += 1
        start_id = str((row.get("start") or {}).get("id"))
        end_id = str((row.get("end") or {}).get("id"))
        if start_id not in node_ids or end_id not in node_ids:
            missing_endpoints.append(str(row.get("id")))

    style_keys = Counter()
    invalid_style_properties = []
    for row in style_edges:
        properties = row.get("properties") or {}
        key = (str((row.get("start") or {}).get("id")), str(properties.get("style")))
        style_keys[key] += 1
        if set(properties) != EXPRESSES_STYLE_PROPERTY_ALLOWLIST:
            invalid_style_properties.append(str(row.get("id")))
        try:
            parameters = json.loads(str(properties.get("parameters") or ""))
        except json.JSONDecodeError:
            invalid_style_properties.append(str(row.get("id")))
            continue
        if not isinstance(parameters, list) or not all(isinstance(name, str) for name in parameters):
            invalid_style_properties.append(str(row.get("id")))

    duplicate_style_keys = [key for key, count in style_keys.items() if count > 1]
    if duplicate_node_ids or duplicate_relationship_ids or missing_endpoints:
        raise SystemExit("Unified graph failed ID or endpoint integrity checks")
    if duplicate_style_keys or invalid_style_properties:
        raise SystemExit("EXPRESSES_STYLE failed uniqueness or property checks")

    output_count = write_jsonl(args.output, records)
    report = {
        "sources": {
            "aesthetic_graph": str(args.aesthetic_graph),
            "car_graph": str(args.car_graph),
            "style_relationships": str(args.style_relationships),
        },
        "source_records": {
            "aesthetic": len(aesthetic),
            "car": len(cars),
            "expresses_style": len(style_edges),
        },
        "output": str(args.output),
        "output_records": output_count,
        "nodes": nodes,
        "relationships": relationships,
        "relationship_type_counts": dict(relation_types),
        "duplicate_node_ids": len(duplicate_node_ids),
        "duplicate_relationship_ids": len(duplicate_relationship_ids),
        "missing_relationship_endpoints": len(missing_endpoints),
        "duplicate_car_style_keys": len(duplicate_style_keys),
        "invalid_expresses_style_properties": len(invalid_style_properties),
        "expresses_style_property_allowlist": sorted(EXPRESSES_STYLE_PROPERTY_ALLOWLIST),
        "removed_expresses_style_properties": [
            "evidence",
            "parameter_source",
            "model",
            "prompt_version",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
