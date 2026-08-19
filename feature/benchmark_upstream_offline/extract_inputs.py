#!/usr/bin/env python3
"""Extract feature inputs and graph-backed Style/Type rubrics."""

from __future__ import annotations

import argparse
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .constants import (
    CAR_INSTANCE_LABEL, CAR_LEVEL_LABEL, CAR_TYPE_LABEL, CAR_TYPES, CONTAINS_LABEL,
    DESIGN_PARAMETER_LABEL, DIMENSION_FIELDS, EXPRESSES_STYLE_LABEL, FEATURE_LABELS,
    GUIDES_LABEL, ROUTE_BY_CAR_TYPE, STYLES, STYLE_LABEL,
)
from .io_utils import clean_value, first_text, iter_jsonl, parse_inner, sha256_file, write_json, write_jsonl


def node_snapshot(node: Dict[str, Any]) -> Dict[str, Any]:
    properties = dict(node.get("properties") or {})
    inner = parse_inner(properties.get("properties"))
    description = first_text(
        inner.get("description(描述)"), inner.get("description"),
        inner.get("keyFeatures(关键特征)"), inner.get("keyFeatures"),
        inner.get("trendDescription(趋势描述)"), inner.get("postureDescription(姿态描述)"),
    )
    return {
        "node_id": str(node.get("id")),
        "labels": list(node.get("labels") or []),
        "name": first_text(properties.get("name"), inner.get("name")),
        "description": description,
        "properties": clean_value(inner),
        "source_properties": clean_value({key: value for key, value in properties.items() if key != "properties"}),
    }


