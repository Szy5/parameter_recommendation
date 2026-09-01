#!/usr/bin/env python3
"""Idempotently import the unified JSONL graph into Neo4j."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from neo4j import GraphDatabase

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature.parameter_recommendation.common import iter_jsonl, load_env_file  # type: ignore
else:
    from .common import iter_jsonl, load_env_file


GRAPH_NODE_LABEL = "GraphNode"
GRAPH_NODE_CONSTRAINT = "graph_node_graph_id"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def constraint_name(label: str) -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
    return "graph_id_" + digest


def neo4j_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        if not value:
            return []
        primitive_types = {type(item) for item in value if item is not None}
        if all(item is None or isinstance(item, (str, bool, int, float)) for item in value) and len(primitive_types) <= 1:
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_properties(properties: Dict[str, Any], graph_id: str) -> Dict[str, Any]:
    output = {
        str(key): neo4j_value(value)
        for key, value in properties.items()
        if value is not None
    }
    output["_graph_id"] = graph_id
    return output


def batches(values: Sequence[Dict[str, Any]], size: int) -> Iterable[Sequence[Dict[str, Any]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def load_config(path: Path) -> Tuple[str, str, str, Optional[str]]:
    values = load_env_file(path)
    required = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError("Missing Neo4j configuration: %s" % ", ".join(missing))
    return (
        values["NEO4J_URI"],
        values["NEO4J_USERNAME"],
        values["NEO4J_PASSWORD"],
        values.get("NEO4J_DATABASE") or None,
    )


def graph_labels(path: Path) -> List[str]:
    labels = set()
    for row in iter_jsonl(path):
        if row.get("type") == "node":
            labels.update(str(label) for label in (row.get("labels") or []))
    return sorted(labels)


def import_nodes(session: Any, path: Path, batch_size: int) -> Tuple[int, int]:
    grouped: DefaultDict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
    total = 0
    transactions = 0

    def flush(labels: Tuple[str, ...]) -> None:
        nonlocal transactions
        rows = grouped[labels]
        if not rows:
            return
        label_clause = "".join(":" + quote_identifier(label) for label in labels)
        query = (
            "UNWIND $rows AS row "
            "MERGE (n%s {_graph_id: row.id}) "
            "SET n:%s "
            "SET n += row.properties"
            % (label_clause, quote_identifier(GRAPH_NODE_LABEL))
        )
        session.run(query, rows=rows).consume()
        transactions += 1
        rows.clear()

    for row in iter_jsonl(path):
        if row.get("type") != "node":
            continue
        labels = tuple(sorted(str(label) for label in (row.get("labels") or [])))
        identifier = str(row["id"])
        grouped[labels].append(
            {
                "id": identifier,
                "properties": clean_properties(row.get("properties") or {}, identifier),
            }
        )
        total += 1
        if len(grouped[labels]) >= batch_size:
            flush(labels)
    for labels in list(grouped):
        flush(labels)
    return total, transactions


def import_relationships(session: Any, path: Path, batch_size: int) -> Tuple[int, int]:
    group_type = Tuple[str, str, str]
    grouped: DefaultDict[group_type, List[Dict[str, Any]]] = defaultdict(list)
    total = 0
    transactions = 0

    def flush(key: group_type) -> None:
        nonlocal transactions
        rows = grouped[key]
        if not rows:
            return
        rel_type, start_label, end_label = key
        query = (
            "UNWIND $rows AS row "
            "MATCH (s:%s {_graph_id: row.start_id}) "
            "MATCH (e:%s {_graph_id: row.end_id}) "
            "MERGE (s)-[r:%s {_graph_id: row.id}]->(e) "
            "SET r += row.properties"
            % (
                quote_identifier(start_label),
                quote_identifier(end_label),
                quote_identifier(rel_type),
            )
        )
        result = session.run(query, rows=rows)
        summary = result.consume()
        if summary.counters.relationships_created + summary.counters.properties_set < 0:
            raise RuntimeError("Unexpected Neo4j import summary")
        transactions += 1
        rows.clear()

    for row in iter_jsonl(path):
        if row.get("type") != "relationship":
            continue
        start = row.get("start") or {}
        end = row.get("end") or {}
        start_labels = start.get("labels") or []
        end_labels = end.get("labels") or []
        if not start_labels or not end_labels:
            raise ValueError("Relationship %s has an unlabeled endpoint" % row.get("id"))
        key = (
            str(row.get("label")),
            str(start_labels[0]),
            str(end_labels[0]),
        )
        identifier = str(row["id"])
        grouped[key].append(
            {
                "id": identifier,
                "start_id": str(start.get("id")),
                "end_id": str(end.get("id")),
                "properties": clean_properties(row.get("properties") or {}, identifier),
            }
        )
        total += 1
        if len(grouped[key]) >= batch_size:
            flush(key)
    for key in list(grouped):
        flush(key)
    return total, transactions


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a unified graph JSONL into Neo4j")
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path("feature/.env"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    uri, username, password, database = load_config(args.env)
    labels = graph_labels(args.graph)
    started_at = now_iso()
    started = time.time()
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            before = session.run(
                "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS relationships"
            ).single()
            session.run(
                "CREATE CONSTRAINT %s IF NOT EXISTS FOR (n:%s) "
                "REQUIRE n._graph_id IS UNIQUE"
                % (
                    quote_identifier(GRAPH_NODE_CONSTRAINT),
                    quote_identifier(GRAPH_NODE_LABEL),
                )
            ).consume()
            for label in labels:
                query = (
                    "CREATE CONSTRAINT %s IF NOT EXISTS FOR (n:%s) "
                    "REQUIRE n._graph_id IS UNIQUE"
                    % (quote_identifier(constraint_name(label)), quote_identifier(label))
                )
                session.run(query).consume()
            node_count, node_transactions = import_nodes(
                session, args.graph, args.batch_size
            )
            relationship_count, relationship_transactions = import_relationships(
                session, args.graph, args.batch_size
            )
            after = session.run(
                "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() "
                "RETURN nodes, count(r) AS relationships"
            ).single()
            missing_graph_ids = session.run(
                "MATCH (n) WHERE n._graph_id IS NULL RETURN count(n) AS count"
            ).single()["count"]
            style_properties = session.run(
                "MATCH ()-[r:EXPRESSES_STYLE]->() "
                "RETURN count(r) AS relationships, "
                "count(CASE WHEN r.evidence IS NOT NULL OR r.parameter_source IS NOT NULL "
                "OR r.model IS NOT NULL OR r.prompt_version IS NOT NULL THEN 1 END) AS forbidden_property_relationships"
            ).single()
            relationship_type_counts = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY type"
            ).data()
            label_counts = session.run(
                "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label"
            ).data()

        report = {
            "graph": str(args.graph),
            "database": database or "<default>",
            "started_at": started_at,
            "finished_at": now_iso(),
            "elapsed_seconds": round(time.time() - started, 3),
            "batch_size": args.batch_size,
            "constraints_created_or_verified": len(labels) + 1,
            "source_nodes_processed": node_count,
            "source_relationships_processed": relationship_count,
            "node_transactions": node_transactions,
            "relationship_transactions": relationship_transactions,
            "database_before": dict(before) if before else {},
            "database_after": dict(after) if after else {},
            "nodes_missing_graph_id": missing_graph_ids,
            "expresses_style_validation": dict(style_properties) if style_properties else {},
            "relationship_type_counts": relationship_type_counts,
            "label_counts": label_counts,
            "idempotent_merge": True,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
