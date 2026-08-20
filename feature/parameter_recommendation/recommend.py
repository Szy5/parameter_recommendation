#!/usr/bin/env python3
"""Query the fused graph for the three supported recommendation modes."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        BODY_LABEL,
        BODY_PARAMETER_UNITS,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        EXPRESSES_STYLE_LABEL,
        GUIDES_LABEL,
        iter_jsonl,
        parse_inner,
    )
else:
    from .common import (
        AC_LABEL,
        BODY_LABEL,
        BODY_PARAMETER_UNITS,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        EXPRESSES_STYLE_LABEL,
        GUIDES_LABEL,
        iter_jsonl,
        parse_inner,
    )


def percentile(values: List[float], probability: float) -> float:
    if not values:
        raise ValueError("values is empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def as_number(value: Any) -> Optional[float]:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def summarize(values_by_parameter: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for name in BODY_PARAMETER_UNITS:
        values = values_by_parameter.get(name) or []
        if not values:
            continue
        median = statistics.median(values)
        output.append(
            {
                "parameter": name,
                "unit": BODY_PARAMETER_UNITS[name],
                "sample_size": len(values),
                "recommended_median": round(float(median), 3),
                "observed_range": [round(min(values), 3), round(max(values), 3)],
                "p25_p75": [round(percentile(values, 0.25), 3), round(percentile(values, 0.75), 3)],
            }
        )
    return output


class GraphIndex:
    def __init__(self, graph_path: Path):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.outgoing: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.incoming: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        for record in iter_jsonl(graph_path):
            if record.get("type") == "node":
                self.nodes[str(record["id"])] = record
            elif record.get("type") == "relationship":
                start_id = str((record.get("start") or {}).get("id"))
                end_id = str((record.get("end") or {}).get("id"))
                self.outgoing[start_id].append(record)
                self.incoming[end_id].append(record)

    def nodes_named(self, label: str, name: str) -> List[Dict[str, Any]]:
        return [
            node
            for node in self.nodes.values()
            if label in (node.get("labels") or [])
            and str((node.get("properties") or {}).get("name") or "") == name
        ]


def class_instance_ids(index: GraphIndex, car_class: str) -> Set[str]:
    result: Set[str] = set()
    for class_node in index.nodes_named(CAR_CLASS_LABEL, car_class):
        for edge in index.outgoing[str(class_node["id"])]:
            if edge.get("label") == CONTAINS_LABEL:
                end = edge.get("end") or {}
                if CAR_INSTANCE_LABEL in (end.get("labels") or []):
                    result.add(str(end.get("id")))
    return result


def body_values(index: GraphIndex, instance_ids: Set[str]) -> Dict[str, List[float]]:
    values: DefaultDict[str, List[float]] = defaultdict(list)
    for instance_id in instance_ids:
        for edge in index.outgoing[instance_id]:
            if edge.get("label") != CONTAINS_LABEL:
                continue
            end = edge.get("end") or {}
            if BODY_LABEL not in (end.get("labels") or []):
                continue
            props = end.get("properties") or {}
            for name in BODY_PARAMETER_UNITS:
                number = as_number(props.get(name))
                if number is not None:
                    values[name].append(number)
    return dict(values)


def recommend_class(index: GraphIndex, car_class: str) -> Dict[str, Any]:
    instances = class_instance_ids(index, car_class)
    return {
        "mode": "汽车级别",
        "car_class": car_class,
        "matched_instances": len(instances),
        "recommendations": summarize(body_values(index, instances)),
    }


def parameter_fields(node: Dict[str, Any]) -> Dict[str, Any]:
    props = node.get("properties") or {}
    inner = parse_inner(props.get("properties"))
    return {
        "parameter": props.get("name") or inner.get("parameterName(参数名称)") or inner.get("parameterName"),
        "range": inner.get("range(范围)") if "range(范围)" in inner else inner.get("range"),
        "unit": inner.get("unit(单位)") if "unit(单位)" in inner else inner.get("unit"),
        "description": inner.get("description(描述)") or inner.get("description"),
    }


def recommend_style(index: GraphIndex, style: str) -> Dict[str, Any]:
    recommendations: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    style_nodes = index.nodes_named(AC_LABEL, style)
    for style_node in style_nodes:
        for edge in index.outgoing[str(style_node["id"])]:
            if edge.get("label") != GUIDES_LABEL:
                continue
            end_id = str((edge.get("end") or {}).get("id"))
            parameter_node = index.nodes.get(end_id)
            if not parameter_node or DP_LABEL not in (parameter_node.get("labels") or []):
                continue
            fields = parameter_fields(parameter_node)
            identity = str(fields.get("parameter"))
            if identity in seen:
                continue
            seen.add(identity)
            edge_props = edge.get("properties") or {}
            fields["guidance"] = parse_inner(edge_props.get("properties"))
            merged_sources = edge_props.get("merged_source_relationships")
            if isinstance(merged_sources, str):
                try:
                    parsed_sources = json.loads(merged_sources)
                except json.JSONDecodeError:
                    parsed_sources = []
                if isinstance(parsed_sources, list):
                    fields["guidance_sources"] = [
                        {
                            "source_edge_id": source.get("source_edge_id"),
                            "relationship_name": (source.get("properties") or {}).get("name"),
                            "guidance": parse_inner(
                                (source.get("properties") or {}).get("properties")
                            ),
                        }
                        for source in parsed_sources
                        if isinstance(source, dict)
                    ]
            recommendations.append(fields)
    return {
        "mode": "汽车风格",
        "style": style,
        "matched_style_nodes": len(style_nodes),
        "recommendations": recommendations,
    }


def recommend_style_and_class(
    index: GraphIndex, style: str, car_class: str, score_threshold: float, confidence_threshold: float
) -> Dict[str, Any]:
    instances = class_instance_ids(index, car_class)
    style_node_ids = {str(node["id"]) for node in index.nodes_named(AC_LABEL, style)}
    values: DefaultDict[str, List[float]] = defaultdict(list)
    matched_instances: Set[str] = set()
    matched_edges = 0
    parameterized_edges = 0
    edges_without_parameter_evidence = 0
    for instance_id in instances:
        for edge in index.outgoing[instance_id]:
            if edge.get("label") != EXPRESSES_STYLE_LABEL:
                continue
            if str((edge.get("end") or {}).get("id")) not in style_node_ids:
                continue
            props = edge.get("properties") or {}
            if float(props.get("score", 0)) < score_threshold:
                continue
            if float(props.get("confidence", 0)) < confidence_threshold:
                continue
            matched_edges += 1
            matched_instances.add(instance_id)
            parameters = props.get("parameters") or "[]"
            if isinstance(parameters, str):
                try:
                    parameters = json.loads(parameters)
                except json.JSONDecodeError:
                    parameters = []
            if isinstance(parameters, list) and parameters:
                parameterized_edges += 1
            else:
                edges_without_parameter_evidence += 1
            for item in parameters if isinstance(parameters, list) else []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                if name not in BODY_PARAMETER_UNITS:
                    continue
                number = as_number(item.get("value"))
                if number is not None:
                    values[name].append(number)
    return {
        "mode": "汽车风格+汽车级别",
        "style": style,
        "car_class": car_class,
        "class_instances": len(instances),
        "matched_instances": len(matched_instances),
        "matched_expresses_style_edges": matched_edges,
        "parameterized_edges": parameterized_edges,
        "edges_without_parameter_evidence": edges_without_parameter_evidence,
        "score_threshold": score_threshold,
        "confidence_threshold": confidence_threshold,
        "recommendations": summarize(dict(values)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend parameters from fused graph")
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--style", default=None)
    parser.add_argument("--car-class", default=None)
    parser.add_argument("--score-threshold", type=float, default=0.65)
    parser.add_argument("--confidence-threshold", type=float, default=0.65)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.style and not args.car_class:
        raise SystemExit("Pass --style, --car-class, or both")

    index = GraphIndex(args.graph)
    if args.style and args.car_class:
        result = recommend_style_and_class(
            index,
            style=args.style,
            car_class=args.car_class,
            score_threshold=args.score_threshold,
            confidence_threshold=args.confidence_threshold,
        )
    elif args.style:
        result = recommend_style(index, args.style)
    else:
        result = recommend_class(index, args.car_class)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