def numeric(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def style_name(endpoint: Dict[str, Any]) -> str:
    name = str((endpoint.get("properties") or {}).get("name") or "").strip()
    if name in STYLES:
        return name
    node_id = str(endpoint.get("id") or "")
    for candidate in STYLES:
        if node_id == "meixue_style_" + candidate:
            return candidate
    return ""


def guide_details(relationship: Dict[str, Any]) -> Dict[str, Any]:
    end = relationship.get("end") or {}
    end_props = end.get("properties") or {}
    parameter_inner = parse_inner(end_props.get("properties"))
    result = {
        "parameter": first_text(end_props.get("name"), parameter_inner.get("parameterName(参数名称)")),
        "description": first_text(parameter_inner.get("description(描述)"), parameter_inner.get("description")),
    }
    return result


def extract_graph(graph_path: Path) -> Dict[str, Any]:
    features: List[Dict[str, Any]] = []
    node_by_id: Dict[str, Dict[str, Any]] = {}
    style_nodes: Dict[str, Dict[str, Any]] = {}
    type_nodes: Dict[str, Dict[str, Any]] = {}
    style_guides: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    instances_by_type: DefaultDict[str, Set[str]] = defaultdict(set)
    instance_props: Dict[str, Dict[str, Any]] = {}
    level_by_instance: DefaultDict[str, List[str]] = defaultdict(list)
    styles_by_instance: DefaultDict[str, List[str]] = defaultdict(list)

    for record in iter_jsonl(graph_path):
        if record.get("type") == "node":
            node_id = str(record.get("id"))
            node_by_id[node_id] = record
            labels = set(record.get("labels") or [])
            if labels.intersection(FEATURE_LABELS):
                features.append(node_snapshot(record))
            if STYLE_LABEL in labels:
                name = str((record.get("properties") or {}).get("name") or "")
                if name in STYLES:
                    style_nodes[name] = record
            if CAR_TYPE_LABEL in labels:
                name = str((record.get("properties") or {}).get("name") or "")
                if name in CAR_TYPES:
                    type_nodes[name] = record
            if CAR_INSTANCE_LABEL in labels:
                instance_props[node_id] = dict(record.get("properties") or {})
            continue

        if record.get("type") != "relationship":
            continue
        label = record.get("label")
        start = record.get("start") or {}
        end = record.get("end") or {}
        start_labels = set(start.get("labels") or [])
        end_labels = set(end.get("labels") or [])

        if label == GUIDES_LABEL and DESIGN_PARAMETER_LABEL in end_labels:
            source_style = style_name(start)
            if source_style:
                style_guides[source_style].append(guide_details(record))
        elif label == CONTAINS_LABEL and CAR_TYPE_LABEL in start_labels and CAR_INSTANCE_LABEL in end_labels:
            car_type = str((start.get("properties") or {}).get("name") or "")
            instance_id = str(end.get("id"))
            if car_type in CAR_TYPES:
                instances_by_type[car_type].add(instance_id)
                instance_props[instance_id] = dict(end.get("properties") or instance_props.get(instance_id, {}))
        elif label == CONTAINS_LABEL and CAR_LEVEL_LABEL in start_labels and CAR_INSTANCE_LABEL in end_labels:
            level = str((start.get("properties") or {}).get("name") or "")
            instance_id = str(end.get("id"))
            if level:
                level_by_instance[instance_id].append(level)
            # These endpoint snapshots contain normalized body dimensions in this graph.
            merged = dict(instance_props.get(instance_id, {}))
            merged.update(end.get("properties") or {})
            instance_props[instance_id] = merged
        elif label == EXPRESSES_STYLE_LABEL and CAR_INSTANCE_LABEL in start_labels:
            target_style = style_name(end)
            if target_style:
                styles_by_instance[str(start.get("id"))].append(target_style)

    features.sort(key=lambda row: row["node_id"])
    return {
        "features": features,
        "node_by_id": node_by_id,
        "style_nodes": style_nodes,
        "type_nodes": type_nodes,
        "style_guides": style_guides,
        "instances_by_type": instances_by_type,
        "instance_props": instance_props,
        "level_by_instance": level_by_instance,
        "styles_by_instance": styles_by_instance,
    }


def build_style_rubric(extracted: Dict[str, Any]) -> Dict[str, Any]:
    styles: Dict[str, Any] = {}
    for style in STYLES:
        seen: Set[Tuple[str, str]] = set()
        guides: List[Dict[str, Any]] = []
        for guide in extracted["style_guides"].get(style, []):
            key = (str(guide.get("parameter") or ""), str(guide.get("description") or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            guides.append(guide)
        guides.sort(key=lambda row: (row.get("parameter", ""), row.get("description", "")))
        styles[style] = {
            "node_id": str((extracted["style_nodes"].get(style) or {}).get("id") or "meixue_style_" + style),
            "guide_count": len(guides),
            "guides": guides,
        }
    return {"styles": styles, "source_relation": GUIDES_LABEL}


def rounded_median(values: Sequence[float]) -> Any:
    value = median(values)
    return int(value) if float(value).is_integer() else round(float(value), 2)


def build_type_rubric(extracted: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for car_type in CAR_TYPES:
        instance_ids = sorted(extracted["instances_by_type"].get(car_type, set()))
        levels: Counter = Counter()
        styles: Counter = Counter()
        dimensions: DefaultDict[str, List[float]] = defaultdict(list)
        for instance_id in instance_ids:
            levels.update(extracted["level_by_instance"].get(instance_id, []))
            styles.update(extracted["styles_by_instance"].get(instance_id, []))
            properties = extracted["instance_props"].get(instance_id, {})
            for field in DIMENSION_FIELDS:
                value = numeric(properties.get(field))
                if value is not None:
                    dimensions[field].append(value)
        node = extracted["type_nodes"].get(car_type) or {}
        result[car_type] = {
            "node_id": str(node.get("id") or ""),
            "route_type": ROUTE_BY_CAR_TYPE[car_type],
            "sample_count": len(instance_ids),
            "level_distribution": dict(sorted(levels.items(), key=lambda item: (-item[1], item[0]))),
            "top_styles": [
                {"name": name, "count": count}
                for name, count in sorted(styles.items(), key=lambda item: (-item[1], item[0]))[:3]
            ],
            "dimension_medians": {
                field: {"median": rounded_median(values), "sample_count": len(values)}
                for field, values in dimensions.items()
            },
        }
    return {"car_types": result, "route_mapping": ROUTE_BY_CAR_TYPE}


def allocate_sample_sizes(counts: Dict[str, int], size: int) -> Dict[str, int]:
    if size < 0:
        raise ValueError("sample size must be non-negative")
    target = min(size, sum(counts.values()))
    labels = [label for label in FEATURE_LABELS if counts.get(label, 0)]
    allocation = {label: 0 for label in labels}
    if not labels:
        return allocation
    base = target // len(labels)
    for label in labels:
        allocation[label] = min(base, counts[label])
    remaining = target - sum(allocation.values())
    while remaining:
        available = [label for label in labels if allocation[label] < counts[label]]
        if not available:
            break
        for label in available:
            if not remaining:
                break
            allocation[label] += 1
            remaining -= 1
    return allocation


def sample_features(records: Sequence[Dict[str, Any]], size: int, seed: int) -> List[Dict[str, Any]]:
    grouped: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        matched = [label for label in FEATURE_LABELS if label in (record.get("labels") or [])]
        if matched:
            grouped[matched[0]].append(record)
    allocation = allocate_sample_sizes({label: len(rows) for label, rows in grouped.items()}, size)
    selected: List[Dict[str, Any]] = []
    for index, label in enumerate(FEATURE_LABELS):
        rows = list(grouped.get(label, []))
        random.Random(seed + index * 1009).shuffle(rows)
        for row in rows[: allocation.get(label, 0)]:
            copy = dict(row)
            copy["sample_stratum"] = label
            selected.append(copy)
    selected.sort(key=lambda row: row["node_id"])
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()

    extracted = extract_graph(args.graph)
    style_rubric = build_style_rubric(extracted)
    type_rubric = build_type_rubric(extracted)
    sample = sample_features(extracted["features"], args.sample_size, args.seed)
    counts = Counter(row["labels"][0] for row in extracted["features"])
    sample_counts = Counter(row["sample_stratum"] for row in sample)

    write_jsonl(args.output_dir / "features_all.jsonl", extracted["features"])
    write_jsonl(args.output_dir / ("features_smoke_%d.jsonl" % len(sample)), sample)
    write_json(args.output_dir / "style_rubric.json", style_rubric)
    write_json(args.output_dir / "type_rubric.json", type_rubric)
    report = {
        "graph": str(args.graph),
        "graph_sha256": sha256_file(args.graph),
        "feature_count": len(extracted["features"]),
        "feature_counts_by_label": {label: counts[label] for label in FEATURE_LABELS},
        "sample_count": len(sample),
        "sample_seed": args.seed,
        "sample_counts_by_label": {label: sample_counts[label] for label in FEATURE_LABELS},
        "style_count": len(style_rubric["styles"]),
        "style_guide_counts": {name: value["guide_count"] for name, value in style_rubric["styles"].items()},
        "car_type_count": len(type_rubric["car_types"]),
        "car_instance_count": sum(value["sample_count"] for value in type_rubric["car_types"].values()),
        "level_assignment_count": sum(
            sum(value["level_distribution"].values()) for value in type_rubric["car_types"].values()
        ),
    }
    write_json(args.output_dir / "extraction_report.json", report)
    print(__import__("json").dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
