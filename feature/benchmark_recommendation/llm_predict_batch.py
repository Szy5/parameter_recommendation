"""Concurrent LLM prediction with audit logging and console progress."""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import BenchmarkRecommendationConfig, PredictionConfig
from .graph_loader import GraphData
from .llm_predictor import LLMPredictionResult, SYSTEM_PROMPT, predict_with_llm


@dataclass
class PredictionTask:
    case_id: str
    keywords: List[str]
    recalled_nodes: List[Dict[str, Any]]
    paths: List[Dict[str, Any]]
    main_rows: List[Dict[str, Any]]
    neighbor_rows: List[Dict[str, Any]]
    postprocess_stats: Optional[Dict[str, Any]]


def _predict_one_task(
    task: PredictionTask,
    *,
    descriptions: Dict[str, str],
    llm_config,
    env_path: Path,
    graph: Optional[GraphData],
    prediction_config: PredictionConfig,
    model_name: str,
) -> Tuple[str, LLMPredictionResult, Dict[str, Any]]:
    started = time.time()
    result = predict_with_llm(
        keywords=task.keywords,
        recalled_nodes=task.recalled_nodes,
        paths=task.paths,
        descriptions=descriptions,
        llm_config=llm_config,
        env_path=env_path,
        graph=graph,
        path_rows=task.main_rows,
        prediction_config=prediction_config,
    )

    latency_ms = int((time.time() - started) * 1000)
    user_prompt = (result.rag_context or {}).get("context_text")
    audit = {
        "case_id": task.case_id,
        "model": model_name,
        "latency_ms": latency_ms,
        "input": {
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "keywords": list(task.keywords),
            "path_count": len(task.paths),
            "recalled_count": len(task.recalled_nodes),
        },
        "output": {
            "raw_response": result.raw_response,
            "parsed": {
                "car_style": [c["name"] for c in result.car_style],
                "car_type": [c["name"] for c in result.car_type],
                "car_level": result.car_level,
                "need_user_confirmation": result.need_user_confirmation,
                "reasoning": result.reasoning,
                "confidence": result.confidence,
                "prediction_mode": result.prediction_mode,
            },
            "prediction_mode": result.prediction_mode,
            "error": None,
        },
        "usage": dict(result.usage or {}),
    }
    return task.case_id, result, audit


def run_llm_predictions_concurrent(
    tasks: Sequence[PredictionTask],
    *,
    config: BenchmarkRecommendationConfig,
    env_path: Path,
    descriptions: Dict[str, str],
    graph: Optional[GraphData],
    model_name: str,
) -> Tuple[Dict[str, LLMPredictionResult], List[Dict[str, Any]]]:
    if not tasks:
        return {}, []

    llm_config = config.llm_prediction
    workers = max(1, int(llm_config.workers))
    total = len(tasks)
    completed = 0
    lock = threading.Lock()
    results: Dict[str, LLMPredictionResult] = {}
    audit_calls: List[Dict[str, Any]] = []

    if llm_config.show_progress:
        print("[LLM predict] starting %d cases, workers=%d" % (total, workers), file=sys.stderr, flush=True)

    def _run(task: PredictionTask) -> Tuple[str, LLMPredictionResult, Dict[str, Any]]:
        return _predict_one_task(
            task,
            descriptions=descriptions,
            llm_config=llm_config,
            env_path=env_path,
            graph=graph,
            prediction_config=config.prediction,
            model_name=model_name,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            case_id = task.case_id
            try:
                cid, result, audit = future.result()
                results[cid] = result
                audit_calls.append(audit)
                status = result.prediction_mode
            except Exception as exc:  # noqa: BLE001
                audit_calls.append(
                    {
                        "case_id": case_id,
                        "model": model_name,
                        "latency_ms": 0,
                        "input": {
                            "system_prompt": SYSTEM_PROMPT,
                            "user_prompt": None,
                            "keywords": list(task.keywords),
                            "path_count": len(task.paths),
                            "recalled_count": len(task.recalled_nodes),
                        },
                        "output": {
                            "raw_response": None,
                            "parsed": None,
                            "prediction_mode": "error",
                            "error": str(exc),
                        },
                        "usage": {},
                    }
                )
                status = "error"

            with lock:
                completed += 1
                if llm_config.show_progress:
                    print(
                        "[LLM predict] %d/%d %s done (%s)" % (completed, total, case_id, status),
                        file=sys.stderr,
                        flush=True,
                    )

    audit_calls.sort(key=lambda row: next(i for i, t in enumerate(tasks) if t.case_id == row["case_id"]))
    return results, audit_calls


def write_llm_audit_json(
    audit_path: Path,
    *,
    audit_calls: Sequence[Dict[str, Any]],
    config: BenchmarkRecommendationConfig,
    model_name: str,
    stage: str,
) -> None:
    payload = {
        "stage": "predict_llm_audit",
        "pipeline_stage": stage,
        "model": model_name,
        "call_count": len(audit_calls),
        "config": {
            "workers": config.llm_prediction.workers,
            "temperature": config.llm_prediction.temperature,
            "timeout_seconds": config.llm_prediction.timeout_seconds,
            "max_paths_in_context": config.llm_prediction.max_paths_in_context,
        },
        "calls": list(audit_calls),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if config.llm_prediction.show_progress:
        print("[LLM predict] audit log -> %s (%d calls)" % (audit_path, len(audit_calls)), file=sys.stderr, flush=True)
