#!/usr/bin/env python3
"""Calculate Top-N Schema union connectivity grouped by start label."""

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
from feature.topn_n_schema_connectivity.connectivity import (
    PATH_EXTRACTION_DIRECTION_MODE,
    SUPPORTED_DIRECTION_MODES,
)

from .connectivity import (
    SchemaGroup,
    build_label_connectivity_query,
    calculate_label_connectivity,
    group_schemas_by_start_label,
    load_selected_schema_strings,
    sort_label_results,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMAS_JSON = PROJECT_ROOT / "data" / "top25_schema_connectivity_500.json"
DEFAULT_ENV = PROJECT_ROOT / "feature" / ".env"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "top25_schema_connectivity_by_label_500.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schemas-json",
        type=Path,
        default=DEFAULT_SCHEMAS_JSON,
        help="JSON containing the selected SCHEMAS list",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=25,
        help="Use the first N selected Schemas (default: 25)",
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
        help="Output JSON path",
    )
    parser.add_argument(
        "--direction-mode",
        choices=SUPPORTED_DIRECTION_MODES,
        default=PATH_EXTRACTION_DIRECTION_MODE,
        help=(
            "path_extraction keeps original middle relationships undirected; "
            "literal follows every displayed arrow"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate grouped Cypher without connecting to Neo4j",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Record one label-group error and continue",
    )
    return parser.parse_args()


def dry_run_result(group: SchemaGroup, direction_mode: str) -> Dict[str, Any]:
    return {
        "start_label": group.start_label,
        "schema_count": len(group.schemas),
        "schemas": [parsed.raw for parsed in group.schemas],
        "endpoint_labels": sorted({parsed.end_label for parsed in group.schemas}),
        "total_start_nodes": None,
        "connected_start_nodes": None,
        "connectivity": None,
        "connectivity_percent": None,
        "status": "dry_run",
        "direction_mode": direction_mode,
        "cypher": build_label_connectivity_query(group, direction_mode),
    }


def main() -> None:
    args = parse_args()
    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")

    all_schemas = load_selected_schema_strings(args.schemas_json)
    if args.top_n > len(all_schemas):
        raise ValueError(
            "--top-n=%d exceeds the %d Schemas available in %s"
            % (args.top_n, len(all_schemas), args.schemas_json)
        )

    schemas = all_schemas[: args.top_n]
    groups = group_schemas_by_start_label(schemas)
    results: List[Dict[str, Any]] = []
    repository = None
    try:
        if not args.dry_run:
            repository = Neo4jRecommendationRepository.connect(
                Neo4jConfig.from_env(args.env)
            )

        for group_rank, group in enumerate(groups, start=1):
            try:
                if args.dry_run:
                    result = dry_run_result(group, args.direction_mode)
                else:
                    assert repository is not None
                    result = calculate_label_connectivity(
                        repository,
                        group,
                        args.direction_mode,
                    )
                result["group_rank"] = group_rank
                results.append(result)
            except Exception as exc:
                if not args.continue_on_error:
                    raise
                results.append(
                    {
                        "group_rank": group_rank,
                        "start_label": group.start_label,
                        "schema_count": len(group.schemas),
                        "schemas": [parsed.raw for parsed in group.schemas],
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
            "top_n": args.top_n,
            "schema_count": len(schemas),
            "start_label_count": len(groups),
            "definition": "connected_start_nodes / total_start_nodes",
            "grouping_rule": "group Top-N Schemas by their leftmost start label",
            "connected_rule": (
                "a start node counts once when any Schema in its label group "
                "reaches either 汽车风格 or 汽车车型"
            ),
            "deduplication_rule": (
                "one start node contributes at most one connected count even "
                "when multiple Schemas or paths match"
            ),
            "direction_mode": args.direction_mode,
            "dry_run": bool(args.dry_run),
            "ok_count": ok_count,
            "error_count": error_count,
        },
        "results": results,
        "labels_sorted_by_connectivity": sort_label_results(results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print("schema count = %d" % len(schemas))
    print("start label count = %d" % len(groups))
    print("ok count = %d" % ok_count)
    print("error count = %d" % error_count)
    print("output = %s" % args.output)


if __name__ == "__main__":
    main()
