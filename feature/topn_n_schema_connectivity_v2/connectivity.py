"""Calculate the union connectivity of Top-N Schemas grouped by start label."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Protocol, Sequence, Tuple

from feature.topn_n_schema_connectivity.connectivity import (
    PATH_EXTRACTION_DIRECTION_MODE,
    ParsedSchema,
    edge_directions,
    load_schema_strings,
    parse_schema,
    quote_identifier,
)


class ReadRepository(Protocol):
    def _read(self, query: str, **parameters: Any) -> List[Dict[str, Any]]:
        ...


@dataclass(frozen=True)
class SchemaGroup:
    """All selected Schemas that share the same recalled-node label."""

    start_label: str
    schemas: Tuple[ParsedSchema, ...]


def load_selected_schema_strings(path: Path) -> List[str]:
    """Load selected Schemas from V1 results or the legacy SCHEMAS shape."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        schemas = []
        seen = set()
        for index, result in enumerate(payload["results"], start=1):
            if not isinstance(result, dict) or not isinstance(result.get("schema"), str):
                raise ValueError("result item %d does not contain a Schema" % index)
            schema = str(result["schema"]).strip()
            if not schema:
                raise ValueError("result item %d contains an empty Schema" % index)
            if schema in seen:
                raise ValueError("duplicate Schema at result item %d: %s" % (index, schema))
            seen.add(schema)
            schemas.append(schema)
        if not schemas:
            raise ValueError("results list is empty")
        return schemas

    return load_schema_strings(path)


def group_schemas_by_start_label(schema_strings: Sequence[str]) -> List[SchemaGroup]:
    """Parse Schemas and group them by their leftmost label.

    Group order follows the first occurrence of each start label in the Top-N
    input. Schema order inside a group also follows the Top-N input.
    """

    grouped: Dict[str, List[ParsedSchema]] = {}
    for schema_string in schema_strings:
        parsed = parse_schema(schema_string)
        grouped.setdefault(parsed.start_label, []).append(parsed)
    return [
        SchemaGroup(start_label=label, schemas=tuple(schemas))
        for label, schemas in grouped.items()
    ]


def _path_pattern(
    parsed: ParsedSchema,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> str:
    """Build a path pattern starting from the shared ``start`` variable."""

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
        parts.append("(s%d:%s)" % (index, quote_identifier(node_label)))
    return "".join(parts)


def _validate_group(group: SchemaGroup) -> None:
    if not group.schemas:
        raise ValueError("a Schema group must contain at least one Schema")
    for parsed in group.schemas:
        if parsed.start_label != group.start_label:
            raise ValueError(
                "Schema %r starts with %r, expected %r"
                % (parsed.raw, parsed.start_label, group.start_label)
            )


def build_label_connectivity_query(
    group: SchemaGroup,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> str:
    """Build one Cypher query for the union of a start-label Schema group.

    A start node is connected when at least one ``EXISTS`` branch succeeds.
    The outer aggregation keeps the denominator and numerator at start-node
    grain, regardless of how many Schemas or concrete paths the node matches.
    """

    _validate_group(group)
    exists_expressions = []
    for parsed in group.schemas:
        pattern = _path_pattern(parsed, direction_mode)
        exists_expressions.append("EXISTS {\n    MATCH %s\n  }" % pattern)

    connected_expression = "\n  OR ".join(exists_expressions)
    return (
        "MATCH (start:%s)\n"
        "WITH start, (\n"
        "  %s\n"
        ") AS connected\n"
        "RETURN count(start) AS total_start_nodes,\n"
        "       sum(CASE WHEN connected THEN 1 ELSE 0 END) AS connected_start_nodes"
        % (quote_identifier(group.start_label), connected_expression)
    )


def calculate_label_connectivity(
    repository: ReadRepository,
    group: SchemaGroup,
    direction_mode: str = PATH_EXTRACTION_DIRECTION_MODE,
) -> Dict[str, Any]:
    """Execute a label-group query and return its union connectivity."""

    query = build_label_connectivity_query(group, direction_mode)
    rows = repository._read(query)
    if len(rows) != 1:
        raise RuntimeError(
            "label connectivity query must return exactly one row, got %d"
            % len(rows)
        )

    total_start_nodes = int(rows[0].get("total_start_nodes") or 0)
    connected_start_nodes = int(rows[0].get("connected_start_nodes") or 0)
    if connected_start_nodes < 0 or connected_start_nodes > total_start_nodes:
        raise RuntimeError(
            "invalid connectivity counts: connected=%d, total=%d"
            % (connected_start_nodes, total_start_nodes)
        )

    if total_start_nodes == 0:
        connectivity = None
        status = "no_start_nodes"
    else:
        connectivity = connected_start_nodes / total_start_nodes
        status = "ok"

    return {
        "start_label": group.start_label,
        "schema_count": len(group.schemas),
        "schemas": [parsed.raw for parsed in group.schemas],
        "endpoint_labels": sorted({parsed.end_label for parsed in group.schemas}),
        "total_start_nodes": total_start_nodes,
        "connected_start_nodes": connected_start_nodes,
        "connectivity": connectivity,
        "connectivity_percent": (
            connectivity * 100.0 if connectivity is not None else None
        ),
        "status": status,
        "direction_mode": direction_mode,
        "cypher": query,
    }


def sort_label_results(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a compact label/connectivity list ordered from high to low."""

    sortable = [
        {
            "start_label": str(result["start_label"]),
            "connectivity": result.get("connectivity"),
        }
        for result in results
        if result.get("status") in ("ok", "no_start_nodes")
    ]
    return sorted(
        sortable,
        key=lambda row: (
            row["connectivity"] is None,
            -(float(row["connectivity"]) if row["connectivity"] is not None else 0.0),
            row["start_label"],
        ),
    )
