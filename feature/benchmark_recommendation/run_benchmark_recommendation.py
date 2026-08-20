#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from feature.parameter_recommendation.neo4j_recommend import Neo4jConfig

from .config import (
    BenchmarkRecommendationConfig,
    LiveRecallConfig,
    PathSearchConfig,
    PredictionConfig,
    RecallConfig,
    RecommendationConfig,
)
from .live_recall_service import LiveRecallEngine
from .neo4j_repository import BenchmarkNeo4jRepository
from .pipeline import run_benchmark_recommendation_offline
from .recommend_types import parse_recommend_types


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _is_flat_dict(value: Any) -> bool:
    return isinstance(value, dict) and all(_is_scalar(v) for v in value.values())


def _render_json(value: Any, indent: int = 0, parent_key: str = "") -> str:
    space = " " * indent
    next_indent = indent + 2
    child_space = " " * next_indent

    compact_list_keys = {
        "recalled_nodes",
        "car_style",
        "car_type",
        "paths",
        "parameters",
        "recommended_vehicles",
    }

    if isinstance(value, dict):
        if _is_flat_dict(value):
            return json.dumps(value, ensure_ascii=False)
        if not value:
            return "{}"
        lines: List[str] = ["{"]
        items = list(value.items())
        for idx, (key, item) in enumerate(items):
            rendered = _render_json(item, next_indent, key)
            suffix = "," if idx < len(items) - 1 else ""
            if "\n" in rendered:
                lines.append(f'{child_space}"{key}": {rendered}{suffix}')
            else:
                lines.append(f'{child_space}"{key}": {rendered}{suffix}')
        lines.append(f"{space}" + "}")
        return "\n".join(lines)

    if isinstance(value, list):
        if not value:
            return "[]"
        if parent_key in compact_list_keys and all(isinstance(item, dict) for item in value):
            lines = ["["]
            for idx, item in enumerate(value):
                suffix = "," if idx < len(value) - 1 else ""
                lines.append(f'{" " * next_indent}{json.dumps(item, ensure_ascii=False)}{suffix}')
            lines.append(f"{space}]")
            return "\n".join(lines)
        lines = ["["]
        for idx, item in enumerate(value):
            rendered = _render_json(item, next_indent, parent_key)
            suffix = "," if idx < len(value) - 1 else ""
            lines.append(f'{" " * next_indent}{rendered}{suffix}')
        lines.append(f"{space}]")
        return "\n".join(lines)

    return json.dumps(value, ensure_ascii=False)


