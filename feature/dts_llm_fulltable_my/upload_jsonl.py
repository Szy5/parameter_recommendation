#!/usr/bin/env python3
"""Import the package's flat graph JSONL format into Neo4j.

Accepted rows:
  {"type":"node", "id":"...", "labels":[...], "properties":{...}}
  {"type":"relationship", "label":"...", "start_id":"...",
   "end_id":"...", "properties":{...}}

The source JSONL is read-only. Neo4j cannot store nested maps as properties,
so nested dict/list values are serialized to compact JSON only in the values
sent to Neo4j. Exact re-imports are idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Iterable, Optional, Sequence

from feature.parameter_recommendation.common import load_env_file


GRAPH_NODE_LABEL = "GraphNode"
NODE_CONSTRAINT = "flat_jsonl_graph_node_id"


def quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def chunks(values: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def neo4j_value(value: Any) -> Any:
    """Convert JSON values to legal Neo4j property values without editing input."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        primitive = [item for item in value if item is not None]
        kinds = {type(item) for item in primitive}
        if all(isinstance(item, (str, bool, int, float)) for item in primitive) and len(kinds) <= 1:
            return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def clean_properties(properties: dict) -> dict:
    return {str(key): neo4j_value(value) for key, value in properties.items() if value is not None}


def relationship_id(row: dict) -> str:
    supplied = row.get("id")
    if supplied is not None and str(supplied).strip():
        return str(supplied)
    payload = {
        "label": row["label"], "start_id": row["start_id"], "end_id": row["end_id"],
        "properties": row.get("properties") or {},
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "flat-jsonl-rel:" + digest


def load_graph(path: Path) -> tuple[list[dict], list[dict], dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    node_by_id: dict[str, dict] = {}
    relationships: list[dict] = []
    relationship_ids: set[str] = set()
    serialized_property_values = 0
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_no} 行不是有效 JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"第 {line_no} 行必须是 JSON 对象")
            kind = row.get("type")
            if kind == "node":
                identifier = str(row.get("id", "")).strip()
                labels = row.get("labels")
                properties = row.get("properties", {})
                if not identifier:
                    raise ValueError(f"第 {line_no} 行节点缺少 id")
                if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and x for x in labels):
                    raise ValueError(f"第 {line_no} 行节点 labels 必须是非空字符串数组")
                if not isinstance(properties, dict):
                    raise ValueError(f"第 {line_no} 行节点 properties 必须是对象")
                normalized = {
                    "id": identifier, "labels": list(dict.fromkeys(labels)),
                    "properties": clean_properties(properties),
                }
                normalized["properties"].setdefault("id", identifier)
                existing = node_by_id.get(identifier)
                if existing is not None and existing != normalized:
                    raise ValueError(f"第 {line_no} 行节点 id 重复且内容冲突: {identifier}")
                node_by_id[identifier] = normalized
                serialized_property_values += sum(
                    isinstance(value, (dict, list)) and isinstance(normalized["properties"].get(str(key)), str)
                    for key, value in properties.items()
                )
            elif kind == "relationship":
                label = row.get("label")
                start_id, end_id = row.get("start_id"), row.get("end_id")
                properties = row.get("properties", {})
                if not isinstance(label, str) or not label:
                    raise ValueError(f"第 {line_no} 行关系缺少 label")
                if not isinstance(start_id, str) or not start_id or not isinstance(end_id, str) or not end_id:
                    raise ValueError(f"第 {line_no} 行关系缺少 start_id/end_id")
                if not isinstance(properties, dict):
                    raise ValueError(f"第 {line_no} 行关系 properties 必须是对象")
                identifier = relationship_id(row)
                normalized_properties = clean_properties(properties)
                normalized_properties["_graph_id"] = identifier
                normalized = {
                    "id": identifier, "label": label, "start_id": start_id,
                    "end_id": end_id, "properties": normalized_properties,
                }
                if identifier not in relationship_ids:
                    relationships.append(normalized)
                    relationship_ids.add(identifier)
                serialized_property_values += sum(
                    isinstance(value, (dict, list)) and isinstance(normalized_properties.get(str(key)), str)
                    for key, value in properties.items()
                )
            else:
                raise ValueError(f"第 {line_no} 行 type 只能是 node 或 relationship")

    missing = sorted({r[endpoint] for r in relationships for endpoint in ("start_id", "end_id")
                      if r[endpoint] not in node_by_id})
    if missing:
        sample = ", ".join(missing[:5])
        print(f"有 {len(missing)} 个关系端点在节点记录中不存在: {sample}")
    if not node_by_id:
        raise ValueError("JSONL 中没有节点")
    stats = {
        "source_rows": len(node_by_id) + len(relationships),
        "nodes": len(node_by_id), "relationships": len(relationships),
        "node_label_sets": len({tuple(sorted(n["labels"])) for n in node_by_id.values()}),
        "relationship_types": sorted({r["label"] for r in relationships}),
        "property_values_serialized_as_json": serialized_property_values,
    }
    return list(node_by_id.values()), relationships, stats


