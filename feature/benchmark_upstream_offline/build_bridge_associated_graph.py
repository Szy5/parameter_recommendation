#!/usr/bin/env python3
"""Keep Judge AssociatedWith edges only on bridge nodes, reverse them, merge into a new dump."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .constants import BRIDGE_LABELS, CAR_TYPE_LABEL, STYLE_LABEL
from .io_utils import iter_jsonl, write_json, write_jsonl

BRIDGE_LABEL_SET = set(BRIDGE_LABELS)
HEAD_LABELS = {STYLE_LABEL, CAR_TYPE_LABEL}


def relationship_id(task: str, head_id: str, bridge_id: str) -> str:
    digest = hashlib.sha256(
        ("bridge\0" + task + "\0" + head_id + "\0" + bridge_id).encode("utf-8")
    ).hexdigest()[:24]
    return "bridge_%s_associated_%s" % (task, digest)


def first_label(node: Dict[str, Any]) -> str:
    labels = node.get("labels") or []
    return str(labels[0]) if labels else ""


def is_bridge_node(node: Dict[str, Any]) -> bool:
    return bool(BRIDGE_LABEL_SET.intersection(str(label) for label in (node.get("labels") or [])))


def load_nodes(graph_path: Path) -> Dict[str, Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(graph_path):
        if row.get("type") != "node":
            continue
        nodes[str(row["id"])] = row
    return nodes


def compact_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node["id"],
        "labels": list(node.get("labels") or []),
        "properties": dict(node.get("properties") or {}),
    }


def filter_and_reverse(
    edges: Iterable[Dict[str, Any]],
    nodes: Dict[str, Dict[str, Any]],
    task: str,
    expected_head_label: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    stats: Counter = Counter()
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in edges:
        stats["input"] += 1
        start = row.get("start") or {}
        end = row.get("end") or {}
        feature_id = str(start.get("id") or "")
        head_id = str(end.get("id") or "")
        feature = nodes.get(feature_id)
        head = nodes.get(head_id)
        if feature is None or head is None:
            stats["missing_endpoint"] += 1
            continue
        if not is_bridge_node(feature):
            stats["dropped_non_bridge"] += 1
            stats["dropped_" + first_label(feature)] += 1
            continue
        if expected_head_label not in (head.get("labels") or []):
            stats["dropped_bad_head"] += 1
            continue
        confidence = float((row.get("properties") or {}).get("confidence") or 0)
        key = (head_id, feature_id)
        previous = best.get(key)
        if previous is not None and float((previous.get("properties") or {}).get("confidence") or 0) >= confidence:
            stats["dropped_duplicate"] += 1
            continue
        if previous is not None:
            stats["replaced_duplicate"] += 1
        properties = dict(row.get("properties") or {})
        properties["edge_role"] = "bridge_associated"
        properties["judge_source_id"] = row.get("id")
        best[key] = {
            "type": "relationship",
            "id": relationship_id(task, head_id, feature_id),
            "label": row.get("label"),
            "properties": properties,
            "start": compact_node(head),
            "end": compact_node(feature),
        }
    kept_rows = sorted(best.values(), key=lambda item: item["id"])
    stats["output"] = len(kept_rows)
    return kept_rows, dict(stats)


def stream_merged_graph(
    graph_path: Path,
    extra_relationships: List[Dict[str, Any]],
    output_path: Path,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for row in iter_jsonl(graph_path):
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        for row in extra_relationships:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    temporary.replace(output_path)
    return count


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--style-edges", type=Path, required=True)
    parser.add_argument("--type-edges", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--merged-graph", type=Path, required=True)
    args = parser.parse_args(argv)

    nodes = load_nodes(args.graph)
    style_edges, style_stats = filter_and_reverse(
        iter_jsonl(args.style_edges), nodes, "style", STYLE_LABEL
    )
    type_edges, type_stats = filter_and_reverse(
        iter_jsonl(args.type_edges), nodes, "type", CAR_TYPE_LABEL
    )
    write_jsonl(args.output_dir / "style_edges.jsonl", style_edges)
    write_jsonl(args.output_dir / "type_edges.jsonl", type_edges)
    merged_count = stream_merged_graph(args.graph, style_edges + type_edges, args.merged_graph)

    end_labels = Counter()
    start_labels = Counter()
    for row in style_edges + type_edges:
        start_labels[first_label(row["start"])] += 1
        end_labels[first_label(row["end"])] += 1

    summary = {
        "bridge_labels": list(BRIDGE_LABELS),
        "head_labels": sorted(HEAD_LABELS),
        "graph_nodes": len(nodes),
        "style": style_stats,
        "type": type_stats,
        "kept_total": len(style_edges) + len(type_edges),
        "merged_graph_rows": merged_count,
        "associated_start_labels": dict(start_labels),
        "associated_end_labels": dict(end_labels),
        "outputs": {
            "style_edges": str(args.output_dir / "style_edges.jsonl"),
            "type_edges": str(args.output_dir / "type_edges.jsonl"),
            "merged_graph": str(args.merged_graph),
        },
    }
    write_json(args.output_dir / "filter_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
