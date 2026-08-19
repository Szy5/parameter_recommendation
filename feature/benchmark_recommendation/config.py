from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from .recommend_types import VALID_RECOMMEND_TYPES, parse_recommend_types


RecallMode = Literal["top_k", "threshold", "top_k_and_threshold"]
StrategyName = Literal["style_guides", "style_type", "style_type_level", "none"]


@dataclass(frozen=True)
class RecallConfig:
    mode: RecallMode = "top_k_and_threshold"
    top_k: int = 20
    min_score: float = 0.60
    # Safety upper bound for `threshold` mode.
    max_candidates: int = 50


@dataclass(frozen=True)
class LiveRecallConfig:
    features_jsonl: str = (
        "feature/artifacts/benchmark_upstream_offline/validation/"
        "v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl"
    )
    model: str = "BAAI/bge-m3"
    revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    batch_size: int = 32
    max_seq_length: int = 512
    device: Optional[str] = None
    # How many ranked nodes to materialize before apply_recall_config in non-threshold modes.
    pool_size: int = 50


@dataclass(frozen=True)
class PredictionConfig:
    # If top1-top2 difference is <= this value, keep top2 and require confirmation.
    top_score_diff_ambiguity: float = 0.02
    # If all candidate scores are <= this value, treat as empty.
    min_candidate_score: float = 1e-9
    # Level ambiguity uses weighted counts.
    level_top_weight_diff_ambiguity: float = 0.05


@dataclass(frozen=True)
class RecommendationConfig:
    max_styles: int = 2
    max_types: int = 2
    # At most this many (style,type) groups are executed.
    max_combinations: int = 4
    # If sample size is small, we still return results but emit warnings.
    small_sample_threshold: int = 10
    # If enabled, will fall back from StyleTypeLevelStrategy to StyleTypeStrategy
    # when style+type+level intersection is too small.
    fallback_on_small_sample: bool = True
    fallback_small_sample_threshold: int = 8
    # Which recommendation buckets to run, e.g. ("style_guides", "style_type").
    recommend_types: Tuple[str, ...] = VALID_RECOMMEND_TYPES


@dataclass(frozen=True)
class BenchmarkRecommendationConfig:
    recall: RecallConfig = RecallConfig()
    prediction: PredictionConfig = PredictionConfig()
    recommendation: RecommendationConfig = RecommendationConfig()

    # Where to print "level" when we can't infer confidently.
    # `None` means "do not invent a level".
    default_level: Optional[str] = None

