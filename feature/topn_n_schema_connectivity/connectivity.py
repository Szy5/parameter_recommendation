"""Parse path schemas and calculate their start-node connectivity in Neo4j."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from feature.benchmark_recommendation.path_morphology import (
    ASSOCIATED_STYLE,
    ASSOCIATED_TYPE,
    ORIG_REL_TYPES,
    short_rel,
)


SUPPORTED_ENDPOINT_LABELS = ("汽车风格", "汽车车型")
PATH_EXTRACTION_DIRECTION_MODE = "path_extraction"
LITERAL_DIRECTION_MODE = "literal"
SUPPORTED_DIRECTION_MODES = (
    PATH_EXTRACTION_DIRECTION_MODE,
    LITERAL_DIRECTION_MODE,
)

# Schema files use short relationship names, while Neo4j stores the original
# bilingual relationship types.  AssociatedWith types are already unshortened.
RELATIONSHIP_TYPE_BY_SCHEMA_NAME = {
    short_rel(relationship_type): relationship_type
    for relationship_type in ORIG_REL_TYPES
}
RELATIONSHIP_TYPE_BY_SCHEMA_NAME.update(
    {
        ASSOCIATED_STYLE: ASSOCIATED_STYLE,
        ASSOCIATED_TYPE: ASSOCIATED_TYPE,
    }
)


class ReadRepository(Protocol):
    def _read(self, query: str, **parameters: Any) -> List[Dict[str, Any]]:
        ...


@dataclass(frozen=True)
class ParsedSchema:
    """Alternating node labels and relationship types from one path schema."""

    raw: str
    node_labels: Tuple[str, ...]
    relationship_types: Tuple[str, ...]
    direction: str

    @property
    def start_label(self) -> str:
        return self.node_labels[0]

    @property
    def end_label(self) -> str:
        return self.node_labels[-1]


def quote_identifier(value: str) -> str:
    """Quote a Neo4j label or relationship type as a Cypher identifier."""

    return "`" + str(value).replace("`", "``") + "`"


def _normalize_relationship_type(value: str) -> str:
    relationship_type = str(value).strip()
    if not relationship_type:
        raise ValueError("relationship type must not be empty")
    if relationship_type in RELATIONSHIP_TYPE_BY_SCHEMA_NAME:
        return RELATIONSHIP_TYPE_BY_SCHEMA_NAME[relationship_type]
    if relationship_type in RELATIONSHIP_TYPE_BY_SCHEMA_NAME.values():
        return relationship_type
    supported = ", ".join(sorted(RELATIONSHIP_TYPE_BY_SCHEMA_NAME))
    raise ValueError(
        "unsupported relationship type %r; expected one of: %s"
        % (relationship_type, supported)
    )


def parse_schema(
    schema: str,
    *,
    require_supported_endpoint: bool = True,
) -> ParsedSchema:
    """Parse ``Node <- Rel <- Node`` or ``Node -> Rel -> Node``.

    The reversed Top-N files use ``<-`` and their leftmost node type is the
    connectivity denominator.  Arrow parsing is kept separately from query
    semantics because the original path extractor matched middle graph
    relationships without direction.
    """

    raw = str(schema).strip()
    if not raw:
        raise ValueError("schema must not be empty")

    has_incoming = " <- " in raw
    has_outgoing = " -> " in raw
    if has_incoming == has_outgoing:
        raise ValueError(
            "schema must use exactly one uniform arrow style: ' <- ' or ' -> '"
        )

    separator = " <- " if has_incoming else " -> "
    direction = "incoming" if has_incoming else "outgoing"
    parts = [part.strip() for part in raw.split(separator)]
    if len(parts) < 3 or len(parts) % 2 == 0:
        raise ValueError(
            "schema must alternate node labels and relationship types"
        )
    if any(not part for part in parts):
        raise ValueError("schema contains an empty node label or relationship type")

    node_labels = tuple(parts[0::2])
    relationship_types = tuple(
        _normalize_relationship_type(value) for value in parts[1::2]
    )
    if len(node_labels) != len(relationship_types) + 1:
        raise ValueError("schema node/relationship counts are inconsistent")
    if require_supported_endpoint and node_labels[-1] not in SUPPORTED_ENDPOINT_LABELS:
        raise ValueError(
            "schema endpoint must be one of %s, got %r"
            % (SUPPORTED_ENDPOINT_LABELS, node_labels[-1])
        )

    return ParsedSchema(
        raw=raw,
        node_labels=node_labels,
        relationship_types=relationship_types,
        direction=direction,
    )


def edge_directions(
    parsed: ParsedSchema,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> Tuple[str, ...]:
    """Return the Cypher direction for every relationship in a Schema.

    ``path_extraction`` reproduces how the Top-N source paths were generated:
    original middle relationships were matched undirected, while the final
    AssociatedWith edge retained its graph direction.  ``literal`` treats the
    displayed arrow as the direction of every relationship and is useful only
    for comparison or for a future direction-preserving Schema source.
    """

    if direction_mode not in SUPPORTED_DIRECTION_MODES:
        raise ValueError(
            "unknown direction mode %r; expected one of %s"
            % (direction_mode, SUPPORTED_DIRECTION_MODES)
        )
    directions = []
    for relationship_type in parsed.relationship_types:
        if (
            direction_mode == PATH_EXTRACTION_DIRECTION_MODE
            and relationship_type not in (ASSOCIATED_STYLE, ASSOCIATED_TYPE)
        ):
            directions.append("undirected")
        else:
            directions.append(parsed.direction)
    return tuple(directions)


def _path_pattern(
    parsed: ParsedSchema,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> str:
    parts = ["(start:%s)" % quote_identifier(parsed.start_label)]
    for index, (relationship_type, node_label, edge_direction) in enumerate(
        zip(
            parsed.relationship_types,
            parsed.node_labels[1:],
            edge_directions(parsed, direction_mode),
        ),
        start=1,
    ):
        relationship = "[:%s]" % quote_identifier(relationship_type)
        if edge_direction == "incoming":
            parts.append("<-%s-" % relationship)
        elif edge_direction == "outgoing":
            parts.append("-%s->" % relationship)
        else:
            parts.append("-%s-" % relationship)
        parts.append("(n%d:%s)" % (index, quote_identifier(node_label)))
    return "".join(parts)


def build_connectivity_query(
    parsed: ParsedSchema,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> str:
    """Build one aggregation query for one Schema.

    ``EXISTS`` keeps one row per start node, so the numerator counts connected
    start nodes rather than the potentially much larger number of matching
    paths.
    """

    pattern = _path_pattern(parsed, direction_mode)
    return (
        "MATCH (start:%s)\n"
        "WITH start, EXISTS {\n"
        "  MATCH %s\n"
        "} AS connected\n"
        "RETURN count(start) AS total_start_nodes,\n"
        "       sum(CASE WHEN connected THEN 1 ELSE 0 END) AS connected_start_nodes"
        % (quote_identifier(parsed.start_label), pattern)
    )


def calculate_schema_connectivity(
    repository: ReadRepository,
    parsed: ParsedSchema,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> Dict[str, Any]:
    """Execute one Schema query and return its connectivity metrics."""

    query = build_connectivity_query(parsed, direction_mode)
    rows = repository._read(query)
    if len(rows) != 1:
        raise RuntimeError(
            "connectivity query must return exactly one row, got %d" % len(rows)
        )

    total_start_nodes = int(rows[0].get("total_start_nodes") or 0)
    connected_start_nodes = int(rows[0].get("connected_start_nodes") or 0)
    if connected_start_nodes < 0 or connected_start_nodes > total_start_nodes:
        raise RuntimeError(
            "invalid connectivity counts: connected=%d, total=%d"
            % (connected_start_nodes, total_start_nodes)
        )

    connectivity: Optional[float]
    if total_start_nodes == 0:
        connectivity = None
        status = "no_start_nodes"
    else:
        connectivity = connected_start_nodes / total_start_nodes
        status = "ok"

    return {
        "schema": parsed.raw,
        "start_label": parsed.start_label,
        "end_label": parsed.end_label,
        "direction": parsed.direction,
        "direction_mode": direction_mode,
        "edge_directions": list(edge_directions(parsed, direction_mode)),
        "relationship_types": list(parsed.relationship_types),
        "total_start_nodes": total_start_nodes,
        "connected_start_nodes": connected_start_nodes,
        "connectivity": connectivity,
        "connectivity_percent": (
            connectivity * 100.0 if connectivity is not None else None
        ),
        "status": status,
        "cypher": query,
    }


def load_schema_strings(path: Path) -> List[str]:
    """Load Schemas from ``pass_rate*.json`` or a simple JSON list."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    values: Optional[Sequence[Any]] = None
    if isinstance(payload, Mapping):
        candidate = payload.get("SCHEMAS") or payload.get("schemas")
        if isinstance(candidate, list):
            values = candidate
    elif isinstance(payload, list):
        if payload and all(isinstance(item, str) for item in payload):
            values = payload
        else:
            for item in payload:
                if not isinstance(item, Mapping):
                    continue
                candidate = item.get("SCHEMAS") or item.get("schemas")
                if isinstance(candidate, list):
                    values = candidate
                    break

    if values is None:
        raise ValueError(
            "%s does not contain a SCHEMAS list" % Path(path)
        )

    schemas: List[str] = []
    seen = set()
    for index, item in enumerate(values, start=1):
        if isinstance(item, str):
            schema = item.strip()
        elif isinstance(item, Mapping) and isinstance(item.get("schema"), str):
            schema = str(item["schema"]).strip()
        else:
            raise ValueError("schema item %d is not a string or schema object" % index)
        if not schema:
            raise ValueError("schema item %d is empty" % index)
        if schema in seen:
            raise ValueError("duplicate schema at item %d: %s" % (index, schema))
        seen.add(schema)
        schemas.append(schema)

    if not schemas:
        raise ValueError("SCHEMAS list is empty")
    return schemas
