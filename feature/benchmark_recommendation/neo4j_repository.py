"""Read-only Neo4j access for benchmark parameter recommendation.

Displayed Cypher follows the interactive templates:

- style guides:
  MATCH p=(s:汽车风格)-[:`Guides(指导)`]->(:`DesignParameter(设计参数)`)
  WHERE s.name CONTAINS '豪华'
  RETURN p LIMIT 250;

- style + type:
  MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
  WHERE style.name CONTAINS '科技' AND n.name CONTAINS 'SUV'
  RETURN p LIMIT 50;
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from feature.parameter_recommendation.neo4j_recommend import Neo4jConfig, Neo4jRecommendationRepository


STYLE_GUIDES_QUERY = """
MATCH (s:`汽车风格`)-[g:`Guides(指导)`]->(param:`DesignParameter(设计参数)`)
WHERE s.name CONTAINS $car_style
RETURN s.name AS style_name,
       properties(param) AS parameter_properties,
       properties(g) AS guide_properties
ORDER BY param.name
LIMIT $limit
"""

STYLE_TYPE_QUERY = """
MATCH (n:`汽车车型`)-[:`包含`]-(v:`汽车实例`)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
WHERE style.name CONTAINS $car_style
  AND n.name CONTAINS $car_type
RETURN DISTINCT coalesce(v._graph_id, elementId(v)) AS vehicle_id,
       v.`车型名称` AS model_name,
       n.name AS car_type,
       style.name AS car_style,
       properties(v) AS vehicle_properties
LIMIT $limit
"""

TYPE_BASELINE_QUERY = """
MATCH (n:`汽车车型`)-[:`包含`]->(v:`汽车实例`)
WHERE n.name CONTAINS $car_type
RETURN DISTINCT coalesce(v._graph_id, elementId(v)) AS vehicle_id,
       v.`车型名称` AS model_name,
       properties(v) AS vehicle_properties
LIMIT $limit
"""

STYLE_TYPE_LEVEL_QUERY = """
MATCH (n:`汽车车型`)-[:`包含`]-(v:`汽车实例`)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
MATCH (l:`汽车级别`)-[:`包含`]->(v)
WHERE style.name CONTAINS $car_style
  AND n.name CONTAINS $car_type
  AND l.name CONTAINS $car_level
RETURN DISTINCT coalesce(v._graph_id, elementId(v)) AS vehicle_id,
       v.`车型名称` AS model_name,
       n.name AS car_type,
       style.name AS car_style,
       l.name AS car_level,
       properties(v) AS vehicle_properties
LIMIT $limit
"""


def _cypher_literal(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def style_guides_cypher(car_style: str, limit: int = 250) -> str:
    return (
        "MATCH p=(s:汽车风格)-[:`Guides(指导)`]->(:`DesignParameter(设计参数)`)\n"
        "WHERE s.name CONTAINS '%s'\n"
        "RETURN p LIMIT %d;"
        % (_cypher_literal(car_style), int(limit))
    )


def style_type_cypher(car_style: str, car_type: str, limit: int = 50) -> str:
    return (
        "MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)\n"
        "WHERE style.name CONTAINS '%s' AND n.name CONTAINS '%s'\n"
        "RETURN p LIMIT %d;"
        % (_cypher_literal(car_style), _cypher_literal(car_type), int(limit))
    )


def style_type_level_cypher(car_style: str, car_type: str, car_level: str, limit: int = 50) -> str:
    return (
        "MATCH p=(n:汽车车型)-[:包含]-(v:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)\n"
        "MATCH (l:汽车级别)-[:包含]->(v)\n"
        "WHERE style.name CONTAINS '%s' AND n.name CONTAINS '%s' AND l.name CONTAINS '%s'\n"
        "RETURN p LIMIT %d;"
        % (
            _cypher_literal(car_style),
            _cypher_literal(car_type),
            _cypher_literal(car_level),
            int(limit),
        )
    )


class BenchmarkNeo4jRepository:
    """Parameterized Cypher access with a small in-process cache."""

    def __init__(self, inner: Neo4jRecommendationRepository):
        self.inner = inner
        self._guides_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self._style_type_cache: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
        self._type_baseline_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self._style_type_level_cache: Dict[Tuple[str, str, str, int], List[Dict[str, Any]]] = {}

    @classmethod
    def connect(cls, config: Neo4jConfig) -> "BenchmarkNeo4jRepository":
        return cls(Neo4jRecommendationRepository.connect(config))

    def close(self) -> None:
        self.inner.close()

    def style_guides(self, car_style: str, limit: int = 250) -> List[Dict[str, Any]]:
        key = (car_style, int(limit))
        if key not in self._guides_cache:
            self._guides_cache[key] = self.inner._read(
                STYLE_GUIDES_QUERY, car_style=car_style, limit=int(limit)
            )
        return self._guides_cache[key]

    def style_type_instances(
        self, car_style: str, car_type: str, limit: int = 800
    ) -> List[Dict[str, Any]]:
        key = (car_style, car_type, int(limit))
        if key not in self._style_type_cache:
            self._style_type_cache[key] = self.inner._read(
                STYLE_TYPE_QUERY,
                car_style=car_style,
                car_type=car_type,
                limit=int(limit),
            )
        return self._style_type_cache[key]

    def type_baseline_instances(self, car_type: str, limit: int = 800) -> List[Dict[str, Any]]:
        key = (car_type, int(limit))
        if key not in self._type_baseline_cache:
            self._type_baseline_cache[key] = self.inner._read(
                TYPE_BASELINE_QUERY, car_type=car_type, limit=int(limit)
            )
        return self._type_baseline_cache[key]

    def style_type_level_instances(
        self,
        car_style: str,
        car_type: str,
        car_level: str,
        limit: int = 800,
    ) -> List[Dict[str, Any]]:
        key = (car_style, car_type, car_level, int(limit))
        if key not in self._style_type_level_cache:
            self._style_type_level_cache[key] = self.inner._read(
                STYLE_TYPE_LEVEL_QUERY,
                car_style=car_style,
                car_type=car_type,
                car_level=car_level,
                limit=int(limit),
            )
        return self._style_type_level_cache[key]
