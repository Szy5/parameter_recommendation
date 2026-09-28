"""Upload the already generated, location-validated part of a DTS graph.

This command does not call the LLM. Records still pending physical-location
review remain absent from graph.jsonl and are never uploaded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .upload import upload_graph


def load_approved_graph(out: Path) -> tuple[list[dict], dict]:
    graph_path = out / "graph.jsonl"
    report_path = out / "report.json"
    locations_path = out / "locations.jsonl"
    for path in (graph_path, report_path, locations_path):
        if not path.is_file():
            raise ValueError(f"Missing extraction output: {path}")
    graph = [json.loads(line) for line in graph_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pending = {
        row["source_id"] for line in locations_path.read_text(encoding="utf-8").splitlines()
        if line.strip() for row in [json.loads(line)]
        if not row.get("instances") and row.get("reason") != "未选择 PDF 关系"
    }
    expected = {
        "vehicle_nodes": ("node", "VehicleType"),
        "part_nodes": ("node", "Part"),
        "contains_relationships": ("relationship", "CONTAINS"),
        "dts_relationships": ("relationship", "DTS_POSITION_RELATION"),
    }
    for field, (kind, label) in expected.items():
        actual = sum(row.get("type") == kind and
                     (label in row.get("labels", []) if kind == "node" else row.get("label") == label)
                     for row in graph)
        if actual != report.get(field):
            raise ValueError(f"Graph/report mismatch for {field}: {actual} != {report.get(field)}")
    if report.get("vehicle_nodes") != 4 or report.get("accepted_source_records", 0) < 1:
        raise ValueError("Expected four vehicles and at least one accepted source record")
    if len(pending) != report.get("location", {}).get("review"):
        raise ValueError("Pending location counts disagree; review the extraction output")
    nodes = {row["id"] for row in graph if row.get("type") == "node"}
    if len(nodes) != report["vehicle_nodes"] + report["part_nodes"]:
        raise ValueError("Duplicate node IDs in graph")
    for row in graph:
        if row.get("type") == "node" and "Part" in row.get("labels", []):
            if not row.get("properties", {}).get("location"):
                raise ValueError("Part without confirmed physical location")
        if row.get("type") == "relationship":
            if row.get("start_id") not in nodes or row.get("end_id") not in nodes:
                raise ValueError("Relationship references a missing node")
    return graph, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload only previously approved DTS records; no LLM calls")
    parser.add_argument("--out", type=Path, required=True, help="Directory containing graph.jsonl and report.json")
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--upload", action="store_true", required=True,
                        help="Explicitly permit writing approved records to Neo4j")
    args = parser.parse_args()
    graph, report = load_approved_graph(args.out)
    upload = upload_graph(graph, args.env)
    report.update({"upload": upload, "uploaded": True,
                   "upload_scope": "approved_only",
                   "pending_location_records_not_uploaded": report["location"]["review"]})
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"upload": upload, "pending_location_records_not_uploaded":
                      report["pending_location_records_not_uploaded"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
