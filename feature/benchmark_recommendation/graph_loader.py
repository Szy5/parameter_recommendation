from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

from feature.benchmark_upstream_offline.constants import (
    CAR_INSTANCE_LABEL,
    CAR_LEVEL_LABEL,
    CAR_TYPE_LABEL,
    CONTAINS_LABEL,
    DESIGN_PARAMETER_LABEL,
    DIMENSION_FIELDS,
    EXPRESSES_STYLE_LABEL,
    FEATURE_LABELS,
    GUIDES_LABEL,
    ROUTE_BY_CAR_TYPE,
    STYLE_LABEL,
    STYLES,
    CAR_TYPES,
)
from feature.benchmark_upstream_offline.io_utils import iter_jsonl, parse_inner

from feature.parameter_recommendation.common import BODY_PARAMETER_UNITS


_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _numeric(value: Any) -> Optional[float]:
    """Parse scalar numeric values; reject non-scalar strings."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = _NUMBER_RE.search(text)
    return float(match.group(0)) if match else None


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return ""


@dataclass(frozen=True)
class GraphData:
    feature_node_by_id: Dict[str, Dict[str, Any]]
    # Strategy inputs
    style_guides: Dict[str, List[Dict[str, Any]]]
    instances_by_type: Dict[str, Set[str]]
    level_by_instance: Dict[str, Set[str]]
    styles_by_instance: Dict[str, Set[str]]
    instance_props: Dict[str, Dict[str, Any]]

    # Prediction inputs (AssociatedWith one-hop edges)
    style_edges_by_source: Dict[str, List[Dict[str, Any]]]
    type_edges_by_source: Dict[str, List[Dict[str, Any]]]


def load_graph(
    graph_jsonl_path: Path, associated_confidence_only_gt: Optional[float] = None
) -> GraphData:
    """Load only what we need for keyword->prediction->recommendation.

    We treat `kgdata_*.jsonl` as a closed vocab graph with:
    - style guides: Style(汽车风格) -[Guides(指导)]-> DesignParameter(设计参数)
    - type instances: 汽车车型 -[包含]-> 汽车实例
    - levels: 汽车级别 -[包含]-> 汽车实例
    - style membership: 汽车实例 -[EXPRESSES_STYLE]-> 汽车风格
    - predictions: feature node -[StyleAssociatedWith/TypeAssociatedWith]-> style/type
    """

    feature_node_by_id: Dict[str, Dict[str, Any]] = {}
    style_guides: Dict[str, List[Dict[str, Any]]] = {}
    instances_by_type: Dict[str, Set[str]] = {t: set() for t in CAR_TYPES}
    level_by_instance: Dict[str, Set[str]] = {}
    styles_by_instance: Dict[str, Set[str]] = {}
    instance_props: Dict[str, Dict[str, Any]] = {}

    style_edges_by_source: Dict[str, List[Dict[str, Any]]] = {}
    type_edges_by_source: Dict[str, List[Dict[str, Any]]] = {}

    design_parameter_by_id: Dict[str, Dict[str, Any]] = {}

    for record in iter_jsonl(graph_jsonl_path):
        if record.get("type") == "node":
            node_id = str(record.get("id"))
            labels = set(record.get("labels") or [])
            props = record.get("properties") or {}

            if DESIGN_PARAMETER_LABEL in labels:
                inner = parse_inner(props.get("properties"))
                design_parameter_by_id[node_id] = {
                    "parameter": _first_text(inner.get("parameterName(参数名称)"), inner.get("parameterName")),
                    "range": inner.get("range(范围)", inner.get("range")),
                    "unit": inner.get("unit(单位)", inner.get("unit")),
                    "description": inner.get("description(描述)", inner.get("description")),
                }

            if CAR_INSTANCE_LABEL in labels:
                # Keep raw; we will extract numeric scalars on demand.
                instance_props[node_id] = dict(props)

            if labels.intersection(FEATURE_LABELS):
                feature_node_by_id[node_id] = {
                    "node_id": node_id,
                    "label": list(labels)[0] if labels else "",
                    "name": str(props.get("name") or ""),
                }

            continue

        if record.get("type") != "relationship":
            continue

        label = record.get("label")
        start = record.get("start") or {}
        end = record.get("end") or {}
        start_labels = set(start.get("labels") or [])
        end_labels = set(end.get("labels") or [])

        # Strategy: style guides
        # Note: in this merged graph, relationship endpoints may preserve
        # original labels (e.g. AestheticConcept) even if we conceptually treat
        # them as main styles. So we match by style node "name" in STYLES.
        if label == GUIDES_LABEL and DESIGN_PARAMETER_LABEL in end_labels:
            style_name = str((start.get("properties") or {}).get("name") or "")
            parameter_id = str(end.get("id"))
            parameter_node = design_parameter_by_id.get(parameter_id) or {}
            if style_name not in STYLES or not parameter_node.get("parameter"):
                continue

            rel_props = record.get("properties") or {}
            guidance_inner = parse_inner(rel_props.get("properties"))
            guidance = guidance_inner  # already decoded from JSON string

            guide_row = {
                "parameter": parameter_node.get("parameter"),
                "range": parameter_node.get("range"),
                "unit": parameter_node.get("unit"),
                "description": parameter_node.get("description"),
                "guidance": guidance,
            }
            style_guides.setdefault(style_name, []).append(guide_row)
            continue

        # Strategy: type -> instances
        if label == CONTAINS_LABEL and CAR_TYPE_LABEL in start_labels and CAR_INSTANCE_LABEL in end_labels:
            car_type = str((start.get("properties") or {}).get("name") or "")
            instance_id = str(end.get("id"))
            if car_type:
                instances_by_type.setdefault(car_type, set()).add(instance_id)
                instance_props.setdefault(instance_id, dict(end.get("properties") or {}))
            continue

        # Strategy: level -> instances (and normalized dimensions appear here)
        if label == CONTAINS_LABEL and CAR_LEVEL_LABEL in start_labels and CAR_INSTANCE_LABEL in end_labels:
            level_name = str((start.get("properties") or {}).get("name") or "")
            instance_id = str(end.get("id"))
            if level_name:
                level_by_instance.setdefault(instance_id, set()).add(level_name)
            if instance_id:
                # Merge end props to instance properties snapshot.
                instance_props.setdefault(instance_id, {}).update(end.get("properties") or {})
            continue

        # Strategy: instance -> styles (membership)
        # Note: relationship endpoints for style nodes may have different labels
        # than our conceptual "汽车风格". We match by end.properties.name in STYLES.
        if label == EXPRESSES_STYLE_LABEL and CAR_INSTANCE_LABEL in start_labels:
            style_name = str((end.get("properties") or {}).get("name") or "")
            instance_id = str(start.get("id"))
            if style_name in STYLES and instance_id:
                styles_by_instance.setdefault(instance_id, set()).add(style_name)
            continue

        # Prediction: AssociatedWith edges
        if label == "StyleAssociatedWith":
            source_id = str(start.get("id") or "")
            # End is 汽车风格 node.
            style_name = str((end.get("properties") or {}).get("name") or "")
            confidence_raw = (record.get("properties") or {}).get("confidence")
            confidence = _numeric(confidence_raw)
            if not source_id or not style_name or confidence is None:
                continue
            if associated_confidence_only_gt is not None and confidence <= associated_confidence_only_gt:
                continue
            reason = (record.get("properties") or {}).get("reason")
            style_edges_by_source.setdefault(source_id, []).append(
                {"target": style_name, "confidence": confidence, "reason": reason}
            )
            continue

        if label == "TypeAssociatedWith":
            source_id = str(start.get("id") or "")
            type_name = str((end.get("properties") or {}).get("name") or "")
            confidence_raw = (record.get("properties") or {}).get("confidence")
            confidence = _numeric(confidence_raw)
            if not source_id or not type_name or confidence is None:
                continue
            if associated_confidence_only_gt is not None and confidence <= associated_confidence_only_gt:
                continue
            reason = (record.get("properties") or {}).get("reason")
            type_edges_by_source.setdefault(source_id, []).append(
                {"target": type_name, "confidence": confidence, "reason": reason}
            )
            continue

    return GraphData(
        feature_node_by_id=feature_node_by_id,
        style_guides=style_guides,
        instances_by_type=instances_by_type,
        level_by_instance=level_by_instance,
        styles_by_instance=styles_by_instance,
        instance_props=instance_props,
        style_edges_by_source=style_edges_by_source,
        type_edges_by_source=type_edges_by_source,
    )


def get_instance_dimension_values(graph: GraphData, instance_ids: Iterable[str]) -> Dict[str, List[float]]:
    values: Dict[str, List[float]] = {}
    for instance_id in instance_ids:
        props = graph.instance_props.get(str(instance_id)) or {}
        for field in DIMENSION_FIELDS:
            number = _numeric(props.get(field))
            if number is None:
                continue
            values.setdefault(field, []).append(number)
    return values


def summarize_percentiles(values: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    # percentiles compatible with the "median,p25,p75" requirement (simple linear interpolation).
    def quantile(sorted_values: List[float], q: float) -> float:
        if not sorted_values:
            raise ValueError("empty values")
        if len(sorted_values) == 1:
            return sorted_values[0]
        pos = (len(sorted_values) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(sorted_values) - 1)
        frac = pos - lo
        return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac

    out: List[Dict[str, Any]] = []
    for field in DIMENSION_FIELDS:
        cohort = values.get(field) or []
        if not cohort:
            continue
        ordered = sorted(cohort)
        median = quantile(ordered, 0.5)
        p25 = quantile(ordered, 0.25)
        p75 = quantile(ordered, 0.75)
        out.append(
            {
                "parameter": field,
                "unit": BODY_PARAMETER_UNITS.get(field, ""),
                "sample_count": len(cohort),
                "min": round(ordered[0], 3),
                "p25": round(p25, 3),
                "median": round(median, 3),
                "p75": round(p75, 3),
                "max": round(ordered[-1], 3),
            }
        )
    return out

