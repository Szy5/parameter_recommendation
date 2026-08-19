from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from feature.benchmark_upstream_offline.io_utils import iter_jsonl
from feature.parameter_recommendation.common import BODY_PARAMETER_UNITS

from .config import BenchmarkRecommendationConfig
from .graph_loader import GraphData, load_graph
from .neo4j_repository import BenchmarkNeo4jRepository
from .live_recall_service import LiveRecallEngine
from .prediction_service import infer_level_from_type, predict_style_and_type
from .recall_service import apply_recall_config, load_recall_topk, recall_score_by_id
from .recommendation_strategies import (
    style_guides_recommend,
    style_guides_recommend_neo4j,
    style_type_level_recommend,
    style_type_level_recommend_neo4j,
    style_type_recommend,
    style_type_recommend_neo4j,
)
from .recommend_types import recommend_types_include
from .schemas import BenchmarkRecommendationResponse, RecalledNode


def _empty_predicted() -> Dict[str, Any]:
    return {"car_style": [], "car_type": [], "car_level": None}


def load_benchmark_inputs(benchmark_inputs_jsonl: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for record in iter_jsonl(benchmark_inputs_jsonl):
        rows.append({"id": str(record.get("id")), "keywords": list(record.get("keywords") or [])})
    return rows


def run_benchmark_recommendation_offline(
    *,
    graph_jsonl_path: Path,
    benchmark_inputs_jsonl_path: Path,
    config: BenchmarkRecommendationConfig = BenchmarkRecommendationConfig(),
    stage: str = "recommend",
    recall_top20_jsonl_path: Optional[Path] = None,
    recall_source: str = "offline",
    live_recall_engine: Optional[LiveRecallEngine] = None,
    neo4j_repository: Optional[BenchmarkNeo4jRepository] = None,
) -> List[BenchmarkRecommendationResponse]:
    if recall_source not in ("offline", "live"):
        raise ValueError("unknown recall_source: %s" % recall_source)
    if recall_source == "offline" and recall_top20_jsonl_path is None:
        raise ValueError("recall_top20_jsonl_path is required when recall_source=offline")
    if recall_source == "live" and live_recall_engine is None:
        raise ValueError("live_recall_engine is required when recall_source=live")

    graph: GraphData = load_graph(graph_jsonl_path)
    inputs = load_benchmark_inputs(benchmark_inputs_jsonl_path)
    if recall_source == "live":
        recall_by_case = live_recall_engine.recall_by_case(inputs, config.recall)
    else:
        recall_by_case = load_recall_topk(recall_top20_jsonl_path)

    outputs: List[BenchmarkRecommendationResponse] = []

    for case in inputs:
        case_id = case["id"]
        keywords = case["keywords"]
        retrieved = recall_by_case.get(case_id) or []
        recalled_nodes: List[RecalledNode] = apply_recall_config(retrieved, config.recall)
        recall_scores = recall_score_by_id(recalled_nodes)

        if stage == "recall":
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=[
                        {
                            "node_id": row["node_id"],
                            "label": row["label"],
                            "name": row["name"],
                        }
                        for row in recalled_nodes
                    ],
                    predicted=_empty_predicted(),
                    need_user_confirmation=False,
                    paths=[],
                    warnings=[],
                    recommendation={
                        "style_parameter_guides": [],
                        "range_recommendation": {"style_type": [], "style_type_level": []},
                    },
                )
            )
            continue

        car_style_candidates, car_type_candidates, need_user_confirmation, _meta = predict_style_and_type(
            graph=graph,
            recalled_nodes=recalled_nodes,
            prediction_config=config.prediction,
        )

        # Convert list format for router.
        style_list = [{"name": c["name"], "score": c["score"], "support": c["support"]} for c in car_style_candidates]
        type_list = [{"name": c["name"], "score": c["score"], "support": c["support"]} for c in car_type_candidates]

        type_score_map = {c["name"]: float(c["score"]) for c in car_type_candidates}
        car_level = infer_level_from_type(
            graph=graph,
            car_type_candidates=car_type_candidates,
            car_type_scores=type_score_map,
            keywords=keywords,
            prediction_config=config.prediction,
        )

        if stage == "predict":
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=[
                        {
                            "node_id": row["node_id"],
                            "label": row["label"],
                            "name": row["name"],
                        }
                        for row in recalled_nodes
                    ],
                    predicted={
                        "car_style": style_list[:2],
                        "car_type": type_list[:2],
                        "car_level": car_level,
                    },
                    need_user_confirmation=need_user_confirmation,
                    paths=[],
                    warnings=["stage_predict_only"] if need_user_confirmation else [],
                    recommendation={
                        "style_parameter_guides": [],
                        "range_recommendation": {"style_type": [], "style_type_level": []},
                    },
                )
            )
            continue

        recommendation: Dict[str, Any]
        paths: List[Dict[str, Any]] = []
        warnings: List[str] = []
        recommend_types = config.recommendation.recommend_types

        style_parameter_guides: List[Dict[str, Any]] = []
        if recommend_types_include(recommend_types, "style_guides") and style_list:
            style_parameter_guides = [
                (
                    style_guides_recommend_neo4j(repo=neo4j_repository, car_style=style["name"])
                    if neo4j_repository is not None
                    else style_guides_recommend(graph=graph, car_style=style["name"])
                )
                for style in style_list[: config.recommendation.max_styles]
            ]

        range_style_type: List[Dict[str, Any]] = []
        range_style_type_level: List[Dict[str, Any]] = []
        need_style_type = recommend_types_include(recommend_types, "style_type")
        need_style_type_level = recommend_types_include(recommend_types, "style_type_level")
        if style_list and type_list and (need_style_type or need_style_type_level):
            combos: List[tuple] = []
            for style in style_list[: config.recommendation.max_styles]:
                for car_type_candidate in type_list[: config.recommendation.max_types]:
                    combined_score = float(style["score"]) + float(car_type_candidate["score"])
                    combos.append((combined_score, style["name"], car_type_candidate["name"]))
            combos.sort(key=lambda x: (-x[0], x[1], x[2]))

            for _score, style_name, type_name in combos[: config.recommendation.max_combinations]:
                if need_style_type:
                    if neo4j_repository is not None:
                        style_type_group, _ = style_type_recommend_neo4j(
                            repo=neo4j_repository,
                            car_style=style_name,
                            car_type=type_name,
                            small_sample_threshold=config.recommendation.small_sample_threshold,
                        )
                    else:
                        style_type_group, _ = style_type_recommend(
                            graph=graph,
                            car_style=style_name,
                            car_type=type_name,
                            small_sample_threshold=config.recommendation.small_sample_threshold,
                        )
                    style_type_group["car_style"] = style_name
                    style_type_group["car_type"] = type_name
                    style_type_group["car_level"] = car_level.get("name") if car_level else None
                    range_style_type.append(style_type_group)

                if need_style_type_level and car_level and car_level.get("name"):
                    if neo4j_repository is not None:
                        style_type_level_group = style_type_level_recommend_neo4j(
                            repo=neo4j_repository,
                            car_style=style_name,
                            car_type=type_name,
                            car_level=str(car_level["name"]),
                            small_sample_threshold=config.recommendation.small_sample_threshold,
                            fallback_on_small_sample=config.recommendation.fallback_on_small_sample,
                            fallback_small_sample_threshold=config.recommendation.fallback_small_sample_threshold,
                        )
                    else:
                        style_type_level_group = style_type_level_recommend(
                            graph=graph,
                            car_style=style_name,
                            car_type=type_name,
                            car_level=str(car_level["name"]),
                            small_sample_threshold=config.recommendation.small_sample_threshold,
                            fallback_on_small_sample=config.recommendation.fallback_on_small_sample,
                            fallback_small_sample_threshold=config.recommendation.fallback_small_sample_threshold,
                        )
                    style_type_level_group["car_style"] = style_name
                    style_type_level_group["car_type"] = type_name
                    style_type_level_group["car_level"] = car_level.get("name")
                    range_style_type_level.append(style_type_level_group)

        recommendation = {
            "types": list(recommend_types),
            "style_parameter_guides": style_parameter_guides,
            "range_recommendation": {
                "style_type": range_style_type,
                "style_type_level": range_style_type_level,
            },
        }

        if style_list:
            top_style = style_list[0]["name"]
            for source_id, rs in recall_scores.items():
                for edge in graph.style_edges_by_source.get(source_id, []):
                    if edge["target"] == top_style:
                        head = graph.feature_node_by_id.get(source_id) or {
                            "node_id": source_id,
                            "label": "",
                            "name": source_id,
                        }
                        paths.append(
                            {
                                "path": "%s -> StyleAssociatedWith -> %s" % (
                                    head["name"] or source_id,
                                    top_style,
                                ),
                                "reason": edge.get("reason"),
                            }
                        )
                        break
        if type_list:
            top_type = type_list[0]["name"]
            for source_id, rs in recall_scores.items():
                for edge in graph.type_edges_by_source.get(source_id, []):
                    if edge["target"] == top_type:
                        head = graph.feature_node_by_id.get(source_id) or {
                            "node_id": source_id,
                            "label": "",
                            "name": source_id,
                        }
                        paths.append(
                            {
                                "path": "%s -> TypeAssociatedWith -> %s" % (
                                    head["name"] or source_id,
                                    top_type,
                                ),
                                "reason": edge.get("reason"),
                            }
                        )
                        break

        if need_user_confirmation:
            warnings.append("need_user_confirmation_due_to_ambiguity")
        if car_level is None:
            # keep explicit signal but do not force warning
            pass

        outputs.append(
            BenchmarkRecommendationResponse(
                id=case_id,
                input={"keywords": keywords},
                recalled_nodes=[
                    {
                        "node_id": row["node_id"],
                        "label": row["label"],
                        "name": row["name"],
                    }
                    for row in recalled_nodes
                ],
                predicted={
                    "car_style": style_list[:2],
                    "car_type": type_list[:2],
                    "car_level": car_level,
                },
                need_user_confirmation=need_user_confirmation,
                paths=paths,
                warnings=warnings,
                recommendation=recommendation,
            )
        )

    return outputs

