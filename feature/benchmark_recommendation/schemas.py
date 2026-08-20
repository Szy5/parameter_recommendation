from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, TypedDict


class RecalledNode(TypedDict, total=False):
    node_id: str
    label: str
    name: str
    score: float
    rank: int
    matched_keywords: List[str]


class ScoredCandidate(TypedDict):
    name: str
    score: float
    support: int


class CarLevel(TypedDict, total=False):
    name: str
    source: str


class NumericParameterSummary(TypedDict):
    parameter: str
    unit: str
    sample_count: int
    min: float
    p25: float
    median: float
    p75: float
    max: float


class StyleGuideParameter(TypedDict, total=False):
    parameter: str
    range: str
    unit: str
    description: str
    guidance: Dict[str, Any]


class RecommendationResult(TypedDict, total=False):
    strategy: str
    sample_count: int
    parameters: List[Dict[str, Any]]
    fallback_reason: Optional[str]
    baseline_type_median: Optional[List[Dict[str, Any]]]
    small_sample_warning: Optional[str]


class BenchmarkRecommendationResponse(TypedDict, total=False):
    id: str
    input: Dict[str, Any]
    recalled_nodes: List[RecalledNode]
    predicted: Dict[str, Any]
    need_user_confirmation: bool
    recommendation: Dict[str, Any]
    paths: List[Dict[str, Any]]
    warnings: List[str]
    postprocess: Dict[str, Any]