def load_config(path: Path) -> tuple[str, str, str, Optional[str]]:
    values = load_env_file(path)
    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError("缺少 Neo4j 配置: " + ", ".join(missing))
    return (values["NEO4J_URI"], values["NEO4J_USERNAME"], values["NEO4J_PASSWORD"],
            values.get("NEO4J_DATABASE") or None)


def _write_rows(tx: Any, query: str, rows: Sequence[dict]) -> None:
    tx.run(query, rows=list(rows)).consume()


def upload_graph(nodes: Sequence[dict], relationships: Sequence[dict], env_path: Path,
                 batch_size: int = 500, driver_factory: Any = None) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    uri, username, password, database = load_config(env_path)
    if driver_factory is None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("请先安装 neo4j Python 包") from exc
        driver_factory = GraphDatabase.driver

    node_groups: DefaultDict[tuple[str, ...], list[dict]] = defaultdict(list)
    for node in nodes:
        node_groups[tuple(sorted(node["labels"]))].append({"id": node["id"], "properties": node["properties"]})
    relationship_groups: DefaultDict[str, list[dict]] = defaultdict(list)
    for relationship in relationships:
        relationship_groups[relationship["label"]].append(relationship)

    driver = driver_factory(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run(
                f"CREATE CONSTRAINT {quote_identifier(NODE_CONSTRAINT)} IF NOT EXISTS "
                f"FOR (n:{quote_identifier(GRAPH_NODE_LABEL)}) REQUIRE n._graph_id IS UNIQUE"
            ).consume()

            # Reuse a pre-existing node with the same public `id` by adopting it
            # into this importer's identity label. Refuse ambiguous duplicate IDs.
            all_ids = [node["id"] for node in nodes]
            for batch in chunks([{"id": identifier} for identifier in all_ids], batch_size):
                duplicates = session.run(
                    "UNWIND $rows AS row MATCH (n {id: row.id}) "
                    "WITH row.id AS id, count(n) AS count WHERE count > 1 RETURN id, count",
                    rows=list(batch),
                ).data()
                if duplicates:
                    raise ValueError(f"数据库中存在重复节点 id，无法安全合并: {duplicates[0]['id']}")
                session.run(
                    f"UNWIND $rows AS row MATCH (n {{id: row.id}}) "
                    f"SET n:{quote_identifier(GRAPH_NODE_LABEL)}, n._graph_id = row.id",
                    rows=list(batch),
                ).consume()

            node_transactions = 0
            for labels, rows in sorted(node_groups.items()):
                label_clause = "".join(":" + quote_identifier(label) for label in labels)
                query = (
                    f"UNWIND $rows AS row MERGE (n:{quote_identifier(GRAPH_NODE_LABEL)} "
                    f"{{_graph_id: row.id}}) SET n{label_clause} SET n += row.properties"
                )
                for batch in chunks(rows, batch_size):
                    session.execute_write(_write_rows, query, batch)
                    node_transactions += 1

            relationship_transactions = 0
            for label, rows in sorted(relationship_groups.items()):
                query = (
                    f"UNWIND $rows AS row "
                    f"MATCH (s:{quote_identifier(GRAPH_NODE_LABEL)} {{_graph_id: row.start_id}}) "
                    f"MATCH (e:{quote_identifier(GRAPH_NODE_LABEL)} {{_graph_id: row.end_id}}) "
                    f"MERGE (s)-[r:{quote_identifier(label)} {{_graph_id: row.id}}]->(e) "
                    f"SET r += row.properties"
                )
                for batch in chunks(rows, batch_size):
                    session.execute_write(_write_rows, query, batch)
                    relationship_transactions += 1

            missing = session.run(
                f"MATCH (n:{quote_identifier(GRAPH_NODE_LABEL)}) WHERE n._graph_id IN $ids "
                "WITH count(n) AS imported RETURN size($ids) - imported AS count",
                ids=all_ids,
            ).single()["count"]
            if missing:
                raise RuntimeError(f"上传后缺少 {missing} 个节点")
    finally:
        driver.close()
    return {
        "nodes_processed": len(nodes), "relationships_processed": len(relationships),
        "node_transactions": node_transactions,
        "relationship_transactions": relationship_transactions,
        "idempotent_exact_reimport": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="上传扁平 node/relationship JSONL 到 Neo4j")
    parser.add_argument("--graph", required=True, type=Path, help="graph.jsonl 路径")
    parser.add_argument("--env", type=Path, default=Path("feature/.env"))
    parser.add_argument("--report", type=Path, help="可选的校验/上传报告路径")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--upload", action="store_true", help="不加此参数时只校验，不连接 Neo4j")
    args = parser.parse_args()

    started = time.time()
    nodes, relationships, validation = load_graph(args.graph)
    report = {
        "graph": str(args.graph), "validated_at": now_iso(), "validation": validation,
        "uploaded": False,
        "note": "源 JSONL 未修改；嵌套属性仅在发送 Neo4j 时序列化为 JSON 字符串。",
    }
    if args.upload:
        report["upload"] = upload_graph(nodes, relationships, args.env, args.batch_size)
        report["uploaded"] = True
        report["finished_at"] = now_iso()
    report["elapsed_seconds"] = round(time.time() - started, 3)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
