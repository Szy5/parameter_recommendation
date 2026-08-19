#!/usr/bin/env python3
"""Online parameter recommendations backed by Neo4j.

The public ``RecommendationService.recommend`` method supports exactly three
input shapes: style only, car class only, and style + car class.  Cypher is
kept in the repository class; aggregation and business post-processing are
kept in the service so both layers can be tested independently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from neo4j import GraphDatabase

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        BODY_PARAMETER_UNITS,
        load_env_file,
        parse_inner,
    )
    from feature_v2.parameter_recommendation.recommend import summarize  # type: ignore
else:
    from .common import BODY_PARAMETER_UNITS, load_env_file, parse_inner
    from .recommend import summarize


# Only these body parameters have a stable numeric meaning and unit in the
# current graph.  Centralising the allow-list prevents arbitrary Neo4j
# properties from silently becoming recommendation metrics.
RECOMMENDABLE_BODY_PARAMETERS = tuple(BODY_PARAMETER_UNITS)


STYLE_QUERY = """
MATCH (style:`AestheticConcept(美学概念)`)
WHERE style.name = $style
OPTIONAL MATCH (style)-[guide:`Guides(指导)`]->(parameter:`DesignParameter(设计参数)`)
RETURN properties(style) AS style_properties,
       properties(parameter) AS parameter_properties,
       properties(guide) AS guide_properties
ORDER BY parameter.name
"""


CLASS_BODY_QUERY = """
MATCH (class:`汽车级别`)-[:`包含`]->(vehicle:`汽车实例`)
WHERE class.name = $car_class
OPTIONAL MATCH (vehicle)-[:`包含`]->(:`车身`)-[:`包含`]->(parameter:`车身`)
WHERE parameter.name IN $parameter_names
RETURN coalesce(vehicle._graph_id, elementId(vehicle)) AS vehicle_id,
       vehicle.`车型名称` AS model_name,
       parameter.name AS parameter,
       parameter[parameter.name] AS value
ORDER BY vehicle_id, parameter
"""


STYLE_CLASS_BODY_QUERY = """
MATCH (class:`汽车级别`)-[:`包含`]->(vehicle:`汽车实例`)
WHERE class.name = $car_class
MATCH (vehicle)-[expresses:EXPRESSES_STYLE]->(style:`AestheticConcept(美学概念)`)
WHERE style.name = $style
  AND coalesce(expresses.score, 0.0) >= $score_threshold
  AND coalesce(expresses.confidence, 0.0) >= $confidence_threshold
OPTIONAL MATCH (vehicle)-[:`包含`]->(:`车身`)-[:`包含`]->(parameter:`车身`)
WHERE parameter.name IN $parameter_names
RETURN coalesce(vehicle._graph_id, elementId(vehicle)) AS vehicle_id,
       vehicle.`车型名称` AS model_name,
       expresses.score AS style_score,
       expresses.confidence AS style_confidence,
       expresses.parameters AS style_parameters,
       parameter.name AS parameter,
       parameter[parameter.name] AS value
ORDER BY vehicle_id, parameter
"""


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    username: str
    password: str
    database: Optional[str] = None

    @classmethod
    def from_env(cls, env_path: Path) -> "Neo4jConfig":
        """Load config from a dotenv file, with process variables taking precedence."""
        file_values = load_env_file(env_path)

        def value(name: str) -> str:
            return os.getenv(name, file_values.get(name, "")).strip()

        missing = [
            name
            for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
            if not value(name)
        ]
        if missing:
            raise ValueError("Missing Neo4j configuration: %s" % ", ".join(missing))
        return cls(
            uri=value("NEO4J_URI"),
            username=value("NEO4J_USERNAME"),
            password=value("NEO4J_PASSWORD"),
            database=value("NEO4J_DATABASE") or None,
        )


class Neo4jRecommendationRepository:
    """Read-only graph access.  No business aggregation belongs in this class."""

    def __init__(self, driver: Any, database: Optional[str] = None):
        self.driver = driver
        self.database = database

    @classmethod
    def connect(cls, config: Neo4jConfig) -> "Neo4jRecommendationRepository":
        driver = GraphDatabase.driver(
            config.uri,
            auth=(config.username, config.password),
        )
        driver.verify_connectivity()
        return cls(driver, config.database)

    def close(self) -> None:
        self.driver.close()

    def _read(self, query: str, **parameters: Any) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(query, **parameters)]

    def style_parameters(self, style: str) -> List[Dict[str, Any]]:
        return self._read(STYLE_QUERY, style=style)

    def class_body_parameters(self, car_class: str) -> List[Dict[str, Any]]:
        return self._read(
            CLASS_BODY_QUERY,
            car_class=car_class,
            parameter_names=list(RECOMMENDABLE_BODY_PARAMETERS),
        )

    def style_class_body_parameters(
        self,
        style: str,
        car_class: str,
        score_threshold: float,
        confidence_threshold: float,
    ) -> List[Dict[str, Any]]:
        return self._read(
            STYLE_CLASS_BODY_QUERY,
            style=style,
            car_class=car_class,
            score_threshold=score_threshold,
            confidence_threshold=confidence_threshold,
            parameter_names=list(RECOMMENDABLE_BODY_PARAMETERS),
        )


def _non_empty(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("%s must not be blank" % field)
    return cleaned


def _number(value: Any) -> Optional[float]:
    """Parse scalar graph values; reject ranges and other ambiguous strings."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    return float(text)


