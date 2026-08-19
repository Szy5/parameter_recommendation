#!/usr/bin/env python3
"""Build the final prefixed graph after approved full Judge runs.

This command is also usable for a pilot preview, but a pilot output must never be
mistaken for a complete delivery graph. The report records decision coverage.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        EXPRESSES_STYLE_LABEL,
        MAIN_STYLES,
        endpoint_snapshot,
        iter_jsonl,
        parse_inner,
        safe_slug,
        write_jsonl,
    )
else:
    from .common import (
        AC_LABEL,
        EXPRESSES_STYLE_LABEL,
        MAIN_STYLES,
        endpoint_snapshot,
        iter_jsonl,
        parse_inner,
        safe_slug,
        write_jsonl,
    )


MEIXUE_PREFIX = "meixue_"
CAR_PREFIX = "car_"
STYLE_ID_PREFIX = "meixue_style_"


def latest_ok(path: Path, key: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if row.get("status") == "ok" and row.get(key) is not None:
            result[str(row[key])] = row
    return result


def prefix_endpoint(endpoint: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    copied = dict(endpoint)
    copied["id"] = prefix + str(endpoint["id"])
    return copied


def prefix_record(record: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    copied = dict(record)
    copied["id"] = prefix + str(record["id"])
    if copied.get("type") == "relationship":
        copied["start"] = prefix_endpoint(copied["start"], prefix)
        copied["end"] = prefix_endpoint(copied["end"], prefix)
    return copied


def style_node(style: str, sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    source_names = [str(row.get("concept_name") or "").strip() for row in sources]
    source_names = [name for name in source_names if name]
    inner = {
        "conceptName(概念名称)": style,
        "description(描述)": "由原始 AestheticConcept 实体融合得到的主风格。",
        "mergedConceptNames": source_names,
        "mergedConceptIds": [str(row.get("concept_id")) for row in sources],
        "mergeMethod": "llm_judge",
    }
    return {
        "type": "node",
        "id": STYLE_ID_PREFIX + safe_slug(style),
        "labels": [AC_LABEL],
        "properties": {
            "name": style,
            "frequency_weight": sum(
                float(row.get("frequency_weight") or 0) for row in sources
            ),
            "properties": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
        },
    }


def merge_decisions(
    concept_output: Path, confidence_threshold: float
) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    judged = latest_ok(concept_output, "concept_id")
    mapping: Dict[str, str] = {}
    grouped: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for concept_id, row in judged.items():
        style = row.get("target_style")
        if (
            row.get("can_merge")
            and style in MAIN_STYLES
            and float(row.get("confidence", 0)) >= confidence_threshold
        ):
            mapping[concept_id] = STYLE_ID_PREFIX + safe_slug(str(style))
            grouped[str(style)].append(row)
    return mapping, dict(grouped), judged


def build_meixue_records(
    meixue_path: Path,
    mapping: Dict[str, str],
    grouped: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    raw_nodes: Dict[str, Dict[str, Any]] = {}
    raw_relationships: List[Dict[str, Any]] = []
    for record in iter_jsonl(meixue_path):
        if record.get("type") == "node":
            raw_nodes[str(record["id"])] = record
        elif record.get("type") == "relationship":
            raw_relationships.append(record)

    nodes: Dict[str, Dict[str, Any]] = {}
    for old_id, node in raw_nodes.items():
        if old_id in mapping:
            continue
        prefixed = prefix_record(node, MEIXUE_PREFIX)
        nodes[prefixed["id"]] = prefixed
    for style in MAIN_STYLES:
        # The seven canonical nodes are also the fixed endpoints used by
        # EXPRESSES_STYLE, so they exist even when no source concept was merged.
        node = style_node(style, grouped.get(style, []))
        nodes[node["id"]] = node

    dedup: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    merged_edge_sources: DefaultDict[
        Tuple[str, str, str], List[Dict[str, Any]]
    ] = defaultdict(list)
    missing_endpoint = 0
    for edge in raw_relationships:
        old_start = str((edge.get("start") or {}).get("id"))
        old_end = str((edge.get("end") or {}).get("id"))
        start_id = mapping.get(old_start, MEIXUE_PREFIX + old_start)
        end_id = mapping.get(old_end, MEIXUE_PREFIX + old_end)
        if start_id not in nodes or end_id not in nodes:
            missing_endpoint += 1
            continue
        key = (start_id, str(edge.get("label")), end_id)
        if key not in dedup:
            copied = dict(edge)
            copied["id"] = MEIXUE_PREFIX + str(edge["id"])
            copied["start"] = endpoint_snapshot(nodes[start_id])
            copied["end"] = endpoint_snapshot(nodes[end_id])
            copied["properties"] = dict(edge.get("properties") or {})
            dedup[key] = copied
        merged_edge_sources[key].append(
            {
                "source_edge_id": str(edge.get("id")),
                # Preserve the complete property payload of every source edge.
                # Neo4j cannot store a list of maps as a relationship property,
                # so the merged payload is serialized below as JSON.
                "properties": dict(edge.get("properties") or {}),
            }
        )

    merged_relationship_groups = 0
    preserved_source_relationships = 0
    for key, sources in merged_edge_sources.items():
        if len(sources) > 1:
            merged_relationship_groups += 1
            preserved_source_relationships += len(sources)
            properties = dedup[key].setdefault("properties", {})
            properties["merged_source_edge_ids"] = [
                source["source_edge_id"] for source in sources
            ]
            properties["merged_source_relationships"] = json.dumps(
                sources, ensure_ascii=False, separators=(",", ":")
            )

    records = list(nodes.values()) + list(dedup.values())
    stats = {
        "input_nodes": len(raw_nodes),
        "input_relationships": len(raw_relationships),
        "output_nodes": len(nodes),
        "output_relationships": len(dedup),
        "merged_concept_nodes": len(mapping),
        "canonical_style_nodes": len(MAIN_STYLES),
        "deduplicated_relationships": len(raw_relationships) - len(dedup) - missing_endpoint,
        "merged_relationship_groups": merged_relationship_groups,
        "preserved_source_relationships": preserved_source_relationships,
        "missing_endpoint_relationships": missing_endpoint,
    }
    return records, nodes, stats


def build_car_records(cars_path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    nodes: Dict[str, Dict[str, Any]] = {}
    for raw in iter_jsonl(cars_path):
        record = prefix_record(raw, CAR_PREFIX)
        records.append(record)
        if record.get("type") == "node":
            nodes[record["id"]] = record
    return records, nodes


def style_relationships(
    style_output: Path,
    car_nodes: Dict[str, Dict[str, Any]],
    aesthetic_nodes: Dict[str, Dict[str, Any]],
    score_threshold: float,
    confidence_threshold: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    judged = latest_ok(style_output, "instance_id")
    relationships: List[Dict[str, Any]] = []
    skipped_threshold = 0
    skipped_endpoint = 0
    for instance_id, row in judged.items():
        start_id = CAR_PREFIX + instance_id
        for item in row.get("styles") or []:
            if (
                float(item.get("score", 0)) < score_threshold
                or float(item.get("confidence", 0)) < confidence_threshold
            ):
                skipped_threshold += 1
                continue
            style = str(item.get("style"))
            end_id = STYLE_ID_PREFIX + safe_slug(style)
            if start_id not in car_nodes or end_id not in aesthetic_nodes:
                skipped_endpoint += 1
                continue
            rel_id = "fusion_expresses_style_%s_%s" % (instance_id, safe_slug(style))
            relationships.append(
                {
                    "type": "relationship",
                    "id": rel_id,
                    "label": EXPRESSES_STYLE_LABEL,
                    "properties": {
                        "score": item.get("score"),
                        "confidence": item.get("confidence"),
                        "evidence": item.get("evidence"),
                        "parameters": json.dumps(
                            item.get("parameters") or [], ensure_ascii=False, separators=(",", ":")
                        ),
                        "model": row.get("model"),
                        "prompt_version": row.get("prompt_version"),
                    },
                    "start": endpoint_snapshot(car_nodes[start_id]),
                    "end": endpoint_snapshot(aesthetic_nodes[end_id]),
                }
            )
    stats = {
        "judged_instances": len(judged),
        "new_expresses_style_relationships": len(relationships),
        "skipped_below_threshold": skipped_threshold,
        "skipped_missing_endpoint": skipped_endpoint,
    }
    return relationships, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build fused parameter-recommendation graph")
    parser.add_argument("--meixue", type=Path, default=Path("meixue(1).json"))
    parser.add_argument("--cars", type=Path, default=Path("cars2.json"))
    parser.add_argument("--concept-output", type=Path, required=True)
    parser.add_argument("--style-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--concept-confidence", type=float, default=0.80)
    parser.add_argument("--style-score", type=float, default=0.65)
    parser.add_argument("--style-confidence", type=float, default=0.65)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    mapping, grouped, judged_concepts = merge_decisions(args.concept_output, args.concept_confidence)
    meixue_records, aesthetic_nodes, meixue_stats = build_meixue_records(args.meixue, mapping, grouped)
    car_records, car_nodes = build_car_records(args.cars)
    style_edges, style_stats = style_relationships(
        args.style_output,
        car_nodes,
        aesthetic_nodes,
        score_threshold=args.style_score,
        confidence_threshold=args.style_confidence,
    )

    total_concepts = sum(
        1
        for record in iter_jsonl(args.meixue)
        if record.get("type") == "node" and AC_LABEL in (record.get("labels") or [])
    )
    total_instances = sum(
        1
        for record in iter_jsonl(args.cars)
        if record.get("type") == "node" and "汽车实例" in (record.get("labels") or [])
    )
    complete = len(judged_concepts) == total_concepts and style_stats["judged_instances"] == total_instances
    if not complete and not args.allow_partial:
        raise SystemExit(
            "Judge coverage is partial (concepts %d/%d, instances %d/%d); "
            "pass --allow-partial only for an explicitly labeled preview"
            % (len(judged_concepts), total_concepts, style_stats["judged_instances"], total_instances)
        )

    output_count = write_jsonl(args.output, meixue_records + car_records + style_edges)
    report = {
        "complete_full_coverage": complete,
        "concept_judge_coverage": [len(judged_concepts), total_concepts],
        "style_judge_coverage": [style_stats["judged_instances"], total_instances],
        "meixue": meixue_stats,
        "style_relationships": style_stats,
        "car_records": len(car_records),
        "total_output_records": output_count,
        "output": str(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
