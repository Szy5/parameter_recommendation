#!/usr/bin/env python3
"""Build auditable diagnostics from a completed fixed-type recall run."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List

import numpy as np

from .run_recall import ALLOWED_LABELS, iter_jsonl, rank_top_k, sha256_file, write_json


THRESHOLDS = (0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70)


def threshold_summary(rows: List[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    counts: List[int] = []
    node_ids = set()
    for row in rows:
        kept = [node for node in row["retrieved_nodes"] if float(node["score"]) >= threshold]
        counts.append(len(kept))
        node_ids.update(node["node_id"] for node in kept)
    return {
        "threshold": threshold,
        "cases_with_at_least_one": sum(value > 0 for value in counts),
        "cases_with_full_top20": sum(value == 20 for value in counts),
        "retrieval_slots": sum(counts),
        "unique_nodes": len(node_ids),
        "mean_nodes_per_case": round(sum(counts) / len(counts), 2),
        "median_nodes_per_case": float(median(counts)),
        "min_nodes_per_case": min(counts),
        "max_nodes_per_case": max(counts),
    }


def build_analysis(run_dir: Path, manual_review_path: Path) -> Dict[str, Any]:
    corpus = list(iter_jsonl(run_dir / "corpus.jsonl"))
    rows = list(iter_jsonl(run_dir / "recall_top20.jsonl"))
    manual = list(iter_jsonl(manual_review_path))
    corpus_labels = Counter(row["label"] for row in corpus)
    retrieved_labels = Counter(
        node["label"] for row in rows for node in row["retrieved_nodes"]
    )
    label_rows = []
    for label in ALLOWED_LABELS:
        corpus_share = corpus_labels[label] / len(corpus)
        retrieved_share = retrieved_labels[label] / (len(rows) * 20)
        label_rows.append({
            "label": label,
            "corpus_nodes": corpus_labels[label],
            "corpus_share": round(corpus_share, 6),
            "top20_slots": retrieved_labels[label],
            "top20_share": round(retrieved_share, 6),
            "enrichment_ratio": round(retrieved_share / corpus_share, 3),
        })

    strict = sum(int(row["relevant"]) for row in manual)
    partial = sum(int(row["partial"]) for row in manual)
    irrelevant = sum(int(row["irrelevant"]) for row in manual)
    reviewed = strict + partial + irrelevant
    return {
        "scope": {
            "benchmark_cases": len(rows),
            "corpus_nodes": len(corpus),
            "allowed_labels": list(ALLOWED_LABELS),
            "top_k": 20,
        },
        "headline": {
            "top20_retrieval_slots": len(rows) * 20,
            "top20_unique_nodes": len({node["node_id"] for row in rows for node in row["retrieved_nodes"]}),
            "top20_corpus_coverage": round(
                len({node["node_id"] for row in rows for node in row["retrieved_nodes"]}) / len(corpus), 6
            ),
        },
        "threshold_sensitivity": [threshold_summary(rows, threshold) for threshold in THRESHOLDS],
        "label_enrichment": label_rows,
        "manual_review": {
            "cases": len(manual),
            "ranks_per_case": 10,
            "reviewed_slots": reviewed,
            "relevant": strict,
            "partial": partial,
            "irrelevant": irrelevant,
            "strict_precision_at_10": round(strict / reviewed, 4),
            "inclusive_precision_at_10": round((strict + partial) / reviewed, 4),
            "definition": "固定审查 B001–B010；strict 仅计 relevant，inclusive 计 relevant+partial。",
            "rows": manual,
        },
    }


def validate_run(run_dir: Path) -> Dict[str, Any]:
    corpus = list(iter_jsonl(run_dir / "corpus.jsonl"))
    queries = list(iter_jsonl(run_dir / "benchmark_queries.jsonl"))
    recall = list(iter_jsonl(run_dir / "recall_top20.jsonl"))
    nodes = np.load(run_dir / "node_embeddings.npy")
    query_vectors = np.load(run_dir / "query_embeddings.npy")
    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("corpus_count", len(corpus) == 9649, f"found={len(corpus)}, expected=9649")
    check("query_count", len(queries) == 100, f"found={len(queries)}, expected=100")
    check("recall_count", len(recall) == 100, f"found={len(recall)}, expected=100")
    check("node_matrix", nodes.shape == (9649, 1024) and nodes.dtype == np.float32,
          f"shape={nodes.shape}, dtype={nodes.dtype}")
    check("query_matrix", query_vectors.shape == (100, 1024) and query_vectors.dtype == np.float32,
          f"shape={query_vectors.shape}, dtype={query_vectors.dtype}")
    check("node_norms", np.allclose(np.linalg.norm(nodes, axis=1), 1.0, atol=1e-5),
          "all node vector norms are within 1e-5 of 1")
    check("query_norms", np.allclose(np.linalg.norm(query_vectors, axis=1), 1.0, atol=1e-5),
          "all query vector norms are within 1e-5 of 1")
    check("finite_vectors", np.isfinite(nodes).all() and np.isfinite(query_vectors).all(),
          "all matrix values are finite")

    scores = query_vectors @ nodes.T
    node_ids = [row["node_id"] for row in corpus]
    exact = True
    structure = True
    allowed = set(ALLOWED_LABELS)
    for query_index, row in enumerate(recall):
        got = row["retrieved_nodes"]
        expected_indexes = rank_top_k(scores[query_index], node_ids, 20)
        expected_ids = [node_ids[index] for index in expected_indexes]
        exact = exact and [node["node_id"] for node in got] == expected_ids
        structure = structure and len(got) == 20
        structure = structure and [node["rank"] for node in got] == list(range(1, 21))
        structure = structure and len({node["node_id"] for node in got}) == 20
        structure = structure and all(node["label"] in allowed for node in got)
        structure = structure and all(got[i]["score"] >= got[i + 1]["score"] for i in range(19))
    check("recall_structure", structure, "20 distinct allowed nodes, contiguous ranks, non-increasing scores")
    check("top20_recomputed", exact, "saved Top-20 node IDs exactly match matrix recomputation")

    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    hash_ok = all(
        sha256_file(run_dir / name) == metadata["sha256"]
        for name, metadata in manifest["outputs"].items()
    )
    check("manifest_hashes", hash_ok, "all original run outputs match recorded SHA-256")
    return {
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
    }


def write_checksums(run_dir: Path, paths: Iterable[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.name}" for path in paths]
    (run_dir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--manual-review", type=Path, required=True)
    args = parser.parse_args()
    analysis_path = args.run_dir / "analysis_summary.json"
    validation_path = args.run_dir / "validation_report.json"
    write_json(analysis_path, build_analysis(args.run_dir, args.manual_review))
    write_json(validation_path, validate_run(args.run_dir))
    write_checksums(args.run_dir, [
        args.run_dir / "corpus.jsonl",
        args.run_dir / "benchmark_queries.jsonl",
        args.run_dir / "node_embeddings.npy",
        args.run_dir / "query_embeddings.npy",
        args.run_dir / "recall_top20.jsonl",
        args.run_dir / "summary.json",
        args.run_dir / "run_manifest.json",
        args.manual_review,
        analysis_path,
        validation_path,
    ])
    report = json.loads(validation_path.read_text(encoding="utf-8"))
    print(json.dumps({"analysis": str(analysis_path), "validation": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
