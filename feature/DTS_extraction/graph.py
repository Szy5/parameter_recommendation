"""Turn validated source records into four disjoint Neo4j subgraphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .schema import METRICS, SHEET_VEHICLES, labels_for_part, part_id, vehicle_id
from .topology import PdfTopology
from .workbook import SourceRecord


def build_graph(
    records: Sequence[SourceRecord],
    endpoints: Dict[str, Tuple[str, str]],
    extraction_errors: Dict[str, str],
    topology: PdfTopology,
) -> Tuple[List[dict], List[dict], dict]:
    nodes: Dict[str, dict] = {}
    relationships: Dict[str, dict] = {}
    review: List[dict] = []
    for sheet, name in SHEET_VEHICLES.items():
        identifier = vehicle_id(sheet)
        nodes[identifier] = {
            "type": "node", "id": identifier, "labels": ["VehicleType"],
            "properties": {"id": identifier, "name": name},
        }

    position_count = 0
    for record in records:
        parts = endpoints.get(record.source_id)
        if not parts or not all(parts):
            review.append({
                "source_id": record.source_id, "vehicle": record.vehicle,
                "code": record.code, "name": record.name,
                "reason": extraction_errors.get(record.source_id, "无法可靠识别两个具体部件"),
                "metrics": record.metric_values(),
            })
            continue
        first, second = parts
        for name in (first, second):
            identifier = part_id(record.sheet, name)
            nodes[identifier] = {
                "type": "node", "id": identifier, "labels": labels_for_part(name),
                "properties": {"id": identifier, "name": name},
            }
            contains = {
                "type": "relationship", "label": "CONTAINS",
                "start_id": vehicle_id(record.sheet), "end_id": identifier,
                "properties": {"classification": record.classification, "area": record.area},
            }
            key = json.dumps([contains["label"], contains["start_id"], contains["end_id"], contains["properties"]], ensure_ascii=False, sort_keys=True)
            relationships[key] = contains

        try:
            position, reason = topology.lookup(first, second, record.classification)
        except ValueError as exc:
            position, reason = None, str(exc)
        if position is None:
            review.append({
                "source_id": record.source_id, "vehicle": record.vehicle,
                "code": record.code, "name": record.name,
                "part_a": first, "part_b": second,
                "classification": record.classification, "area": record.area,
                "reason": reason, "metrics": record.metric_values(),
            })
            continue
        properties = {
            "relative_position": position,
            "classification": record.classification,
            "area": record.area,
        }
        properties.update(record.metric_values())
        assert set(METRICS).issubset(properties)
        relation = {
            "type": "relationship", "label": "DTS_POSITION_RELATION",
            "start_id": part_id(record.sheet, first),
            "end_id": part_id(record.sheet, second),
            "properties": properties,
            "source_ids": [record.source_id],
        }
        key = json.dumps([relation["label"], relation["start_id"], relation["end_id"], properties], ensure_ascii=False, sort_keys=True)
        if key in relationships:
            relationships[key]["source_ids"].append(record.source_id)
        else:
            relationships[key] = relation
        position_count += 1

    graph = list(nodes.values()) + list(relationships.values())
    report = {
        "source_records": len(records),
        "vehicle_nodes": len(SHEET_VEHICLES),
        "part_nodes": len(nodes) - len(SHEET_VEHICLES),
        "contains_relationships": sum(row["label"] == "CONTAINS" for row in relationships.values()),
        "position_relationships": sum(row["label"] == "DTS_POSITION_RELATION" for row in relationships.values()),
        "matched_source_records": position_count,
        "review_records": len(review),
        "collapsed_identical_relationships": position_count - sum(row["label"] == "DTS_POSITION_RELATION" for row in relationships.values()),
    }
    return graph, review, report


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
