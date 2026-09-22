"""CLI: python -m feature.DTS_extraction ..."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .graph import build_graph, write_jsonl
from .llm_extract import extract_parts
from .schema import GRAPH_SCHEMA
from .topology import PdfTopology
from .upload import upload_graph
from .workbook import read_workbook


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PDF-verified DTS graph and optionally upload it")
    parser.add_argument("--xlsx", type=Path, required=True, help="Original DTS workbook")
    parser.add_argument("--pdf", type=Path, required=True, help="Reviewed exterior XMind PDF")
    parser.add_argument("--out", type=Path, required=True, help="Run output directory")
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--model", default=None, help="Override MODEL_NAME from --env")
    parser.add_argument("--batch-size", type=int, default=15, help="LLM records per call, 1-40")
    parser.add_argument("--upload", action="store_true", help="Write verified graph to Neo4j")
    args = parser.parse_args()

    if not args.xlsx.is_file() or not args.pdf.is_file():
        parser.error("--xlsx and --pdf must be existing files")
    args.out.mkdir(parents=True, exist_ok=True)
    topology = PdfTopology(args.pdf)
    records = read_workbook(args.xlsx)
    eligible = [record for record in records if record.name and record.metrics]
    extracted, errors, llm_counts = extract_parts(
        eligible, args.env, args.out / "llm_cache.jsonl", args.batch_size,
        model_override=args.model,
    )
    graph, review, report = build_graph(records, extracted, errors, topology)
    write_jsonl(args.out / "graph.jsonl", graph)
    write_jsonl(args.out / "review.jsonl", review)
    write_jsonl(args.out / "extractions.jsonl", [
        {
            "source_id": record.source_id,
            "vehicle": record.vehicle,
            "code": record.code,
            "raw_name": record.name,
            "classification": record.classification,
            "area": record.area,
            "section": record.section,
            "part_a": extracted.get(record.source_id, ("", ""))[0],
            "part_b": extracted.get(record.source_id, ("", ""))[1],
            "metrics": record.metric_values(),
        }
        for record in records
    ])
    labels_by_id = {
        row["id"]: row["labels"][1]
        for row in graph if row["type"] == "node" and "Part" in row["labels"]
    }
    schema = dict(GRAPH_SCHEMA)
    schema["concrete_part_labels"] = sorted(set(labels_by_id.values()))
    schema["observed_position_patterns"] = sorted({
        (labels_by_id[row["start_id"]], row["properties"]["relative_position"], labels_by_id[row["end_id"]])
        for row in graph if row.get("label") == "DTS_POSITION_RELATION"
    })
    (args.out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    report.update({
        "workbook_sha256": file_sha256(args.xlsx),
        "pdf_sha256": topology.digest,
        "llm_eligible_records": len(eligible),
        "llm": llm_counts,
        "uploaded": False,
    })
    report_path = args.out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.upload:
        if llm_counts["failed_batches"]:
            raise RuntimeError("LLM batch failures exist; inspect review.jsonl before uploading")
        report["upload"] = upload_graph(graph, args.env)
        report["uploaded"] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
