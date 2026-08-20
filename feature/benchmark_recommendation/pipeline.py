from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from feature.benchmark_upstream_offline.io_utils import iter_jsonl

from .config import BenchmarkRecommendationConfig
from .graph_loader import GraphData, load_graph
from .llm_predictor import load_descriptions_for_prediction, predict_with_llm
from .llm_predict_batch import PredictionTask, run_llm_predictions_concurrent, write_llm_audit_json
from feature.parameter_recommendation.common import load_llm_config
from .neo4j_repository import BenchmarkNeo4jRepository
from .live_recall_service import LiveRecallEngine
from .path_postprocess import postprocess_case_paths
from .prediction_service import infer_level_from_type, predict_from_main_paths, predict_style_and_type
from .path_morphology import inspect_path, short_rel
from .recall_service import apply_recall_config, load_recall_topk
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


def _keep_names(items: Any) -> set:
    return set(_candidate_names(items))


def _empty_predicted() -> Dict[str, Any]:
    return {"car_style": [], "car_type": [], "car_level": None}


def _candidate_names(items: Any) -> List[str]:
    names: List[str] = []
    for item in items or []:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            continue
        if name:
            names.append(name)
    return names


def _scored_candidates(items: Any) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for idx, item in enumerate(items or []):
        if isinstance(item, dict) and item.get("name"):
            scored.append(
                {
                    "name": str(item["name"]),
                    "score": float(item.get("score") or max(0.1, 1.0 - idx * 0.05)),
                    "support": int(item.get("support") or 1),
                }
            )
            continue
        if isinstance(item, str) and item.strip():
            scored.append({"name": item.strip(), "score": max(0.1, 1.0 - idx * 0.05), "support": 1})
    return scored


def _public_predicted(predicted_block: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "car_style": _candidate_names(predicted_block.get("car_style")),
        "car_type": _candidate_names(predicted_block.get("car_type")),
        "car_level": predicted_block.get("car_level"),
    }
    if predicted_block.get("reasoning") is not None:
        out["reasoning"] = predicted_block.get("reasoning")
    return out


