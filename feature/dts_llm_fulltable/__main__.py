"""Run: python -m feature.dts_llm_fulltable --pdf ... --xlsx ... --out ..."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from feature.dts_schema_first.graph import write_jsonl
from feature.dts_schema_first.schema import build_schema, sha256_file
from feature.dts_schema_first.upload import upload_graph
from feature.dts_schema_first.workbook import read_workbook

from .pipeline import build_graph, extract_all


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF schema 约束下用 LLM 抽取整个 DTS 工作簿")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--xlsx", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--model", help="覆盖 .env 中的 MODEL_NAME")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--upload", action="store_true", help="确认结果后才写入 Neo4j")
    args = parser.parse_args()
    if not args.pdf.is_file() or not args.xlsx.is_file():
        parser.error("--pdf 和 --xlsx 必须指向现有文件")
    schema = build_schema(args.pdf)
    records = read_workbook(args.xlsx)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    items, errors, llm_stats = extract_all(records, schema, args.env, args.out / "llm_cache.jsonl",
                                           batch_size=args.batch_size, model_override=args.model)
    graph, review, stats = build_graph(records, items, errors)
    write_jsonl(args.out / "extractions.jsonl", [
        {**{"source": {"vehicle": r.vehicle, "code": r.code, "name": r.name}},
         **items.get(r.source_id, {"source_id": r.source_id, "error": errors.get(r.source_id, "未返回")})}
        for r in records
    ])
    write_jsonl(args.out / "graph.jsonl", graph)
    write_jsonl(args.out / "review.jsonl", review)
    report = {**stats, "llm": llm_stats, "pdf_sha256": schema["source_pdf_sha256"],
              "workbook_sha256": sha256_file(args.xlsx), "uploaded": False}
    if args.upload:
        report["upload"] = upload_graph(graph, args.env)
        report["uploaded"] = True
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
