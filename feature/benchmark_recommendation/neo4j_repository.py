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

from .path_morphology import (
    ASSOCIATED_STYLE,
    ASSOCIATED_TYPE,
    ORIG_REL_CYPHER,
    display_from_reverse_walk,
    format_path,
)


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


# Recalled node is a bridge: last hop is AssociatedWith, original hops = 0.
DIRECT_STYLE_PATH_QUERY = """
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})
MATCH (head:`汽车风格`)-[aw:StyleAssociatedWith]->(recalled)
RETURN recalled._graph_id AS recalled_id,
       recalled.name AS recalled_name,
       head.name AS head_name,
       'style' AS head_kind,
       1 AS hops,
       aw.confidence AS confidence
"""

DIRECT_TYPE_PATH_QUERY = """
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})
MATCH (head:`汽车车型`)-[aw:TypeAssociatedWith]->(recalled)
RETURN recalled._graph_id AS recalled_id,
       recalled.name AS recalled_name,
       head.name AS head_name,
       'type' AS head_kind,
       1 AS hops,
       aw.confidence AS confidence
"""

# Recalled node walks original relations 1..4 hops, then AssociatedWith.
# Keep the shortest $per_pair walks per (recalled, head).
MULTI_STYLE_PATH_QUERY = """
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})
MATCH p = (recalled)-[:%s*1..4]-(bridge)
WHERE ALL(n IN nodes(p) WHERE NOT n:`汽车风格` AND NOT n:`汽车车型`
          AND NOT n:`汽车实例` AND NOT n:`汽车级别`)
  AND ALL(n IN nodes(p) WHERE size([m IN nodes(p) WHERE m = n]) = 1)
MATCH (head:`汽车风格`)-[aw:StyleAssociatedWith]->(bridge)
WITH recalled, head, p, aw,
     length(p) + 1 AS hops,
     [n IN nodes(p) | n.name] AS walk_nodes,
     [r IN relationships(p) | type(r)] AS walk_rels
ORDER BY recalled._graph_id, head.name, hops, walk_nodes, walk_rels
        WITH recalled, head, collect({
            hops: hops,
            walk_nodes: walk_nodes,
            walk_rels: walk_rels,
            confidence: aw.confidence
        })[0..($per_pair - 1)] AS bag
UNWIND bag AS item
RETURN recalled._graph_id AS recalled_id,
       recalled.name AS recalled_name,
       head.name AS head_name,
       'style' AS head_kind,
       item.hops AS hops,
       item.walk_nodes AS walk_nodes,
       item.walk_rels AS walk_rels,
       item.confidence AS confidence
""" % ORIG_REL_CYPHER

MULTI_TYPE_PATH_QUERY = """
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})
MATCH p = (recalled)-[:%s*1..4]-(bridge)
WHERE ALL(n IN nodes(p) WHERE NOT n:`汽车风格` AND NOT n:`汽车车型`
          AND NOT n:`汽车实例` AND NOT n:`汽车级别`)
  AND ALL(n IN nodes(p) WHERE size([m IN nodes(p) WHERE m = n]) = 1)
MATCH (head:`汽车车型`)-[aw:TypeAssociatedWith]->(bridge)
WITH recalled, head, p, aw,
     length(p) + 1 AS hops,
     [n IN nodes(p) | n.name] AS walk_nodes,
     [r IN relationships(p) | type(r)] AS walk_rels
ORDER BY recalled._graph_id, head.name, hops, walk_nodes, walk_rels
        WITH recalled, head, collect({
            hops: hops,
            walk_nodes: walk_nodes,
            walk_rels: walk_rels,
            confidence: aw.confidence
        })[0..($per_pair - 1)] AS bag
UNWIND bag AS item
RETURN recalled._graph_id AS recalled_id,
       recalled.name AS recalled_name,
       head.name AS head_name,
       'type' AS head_kind,
       item.hops AS hops,
       item.walk_nodes AS walk_nodes,
       item.walk_rels AS walk_rels,
       item.confidence AS confidence
""" % ORIG_REL_CYPHER

BATCH_NEIGHBOR_EVIDENCE_QUERY = """
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})-[rel:%s]-(neighbor)
WHERE NOT neighbor:`汽车风格` AND NOT neighbor:`汽车车型`
  AND NOT neighbor:`汽车实例` AND NOT neighbor:`汽车级别`
  AND neighbor <> recalled
WITH recalled, collect({rel: type(rel), neighbor: neighbor.name})[0..($per_node - 1)] AS bag
UNWIND bag AS item
RETURN recalled._graph_id AS recalled_id,
       recalled.name AS recalled_name,
       item.rel AS rel,
       item.neighbor AS neighbor_name
""" % ORIG_REL_CYPHER


MAX_ORIGINAL_WALK_HOPS = 4


