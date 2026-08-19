#!/usr/bin/env python3
"""Create the canonical portable report artifact for fixed-type recall."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .run_recall import write_json


SHORT_LABELS = {
    "AerodynamicFeature(空气动力学特征)": "空气动力",
    "AestheticConcept(美学概念)": "美学概念",
    "DesignAttribute(设计属性)": "设计属性",
    "DesignParameter(设计参数)": "设计参数",
    "FamilyDNA(家族DNA)": "家族DNA",
    "UserTrend(用户与趋势)": "用户趋势",
    "VehiclePosture(汽车姿态)": "汽车姿态",
}


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def values_sql(rows: list[dict[str, object]], fields: list[str]) -> str:
    values = ",\n  ".join(
        "(" + ", ".join(sql_literal(row[field]) for field in fields) + ")" for row in rows
    )
    aliases = ", ".join(fields)
    return f"SELECT * FROM (VALUES\n  {values}\n) AS reviewed_snapshot({aliases})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir
    analysis = json.loads((run_dir / "analysis_summary.json").read_text(encoding="utf-8"))
    run = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8"))

    headline = analysis["headline"]
    manual = analysis["manual_review"]
    threshold_rows = [
        {
            "threshold": f'{row["threshold"]:.2f}',
            "cases": row["cases_with_at_least_one"],
            "slots": row["retrieval_slots"],
            "unique_nodes": row["unique_nodes"],
            "mean_per_case": row["mean_nodes_per_case"],
        }
        for row in analysis["threshold_sensitivity"]
    ]
    label_rows = [
        {
            "label": SHORT_LABELS[row["label"]],
            "corpus_share": row["corpus_share"],
            "top20_share": row["top20_share"],
            "enrichment": row["enrichment_ratio"],
            "top20_slots": row["top20_slots"],
        }
        for row in analysis["label_enrichment"]
    ]
    manual_rows = [
        {
            "case": row["id"],
            "relevant": row["relevant"],
            "partial": row["partial"],
            "irrelevant": row["irrelevant"],
            "note": row["note"],
        }
        for row in manual["rows"]
    ]
    generated_at = run["finished_at"]
    headline_source = "headline_snapshot"
    threshold_source = "threshold_snapshot"
    label_source = "label_snapshot"
    manual_source = "manual_review_snapshot"
    title = "Benchmark Keywords 固定类型节点召回评估"
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": title,
            "description": "100 条甲方 Benchmark keywords 在 9,649 个固定类型节点上的 BGE-M3 Top-20 召回诊断。",
            "generatedAt": generated_at,
            "cards": [
                {
                    "id": "coverage",
                    "description": "跨 100 条查询的 Top-20 去重节点覆盖。",
                    "dataset": "headline",
                    "sourceId": headline_source,
                    "metrics": [
                        {"label": "Top-20 唯一节点", "field": "unique_nodes", "format": "number"},
                        {"label": "语料覆盖率", "field": "coverage", "format": "percent"},
                    ],
                },
                {
                    "id": "manual_quality",
                    "description": "固定审查 B001–B010，每条仅审 Top-10。",
                    "dataset": "headline",
                    "sourceId": headline_source,
                    "metrics": [
                        {"label": "严格相关率@10", "field": "strict_precision", "format": "percent"},
                        {"label": "含部分相关率@10", "field": "inclusive_precision", "format": "percent"},
                    ],
                },
                {
                    "id": "validation",
                    "description": "向量、排序、结构和 SHA-256 独立复算。",
                    "dataset": "headline",
                    "sourceId": headline_source,
                    "metrics": [
                        {"label": "校验通过项", "field": "checks_passed", "format": "number"},
                        {"label": "校验总项", "field": "checks_total", "format": "number"},
                    ],
                },
            ],
            "charts": [
                {
                    "id": "threshold_cases",
                    "title": "相似度阈值越高，有返回节点的 Benchmark 越少",
                    "subtitle": "每个阈值只在已保存的 Top-20 内计数；柱高是 100 条中至少保留 1 个节点的 case 数。",
                    "type": "bar",
                    "dataset": "thresholds",
                    "sourceId": threshold_source,
                    "encodings": {
                        "x": {"field": "threshold", "type": "ordinal", "label": "余弦相似度阈值"},
                        "y": {"field": "cases", "type": "quantitative", "label": "有至少一个节点的 case 数"},
                        "tooltip": [
                            {"field": "slots", "type": "quantitative", "label": "保留槽位"},
                            {"field": "unique_nodes", "type": "quantitative", "label": "唯一节点"},
                            {"field": "mean_per_case", "type": "quantitative", "label": "每 case 平均节点"},
                        ],
                    },
                    "yAxisTitle": "Benchmark case 数（共 100）",
                    "layout": "full",
                },
                {
                    "id": "label_enrichment",
                    "title": "召回类型分布存在明显偏置",
                    "subtitle": "富集倍数 = Top-20 槽位占比 ÷ 语料节点占比；1 表示与语料基线相同。",
                    "type": "bar",
                    "dataset": "labels",
                    "sourceId": label_source,
                    "encodings": {
                        "x": {"field": "label", "type": "nominal", "label": "固定节点类型"},
                        "y": {"field": "enrichment", "type": "quantitative", "label": "富集倍数"},
                        "tooltip": [
                            {"field": "corpus_share", "type": "quantitative", "label": "语料占比", "format": "percent"},
                            {"field": "top20_share", "type": "quantitative", "label": "Top-20 槽位占比", "format": "percent"},
                            {"field": "top20_slots", "type": "quantitative", "label": "Top-20 槽位"},
                        ],
                    },
                    "yAxisTitle": "相对语料基线的富集倍数",
                    "layout": "full",
                },
            ],
            "tables": [
                {
                    "id": "manual_review",
                    "title": "B001–B010 Top-10 固定人工抽查",
                    "subtitle": "三档判断基于关键词与节点 name/description 的语义一致性。",
                    "dataset": "manual",
                    "sourceId": manual_source,
                    "columns": [
                        {"field": "case", "label": "Case", "type": "text"},
                        {"field": "relevant", "label": "相关", "format": "number"},
                        {"field": "partial", "label": "部分相关", "format": "number"},
                        {"field": "irrelevant", "label": "无关", "format": "number"},
                        {"field": "note", "label": "审查说明", "type": "text"},
                    ],
                }
            ],
            "sources": [
                {"id": headline_source, "label": "召回头部指标快照", "path": "analysis_summary.json"},
                {"id": threshold_source, "label": "阈值敏感性快照", "path": "analysis_summary.json"},
                {"id": label_source, "label": "类型富集快照", "path": "analysis_summary.json"},
                {"id": manual_source, "label": "B001–B010 人工审查", "path": "manual_review_b001_b010.jsonl"},
            ],
            "blocks": [
                {"id": "title", "type": "markdown", "body": f"# {title}"},
                {
                    "id": "technical_summary",
                    "type": "markdown",
                    "sourceId": headline_source,
                    "body": "## 技术摘要\n\n100 条 Benchmark 在固定 Top-20 下产生 **2,000 个检索槽位**，去重后找回 **843 个节点**，覆盖 9,649 节点语料的 **8.74%**。固定抽查前 10 条的 Top-10：严格相关 **71/100**，若把部分相关计入则为 **88/100**。因此当前结果足以进入候选生成与下一轮标注，但不宜把固定 Top-20 非空结果解释为完整召回准确率。",
                },
                {"id": "metrics", "type": "metric-strip", "cardIds": ["coverage", "manual_quality", "validation"]},
                {
                    "id": "key_findings",
                    "type": "markdown",
                    "sourceId": headline_source,
                    "body": "## 关键发现\n\n- 不设生产阈值时，100 条查询都能返回 20 个节点；这只是排序机制，不是质量证明。\n- 阈值 **0.60** 时，98 条仍有结果，共 1,622 个槽位、662 个唯一节点；阈值 **0.70** 时只剩 38 条，共 239 个槽位、130 个唯一节点。\n- 类型分布不平衡：汽车姿态约为语料基线的 **2.95 倍**、家族 DNA **2.52 倍**；设计参数只有 **0.21 倍**、美学概念 **0.44 倍**。\n- 人工抽查中 B004、B007 噪声最明显；多关键词查询容易被其中一个强语义词主导。",
                },
                {"id": "threshold_chart", "type": "chart", "chartId": "threshold_cases", "layout": "full"},
                {"id": "label_chart", "type": "chart", "chartId": "label_enrichment", "layout": "full"},
                {
                    "id": "scope",
                    "type": "markdown",
                    "body": "## 范围、数据与指标定义\n\n检索范围严格限定为七类节点：空气动力学特征、美学概念、设计属性、设计参数、家族 DNA、用户与趋势、汽车姿态。语料共 9,649 个节点；查询为 100 条 Benchmark 的原始 keywords，按原顺序用中文分号拼接。\n\n**检索槽位**是各 case 返回数量的合计；**唯一节点**是在所有 case 间按 node_id 去重；**语料覆盖率**是唯一节点数除以 9,649；**阈值覆盖**是在固定 Top-20 内再次过滤，因此不会发现第 21 名以后的高分节点。人工“严格相关率”仅来自固定 B001–B010 的 100 个 Top-10 槽位。",
                },
                {
                    "id": "methodology",
                    "type": "markdown",
                    "sourceId": headline_source,
                    "body": "## 方法\n\n节点文本固定为 `名称：name` 加可选的 `描述：description`，标签不进入 embedding 文本，避免类型词造成泄漏。查询不添加 instruction。模型为 `BAAI/bge-m3` dense embedding，revision 固定为 `5617a9f61b028005a4858fdac845db406aefb181`，输出 1,024 维 float32 标准化向量；用点积计算余弦相似度，分数降序、node_id 升序打破并列。节点矩阵、查询矩阵、Top-20 明细与输入/输出哈希均已落盘。",
                },
                {"id": "manual_table", "type": "table", "tableId": "manual_review", "layout": "full"},
                {
                    "id": "limitations",
                    "type": "markdown",
                    "body": "## 局限与稳健性\n\n当前图没有与这 100 条 Benchmark 对齐的节点级人工金标，所以无法计算真正的 recall@K；甲方旧图中的 nodes/paths 属于不同图版本，未用于检索或评分。相似度不是置信概率，0.60/0.70 只用于敏感性展示。人工抽查样本固定但较小，且判断尚未双人复核。独立校验已重算全部 Top-20，并核对向量形状、归一化、有限值、标签范围及原始 SHA-256。",
                },
                {
                    "id": "next_steps",
                    "type": "markdown",
                    "body": "## 下一步\n\n1. 先人工标注分层样本，尤其覆盖 B004/B007 类多意图查询和七种节点类型，再据此选择 Top-K/阈值。\n2. 将复合 keywords 拆成单关键词分别召回后做去重融合，降低单个强语义词支配整条查询的问题。\n3. 加类型配额或按类型独立召回，重点补偿设计参数、美学概念与空气动力学特征。\n4. 通过节点级金标比较 dense-only、hybrid（dense+sparse）和 reranker，确认提升后再接入后续 edges/P2。",
                },
                {
                    "id": "questions",
                    "type": "markdown",
                    "body": "## 待进一步回答\n\n- 下游每条 case 实际需要多少候选节点，5、10 还是 20？\n- 对设计参数等稀疏类型，宁可多召回还是宁可少噪声？\n- 部分相关节点在后续构边中是否允许作为弱候选？\n- 甲方能否提供当前图版本的节点级相关性标注或可映射标识？",
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready" if validation["status"] == "passed" else "partial",
            "datasets": {
                "headline": [{
                    "unique_nodes": headline["top20_unique_nodes"],
                    "coverage": headline["top20_corpus_coverage"],
                    "strict_precision": manual["strict_precision_at_10"],
                    "inclusive_precision": manual["inclusive_precision_at_10"],
                    "checks_passed": sum(item["passed"] for item in validation["checks"]),
                    "checks_total": len(validation["checks"]),
                }],
                "thresholds": threshold_rows,
                "labels": label_rows,
                "manual": manual_rows,
            },
        },
        "sources": [
            {
                "id": headline_source,
                "query": {
                    "engine": "portable_values_snapshot",
                    "description": "从 analysis_summary.json 与 validation_report.json 固化的报告头部指标。",
                    "sql": values_sql([{
                        "unique_nodes": headline["top20_unique_nodes"],
                        "coverage": headline["top20_corpus_coverage"],
                        "strict_precision": manual["strict_precision_at_10"],
                        "inclusive_precision": manual["inclusive_precision_at_10"],
                        "checks_passed": sum(item["passed"] for item in validation["checks"]),
                        "checks_total": len(validation["checks"]),
                    }], ["unique_nodes", "coverage", "strict_precision", "inclusive_precision", "checks_passed", "checks_total"]),
                    "tables_used": ["analysis_summary.json", "validation_report.json"],
                    "metric_definitions": {
                        "unique_nodes": "100 条查询的前 20 名 node_id 并集大小。",
                        "coverage": "Top-20 唯一节点数 / 9,649。",
                        "strict_precision": "固定 B001–B010 Top-10 中人工标为 relevant 的比例。",
                    },
                    "executed_at": generated_at,
                },
            },
            {
                "id": threshold_source,
                "query": {
                    "engine": "portable_values_snapshot",
                    "description": "已保存 Top-20 内的余弦相似度阈值敏感性。",
                    "sql": values_sql(threshold_rows, ["threshold", "cases", "slots", "unique_nodes", "mean_per_case"]),
                    "tables_used": ["analysis_summary.json"],
                    "executed_at": generated_at,
                },
            },
            {
                "id": label_source,
                "query": {
                    "engine": "portable_values_snapshot",
                    "description": "Top-20 槽位类型占比相对 9,649 节点语料占比的富集倍数。",
                    "sql": values_sql(label_rows, ["label", "corpus_share", "top20_share", "enrichment", "top20_slots"]),
                    "tables_used": ["analysis_summary.json"],
                    "executed_at": generated_at,
                },
            },
            {
                "id": manual_source,
                "query": {
                    "engine": "portable_values_snapshot",
                    "description": "固定 B001–B010 的 Top-10 三档人工相关性审查。",
                    "sql": values_sql(manual_rows, ["case", "relevant", "partial", "irrelevant", "note"]),
                    "tables_used": ["manual_review_b001_b010.jsonl"],
                    "executed_at": generated_at,
                },
            },
        ],
        "package_info": {
            "originUrl": "artifact://benchmark-fixed-type-recall/bge-m3_5617a9f",
            "controls": {"edit": False, "refresh": False},
        },
    }
    write_json(run_dir / "artifact.json", artifact)
    notes = """# Report source notes

- Snapshot: `run_manifest.json` finished_at; not a live connection.
- Chart 1 question: raising the cosine threshold leaves how many Benchmark cases with at least one candidate? Form: vertical bar; x=threshold, y=cases; unit=count; denominator=100 cases; no zero suppression.
- Chart 2 question: which fixed labels are over/under-selected relative to corpus availability? Form: vertical bar; x=label, y=enrichment ratio; unit=ratio; denominator=Top-20 slots vs corpus nodes.
- Manual table: fixed B001–B010, ranks 1–10, reviewed once; not extrapolated as full-benchmark accuracy.
- Primary sources: `recall_top20.jsonl`, `analysis_summary.json`, `validation_report.json`, and the input paths/hash values in `run_manifest.json`.
"""
    (run_dir / "report_source_notes.md").write_text(notes, encoding="utf-8")
    print(run_dir / "artifact.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
