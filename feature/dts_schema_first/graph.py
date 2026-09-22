"""Build four vehicle-scoped subgraphs from schema-validated edge selections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

from .schema import METRICS, SHEET_VEHICLES, approved_edges
from .workbook import SourceRecord


def vehicle_id(sheet: str) -> str:
    return "DTS:vehicle:" + sheet


def part_id(sheet: str, label: str, location: str = "") -> str:
    """A named part at a different physical location is a different node."""
    # Keep the legacy two-argument ID stable for the separate full-table package.
    material = sheet + "\0" + label + ("\0" + location if location else "")
    digest = hashlib.sha256(material.encode()).hexdigest()[:24]
    return "DTS:part:" + sheet + ":" + digest


def build_graph(records: Sequence[SourceRecord], selections: dict, errors: dict, schema: dict,
                instances: dict | None = None, location_errors: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    allowed = approved_edges(schema)
    instances = instances or {}
    location_errors = location_errors or {}
    nodes = {
        vehicle_id(sheet): {
            "type": "node", "id": vehicle_id(sheet), "labels": ["VehicleType"],
            "properties": {"id": vehicle_id(sheet), "name": name},
        }
        for sheet, name in SHEET_VEHICLES.items()
    }
    relationships: dict[str, dict] = {}
    review = []
    accepted = 0
    for record in records:
        choice = selections.get(record.source_id)
        edge_ids = choice.get("edge_ids", []) if choice else []
        located = instances.get(record.source_id, {}).get("instances", [])
        if not edge_ids or any(edge_id not in allowed for edge_id in edge_ids) or not located:
            reason = location_errors.get(record.source_id) or errors.get(record.source_id)
            if reason is None:
                if edge_ids:
                    reason = "具体部件的物理位置未确认，不能合并同名实例"
                else:
                    reason = "PDF 仅有外形方位，内部关系无对应 schema" if record.classification == "内部" else "PDF schema 中无唯一、可确认的外形关系"
            review.append({
                "source_id": record.source_id, "vehicle": record.vehicle, "code": record.code,
                "raw_name": record.name, "classification": record.classification,
                "area": record.area, "section": record.section,
                "radii_base_part": record.radii_base_part,
                "metrics": record.metric_values(),
                "reason": reason,
            })
            continue
        first, second = choice["part_a"], choice["part_b"]
        if not first or not second or first == second:
            raise ValueError("Selection has no validated concrete part labels")
        pairs = {(allowed[edge_id]["source"], allowed[edge_id]["target"]) for edge_id in edge_ids}
        if len(pairs) != 1:
            raise ValueError("Selection mixes unrelated PDF category pairs")
        for located_edge in located:
            edge_id = located_edge["edge_id"]
            if edge_id not in edge_ids:
                raise ValueError("Location selection contains an unselected PDF edge")
            edge = allowed[edge_id]
            first_location = located_edge["part_a_location"]
            second_location = located_edge["part_b_location"]
            for label, location in ((first, first_location), (second, second_location)):
                identifier = part_id(record.sheet, label, location)
                nodes[identifier] = {
                    "type": "node", "id": identifier, "labels": ["Part", label],
                    "properties": {"id": identifier, "name": label, "location": location},
                }
                contains = {
                    "type": "relationship", "label": "CONTAINS",
                    "start_id": vehicle_id(record.sheet), "end_id": identifier,
                    "properties": {"location": location},
                }
                relationships[json.dumps(["CONTAINS", contains["start_id"], identifier])] = contains
            properties = {
                "relative_position": edge["relative_position"],
                **record.metric_values(),
                "classification": record.classification,
                "area": record.area,
            }
            assert set(METRICS).issubset(properties)
            relation = {
                "type": "relationship", "label": "DTS_POSITION_RELATION",
                "start_id": part_id(record.sheet, first, first_location),
                "end_id": part_id(record.sheet, second, second_location),
                "properties": properties,
                "source_ids": [record.source_id],
            }
            key = json.dumps([relation["label"], relation["start_id"], relation["end_id"], properties], ensure_ascii=False, sort_keys=True)
            if key in relationships:
                relationships[key]["source_ids"].append(record.source_id)
            else:
                relationships[key] = relation
        accepted += 1
    graph = list(nodes.values()) + list(relationships.values())
    dts_count = sum(row.get("label") == "DTS_POSITION_RELATION" for row in relationships.values())
    report = {
        "source_records": len(records), "accepted_source_records": accepted,
        "review_records": len(review), "vehicle_nodes": len(SHEET_VEHICLES),
        "part_nodes": len(nodes) - len(SHEET_VEHICLES),
        "contains_relationships": sum(row.get("label") == "CONTAINS" for row in relationships.values()),
        "dts_relationships": dts_count,
        "collapsed_identical_dts_records": sum(len(row.get("source_ids", [])) for row in relationships.values() if row.get("label") == "DTS_POSITION_RELATION") - dts_count,
    }
    return graph, review, report


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
