"""Opt-in, idempotent Neo4j upload; graph JSONL never contains credentials."""

from __future__ import annotations

import os
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional, Sequence

from feature.parameter_recommendation.common import load_env_file

from .schema import METRICS


def neo4j_config(env_path: Path) -> tuple[str, str, str, Optional[str]]:
    values = load_env_file(env_path)

    def setting(key: str) -> str:
        return os.getenv(key, values.get(key, "")).strip()

    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    missing = [key for key in required if not setting(key)]
    if missing:
        raise ValueError("Missing Neo4j settings: " + ", ".join(missing))
    return setting("NEO4J_URI"), setting("NEO4J_USERNAME"), setting("NEO4J_PASSWORD"), setting("NEO4J_DATABASE") or None


def _quote(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _chunks(rows: Sequence[dict], size: int):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def _run(tx: object, query: str, rows: Sequence[dict]) -> None:
    tx.run(query, rows=list(rows)).consume()


CONTAINS_QUERY = (
    "UNWIND $rows AS row MATCH (v:VehicleType {id: row.start_id}) "
    "MATCH (p:Part {id: row.end_id}) MERGE (v)-[:CONTAINS {location: row.location}]->(p)"
)
LEGACY_CONTAINS_QUERY = (
    "UNWIND $rows AS row MATCH (v:VehicleType {id: row.start_id}) "
    "MATCH (p:Part {id: row.end_id}) MERGE (v)-[:CONTAINS]->(p)"
)

VEHICLE_QUERY = "UNWIND $rows AS row MERGE (v:VehicleType {id: row.id}) SET v.name = row.name"


def part_query(label: str, with_location: bool = True) -> str:
    if not with_location:
        return f"UNWIND $rows AS row MERGE (p:Part {{id: row.id}}) SET p.name = row.name SET p:{_quote(label)}"
    return (f"UNWIND $rows AS row MERGE (p:Part {{id: row.id}}) "
            f"SET p.name = row.name, p.location = row.location SET p:{_quote(label)}")


def dts_query() -> str:
    properties = ("relative_position", *METRICS, "classification", "area")
    body = ", ".join(f"{_quote(key)}: row.{_quote(key)}" for key in properties)
    return (
        "UNWIND $rows AS row MATCH (a:Part {id: row.start_id}) "
        "MATCH (b:Part {id: row.end_id}) "
        f"MERGE (a)-[:DTS_POSITION_RELATION {{{body}}}]->(b)"
    )


def _old_graph_ids(path: Path) -> list[str]:
    """Accept only the exact legacy DTS node snapshot as a replacement target."""
    if not path.is_file():
        raise FileNotFoundError(path)
    ids = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "node":
                identifier = row.get("id")
                if not isinstance(identifier, str) or not identifier.startswith(("DTS:vehicle:", "DTS:part:")):
                    raise ValueError("Old graph contains a non-DTS node")
                ids.append(identifier)
    if len(ids) != len(set(ids)) or sum(identifier.startswith("DTS:vehicle:") for identifier in ids) != 4:
        raise ValueError("Old graph must contain exactly four unique DTS vehicle nodes")
    return ids


def _replace_tx(tx: object, old_ids: list[str], vehicles: list[dict], parts: dict,
                contains: list[dict], dts: list[dict], batch_size: int) -> None:
    tx.run("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", ids=old_ids).consume()
    for chunk in _chunks(vehicles, batch_size):
        _run(tx, VEHICLE_QUERY, chunk)
    for label, rows in sorted(parts.items()):
        for chunk in _chunks(rows, batch_size):
            _run(tx, part_query(label), chunk)
    for chunk in _chunks(contains, batch_size):
        _run(tx, CONTAINS_QUERY, chunk)
    for chunk in _chunks(dts, batch_size):
        _run(tx, dts_query(), chunk)


def upload_graph(graph: Sequence[dict], env_path: Path, batch_size: int = 200,
                 driver_factory: Optional[Callable[..., object]] = None,
                 replace_old_graph_path: Optional[Path] = None) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    vehicles = [row["properties"] for row in graph if row["type"] == "node" and row["labels"] == ["VehicleType"]]
    if len(vehicles) != 4:
        raise ValueError("Exactly four VehicleType nodes required")
    parts = defaultdict(list)
    contains, dts = [], []
    part_rows = [row for row in graph if row["type"] == "node" and "Part" in row["labels"]]
    location_flags = {bool(row["properties"].get("location")) for row in part_rows}
    if len(location_flags) != 1:
        raise ValueError("Cannot mix location-aware and legacy Part nodes")
    with_location = location_flags == {True}
    if replace_old_graph_path and not with_location:
        raise ValueError("Old graph replacement requires location-aware Part nodes")
    for row in graph:
        if row["type"] == "node" and "Part" in row["labels"]:
            if len(row["labels"]) != 2:
                raise ValueError("Part must have one concrete PDF label")
            if with_location and not row["properties"].get("location"):
                raise ValueError("Part is missing a physical location")
            parts[row["labels"][1]].append(row["properties"])
        elif row["type"] == "relationship" and row["label"] == "CONTAINS":
            if with_location and not row["properties"].get("location"):
                raise ValueError("CONTAINS is missing a physical location")
            contains.append({"start_id": row["start_id"], "end_id": row["end_id"],
                             "location": row["properties"].get("location", "")})
        elif row["type"] == "relationship" and row["label"] == "DTS_POSITION_RELATION":
            dts.append({"start_id": row["start_id"], "end_id": row["end_id"], **row["properties"]})
    if not dts:
        raise ValueError("No approved PDF relations to upload")
    old_ids = _old_graph_ids(replace_old_graph_path) if replace_old_graph_path else None
    uri, username, password, database = neo4j_config(env_path)
    if driver_factory is None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("Install neo4j before --upload") from exc
        driver_factory = GraphDatabase.driver
    driver = driver_factory(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run("CREATE CONSTRAINT dts_vehicle_id IF NOT EXISTS FOR (v:VehicleType) REQUIRE v.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT dts_part_id IF NOT EXISTS FOR (p:Part) REQUIRE p.id IS UNIQUE").consume()
            existing_ids = set(session.run(
                "MATCH (n) WHERE n.id STARTS WITH 'DTS:vehicle:' OR n.id STARTS WITH 'DTS:part:' RETURN n.id AS id"
            ).value())
            new_ids = {row["id"] for row in graph if row["type"] == "node"}
            if old_ids is None:
                if existing_ids - new_ids:
                    raise ValueError("Database has legacy DTS nodes; use --replace-old-graph with an exact backup JSONL")
                for chunk in _chunks(vehicles, batch_size):
                    session.execute_write(_run, VEHICLE_QUERY, chunk)
                for label, rows in sorted(parts.items()):
                    for chunk in _chunks(rows, batch_size):
                        session.execute_write(_run, part_query(label, with_location), chunk)
                for chunk in _chunks(contains, batch_size):
                    session.execute_write(_run, CONTAINS_QUERY if with_location else LEGACY_CONTAINS_QUERY, chunk)
                for chunk in _chunks(dts, batch_size):
                    session.execute_write(_run, dts_query(), chunk)
            else:
                if existing_ids != set(old_ids):
                    raise ValueError("Database DTS nodes differ from the supplied old graph; refusing replacement")
                rel_types = set(session.run(
                    "MATCH (n)-[r]-() WHERE n.id IN $ids RETURN DISTINCT type(r) AS kind",
                    ids=old_ids,
                ).value())
                if rel_types - {"CONTAINS", "DTS_POSITION_RELATION"}:
                    raise ValueError("Old DTS nodes have unrelated relationships; refusing replacement")
                outside_edges = session.run(
                    "MATCH (n)-[r]-(other) WHERE n.id IN $ids "
                    "AND NOT (coalesce(other.id, '') IN $ids) RETURN count(r) AS count",
                    ids=old_ids,
                ).value()[0]
                if outside_edges:
                    raise ValueError("Old DTS nodes connect to nodes outside the supplied graph; refusing replacement")
                session.execute_write(_replace_tx, old_ids, vehicles, parts, contains, dts, batch_size)
    finally:
        driver.close()
    return {
        "vehicle_nodes_processed": len(vehicles),
        "part_nodes_processed": sum(map(len, parts.values())),
        "contains_processed": len(contains), "dts_processed": len(dts),
        "old_nodes_replaced": len(old_ids or []),
    }