def _ordered_unique(values: List[str]) -> List[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _effective_max_hops(max_hops: int) -> int:
    return min(max(int(max_hops), 0), MAX_ORIGINAL_WALK_HOPS + 1)


def _multi_path_query(query: str, walk_hops: int) -> str:
    depth = int(walk_hops)
    if depth < 1 or depth > MAX_ORIGINAL_WALK_HOPS:
        raise ValueError("walk_hops must be in 1..%d" % MAX_ORIGINAL_WALK_HOPS)
    return query.replace("*1..4", "*1..%d" % depth, 1)


def _to_display_row(
    rec: Dict[str, Any],
    associated_rel: str,
    walk_nodes: List[str],
    walk_rels: List[str],
) -> Dict[str, Any]:
    path_nodes, path_rels = display_from_reverse_walk(
        str(rec["head_name"]), associated_rel, walk_nodes, walk_rels
    )
    return {
        "recalled_id": rec["recalled_id"],
        "recalled_name": rec["recalled_name"],
        "head_name": rec["head_name"],
        "head_kind": rec["head_kind"],
        "hops": int(rec["hops"]),
        "confidence": rec.get("confidence"),
        "path_nodes": path_nodes,
        "path_rels": path_rels,
        "path": format_path(path_nodes, path_rels),
        "template": "aesthetic_to_main_combined",
    }


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
        self._main_path_cache: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = {}
        self._neighbor_path_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}

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

    def batch_main_paths(
        self,
        node_ids: List[str],
        per_pair: int = 2,
        max_hops: int = 5,
    ) -> List[Dict[str, Any]]:
        """Reverse-walk original relations, then AssociatedWith, flip to client display order."""
        ordered_ids = _ordered_unique(node_ids)
        effective_max_hops = _effective_max_hops(max_hops)
        pair_limit = max(int(per_pair), 1)
        if not ordered_ids or effective_max_hops < 1:
            return []

        missing_ids = [
            node_id
            for node_id in ordered_ids
            if (node_id, effective_max_hops, pair_limit) not in self._main_path_cache
        ]
        if missing_ids:
            fetched: Dict[str, List[Dict[str, Any]]] = {
                node_id: [] for node_id in missing_ids
            }
            for query in (DIRECT_STYLE_PATH_QUERY, DIRECT_TYPE_PATH_QUERY):
                for rec in self.inner._read(query, node_ids=missing_ids):
                    recalled_id = str(rec.get("recalled_id") or "")
                    if recalled_id not in fetched:
                        continue
                    fetched[recalled_id].append(
                        _to_display_row(
                            rec,
                            associated_rel=(
                                ASSOCIATED_STYLE
                                if rec["head_kind"] == "style"
                                else ASSOCIATED_TYPE
                            ),
                            walk_nodes=[rec["recalled_name"]],
                            walk_rels=[],
                        )
                    )

            walk_hops = effective_max_hops - 1
            if walk_hops >= 1:
                for base_query in (MULTI_STYLE_PATH_QUERY, MULTI_TYPE_PATH_QUERY):
                    query = _multi_path_query(base_query, walk_hops)
                    for rec in self.inner._read(
                        query,
                        node_ids=missing_ids,
                        per_pair=pair_limit,
                    ):
                        recalled_id = str(rec.get("recalled_id") or "")
                        if recalled_id not in fetched:
                            continue
                        fetched[recalled_id].append(
                            _to_display_row(
                                rec,
                                associated_rel=(
                                    ASSOCIATED_STYLE
                                    if rec["head_kind"] == "style"
                                    else ASSOCIATED_TYPE
                                ),
                                walk_nodes=list(rec.get("walk_nodes") or []),
                                walk_rels=list(rec.get("walk_rels") or []),
                            )
                        )

            for node_id, node_rows in fetched.items():
                self._main_path_cache[
                    (node_id, effective_max_hops, pair_limit)
                ] = node_rows

        rows: List[Dict[str, Any]] = []
        for node_id in ordered_ids:
            rows.extend(
                self._main_path_cache[
                    (node_id, effective_max_hops, pair_limit)
                ]
            )
        return rows

    def batch_neighbor_evidence(
        self, node_ids: List[str], per_node: int = 2
    ) -> List[Dict[str, Any]]:
        ordered_ids = _ordered_unique(node_ids)
        node_limit = max(int(per_node), 1)
        if not ordered_ids:
            return []

        missing_ids = [
            node_id
            for node_id in ordered_ids
            if (node_id, node_limit) not in self._neighbor_path_cache
        ]
        if missing_ids:
            fetched: Dict[str, List[Dict[str, Any]]] = {
                node_id: [] for node_id in missing_ids
            }
            for rec in self.inner._read(
                BATCH_NEIGHBOR_EVIDENCE_QUERY,
                node_ids=missing_ids,
                per_node=node_limit,
            ):
                recalled_id = str(rec.get("recalled_id") or "")
                if recalled_id in fetched:
                    fetched[recalled_id].append(rec)
            for node_id, node_rows in fetched.items():
                self._neighbor_path_cache[(node_id, node_limit)] = node_rows

        rows: List[Dict[str, Any]] = []
        for node_id in ordered_ids:
            rows.extend(self._neighbor_path_cache[(node_id, node_limit)])
        return rows

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
