"""python -m feature.dts_schema_first --pdf ... --xlsx ... --out ..."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .extract import extract
from .graph import build_graph, write_jsonl
from .locations import resolve_locations
from .schema import approved_edges, build_schema, sha256_file
from .upload import upload_graph
from .workbook import read_workbook


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF schema first → LLM constrained XLSX extraction → optional Neo4j upload")
    parser.add_argument("--pdf", type=Path, required=True, help="Exact reviewed exterior XMind PDF")
    parser.add_argument("--xlsx", type=Path, help="Four-sheet DTS workbook; omit only with --schema-only")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--model", default=None, help="Optional override of model in --env")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--selection-cache", type=Path,
                        help="Reuse a previous llm_cache.jsonl when writing v2 results to a new output directory")
    parser.add_argument("--retry-review", action="store_true", help="Re-call LLM for cached validation/review errors")
    parser.add_argument("--schema-only", action="store_true", help="Audit and write schema.json; no XLSX, LLM, or Neo4j")
    parser.add_argument("--upload", action="store_true", help="Upload only schema-approved records to Neo4j")
    parser.add_argument("--replace-old-graph", type=Path,
                        help="With --upload, atomically replace the exact legacy DTS graph in this JSONL file")
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error("--pdf must point to the reviewed PDF")
    if args.schema_only and args.upload:
        parser.error("--schema-only and --upload cannot be combined")
    if args.replace_old_graph and not args.upload:
        parser.error("--replace-old-graph requires --upload")
    if not args.schema_only and (args.xlsx is None or not args.xlsx.is_file()):
        parser.error("--xlsx must point to the DTS workbook")
    args.out.mkdir(parents=True, exist_ok=True)

    # Ordering is intentional: the complete, PDF-verified schema is frozen on
    # disk before the workbook is opened and before the LLM is called.
    schema = build_schema(args.pdf)
    (args.out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.schema_only:
        print(json.dumps({
            "schema": str(args.out / "schema.json"),
            "pdf_part_categories": len(schema["pdf_part_categories"]),
            "pdf_edges": len(schema["edges"]),
        }, ensure_ascii=False, indent=2))
        return

    records = read_workbook(args.xlsx)
    # The supplied PDF is explicitly an exterior map. Interior records are
    # reviewed, not sent to a paid model that cannot choose a valid edge.
    eligible = [record for record in records if record.classification == "外部" and record.name and record.metrics]
    selections, errors, llm_counts = extract(
        eligible, schema, args.env, args.selection_cache or args.out / "llm_cache.jsonl",
        batch_size=args.batch_size, model_override=args.model, retry_review=args.retry_review,
    )
    try:
        located, location_errors, location_counts = resolve_locations(
            records, selections, schema, args.env, args.out / "location_cache.jsonl",
            batch_size=args.batch_size, model_override=args.model,
        )
    except RuntimeError as exc:
        raise SystemExit(f"[DTS] 物理位置识别未完成：{exc}；已完成缓存保留，可联网后重跑。") from exc
    graph, review, report = build_graph(records, selections, errors, schema, located, location_errors)
    write_jsonl(args.out / "graph.jsonl", graph)
    write_jsonl(args.out / "review.jsonl", review)
    write_jsonl(args.out / "locations.jsonl", [
        {"source_id": record.source_id, "vehicle": record.vehicle, "code": record.code,
         **located.get(record.source_id, {"instances": [], "reason": location_errors.get(record.source_id, "未选择 PDF 关系")})}
        for record in records
    ])
    write_jsonl(args.out / "extractions.jsonl", [
        {
            "source_id": record.source_id, "vehicle": record.vehicle,
            "code": record.code, "raw_name": record.name,
            "classification": record.classification, "area": record.area,
            "radii_base_part": record.radii_base_part,
            "metrics": record.metric_values(),
            **selections.get(record.source_id, {"edge_ids": []}),
        }
        for record in records
    ])
    report.update({
        "schema_version": schema["schema_version"],
        "pdf_sha256": schema["source_pdf_sha256"],
        "workbook_sha256": sha256_file(args.xlsx),
        "schema_pdf_edges": len(schema["edges"]),
        "schema_selectable_edges": len(approved_edges(schema)),
        "llm_eligible_records": len(eligible), "llm": llm_counts,
        "graph_version": schema["graph_version"], "location": location_counts,
        "internal_records_without_pdf_schema": sum(r.classification == "内部" for r in records),
        "uploaded": False,
    })
    report_path = args.out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.upload:
        if location_errors:
            raise ValueError(f"{len(location_errors)} 条已选关系的物理位置待审；为避免上传错误节点，拒绝上传")
        report["upload"] = upload_graph(graph, args.env, replace_old_graph_path=args.replace_old_graph)
        report["uploaded"] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