def _decode_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _cohort(rows: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, List[float]], Dict[str, Dict[str, Any]]]:
    values: Dict[str, List[float]] = {}
    vehicles: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        vehicle_id = str(row.get("vehicle_id") or "")
        if vehicle_id:
            vehicles.setdefault(
                vehicle_id,
                {"vehicle_id": vehicle_id, "model_name": row.get("model_name")},
            )
        name = str(row.get("parameter") or "")
        number = _number(row.get("value"))
        if name in BODY_PARAMETER_UNITS and number is not None:
            values.setdefault(name, []).append(number)
    return values, vehicles


def _style_parameter(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    props = dict(row.get("parameter_properties") or {})
    if not props:
        return None
    inner = parse_inner(props.get("properties"))
    guide_props = dict(row.get("guide_properties") or {})
    result = {
        "parameter": props.get("name")
        or inner.get("parameterName(参数名称)")
        or inner.get("parameterName"),
        "range": inner.get("range(范围)", inner.get("range")),
        "unit": inner.get("unit(单位)", inner.get("unit")),
        "description": inner.get("description(描述)") or inner.get("description"),
        "guidance": parse_inner(guide_props.get("properties")),
    }
    merged_sources = _decode_json_list(guide_props.get("merged_source_relationships"))
    if merged_sources:
        result["guidance_sources"] = [
            {
                "source_edge_id": source.get("source_edge_id"),
                "relationship_name": (source.get("properties") or {}).get("name"),
                "guidance": parse_inner(
                    (source.get("properties") or {}).get("properties")
                ),
            }
            for source in merged_sources
            if isinstance(source, dict)
        ]
    return result


def _add_baseline_comparison(
    recommendations: List[Dict[str, Any]],
    baseline_recommendations: Sequence[Mapping[str, Any]],
) -> None:
    baseline = {str(item["parameter"]): item for item in baseline_recommendations}
    for item in recommendations:
        reference = baseline.get(str(item["parameter"]))
        if not reference:
            continue
        cohort_median = float(item["recommended_median"])
        baseline_median = float(reference["recommended_median"])
        delta = cohort_median - baseline_median
        item["class_baseline_median"] = baseline_median
        item["median_delta_from_class"] = round(delta, 3)
        item["median_delta_percent"] = (
            round(delta / baseline_median * 100.0, 2) if baseline_median else None
        )


class RecommendationService:
    def __init__(self, repository: Any):
        self.repository = repository

    def recommend(
        self,
        style: Optional[str] = None,
        car_class: Optional[str] = None,
        score_threshold: float = 0.65,
        confidence_threshold: float = 0.65,
        sample_limit: int = 10,
    ) -> Dict[str, Any]:
        style = _non_empty(style, "style")
        car_class = _non_empty(car_class, "car_class")
        if not style and not car_class:
            raise ValueError("style and car_class cannot both be empty")
        for name, threshold in (
            ("score_threshold", score_threshold),
            ("confidence_threshold", confidence_threshold),
        ):
            if threshold < 0.0 or threshold > 1.0:
                raise ValueError("%s must be between 0 and 1" % name)
        if sample_limit < 0:
            raise ValueError("sample_limit must be >= 0")

        if style and car_class:
            return self._recommend_style_and_class(
                style,
                car_class,
                score_threshold,
                confidence_threshold,
                sample_limit,
            )
        if style:
            return self._recommend_style(style)
        return self._recommend_class(str(car_class), sample_limit)

    def _recommend_style(self, style: str) -> Dict[str, Any]:
        rows = self.repository.style_parameters(style)
        style_found = any(row.get("style_properties") for row in rows)
        recommendations: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            item = _style_parameter(row)
            if not item or not item.get("parameter") or item["parameter"] in seen:
                continue
            seen.add(item["parameter"])
            recommendations.append(item)
        return {
            "mode": "汽车风格",
            "style": style,
            "matched_style_nodes": 1 if style_found else 0,
            "recommendations": recommendations,
        }

    def _recommend_class(self, car_class: str, sample_limit: int) -> Dict[str, Any]:
        rows = self.repository.class_body_parameters(car_class)
        values, vehicles = _cohort(rows)
        return {
            "mode": "汽车级别",
            "car_class": car_class,
            "matched_instances": len(vehicles),
            "sample_vehicles": list(vehicles.values())[:sample_limit],
            "recommendations": summarize(values),
        }

    def _recommend_style_and_class(
        self,
        style: str,
        car_class: str,
        score_threshold: float,
        confidence_threshold: float,
        sample_limit: int,
    ) -> Dict[str, Any]:
        # Online intersection: first select class vehicles whose offline style
        # relationship passes thresholds, then aggregate their live body nodes.
        matched_rows = self.repository.style_class_body_parameters(
            style,
            car_class,
            score_threshold,
            confidence_threshold,
        )
        class_rows = self.repository.class_body_parameters(car_class)
        matched_values, matched_vehicles = _cohort(matched_rows)
        baseline_values, class_vehicles = _cohort(class_rows)
        recommendations = summarize(matched_values)
        baseline_recommendations = summarize(baseline_values)
        _add_baseline_comparison(recommendations, baseline_recommendations)

        scores: Dict[str, Dict[str, float]] = {}
        design_parameters = set()
        for row in matched_rows:
            vehicle_id = str(row.get("vehicle_id") or "")
            if vehicle_id and vehicle_id not in scores:
                scores[vehicle_id] = {
                    "score": float(row.get("style_score") or 0.0),
                    "confidence": float(row.get("style_confidence") or 0.0),
                }
            for parameter in _decode_json_list(row.get("style_parameters")):
                if isinstance(parameter, str) and parameter.strip():
                    design_parameters.add(parameter.strip())

        samples = []
        for vehicle_id, vehicle in list(matched_vehicles.items())[:sample_limit]:
            sample = dict(vehicle)
            sample.update(scores.get(vehicle_id, {}))
            samples.append(sample)

        return {
            "mode": "汽车风格+汽车级别",
            "style": style,
            "car_class": car_class,
            "score_threshold": score_threshold,
            "confidence_threshold": confidence_threshold,
            "class_instances": len(class_vehicles),
            "matched_instances": len(matched_vehicles),
            "match_rate": (
                round(len(matched_vehicles) / len(class_vehicles), 4)
                if class_vehicles
                else 0.0
            ),
            "postprocessing": (
                "按级别取实例，经EXPRESSES_STYLE阈值筛选后，读取匹配实例当前车身数值；"
                "对匹配子集聚合，并与该级别全量基线比较"
            ),
            "style_design_parameter_basis": sorted(design_parameters),
            "sample_vehicles": samples,
            "recommendations": recommendations,
        }


def _default_env_path() -> Path:
    root_env = Path(".env")
    return root_env if root_env.exists() else Path("feature_v2/.env")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend vehicle parameters from Neo4j")
    parser.add_argument("--env", type=Path, default=_default_env_path())
    parser.add_argument("--style")
    parser.add_argument("--car-class")
    parser.add_argument("--score-threshold", type=float, default=0.65)
    parser.add_argument("--confidence-threshold", type=float, default=0.65)
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = Neo4jConfig.from_env(args.env)
    repository = Neo4jRecommendationRepository.connect(config)
    try:
        result = RecommendationService(repository).recommend(
            style=args.style,
            car_class=args.car_class,
            score_threshold=args.score_threshold,
            confidence_threshold=args.confidence_threshold,
            sample_limit=args.sample_limit,
        )
    finally:
        repository.close()

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
