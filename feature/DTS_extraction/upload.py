"""Explicit, idempotent Neo4j upload of validated DTS graph JSONL rows."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from feature.parameter_recommendation.common import load_env_file

from .schema import METRICS


def quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def neo4j_config(env_path: Path) -> Tuple[str, str, str, Optional[str]]:
    values = load_env_file(env_path)
    def setting(key: str) -> str:
        return os.getenv(key, values.get(key, "")).strip()
    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    missing = [key for key in required if not setting(key)]
    if missing:
        raise ValueError("Missing Neo4j settings: " + ", ".join(missing))
    return setting("NEO4J_URI"), setting("NEO4J_USERNAME"), setting("NEO4J_PASSWORD"), setting("NEO4J_DATABASE") or None


def dts_merge_query() -> str:
    properties = ["relative_position", *METRICS, "classification", "area"]
    rel_properties = ", ".join("%s: row.%s" % (quote_identifier(name), quote_identifier(name)) for name in properties)
    return (
        "UNWIND $rows AS row "
        "MATCH (a:Part {id: row.start_id}) "
        "MATCH (b:Part {id: row.end_id}) "
        "MERGE (a)-[r:DTS_POSITION_RELATION {%s}]->(b)"
    ) % rel_properties


CONTAINS_QUERY = (
    "UNWIND $rows AS row "
    "MATCH (v:VehicleType {id: row.start_id}) "
    "MATCH (p:Part {id: row.end_id}) "
    "MERGE (v)-[r:CONTAINS {classification: row.classification, area: row.area}]->(p)"
)


def _chunks(rows: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def _run(tx: object, query: str, rows: Sequence[dict]) -> None:
    tx.run(query, rows=list(rows)).consume()


def upload_graph(
    graph: Sequence[dict],
    env_path: Path,
    batch_size: int = 200,
    driver_factory: Optional[Callable[..., object]] = None,
) -> Dict[str, int]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    uri, username, password, database = neo4j_config(env_path)
    if driver_factory is None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("Install neo4j (see requirements.txt) to upload") from exc
        driver_factory = GraphDatabase.driver

    vehicles = [row for row in graph if row["type"] == "node" and "VehicleType" in row["labels"]]
    parts_by_label: DefaultDict[str, List[dict]] = defaultdict(list)
    contains: List[dict] = []
    dts: List[dict] = []
    for row in graph:
        if row["type"] == "node" and "Part" in row["labels"]:
            if len(row["labels"]) != 2:
                raise ValueError("Every Part must have exactly one concrete label")
            parts_by_label[row["labels"][1]].append(row["properties"])
        elif row["type"] == "relationship":
            props = row["properties"]
            if row["label"] == "CONTAINS":
                contains.append({"start_id": row["start_id"], "end_id": row["end_id"], **props})
            elif row["label"] == "DTS_POSITION_RELATION":
                dts.append({"start_id": row["start_id"], "end_id": row["end_id"], **props})
            else:
                raise ValueError("Unexpected relationship type: %s" % row["label"])
    if len(vehicles) != 4:
        raise ValueError("Expected exactly four VehicleType nodes before upload")
    if not dts:
        raise ValueError("No PDF-verified DTS_POSITION_RELATION rows to upload")

    driver = driver_factory(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run("CREATE CONSTRAINT dts_vehicle_id IF NOT EXISTS FOR (v:VehicleType) REQUIRE v.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT dts_part_id IF NOT EXISTS FOR (p:Part) REQUIRE p.id IS UNIQUE").consume()
            for chunk in _chunks([row["properties"] for row in vehicles], batch_size):
                session.execute_write(_run, "UNWIND $rows AS row MERGE (v:VehicleType {id: row.id}) SET v.name = row.name", chunk)
            for label, rows in sorted(parts_by_label.items()):
                query = (
                    "UNWIND $rows AS row MERGE (p:Part {id: row.id}) "
                    "SET p.name = row.name SET p:%s" % quote_identifier(label)
                )
                for chunk in _chunks(rows, batch_size):
                    session.execute_write(_run, query, chunk)
            for chunk in _chunks(contains, batch_size):
                session.execute_write(_run, CONTAINS_QUERY, chunk)
            for chunk in _chunks(dts, batch_size):
                session.execute_write(_run, dts_merge_query(), chunk)
    finally:
        driver.close()
    return {
        "vehicle_nodes_processed": len(vehicles),
        "part_nodes_processed": sum(len(rows) for rows in parts_by_label.values()),
        "contains_relationships_processed": len(contains),
        "dts_relationships_processed": len(dts),
    }
