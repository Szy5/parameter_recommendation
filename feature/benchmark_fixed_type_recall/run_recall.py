#!/usr/bin/env python3
"""Embed the fixed feature corpus and retrieve Top-K nodes for Benchmark keywords."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Tuple

import numpy as np


ALLOWED_LABELS = (
    "AerodynamicFeature(空气动力学特征)",
    "AestheticConcept(美学概念)",
    "DesignAttribute(设计属性)",
    "DesignParameter(设计参数)",
    "FamilyDNA(家族DNA)",
    "UserTrend(用户与趋势)",
    "VehiclePosture(汽车姿态)",
)
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)
WHITESPACE_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def normalize_text(value: Any) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()


def build_document_text(record: Dict[str, Any]) -> str:
    name = normalize_text(record.get("name"))
    if not name:
        raise ValueError("feature is missing name: %s" % record.get("node_id"))
    description = normalize_text(record.get("description"))
    parts = ["名称：" + name]
    if description:
        parts.append("描述：" + description)
    return "\n".join(parts)


def build_query_text(keywords: Sequence[Any]) -> str:
    values = [normalize_text(value) for value in keywords]
    if not values or any(not value for value in values):
        raise ValueError("keywords must be a non-empty array of non-empty strings")
    return "；".join(values)


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d is invalid JSONL" % (path, line_number)) from exc
            if not isinstance(value, dict):
                raise ValueError("%s:%d must be a JSON object" % (path, line_number))
            yield value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_corpus(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for record in iter_jsonl(path):
        node_id = str(record.get("node_id") or "")
        labels = record.get("labels") or []
        if not node_id or node_id in seen:
            raise ValueError("missing or duplicate node_id: %s" % node_id)
        if len(labels) != 1 or labels[0] not in ALLOWED_LABELS:
            raise ValueError("node outside fixed recall labels: %s %s" % (node_id, labels))
        seen.add(node_id)
        rows.append({
            "node_id": node_id,
            "label": str(labels[0]),
            "name": normalize_text(record.get("name")),
            "description": normalize_text(record.get("description")),
            "text": build_document_text(record),
        })
    if len(rows) != 9649:
        raise ValueError("expected 9649 fixed-type nodes, found %d" % len(rows))
    return rows


def prepare_queries(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for record in iter_jsonl(path):
        case_id = str(record.get("id") or "")
        keywords = record.get("keywords")
        if not case_id or case_id in seen:
            raise ValueError("missing or duplicate Benchmark id: %s" % case_id)
        if not isinstance(keywords, list):
            raise ValueError("keywords must be an array: %s" % case_id)
        seen.add(case_id)
        rows.append({
            "id": case_id,
            "keywords": [normalize_text(value) for value in keywords],
            "query_text": build_query_text(keywords),
        })
    if len(rows) != 100:
        raise ValueError("expected 100 Benchmark cases, found %d" % len(rows))
    return rows


def rank_top_k(scores: np.ndarray, node_ids: Sequence[str], top_k: int) -> List[int]:
    if scores.ndim != 1 or scores.shape[0] != len(node_ids):
        raise ValueError("score and node ID dimensions do not match")
    if top_k <= 0 or top_k > len(node_ids):
        raise ValueError("invalid top_k")
    # lexsort uses the last key as primary: descending score, then ascending node_id.
    order = np.lexsort((np.asarray(node_ids, dtype=object), -scores))
    return [int(index) for index in order[:top_k]]


def lexical_matches(keywords: Sequence[str], node: Dict[str, Any]) -> List[str]:
    haystack = (node["name"] + " " + node["description"]).casefold()
    return [keyword for keyword in keywords if keyword.casefold() in haystack]


def percentile(values: Sequence[float], quantile: float) -> float:
    return round(float(np.quantile(np.asarray(values, dtype=np.float64), quantile)), 6)


def summarize_recall(
    rows: Sequence[Dict[str, Any]],
    corpus_size: int,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> Dict[str, Any]:
    if not rows:
        raise ValueError("recall rows are empty")
    top_k = len(rows[0]["retrieved_nodes"])
    by_k: Dict[str, Any] = {}
    for current_k in (1, 5, 10, 15, 20):
        if current_k > top_k:
            continue
        selected = [item for row in rows for item in row["retrieved_nodes"][:current_k]]
        unique_nodes = {item["node_id"] for item in selected}
        labels = Counter(item["label"] for item in selected)
        lexical_slots = sum(bool(item["matched_keywords"]) for item in selected)
        cases_with_lexical = sum(
            any(item["matched_keywords"] for item in row["retrieved_nodes"][:current_k])
            for row in rows
        )
        by_k[str(current_k)] = {
            "cases": len(rows),
            "retrieval_slots": len(selected),
            "unique_nodes": len(unique_nodes),
            "corpus_coverage_rate": round(len(unique_nodes) / corpus_size, 6),
            "label_slot_counts": dict(sorted(labels.items())),
            "slots_with_exact_keyword_substring": lexical_slots,
            "cases_with_exact_keyword_substring": cases_with_lexical,
        }

    by_threshold: Dict[str, Any] = {}
    for threshold in thresholds:
        case_counts = []
        selected_items = []
        for row in rows:
            current = [item for item in row["retrieved_nodes"] if item["score"] >= threshold]
            case_counts.append(len(current))
            selected_items.extend(current)
        unique_nodes = {item["node_id"] for item in selected_items}
        by_threshold[str(threshold)] = {
            "cases_with_at_least_one": sum(value > 0 for value in case_counts),
            "cases_with_full_top_k": sum(value == top_k for value in case_counts),
            "retrieval_slots": len(selected_items),
            "unique_nodes": len(unique_nodes),
            "mean_nodes_per_case": round(sum(case_counts) / len(case_counts), 4),
            "median_nodes_per_case": percentile(case_counts, 0.5),
            "min_nodes_per_case": min(case_counts),
            "max_nodes_per_case": max(case_counts),
        }

    score_by_rank = []
    for rank in range(top_k):
        values = [float(row["retrieved_nodes"][rank]["score"]) for row in rows]
        score_by_rank.append({
            "rank": rank + 1,
            "min": round(min(values), 6),
            "p25": percentile(values, 0.25),
            "median": percentile(values, 0.5),
            "p75": percentile(values, 0.75),
            "max": round(max(values), 6),
            "mean": round(sum(values) / len(values), 6),
        })
    return {
        "note": "Counts and cosine similarity are retrieval diagnostics, not node-level accuracy; no current-graph node gold set exists.",
        "case_count": len(rows),
        "corpus_size": corpus_size,
        "top_k": top_k,
        "by_k": by_k,
        "by_threshold": by_threshold,
        "score_by_rank": score_by_rank,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--revision", default=MODEL_REVISION)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit("refusing non-empty output directory: %s" % args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_iso()
    started = time.time()

    corpus = prepare_corpus(args.features)
    queries = prepare_queries(args.benchmark)
    write_jsonl(args.output_dir / "corpus.jsonl", corpus)
    write_jsonl(args.output_dir / "benchmark_queries.jsonl", queries)

    import sentence_transformers
    import torch
    import transformers
    from sentence_transformers import SentenceTransformer

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print("loading model=%s revision=%s device=%s" % (args.model, args.revision, device), flush=True)
    model = SentenceTransformer(args.model, revision=args.revision, device=device, trust_remote_code=False)
    model.max_seq_length = args.max_seq_length

    print("encoding %d fixed-type nodes" % len(corpus), flush=True)
    node_embeddings = model.encode(
        [row["text"] for row in corpus],
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
        precision="float32",
    ).astype(np.float32, copy=False)
    print("encoding %d Benchmark queries" % len(queries), flush=True)
    query_embeddings = model.encode(
        [row["query_text"] for row in queries],
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
        precision="float32",
    ).astype(np.float32, copy=False)

    if node_embeddings.shape[0] != len(corpus) or query_embeddings.shape[0] != len(queries):
        raise RuntimeError("embedding row count mismatch")
    if node_embeddings.shape[1] != query_embeddings.shape[1]:
        raise RuntimeError("embedding dimensions differ")
    if not np.isfinite(node_embeddings).all() or not np.isfinite(query_embeddings).all():
        raise RuntimeError("embedding contains NaN or Infinity")
    if not np.allclose(np.linalg.norm(node_embeddings, axis=1), 1.0, atol=1e-4):
        raise RuntimeError("node embeddings are not normalized")
    if not np.allclose(np.linalg.norm(query_embeddings, axis=1), 1.0, atol=1e-4):
        raise RuntimeError("query embeddings are not normalized")

    np.save(args.output_dir / "node_embeddings.npy", node_embeddings, allow_pickle=False)
    np.save(args.output_dir / "query_embeddings.npy", query_embeddings, allow_pickle=False)
    scores = query_embeddings @ node_embeddings.T
    node_ids = [row["node_id"] for row in corpus]
    recall_rows: List[Dict[str, Any]] = []
    for query_index, query in enumerate(queries):
        indices = rank_top_k(scores[query_index], node_ids, args.top_k)
        retrieved = []
        for rank, corpus_index in enumerate(indices, 1):
            node = corpus[corpus_index]
            retrieved.append({
                "rank": rank,
                "node_id": node["node_id"],
                "label": node["label"],
                "name": node["name"],
                "description": node["description"],
                "score": round(float(scores[query_index, corpus_index]), 6),
                "matched_keywords": lexical_matches(query["keywords"], node),
            })
        recall_rows.append({
            "id": query["id"],
            "keywords": query["keywords"],
            "query_text": query["query_text"],
            "retrieved_nodes": retrieved,
        })
    write_jsonl(args.output_dir / "recall_top20.jsonl", recall_rows)
    summary = summarize_recall(recall_rows, len(corpus))
    summary.update({
        "model": args.model,
        "model_revision": args.revision,
        "embedding_dimension": int(node_embeddings.shape[1]),
        "node_embedding_shape": list(node_embeddings.shape),
        "query_embedding_shape": list(query_embeddings.shape),
        "embedding_dtype": str(node_embeddings.dtype),
    })
    write_json(args.output_dir / "summary.json", summary)

    output_names = (
        "corpus.jsonl",
        "benchmark_queries.jsonl",
        "node_embeddings.npy",
        "query_embeddings.npy",
        "recall_top20.jsonl",
        "summary.json",
    )
    manifest = {
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_seconds": round(time.time() - started, 3),
        "model": args.model,
        "model_revision": args.revision,
        "device": device,
        "device_name": torch.cuda.get_device_name(0) if device.startswith("cuda") else platform.processor(),
        "batch_size": args.batch_size,
        "max_seq_length": args.max_seq_length,
        "top_k": args.top_k,
        "document_text_contract": "名称：<name>\\n描述：<description>; omit description line when empty; label excluded",
        "query_text_contract": "keywords joined in original order with Chinese semicolon; no query instruction",
        "features": str(args.features),
        "features_sha256": sha256_file(args.features),
        "benchmark": str(args.benchmark),
        "benchmark_sha256": sha256_file(args.benchmark),
        "versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "sentence_transformers": sentence_transformers.__version__,
        },
        "outputs": {name: {"sha256": sha256_file(args.output_dir / name), "bytes": (args.output_dir / name).stat().st_size} for name in output_names},
    }
    write_json(args.output_dir / "run_manifest.json", manifest)
    print(json.dumps({"output_dir": str(args.output_dir), "summary": summary, "elapsed_seconds": manifest["elapsed_seconds"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
