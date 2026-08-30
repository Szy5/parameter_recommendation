#!/usr/bin/env python3
"""Add the shared GraphNode label and _graph_id constraint to an existing graph."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from neo4j import GraphDatabase

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature.parameter_recommendation.import_jsonl_to_neo4j import (  # type: ignore
        GRAPH_NODE_CONSTRAINT,
        GRAPH_NODE_LABEL,
        load_config,
        quote_identifier,
    )
else:
    from .import_jsonl_to_neo4j import (
        GRAPH_NODE_CONSTRAINT,
        GRAPH_NODE_LABEL,
        load_config,
        quote_identifier,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def migrate(session: Any) -> Dict[str, Any]:
    before = session.run(
        "MATCH (n) "
        "RETURN count(n) AS nodes, "
        "count(CASE WHEN n._graph_id IS NULL THEN 1 END) AS missing_graph_ids, "
        "count(CASE WHEN $graph_node_label IN labels(n) THEN 1 END) AS graph_nodes",
        graph_node_label=GRAPH_NODE_LABEL,
    ).single()
    duplicate_rows = session.run(
        "MATCH (n) WHERE n._graph_id IS NOT NULL "
        "WITH n._graph_id AS graph_id, count(*) AS occurrences "
        "WHERE occurrences > 1 "
        "RETURN graph_id, occurrences ORDER BY occurrences DESC LIMIT 20"
    ).data()

    missing_graph_ids = int(before["missing_graph_ids"] if before else 0)
    if missing_graph_ids:
        raise ValueError(
            "refusing migration: %d nodes have no _graph_id" % missing_graph_ids
        )
    if duplicate_rows:
        raise ValueError(
            "refusing migration: duplicate _graph_id values found: %s"
            % json.dumps(duplicate_rows, ensure_ascii=False)
        )

    session.run(
        "MATCH (n) WHERE NOT n:%s SET n:%s"
        % (quote_identifier(GRAPH_NODE_LABEL), quote_identifier(GRAPH_NODE_LABEL))
    ).consume()
    session.run(
        "CREATE CONSTRAINT %s IF NOT EXISTS FOR (n:%s) "
        "REQUIRE n._graph_id IS UNIQUE"
        % (
            quote_identifier(GRAPH_NODE_CONSTRAINT),
            quote_identifier(GRAPH_NODE_LABEL),
        )
    ).consume()

    after = session.run(
        "MATCH (n) "
        "RETURN count(n) AS nodes, "
        "count(CASE WHEN $graph_node_label IN labels(n) THEN 1 END) AS graph_nodes",
        graph_node_label=GRAPH_NODE_LABEL,
    ).single()
    constraint_count = session.run(
        "SHOW CONSTRAINTS YIELD name "
        "WHERE name = $name RETURN count(*) AS count",
        name=GRAPH_NODE_CONSTRAINT,
    ).single()
    return {
        "before": dict(before) if before else {},
        "after": dict(after) if after else {},
        "duplicate_graph_ids": 0,
        "constraint": GRAPH_NODE_CONSTRAINT,
        "constraint_present": bool(constraint_count and constraint_count["count"] == 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently add the GraphNode label and _graph_id constraint"
    )
    parser.add_argument("--env", type=Path, default=Path("feature/.env"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "feature/benchmark_recommendation/artifacts/graph_node_index_migration_report.json"
        ),
    )
    args = parser.parse_args()

    uri, username, password, database = load_config(args.env)
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            report = migrate(session)
    finally:
        driver.close()

    report["finished_at"] = _now_iso()
    report["database"] = database or "<default>"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