def _public_paths(paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    display: List[Dict[str, Any]] = []
    for rec in paths or []:
        path_str = str(rec.get("path") or "").strip()
        if not path_str:
            continue
        item: Dict[str, Any] = {"path": path_str}
        if rec.get("hop_count") is not None:
            item["hop_count"] = int(rec.get("hop_count") or 0)
        display.append(item)
    return display


def load_benchmark_inputs(benchmark_inputs_jsonl: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for record in iter_jsonl(benchmark_inputs_jsonl):
        rows.append({"id": str(record.get("id")), "keywords": list(record.get("keywords") or [])})
    return rows


def load_cases_from_input_json(input_json: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    payload = json.loads(input_json.read_text(encoding="utf-8"))
    cases = list(payload.get("cases") or [])
    meta = {
        "stage": payload.get("stage"),
        "config": payload.get("config") or {},
    }
    rows: List[Dict[str, Any]] = []
    for case in cases:
        input_block = case.get("input") or {}
        rows.append(
            {
                "id": str(case.get("id")),
                "keywords": list(input_block.get("keywords") or []),
                "recalled_nodes": list(case.get("recalled_nodes") or []),
                "paths": list(case.get("paths") or []),
                "predicted": dict(case.get("predicted") or _empty_predicted()),
                "need_user_confirmation": bool(case.get("need_user_confirmation")),
            }
        )
    return rows, meta


def _paths_from_neo4j(
    recalled_nodes: List[Any],
    neo4j_repository: BenchmarkNeo4jRepository,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    node_ids = [str(row.get("node_id") or "") for row in recalled_nodes if row.get("node_id")]
    main_rows = neo4j_repository.batch_main_paths(node_ids)
    neighbor_rows = neo4j_repository.batch_neighbor_evidence(node_ids)
    return main_rows, neighbor_rows


def _inspect_paths(
    main_rows: List[Dict[str, Any]],
    neighbor_rows: List[Dict[str, Any]],
    *,
    max_hops: int,
    include_neighbor: bool,
) -> List[Dict[str, Any]]:
    paths: List[Dict[str, Any]] = []
    seen = set()
    for rec in main_rows:
        hops = int(rec.get("hops") or rec.get("hop_count") or 0)
        if hops < 1 or hops > int(max_hops):
            continue
        path_str = inspect_path(str(rec.get("path") or ""))
        if not path_str or path_str in seen:
            continue
        seen.add(path_str)
        paths.append(
            {
                "path": path_str,
                "template": "aesthetic_to_main_combined",
                "hop_count": hops,
            }
        )
    if include_neighbor and int(max_hops) >= 1:
        for rec in neighbor_rows:
            path_str = inspect_path(
                "%s --%s--> %s"
                % (rec.get("recalled_name"), short_rel(str(rec.get("rel") or "")), rec.get("neighbor_name"))
            )
            if not path_str or path_str in seen:
                continue
            seen.add(path_str)
            paths.append({"path": path_str, "template": "aesthetic_to_neighbor_evidence", "hop_count": 1})
    return paths


def _maybe_postprocess_paths(
    recalled_nodes: List[Dict[str, Any]],
    paths: List[Dict[str, Any]],
    config: BenchmarkRecommendationConfig,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not config.path.enable_postprocess:
        return paths, None
    display, stats = postprocess_case_paths(
        recalled_nodes,
        paths,
        max_style_heads=config.path.max_style_heads,
        max_type_heads=config.path.max_type_heads,
    )
    return display, stats


def _display_paths(
    main_rows: List[Dict[str, Any]],
    neighbor_rows: List[Dict[str, Any]],
    keep_styles: set,
    keep_types: set,
) -> List[Dict[str, Any]]:
    paths: List[Dict[str, Any]] = []
    seen = set()
    for rec in main_rows:
        head = rec.get("head_name")
        kind = rec.get("head_kind")
        if kind == "style" and keep_styles and head not in keep_styles:
            continue
        if kind == "type" and keep_types and head not in keep_types:
            continue
        path_str = rec.get("path") or ""
        if not path_str or path_str in seen:
            continue
        seen.add(path_str)
        paths.append(
            {
                "path": inspect_path(str(path_str)),
                "template": "aesthetic_to_main_combined",
                "hop_count": int(rec.get("hops") or 0),
            }
        )
    for rec in neighbor_rows:
        rel_short = short_rel(str(rec.get("rel") or ""))
        path_str = "%s --%s--> %s" % (rec.get("recalled_name"), rel_short, rec.get("neighbor_name"))
        path_str = inspect_path(path_str)
        if path_str in seen:
            continue
        seen.add(path_str)
        paths.append({"path": path_str, "template": "aesthetic_to_neighbor_evidence", "hop_count": 1})
    return paths


def _llm_result_to_predicted_block(llm_result) -> Tuple[Dict[str, Any], bool, List[str], Optional[Dict[str, Any]]]:
    warnings: List[str] = []
    if llm_result.prediction_mode == "vote_fallback":
        warnings.append("llm_invalid_output_vote_fallback")
    return (
        {
            "car_style": _candidate_names(llm_result.car_style[:2]),
            "car_type": _candidate_names(llm_result.car_type[:2]),
            "car_level": llm_result.car_level,
            "reasoning": llm_result.reasoning,
        },
        llm_result.need_user_confirmation,
        warnings,
        llm_result.rag_context,
    )


def _run_prediction(
    *,
    case_id: str,
    keywords: List[str],
    recalled_nodes: List[Dict[str, Any]],
    paths: List[Dict[str, Any]],
    main_rows: List[Dict[str, Any]],
    config: BenchmarkRecommendationConfig,
    graph: Optional[GraphData],
    env_path: Path,
    node_descriptions: Dict[str, str],
) -> Tuple[Dict[str, Any], bool, List[str], Optional[Dict[str, Any]]]:
    warnings: List[str] = []

    if config.prediction_mode == "vote":
        if main_rows:
            style_list, type_list, need_confirm, _meta = predict_from_main_paths(
                path_rows=main_rows,
                recalled_nodes=recalled_nodes,
                prediction_config=config.prediction,
            )
        elif graph is not None:
            style_list, type_list, need_confirm, _meta = predict_style_and_type(
                graph=graph,
                recalled_nodes=recalled_nodes,
                prediction_config=config.prediction,
            )
            warnings.append("path_morphology_requires_neo4j_fallback_one_hop_vote")
        else:
            style_list, type_list, need_confirm = [], [], False
            warnings.append("vote_prediction_requires_paths_or_graph")
        type_score_map = {c["name"]: float(c["score"]) for c in type_list}
        car_level = (
            infer_level_from_type(
                graph=graph,
                car_type_candidates=type_list,
                car_type_scores=type_score_map,
                keywords=keywords,
                prediction_config=config.prediction,
            )
            if graph is not None
            else None
        )
        return (
            {
                "car_style": _candidate_names(style_list[:2]),
                "car_type": _candidate_names(type_list[:2]),
                "car_level": car_level,
            },
            need_confirm,
            warnings,
            None,
        )

    llm_result = predict_with_llm(
        keywords=keywords,
        recalled_nodes=recalled_nodes,
        paths=paths,
        descriptions=node_descriptions,
        llm_config=config.llm_prediction,
        env_path=env_path,
        graph=graph,
        path_rows=main_rows,
        prediction_config=config.prediction,
    )
    if llm_result.prediction_mode == "vote_fallback":
        warnings.append("llm_invalid_output_vote_fallback")
    return (
        {
            "car_style": _candidate_names(llm_result.car_style[:2]),
            "car_type": _candidate_names(llm_result.car_type[:2]),
            "car_level": llm_result.car_level,
            "reasoning": llm_result.reasoning,
        },
        llm_result.need_user_confirmation,
        warnings,
        llm_result.rag_context,
    )


def _build_recommendation(
    *,
    style_list: List[Dict[str, Any]],
    type_list: List[Dict[str, Any]],
    car_level: Any,
    config: BenchmarkRecommendationConfig,
    graph: Optional[GraphData],
    neo4j_repository: Optional[BenchmarkNeo4jRepository],
) -> Dict[str, Any]:
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

    return {
        "types": list(recommend_types),
        "style_parameter_guides": style_parameter_guides,
        "range_recommendation": {
            "style_type": range_style_type,
            "style_type_level": range_style_type_level,
        },
    }


def _recalled_nodes_payload(recalled_nodes: List[Any], *, include_score: bool = False) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in recalled_nodes:
        item = {
            "node_id": row["node_id"],
            "label": row["label"],
            "name": row["name"],
        }
        if include_score:
            item["score"] = round(float(row.get("score") or 0.0), 6)
        rows.append(item)
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
    input_json_path: Optional[Path] = None,
    env_path: Path = Path("feature/.env"),
    node_descriptions: Optional[Dict[str, str]] = None,
) -> List[BenchmarkRecommendationResponse]:
    normalized_stage = {"path": "evidence"}.get(stage, stage)
    if normalized_stage not in ("recall", "evidence", "predict", "recommend"):
        raise ValueError("unknown stage: %s" % stage)
    if recall_source not in ("offline", "live"):
        raise ValueError("unknown recall_source: %s" % recall_source)

    preloaded_cases: Optional[List[Dict[str, Any]]] = None
    if input_json_path is not None:
        preloaded_cases, _meta = load_cases_from_input_json(input_json_path)
        if normalized_stage == "predict" and not any(c.get("paths") for c in preloaded_cases):
            raise ValueError("predict stage requires paths in --input-json (run evidence stage first)")
        if normalized_stage == "recommend" and not any(c.get("predicted") for c in preloaded_cases):
            raise ValueError("recommend stage requires predicted in --input-json (run predict stage first)")

    need_neo4j = normalized_stage in ("evidence", "predict", "recommend") and preloaded_cases is None
    if need_neo4j and neo4j_repository is None and normalized_stage != "recall":
        if normalized_stage == "recommend" and preloaded_cases is not None:
            pass
        elif normalized_stage in ("evidence", "predict"):
            raise ValueError("stage %s requires a Neo4j repository when not using --input-json" % normalized_stage)

    graph: Optional[GraphData] = None
    if normalized_stage in ("predict", "recommend"):
        graph = load_graph(graph_jsonl_path)

    descriptions = node_descriptions
    if descriptions is None and normalized_stage in ("predict", "recommend") and config.prediction_mode == "llm":
        features_path = Path(config.llm_prediction.features_jsonl)
        if not features_path.is_absolute():
            features_path = Path.cwd() / features_path
        descriptions = load_descriptions_for_prediction(features_path)

    inputs = load_benchmark_inputs(benchmark_inputs_jsonl_path)
    input_by_id = {row["id"]: row for row in inputs}

    recall_by_case: Dict[str, List[Dict[str, Any]]] = {}
    if preloaded_cases is None and normalized_stage != "recommend":
        if recall_source == "offline" and recall_top20_jsonl_path is None:
            raise ValueError("recall_top20_jsonl_path is required when recall_source=offline")
        if recall_source == "live" and live_recall_engine is None:
            raise ValueError("live_recall_engine is required when recall_source=live")
        if recall_source == "live":
            recall_by_case = live_recall_engine.recall_by_case(inputs, config.recall)
        else:
            recall_by_case = load_recall_topk(recall_top20_jsonl_path)

    case_rows: List[Dict[str, Any]]
    if preloaded_cases is not None:
        case_rows = preloaded_cases
    else:
        case_rows = [{"id": row["id"], "keywords": row["keywords"]} for row in inputs]

    outputs: List[BenchmarkRecommendationResponse] = []
    pending_llm: List[Dict[str, Any]] = []
    use_llm_batch = (
        normalized_stage in ("predict", "recommend") and config.prediction_mode == "llm"
    )

    for case in case_rows:
        case_id = case["id"]
        keywords = case.get("keywords") or input_by_id.get(case_id, {}).get("keywords") or []

        if preloaded_cases is not None and normalized_stage == "recommend":
            predicted = _public_predicted(case.get("predicted") or _empty_predicted())
            style_list = _scored_candidates(predicted.get("car_style") or [])
            type_list = _scored_candidates(predicted.get("car_type") or [])
            car_level = predicted.get("car_level")
            recommendation = _build_recommendation(
                style_list=style_list,
                type_list=type_list,
                car_level=car_level,
                config=config,
                graph=graph,
                neo4j_repository=neo4j_repository,
            )
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=case.get("recalled_nodes") or [],
                    predicted=predicted,
                    need_user_confirmation=bool(case.get("need_user_confirmation")),
                    paths=_public_paths(case.get("paths") or []),
                    warnings=[],
                    recommendation=recommendation,
                )
            )
            continue

        recalled_nodes: List[RecalledNode]
        if preloaded_cases is not None:
            recalled_nodes = list(case.get("recalled_nodes") or [])
        else:
            retrieved = recall_by_case.get(case_id) or []
            recalled_nodes = apply_recall_config(retrieved, config.recall)

        if normalized_stage == "recall":
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=_recalled_nodes_payload(recalled_nodes),
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

        main_rows: List[Dict[str, Any]] = []
        neighbor_rows: List[Dict[str, Any]] = []
        paths: List[Dict[str, Any]]
        postprocess_stats: Optional[Dict[str, Any]] = None

        if preloaded_cases is not None:
            paths = list(case.get("paths") or [])
            if neo4j_repository is not None:
                main_rows, neighbor_rows = _paths_from_neo4j(recalled_nodes, neo4j_repository)
        else:
            main_rows, neighbor_rows = _paths_from_neo4j(recalled_nodes, neo4j_repository)
            paths = _inspect_paths(
                main_rows,
                neighbor_rows,
                max_hops=config.path.max_hops,
                include_neighbor=config.path.include_neighbor,
            )
            paths, postprocess_stats = _maybe_postprocess_paths(
                _recalled_nodes_payload(recalled_nodes, include_score=True),
                paths,
                config,
            )

        if normalized_stage == "evidence":
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=_recalled_nodes_payload(recalled_nodes, include_score=True),
                    paths=paths,
                    postprocess=postprocess_stats,
                )
            )
            continue

        if use_llm_batch:
            pending_llm.append(
                {
                    "task": PredictionTask(
                        case_id=case_id,
                        keywords=keywords,
                        recalled_nodes=list(recalled_nodes),
                        paths=paths,
                        main_rows=main_rows,
                        neighbor_rows=neighbor_rows,
                        postprocess_stats=postprocess_stats,
                    ),
                    "keywords": keywords,
                    "recalled_nodes": recalled_nodes,
                    "paths": paths,
                    "main_rows": main_rows,
                    "neighbor_rows": neighbor_rows,
                    "postprocess_stats": postprocess_stats,
                }
            )
            continue

        predicted_block, need_confirm, warnings, _rag_context = _run_prediction(
            case_id=case_id,
            keywords=keywords,
            recalled_nodes=recalled_nodes,
            paths=paths,
            main_rows=main_rows,
            config=config,
            graph=graph,
            env_path=env_path,
            node_descriptions=descriptions or {},
        )
        style_list = _scored_candidates(predicted_block.get("car_style") or [])
        type_list = _scored_candidates(predicted_block.get("car_type") or [])
        car_level = predicted_block.get("car_level")

        display_paths = _public_paths(paths)
        if style_list or type_list:
            display_paths = _public_paths(
                _display_paths(
                    main_rows,
                    neighbor_rows,
                    keep_styles=_keep_names(style_list),
                    keep_types=_keep_names(type_list),
                )
                or paths
            )

        if normalized_stage == "predict":
            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=_recalled_nodes_payload(recalled_nodes),
                    predicted=_public_predicted(predicted_block),
                    need_user_confirmation=need_confirm,
                    paths=display_paths,
                    warnings=warnings + (["stage_predict_only"] if need_confirm else []),
                    recommendation={
                        "style_parameter_guides": [],
                        "range_recommendation": {"style_type": [], "style_type_level": []},
                    },
                    postprocess=postprocess_stats,
                )
            )
            continue

        recommendation = _build_recommendation(
            style_list=style_list,
            type_list=type_list,
            car_level=car_level,
            config=config,
            graph=graph,
            neo4j_repository=neo4j_repository,
        )
        if need_confirm:
            warnings.append("need_user_confirmation_due_to_ambiguity")

        outputs.append(
            BenchmarkRecommendationResponse(
                id=case_id,
                input={"keywords": keywords},
                recalled_nodes=_recalled_nodes_payload(recalled_nodes),
                predicted=_public_predicted(predicted_block),
                need_user_confirmation=need_confirm,
                paths=display_paths,
                warnings=warnings,
                recommendation=recommendation,
                postprocess=postprocess_stats,
            )
        )

    if pending_llm:
        api_key, base_url, model_name = load_llm_config(env_path, model_override=config.llm_prediction.model or None)
        del api_key, base_url  # credentials loaded once for model name resolution
        tasks = [item["task"] for item in pending_llm]
        llm_results, audit_calls = run_llm_predictions_concurrent(
            tasks,
            config=config,
            env_path=env_path,
            descriptions=descriptions or {},
            graph=graph,
            model_name=model_name,
        )
        if config.llm_prediction.audit_json:
            write_llm_audit_json(
                Path(config.llm_prediction.audit_json),
                audit_calls=audit_calls,
                config=config,
                model_name=model_name,
                stage=normalized_stage,
            )

        for item in pending_llm:
            case_id = item["task"].case_id
            keywords = item["keywords"]
            recalled_nodes = item["recalled_nodes"]
            paths = item["paths"]
            main_rows = item["main_rows"]
            neighbor_rows = item["neighbor_rows"]
            postprocess_stats = item["postprocess_stats"]

            llm_result = llm_results.get(case_id)
            if llm_result is None:
                predicted_block = {
                    "car_style": [],
                    "car_type": [],
                    "car_level": None,
                    "reasoning": "",
                }
                need_confirm = False
                warnings = ["llm_call_failed"]
            else:
                predicted_block, need_confirm, warnings, _rag_context = _llm_result_to_predicted_block(llm_result)

            style_list = _scored_candidates(predicted_block.get("car_style") or [])
            type_list = _scored_candidates(predicted_block.get("car_type") or [])
            car_level = predicted_block.get("car_level")

            display_paths = _public_paths(paths)
            if style_list or type_list:
                display_paths = _public_paths(
                    _display_paths(
                        main_rows,
                        neighbor_rows,
                        keep_styles=_keep_names(style_list),
                        keep_types=_keep_names(type_list),
                    )
                    or paths
                )

            if normalized_stage == "predict":
                outputs.append(
                    BenchmarkRecommendationResponse(
                        id=case_id,
                        input={"keywords": keywords},
                        recalled_nodes=_recalled_nodes_payload(recalled_nodes),
                        predicted=_public_predicted(predicted_block),
                        need_user_confirmation=need_confirm,
                        paths=display_paths,
                        warnings=warnings + (["stage_predict_only"] if need_confirm else []),
                        recommendation={
                            "style_parameter_guides": [],
                            "range_recommendation": {"style_type": [], "style_type_level": []},
                        },
                        postprocess=postprocess_stats,
                    )
                )
                continue

            recommendation = _build_recommendation(
                style_list=style_list,
                type_list=type_list,
                car_level=car_level,
                config=config,
                graph=graph,
                neo4j_repository=neo4j_repository,
            )
            if need_confirm:
                warnings.append("need_user_confirmation_due_to_ambiguity")

            outputs.append(
                BenchmarkRecommendationResponse(
                    id=case_id,
                    input={"keywords": keywords},
                    recalled_nodes=_recalled_nodes_payload(recalled_nodes),
                    predicted=_public_predicted(predicted_block),
                    need_user_confirmation=need_confirm,
                    paths=display_paths,
                    warnings=warnings,
                    recommendation=recommendation,
                    postprocess=postprocess_stats,
                )
            )

    return outputs
