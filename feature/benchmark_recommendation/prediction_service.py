from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import PredictionConfig
from .schemas import CarLevel, RecommendationResult, ScoredCandidate
from .graph_loader import GraphData


LEVEL_KEYWORDS: Dict[str, Sequence[str]] = {
    "A0": ("A0", "A0级", "A0 级"),
    "A": ("A级", "A 级", "A类", "A档"),
    "B": ("B级", "B 级", "B类", "B档"),
    "C": ("C级", "C 级", "C类", "C档"),
    "D": ("D级", "D 级", "D类", "D档"),
}


def _sorted_candidates(scored: Dict[str, Tuple[float, int]]) -> List[ScoredCandidate]:
    # scored[name] = (score, support)
    return [
        {"name": name, "score": float(score), "support": int(support)}
        for name, (score, support) in sorted(
            scored.items(),
            key=lambda item: (-item[1][0], -item[1][1], item[0]),
        )
    ]


def predict_style_and_type(
    graph: GraphData,
    recalled_nodes: List[Any],
    prediction_config: PredictionConfig,
) -> Tuple[List[ScoredCandidate], List[ScoredCandidate], bool, Dict[str, Any]]:
    recall_scores = {row["node_id"]: float(row["score"]) for row in recalled_nodes}

    # style
    style_scores: Dict[str, Tuple[float, int]] = {}
    style_source_sets: Dict[str, set] = {}
    for source_id, recall_score in recall_scores.items():
        for edge in graph.style_edges_by_source.get(source_id, []):
            target = edge["target"]
            conf = float(edge["confidence"])
            score_delta = recall_score * conf
            if target not in style_scores:
                style_scores[target] = (0.0, 0)
                style_source_sets[target] = set()
            prev_score, prev_support = style_scores[target]
            style_source_sets[target].add(source_id)
            # Update score by additive contribution; support computed after.
            style_scores[target] = (prev_score + score_delta, prev_support)
    for target in list(style_scores.keys()):
        style_scores[target] = (style_scores[target][0], len(style_source_sets[target]))

    style_candidates = _sorted_candidates(style_scores)
    # apply min_candidate_score
    style_candidates = [c for c in style_candidates if c["score"] > prediction_config.min_candidate_score]

    # type
    type_scores: Dict[str, Tuple[float, int]] = {}
    type_source_sets: Dict[str, set] = {}
    for source_id, recall_score in recall_scores.items():
        for edge in graph.type_edges_by_source.get(source_id, []):
            target = edge["target"]
            conf = float(edge["confidence"])
            score_delta = recall_score * conf
            if target not in type_scores:
                type_scores[target] = (0.0, 0)
                type_source_sets[target] = set()
            prev_score, prev_support = type_scores[target]
            type_source_sets[target].add(source_id)
            type_scores[target] = (prev_score + score_delta, prev_support)
    for target in list(type_scores.keys()):
        type_scores[target] = (type_scores[target][0], len(type_source_sets[target]))

    type_candidates = _sorted_candidates(type_scores)
    type_candidates = [c for c in type_candidates if c["score"] > prediction_config.min_candidate_score]

    need_user_confirmation = False
    ambiguity = {"style": None, "type": None}

    def maybe_keep_top2(cands: List[ScoredCandidate], key: str) -> List[ScoredCandidate]:
        nonlocal need_user_confirmation
        if not cands:
            return []
        if len(cands) == 1:
            return cands[:1]
        top1 = cands[0]
        top2 = cands[1]
        diff = float(top1["score"]) - float(top2["score"])
        if diff <= prediction_config.top_score_diff_ambiguity:
            need_user_confirmation = True
            ambiguity[key] = {"diff": diff, "top1": top1["score"], "top2": top2["score"]}
            return cands[:2]
        return cands[:1]

    style_top = maybe_keep_top2(style_candidates, "style")
    type_top = maybe_keep_top2(type_candidates, "type")

    return style_top, type_top, need_user_confirmation, {"ambiguity": ambiguity}


def infer_level_from_type(
    graph: GraphData,
    car_type_candidates: List[ScoredCandidate],
    car_type_scores: Dict[str, float],
    keywords: List[str],
    prediction_config: PredictionConfig,
) -> Optional[CarLevel]:
    """Infer car_level from type->instance->level distribution.

    We keep it conservative: if evidence is not clear, return None.
    """

    # Keyword explicit hints (very lightweight).
    explicit_levels: List[str] = []
    for kw in keywords:
        for level_name, markers in LEVEL_KEYWORDS.items():
            for marker in markers:
                if marker in kw:
                    explicit_levels.append(level_name)
    explicit_levels = sorted(set(explicit_levels))

    level_weight: Dict[str, float] = {}
    level_support: Dict[str, int] = {}

    for type_row in car_type_candidates:
        type_name = type_row["name"]
        type_weight = float(car_type_scores.get(type_name) or type_row["score"])
        for instance_id in graph.instances_by_type.get(type_name, set()):
            for level_name in graph.level_by_instance.get(instance_id, set()):
                level_weight[level_name] = level_weight.get(level_name, 0.0) + type_weight
                level_support[level_name] = level_support.get(level_name, 0) + 1

    if not level_weight:
        return None

    ordered = sorted(level_weight.items(), key=lambda item: (-item[1], item[0]))
    top_level, top_w = ordered[0]
    second_w = ordered[1][1] if len(ordered) > 1 else 0.0
    diff = top_w - second_w

    if diff <= prediction_config.level_top_weight_diff_ambiguity:
        return None

    if explicit_levels:
        # If explicit hint conflicts with the inferred top, keep conservative empty.
        if top_level not in explicit_levels:
            return None

    return {"name": top_level, "source": "type_instance_distribution"}

