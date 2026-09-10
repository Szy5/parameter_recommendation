#!/usr/bin/env python3
"""Calculate Top-N path-schema connectivity against Neo4j."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from feature.parameter_recommendation.neo4j_recommend import (
    Neo4jConfig,
    Neo4jRecommendationRepository,
)

from .connectivity import (
    PATH_EXTRACTION_DIRECTION_MODE,
    SUPPORTED_DIRECTION_MODES,
    build_connectivity_query,
    calculate_schema_connectivity,
    edge_directions,
    load_schema_strings,
    parse_schema,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMAS_JSON = PROJECT_ROOT / "data" / "pass_rate_500.json"
DEFAULT_ENV = PROJECT_ROOT / "feature" / ".env"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "top25_schema_connectivity_500.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schemas-json",
        type=Path,
        default=DEFAULT_SCHEMAS_JSON,
        help="JSON containing the selected SCHEMAS list (default: pass_rate_500.json)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=25,
        help="Use the first N selected Schemas from the input (default: 25)",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=DEFAULT_ENV,
        help="Neo4j dotenv configuration",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Connectivity result JSON",
    )
    parser.add_argument(
        "--direction-mode",
        choices=SUPPORTED_DIRECTION_MODES,
        default=PATH_EXTRACTION_DIRECTION_MODE,
        help=(
            "path_extraction keeps original middle relations undirected; "
            "literal follows every displayed arrow"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse Schemas and write generated Cypher without connecting to Neo4j",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Record an error for one Schema and continue with the remaining Schemas",
    )
    return parser.parse_args()


def dry_run_result(schema: str, direction_mode: str) -> Dict[str, Any]:
    parsed = parse_schema(schema)
    return {
        "schema": parsed.raw,
        "start_label": parsed.start_label,
        "end_label": parsed.end_label,
        "direction": parsed.direction,
        "direction_mode": direction_mode,
        "edge_directions": list(edge_directions(parsed, direction_mode)),
        "relationship_types": list(parsed.relationship_types),
        "total_start_nodes": None,
        "connected_start_nodes": None,
        "connectivity": None,
        "connectivity_percent": None,
        "status": "dry_run",
        "cypher": build_connectivity_query(parsed, direction_mode),
    }


def main() -> None:
    args = parse_args()
    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")

    #top-n 的schema
    all_schemas = load_schema_strings(args.schemas_json)
    if args.top_n > len(all_schemas):
        raise ValueError(
            "--top-n=%d exceeds the %d Schemas available in %s"
            % (args.top_n, len(all_schemas), args.schemas_json)
        )
    schemas = all_schemas[: args.top_n]
    results: List[Dict[str, Any]] = []

    repository = None
    try:
        if not args.dry_run:
            repository = Neo4jRecommendationRepository.connect(
                Neo4jConfig.from_env(args.env)
            )

        for rank, schema in enumerate(schemas, start=1):
            try:
                if args.dry_run:
                    result = dry_run_result(schema, args.direction_mode)
                else:
                    assert repository is not None
                    result = calculate_schema_connectivity(
                        repository,
                        parse_schema(schema),
                        args.direction_mode,
                    )
                result["rank"] = rank
                results.append(result)
            except Exception as exc:
                if not args.continue_on_error:
                    raise
                results.append(
                    {
                        "rank": rank,
                        "schema": schema,
                        "status": "error",
                        "error": "%s: %s" % (type(exc).__name__, exc),
                    }
                )
    finally:
        if repository is not None:
            repository.close()

    ok_count = sum(result.get("status") == "ok" for result in results)
    error_count = sum(result.get("status") == "error" for result in results)
    output = {
        "meta": {
            "generated_at": now_iso(),
            "schemas_json": str(args.schemas_json),
            "schema_count": len(schemas),
            "definition": "connected_start_nodes / total_start_nodes",
            "start_node_rule": "the leftmost node label in each reversed Schema",
            "connected_rule": "a start node counts once if at least one complete Schema path reaches the endpoint",
            "direction_mode": args.direction_mode,
            "direction_note": (
                "path_extraction matches original middle relationships without "
                "direction and keeps AssociatedWith incoming from the endpoint"
                if args.direction_mode == PATH_EXTRACTION_DIRECTION_MODE
                else "literal follows every displayed Schema arrow"
            ),
            "dry_run": bool(args.dry_run),
            "ok_count": ok_count,
            "error_count": error_count,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print("schema count = %d" % len(schemas))
    print("ok count = %d" % ok_count)
    print("error count = %d" % error_count)
    print("output = %s" % args.output)


if __name__ == "__main__":
    main()