DEFAULT_FEATURES_JSONL = Path("data/features_all.jsonl")
DEFAULT_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Benchmark keyword -> modular parameter recommendation")
    parser.add_argument(
        "--stage",
        choices=("recall", "path", "predict", "recommend"),
        default="recommend",
        help="recall, path inspect, recall+predict, or recall+predict+recommend",
    )
    parser.add_argument("--graph-jsonl", type=Path, required=True, help="kgdata_*.jsonl with associated edges")
    parser.add_argument(
        "--recall-top20-jsonl",
        type=Path,
        help="recall_top20.jsonl from benchmark_fixed_type_recall (required for --recall-source offline)",
    )
    parser.add_argument("--benchmark-inputs-jsonl", type=Path, required=True, help="benchmark_100_inputs.jsonl")
    parser.add_argument("--output-json", type=Path, required=True, help="Output JSON file")
    parser.add_argument(
        "--recall-source",
        choices=("offline", "live"),
        default="offline",
        help="offline reads recall_top20.jsonl; live embeds keywords with BGE-M3 on each run",
    )
    parser.add_argument("--features-jsonl", type=Path, default=DEFAULT_FEATURES_JSONL, help="Fixed-type feature corpus for live recall")
    parser.add_argument("--model", default="BAAI/bge-m3", help="Embedding model for live recall")
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION, help="Fixed model revision for live recall")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size for live recall")
    parser.add_argument("--max-seq-length", type=int, default=512, help="Embedding max sequence length for live recall")
    parser.add_argument("--device", default=None, help="cuda/cpu for live recall; default auto-detect")
    parser.add_argument("--live-pool-size", type=int, default=50, help="Candidate pool size before recall filtering in live mode")
    parser.add_argument("--env", type=Path, default=Path("feature/.env"), help="Neo4j dotenv path")
    parser.add_argument(
        "--recommend-source",
        choices=("neo4j", "offline"),
        default="neo4j",
        help="recommend stage reads parameters from Neo4j by default",
    )
    parser.add_argument("--recall-mode", choices=("top_k", "threshold", "top_k_and_threshold"), default="top_k_and_threshold")
    parser.add_argument("--min-score", type=float, default=0.60)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--max-hops", type=int, default=5, help="Keep main paths with hop_count in 1..max-hops (path stage)")
    parser.add_argument(
        "--no-neighbor",
        action="store_true",
        help="Omit aesthetic_to_neighbor_evidence paths (path stage)",
    )
    parser.add_argument("--ambiguity-margin", type=float, default=0.02)
    parser.add_argument("--min-candidate-score", type=float, default=1e-9)
    parser.add_argument("--level-ambiguity-margin", type=float, default=0.05)
    parser.add_argument("--max-styles", type=int, default=2)
    parser.add_argument("--max-types", type=int, default=2)
    parser.add_argument("--max-combinations", type=int, default=4)
    parser.add_argument("--small-sample-threshold", type=int, default=10)
    parser.add_argument("--fallback-on-small-sample", action="store_true")
    parser.add_argument("--fallback-small-sample-threshold", type=int, default=8)
    parser.add_argument(
        "--recommend-types",
        default="style_guides,style_type,style_type_level",
        help="comma-separated recommendation types: style_guides, style_type, style_type_level",
    )
    args = parser.parse_args()
    if args.recall_source == "offline" and args.recall_top20_jsonl is None:
        parser.error("--recall-top20-jsonl is required when --recall-source offline")

    config = BenchmarkRecommendationConfig(
        recall=RecallConfig(
            mode=args.recall_mode,
            top_k=args.top_k,
            min_score=args.min_score,
            max_candidates=args.max_candidates,
        ),
        prediction=PredictionConfig(
            top_score_diff_ambiguity=args.ambiguity_margin,
            min_candidate_score=args.min_candidate_score,
            level_top_weight_diff_ambiguity=args.level_ambiguity_margin,
        ),
        recommendation=RecommendationConfig(
            max_styles=args.max_styles,
            max_types=args.max_types,
            max_combinations=args.max_combinations,
            small_sample_threshold=args.small_sample_threshold,
            fallback_on_small_sample=bool(args.fallback_on_small_sample),
            fallback_small_sample_threshold=args.fallback_small_sample_threshold,
            recommend_types=parse_recommend_types(args.recommend_types),
        ),
        path=PathSearchConfig(
            max_hops=args.max_hops,
            include_neighbor=not args.no_neighbor,
        ),
    )

    neo4j_repository = None
    live_recall_engine = None
    if args.recall_source == "live":
        live_recall_engine = LiveRecallEngine.from_config(
            LiveRecallConfig(
                features_jsonl=str(args.features_jsonl),
                model=args.model,
                revision=args.revision,
                batch_size=args.batch_size,
                max_seq_length=args.max_seq_length,
                device=args.device,
                pool_size=args.live_pool_size,
            )
        )
    if args.stage == "path" or (args.stage in ("predict", "recommend") and args.recommend_source == "neo4j"):
        neo4j_repository = BenchmarkNeo4jRepository.connect(Neo4jConfig.from_env(args.env))
    try:
        outputs = run_benchmark_recommendation_offline(
            graph_jsonl_path=args.graph_jsonl,
            recall_top20_jsonl_path=args.recall_top20_jsonl,
            benchmark_inputs_jsonl_path=args.benchmark_inputs_jsonl,
            config=config,
            stage=args.stage,
            recall_source=args.recall_source,
            live_recall_engine=live_recall_engine,
            neo4j_repository=neo4j_repository,
        )
    finally:
        if neo4j_repository is not None:
            neo4j_repository.close()
    payload = {
        "stage": args.stage,
        "config": {
            "recall": {
                "source": args.recall_source,
                "mode": config.recall.mode,
                "top_k": config.recall.top_k,
                "min_score": config.recall.min_score,
                "max_candidates": config.recall.max_candidates,
                "features_jsonl": str(args.features_jsonl) if args.recall_source == "live" else None,
                "model": args.model if args.recall_source == "live" else None,
                "revision": args.revision if args.recall_source == "live" else None,
                "pool_size": args.live_pool_size if args.recall_source == "live" else None,
            },
            "prediction": {
                "top_score_diff_ambiguity": config.prediction.top_score_diff_ambiguity,
                "min_candidate_score": config.prediction.min_candidate_score,
                "level_top_weight_diff_ambiguity": config.prediction.level_top_weight_diff_ambiguity,
            },
            "recommendation": {
                "max_styles": config.recommendation.max_styles,
                "max_types": config.recommendation.max_types,
                "max_combinations": config.recommendation.max_combinations,
                "small_sample_threshold": config.recommendation.small_sample_threshold,
                "fallback_on_small_sample": config.recommendation.fallback_on_small_sample,
                "fallback_small_sample_threshold": config.recommendation.fallback_small_sample_threshold,
                "recommend_types": list(config.recommendation.recommend_types),
                "source": args.recommend_source if args.stage == "recommend" else "none",
            },
            "path": {
                "max_hops": config.path.max_hops,
                "include_neighbor": config.path.include_neighbor,
            },
        },
        "cases": outputs,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(_render_json(payload) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output_json),
                "count": len(outputs),
                "stage": args.stage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

