#!/usr/bin/env python3
"""Convert validated judge outputs into deterministic graph relationship JSONL."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Set, Tuple

from .constants import CAR_TYPE_LABEL, STYLE_LABEL
from .io_utils import iter_jsonl, read_jsonl, write_jsonl


def relationship_id(task: str, source_id: str, target_id: str) -> str:
    digest = hashlib.sha256((task + "\0" + source_id + "\0" + target_id).encode("utf-8")).hexdigest()[:24]
    return "benchmark_%s_associated_%s" % (task, digest)


def build_edges(
    task: str,
    results: Iterable[Dict[str, Any]],
    features: Dict[str, Dict[str, Any]],
    targets: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    key = "styles" if task == "style" else "types"
    relation_label = "StyleAssociatedWith" if task == "style" else "TypeAssociatedWith"
    target_label = STYLE_LABEL if task == "style" else CAR_TYPE_LABEL
    edges: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for row in results:
        if row.get("status") != "ok" or row.get("task") != task:
            continue
        source_id = str(row.get("node_id"))
        if source_id not in features:
            raise ValueError("judge source node absent from feature input: %s" % source_id)
        for item in row.get(key, []):
            name = str(item["name"])
            target = targets.get(name)
            if not target or not target.get("node_id"):
                raise ValueError("target graph node absent: %s" % name)
            target_id = str(target["node_id"])
            pair = (source_id, target_id)
            if pair in seen:
                continue
            seen.add(pair)
            feature = features[source_id]
            edges.append({
                "type": "relationship",
                "id": relationship_id(task, source_id, target_id),
                "label": relation_label,
                "properties": {"confidence": item["confidence"], "reason": item["reason"]},
                "start": {
                    "id": source_id,
                    "labels": feature.get("labels") or [],
                    "properties": feature.get("source_properties") or {"name": feature.get("name")},
                },
                "end": {
                    "id": target_id,
                    "labels": [target_label],
                    "properties": {"name": name},
                },
            })
    edges.sort(key=lambda row: row["id"])
    return edges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("style", "type"), required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    import json
    rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    targets = rubric["styles" if args.task == "style" else "car_types"]
    features = {str(row["node_id"]): row for row in iter_jsonl(args.features)}
    result_rows = list(iter_jsonl(args.results))
    models = {str(row.get("model")) for row in result_rows if row.get("status") == "ok"}
    versions = {str(row.get("prompt_version")) for row in result_rows if row.get("status") == "ok"}
    if len(models) > 1 or len(versions) > 1:
        raise ValueError("refusing mixed model or prompt-version result batch")
    latest: Dict[str, Dict[str, Any]] = {}
    for row in result_rows:
        if row.get("task") != args.task:
            continue
        node_id = str(row.get("node_id"))
        if row.get("status") == "ok" or node_id not in latest:
            latest[node_id] = row
    failures = [node_id for node_id, row in latest.items() if row.get("status") != "ok"]
    if failures and not args.allow_partial:
        raise ValueError("refusing partial result batch with %d failed task keys" % len(failures))
    edges = build_edges(args.task, latest.values(), features, targets)
    write_jsonl(args.output, edges)
    print("wrote %d %s edges to %s" % (len(edges), args.task, args.output))


if __name__ == "__main__":
    main()
