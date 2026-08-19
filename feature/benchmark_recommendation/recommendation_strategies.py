from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from feature.benchmark_upstream_offline.constants import DIMENSION_FIELDS
from feature.parameter_recommendation.common import BODY_PARAMETER_UNITS, first_text, parse_inner

from .config import RecommendationConfig
from .graph_loader import GraphData, _numeric, get_instance_dimension_values, summarize_percentiles
from .neo4j_repository import (
    BenchmarkNeo4jRepository,
    style_guides_cypher,
    style_type_cypher,
    style_type_level_cypher,
)
from .schemas import NumericParameterSummary


def _quantile(values: List[float], q: float) -> float:
    if not values:
        raise ValueError("empty")
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _summarize_numeric(values_by_field: Dict[str, List[float]]) -> List[NumericParameterSummary]:
    summaries: List[NumericParameterSummary] = []
    for row in summarize_percentiles(values_by_field):
        summaries.append(
            NumericParameterSummary(
                parameter=row["parameter"],
                unit=row["unit"],
                sample_count=row["sample_count"],
                min=float(row["min"]),
                p25=float(row["p25"]),
                median=float(row["median"]),
                p75=float(row["p75"]),
                max=float(row["max"]),
            )
        )
    return summaries


def _style_parameter_from_neo4j_row(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    props = dict(row.get("parameter_properties") or {})
    if not props:
        return None
    inner = parse_inner(props.get("properties"))
    name = first_text(
        inner.get("parameterName(参数名称)"),
        inner.get("parameterName"),
        props.get("name"),
    )
    if not name:
        return None
    return {
        "parameter": name,
        "range": inner.get("range(范围)", inner.get("range")),
        "unit": inner.get("unit(单位)", inner.get("unit")),
        "description": inner.get("description(描述)") or inner.get("description"),
        "guidance": parse_inner(dict(row.get("guide_properties") or {}).get("properties")),
    }


def _vehicles_from_neo4j_rows(rows: Sequence[Mapping[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        vehicle_id = str(row.get("vehicle_id") or "")
        if not vehicle_id or vehicle_id in seen:
            continue
        seen.add(vehicle_id)
        props = dict(row.get("vehicle_properties") or {})
        item: Dict[str, Any] = {
            "vehicle_id": vehicle_id,
            "model_name": row.get("model_name") or props.get("车型名称") or vehicle_id,
        }
        for field in BODY_PARAMETER_UNITS:
            if field in props:
                item[field] = props.get(field)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _values_from_neo4j_rows(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[float]]:
    values: Dict[str, List[float]] = {}
    for row in rows:
        props = dict(row.get("vehicle_properties") or {})
        for field in DIMENSION_FIELDS:
            number = _numeric(props.get(field))
            if number is None:
                continue
            values.setdefault(field, []).append(number)
    return values


def _example_vehicles(graph: GraphData, instance_ids: Sequence[str], limit: int = 5) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for instance_id in sorted(instance_ids)[:limit]:
        props = graph.instance_props.get(instance_id) or {}
        row: Dict[str, Any] = {
            "vehicle_id": instance_id,
            "model_name": props.get("车型名称") or props.get("name") or instance_id,
        }
        for field in BODY_PARAMETER_UNITS:
            if field in props:
                row[field] = props.get(field)
        rows.append(row)
    return rows


def style_guides_recommend(graph: GraphData, car_style: str, limit: int = 50) -> Dict[str, Any]:
    guides = graph.style_guides.get(car_style) or []
    # Deduplicate by parameter name.
    seen = set()
    parameters: List[Dict[str, Any]] = []
    for guide in guides:
        name = guide.get("parameter")
        if not name or name in seen:
            continue
        seen.add(name)
        parameters.append(guide)
        if len(parameters) >= limit:
            break
    cypher = style_guides_cypher(car_style, limit=250)
    return {
        "strategy": "style_guides",
        "car_style": car_style,
        "cypher": cypher,
        "guide_query": cypher,
        "sample_count": len(parameters),
        "parameters": parameters,
        "fallback_reason": None,
        "baseline_type_median": None,
    }


def style_guides_recommend_neo4j(
    repo: BenchmarkNeo4jRepository,
    car_style: str,
    limit: int = 250,
) -> Dict[str, Any]:
    rows = repo.style_guides(car_style, limit=limit)
    seen = set()
    parameters: List[Dict[str, Any]] = []
    for row in rows:
        item = _style_parameter_from_neo4j_row(row)
        name = (item or {}).get("parameter")
        if not item or not name or name in seen:
            continue
        seen.add(name)
        parameters.append(item)
    cypher = style_guides_cypher(car_style, limit=limit)
    return {
        "strategy": "style_guides",
        "car_style": car_style,
        "cypher": cypher,
        "guide_query": cypher,
        "sample_count": len(parameters),
        "parameters": parameters,
        "fallback_reason": None,
        "baseline_type_median": None,
    }


def style_type_recommend(
    graph: GraphData,
    car_style: str,
    car_type: str,
    small_sample_threshold: int,
) -> Tuple[Dict[str, Any], int]:
    instance_ids = graph.instances_by_type.get(car_type) or set()
    filtered = [
        instance_id
        for instance_id in instance_ids
        if car_style in (graph.styles_by_instance.get(instance_id) or set())
    ]

    values_by_field = get_instance_dimension_values(graph, filtered)
    parameters = _summarize_numeric(values_by_field)

    baseline_values = get_instance_dimension_values(graph, instance_ids)
    baseline_parameters = _summarize_numeric(baseline_values)
    baseline_type_median = baseline_parameters  # keep full distribution summary per field

    small_sample_warning = None
    if len(filtered) < small_sample_threshold:
        small_sample_warning = "small_sample_warning"

    # Add baseline medians and deltas per field.
    base_by_param = {row["parameter"]: row for row in baseline_parameters}
    for param in parameters:
        base = base_by_param.get(param["parameter"])
        if not base:
            continue
        param["baseline_type_median"] = base["median"]
        param["median_delta_from_type"] = round(param["median"] - float(base["median"]), 3)

    cypher = style_type_cypher(car_style, car_type, limit=50)
    return (
        {
            "strategy": "style_type",
            "cypher": cypher,
            "query": cypher,
            "sample_count": len(filtered),
            "parameters": parameters,
            "recommended_vehicles": _example_vehicles(graph, filtered),
            "fallback_reason": None,
            "baseline_type_median": baseline_parameters,
            "small_sample_warning": small_sample_warning,
        },
        len(filtered),
    )


def style_type_recommend_neo4j(
    repo: BenchmarkNeo4jRepository,
    car_style: str,
    car_type: str,
    small_sample_threshold: int,
    vehicle_limit: int = 5,
) -> Tuple[Dict[str, Any], int]:
    matched_rows = repo.style_type_instances(car_style, car_type)
    baseline_rows = repo.type_baseline_instances(car_type)
    parameters = _summarize_numeric(_values_from_neo4j_rows(matched_rows))
    baseline_parameters = _summarize_numeric(_values_from_neo4j_rows(baseline_rows))
    base_by_param = {row["parameter"]: row for row in baseline_parameters}
    for param in parameters:
        base = base_by_param.get(param["parameter"])
        if not base:
            continue
        param["baseline_type_median"] = base["median"]
        param["median_delta_from_type"] = round(param["median"] - float(base["median"]), 3)
    sample_count = len({str(row.get("vehicle_id") or "") for row in matched_rows if row.get("vehicle_id")})
    small_sample_warning = None
    if sample_count < small_sample_threshold:
        small_sample_warning = "small_sample_warning"
    cypher = style_type_cypher(car_style, car_type, limit=50)
    return (
        {
            "strategy": "style_type",
            "cypher": cypher,
            "query": cypher,
            "sample_count": sample_count,
            "parameters": parameters,
            "recommended_vehicles": _vehicles_from_neo4j_rows(matched_rows, limit=vehicle_limit),
            "fallback_reason": None,
            "baseline_type_median": baseline_parameters,
            "small_sample_warning": small_sample_warning,
        },
        sample_count,
    )


def style_type_level_recommend(
    graph: GraphData,
    car_style: str,
    car_type: str,
    car_level: str,
    small_sample_threshold: int,
    fallback_on_small_sample: bool,
    fallback_small_sample_threshold: int,
) -> Dict[str, Any]:
    instance_ids = graph.instances_by_type.get(car_type) or set()
    filtered: List[str] = []
    for instance_id in instance_ids:
        styles = graph.styles_by_instance.get(instance_id) or set()
        levels = graph.level_by_instance.get(instance_id) or set()
        if car_style in styles and car_level in levels:
            filtered.append(instance_id)

    values_by_field = get_instance_dimension_values(graph, filtered)
    parameters = _summarize_numeric(values_by_field)

    baseline_values = get_instance_dimension_values(graph, instance_ids)
    baseline_parameters = _summarize_numeric(baseline_values)

    small_sample_warning = None
    if len(filtered) < small_sample_threshold:
        small_sample_warning = "small_sample_warning"

    if fallback_on_small_sample and len(filtered) < fallback_small_sample_threshold:
        # Fallback to style_type but keep same group-level sample_count.
        rec, _ = style_type_recommend(
            graph=graph,
            car_style=car_style,
            car_type=car_type,
            small_sample_threshold=small_sample_threshold,
        )
        rec["fallback_reason"] = "fallback_to_style_type_due_to_small_sample"
        rec["small_sample_warning"] = small_sample_warning
        return rec

    # Add baseline medians and deltas per field.
    base_by_param = {row["parameter"]: row for row in baseline_parameters}
    for param in parameters:
        base = base_by_param.get(param["parameter"])
        if not base:
            continue
        param["baseline_type_median"] = base["median"]
        param["median_delta_from_type"] = round(param["median"] - float(base["median"]), 3)

    cypher = style_type_level_cypher(car_style, car_type, car_level, limit=50)
    return {
        "strategy": "style_type_level",
        "cypher": cypher,
        "query": cypher,
        "sample_count": len(filtered),
        "parameters": parameters,
        "recommended_vehicles": _example_vehicles(graph, filtered),
        "fallback_reason": None,
        "baseline_type_median": baseline_parameters,
        "small_sample_warning": small_sample_warning,
    }


def style_type_level_recommend_neo4j(
    repo: BenchmarkNeo4jRepository,
    car_style: str,
    car_type: str,
    car_level: str,
    small_sample_threshold: int,
    fallback_on_small_sample: bool,
    fallback_small_sample_threshold: int,
    vehicle_limit: int = 5,
) -> Dict[str, Any]:
    matched_rows = repo.style_type_level_instances(car_style, car_type, car_level)
    baseline_rows = repo.type_baseline_instances(car_type)
    sample_count = len({str(row.get("vehicle_id") or "") for row in matched_rows if row.get("vehicle_id")})
    small_sample_warning = None
    if sample_count < small_sample_threshold:
        small_sample_warning = "small_sample_warning"
    fallback_reason = None
    if fallback_on_small_sample and sample_count < fallback_small_sample_threshold:
        fallback_reason = "small_sample_kept_in_style_type_level"

    parameters = _summarize_numeric(_values_from_neo4j_rows(matched_rows))
    baseline_parameters = _summarize_numeric(_values_from_neo4j_rows(baseline_rows))
    base_by_param = {row["parameter"]: row for row in baseline_parameters}
    for param in parameters:
        base = base_by_param.get(param["parameter"])
        if not base:
            continue
        param["baseline_type_median"] = base["median"]
        param["median_delta_from_type"] = round(param["median"] - float(base["median"]), 3)
    cypher = style_type_level_cypher(car_style, car_type, car_level, limit=50)
    return {
        "strategy": "style_type_level",
        "cypher": cypher,
        "query": cypher,
        "sample_count": sample_count,
        "parameters": parameters,
        "recommended_vehicles": _vehicles_from_neo4j_rows(matched_rows, limit=vehicle_limit),
        "fallback_reason": fallback_reason,
        "baseline_type_median": baseline_parameters,
        "small_sample_warning": small_sample_warning,
    }


def recommendation_router(
    graph: GraphData,
    car_style_candidates: List[Dict[str, Any]],
    car_type_candidates: List[Dict[str, Any]],
    car_level: Optional[Dict[str, Any]],
    recommendation_config: RecommendationConfig,
) -> List[Dict[str, Any]]:
    """Return a list of group recommendations (each keeps its own parameters)."""

    groups: List[Dict[str, Any]] = []

    if not car_style_candidates and not car_type_candidates:
        return []

    # Only style => StyleGuidesStrategy.
    if car_style_candidates and not car_type_candidates:
        for style in car_style_candidates[: recommendation_config.max_styles]:
            groups.append(style_guides_recommend(graph, style["name"]))
        return groups

    # style + type => generate (style,type) combinations.
    if car_style_candidates and car_type_candidates:
        combos: List[Tuple[float, str, str]] = []
        for style in car_style_candidates[: recommendation_config.max_styles]:
            for car_type in car_type_candidates[: recommendation_config.max_types]:
                combined_score = float(style["score"]) + float(car_type["score"])
                combos.append((combined_score, style["name"], car_type["name"]))
        combos.sort(key=lambda x: (-x[0], x[1], x[2]))
        for _score, style_name, type_name in combos[: recommendation_config.max_combinations]:
            if car_level and car_level.get("name"):
                rec = style_type_level_recommend(
                    graph=graph,
                    car_style=style_name,
                    car_type=type_name,
                    car_level=str(car_level["name"]),
                    small_sample_threshold=recommendation_config.small_sample_threshold,
                    fallback_on_small_sample=recommendation_config.fallback_on_small_sample,
                    fallback_small_sample_threshold=recommendation_config.fallback_small_sample_threshold,
                )
            else:
                rec, _ = style_type_recommend(
                    graph=graph,
                    car_style=style_name,
                    car_type=type_name,
                    small_sample_threshold=recommendation_config.small_sample_threshold,
                )
            rec["car_style"] = style_name
            rec["car_type"] = type_name
            rec["car_level"] = car_level.get("name") if car_level else None
            groups.append(rec)
        return groups

    # Type-only is not in the spec.
    return []

