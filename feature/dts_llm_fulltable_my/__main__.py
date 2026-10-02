"""Run: python -m feature.dts_llm_fulltable --pdf ... --xlsx ... --out ..."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from feature.dts_schema_first.graph import write_jsonl
from feature.dts_schema_first.schema import build_schema, sha256_file
from feature.dts_schema_first.upload import upload_graph
from feature.dts_schema_first.workbook import read_workbook

from .pipeline import build_graph, extract_all, my_extract_all


#新流程
# 1. 解析pdf和xlsx --> 2. 用匹配解析出每条记录需要的信息 --> 3. llm判断每个source和target分别属于什么类型 -->
# 4. 组装graph
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
    #从pdf中解析出支持的schema
    schema = build_schema(args.pdf)
    #从xlsx中解析出DTS条目
    records = read_workbook(args.xlsx)
    #schema写入磁盘
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    #进一步解析出关系三元组
    items  = extract_records(records)[:20]

    #开始抽取三元组，返回抽取结果，错误信息，llm状态
    label_by_source_id = my_extract_all(items, schema, args.env, args.out / "llm_cache.jsonl",
                                        batch_size=args.batch_size, model_override=args.model)
    #print(json.dumps(label_by_source_id, ensure_ascii=False, indent=2))

    #图谱构建
    graph, review, stats = build_graph(items , label_by_source_id , schema['edges']) #传入解析数据和ai结果数据

    #打印抽取结果
    write_jsonl(args.out / "extractions.jsonl", [
        {**{"source_id": r.get('source_id'), "source_part_type": r.get('source_part_type'), "target_part_type": r.get('target_part_type'), "reason": r.get('reason') }}
        for r in label_by_source_id.values()
    ])
    write_jsonl(args.out / "graph.jsonl", graph)
    write_jsonl(args.out / "review.jsonl", review)
    report = {**stats, "pdf_sha256": schema["source_pdf_sha256"],
              "workbook_sha256": sha256_file(args.xlsx), "uploaded": False}
    if args.upload:
        report["upload"] = upload_graph(graph, args.env)
        report["uploaded"] = True
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def extract_records(records) -> list[dict]:
    items = []
    for r in records:
        if r.area =="对齐度要求" or r.area == "一致性要求" or r.area == "":
            print("有这个！！")
            continue
        name = r.name.strip().replace("\r\n", "").replace("\n", "").replace("\r", "")
        if name == "":
            continue
        results = name.split("至")
        if len(results) == 2:
            items.append({
                "source_id": r.source_id,
                "vehicle": r.vehicle,
                "code": r.code,
                "source_part_name": results[0],
                "target_part_name": results[1],
                "metric": r.metrics,
                "location": r.classification + "-" + r.area
            })
            continue
       # print(name + "------------")
        results = name.strip("与")[0].split("配合")
        if len(results) == 1:
            items.append({
                "source_id": r.source_id,
                "vehicle": r.vehicle,
                "code": r.code,
                "source_part_name": r.area,
                "target_part_name": results[0],
                "metric": r.metrics,
                "location": r.classification + "-" + r.area
            })



    return items



if __name__ == "__main__":
    main()
