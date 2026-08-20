"""LLM-based car style / type prediction from RAG path context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from feature.benchmark_upstream_offline.constants import CAR_TYPES, STYLES
from feature.parameter_recommendation.common import chat_completion, extract_json_object, load_llm_config

from .config import LLMPredictionConfig, PredictionConfig
from .path_narrative import build_case_rag_context, load_node_descriptions
from .prediction_service import infer_level_from_type
from .schemas import CarLevel, ScoredCandidate


SYSTEM_PROMPT = """你是资深汽车造型与产品定义专家，负责根据用户关键词和知识图谱路径证据，预测最合适的汽车风格与汽车车型。

你必须只从下列封闭词表中选择答案：
- 汽车风格（car_style，可多选 1-2 个）：%s
- 汽车车型（car_type，可多选 1-2 个）：%s

规则：
1. 优先依据与用户关键词语义最匹配的路径证据，忽略与用户意图明显冲突的风格/车型头。
2. 证据不足时不要猜测：对应维度必须输出空数组 []。可以只预测风格、只预测车型，或两者都不预测。
3. 以下情况视为证据不足：召回节点或路径过少、路径头与关键词明显无关、关键词与路径互相冲突、无法在封闭词表中找到有依据的选项。
4. 若证据足以支持预测，但多个候选难以区分，设置 need_user_confirmation=true 并保留 top-2；若连 top-1 都不确定，则该维度输出空数组。
5. car_level 仅在关键词或证据明确指向级别时填写（A0/A/B/C/D 或 A级/B级等），否则为 null。
6. 输出必须是单个 JSON 对象，不要 Markdown，不要额外文字。

输出 JSON Schema：
{
  "car_style": [],
  "car_type": [],
  "car_level": null,
  "need_user_confirmation": false,
  "reasoning": "简要中文推理：说明依据了哪些路径，或为什么证据不足而留空"
}
""" % ("、".join(STYLES), "、".join(CAR_TYPES))


@dataclass(frozen=True)
class LLMPredictionResult:
    car_style: List[ScoredCandidate]
    car_type: List[ScoredCandidate]
    car_level: Optional[CarLevel]
    need_user_confirmation: bool
    reasoning: str
    confidence: float
    rag_context: Dict[str, Any]
    prediction_mode: str
    raw_response: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


def _to_candidates(names: Sequence[str], default_score: float = 1.0) -> List[ScoredCandidate]:
    out: List[ScoredCandidate] = []
    for idx, name in enumerate(names):
        if not name or name not in STYLES and name not in CAR_TYPES:
            continue
        score = max(0.1, default_score - idx * 0.05)
        out.append({"name": str(name), "score": float(score), "support": 1})
    return out


def _normalize_llm_payload(payload: Dict[str, Any]) -> Tuple[List[str], List[str], Optional[str], bool, str, float]:
    styles = [str(x).strip() for x in (payload.get("car_style") or []) if str(x).strip()]
    types = [str(x).strip() for x in (payload.get("car_type") or []) if str(x).strip()]
    styles = [s for s in styles if s in STYLES][:2]
    types = [t for t in types if t in CAR_TYPES][:2]
    level_raw = payload.get("car_level")
    level = str(level_raw).strip() if level_raw not in (None, "", "null") else None
    need_confirm = bool(payload.get("need_user_confirmation"))
    reasoning = str(payload.get("reasoning") or "").strip()
    confidence = float(payload.get("confidence") or 0.0)
    return styles, types, level, need_confirm, reasoning, confidence


def predict_with_llm(
    *,
    keywords: Sequence[str],
    recalled_nodes: Sequence[Dict[str, Any]],
    paths: Sequence[Dict[str, Any]],
    descriptions: Dict[str, str],
    llm_config: LLMPredictionConfig,
    env_path: Path,
    graph=None,
    path_rows: Optional[Sequence[Dict[str, Any]]] = None,
    prediction_config: Optional[PredictionConfig] = None,
) -> LLMPredictionResult:
    rag_context = build_case_rag_context(
        keywords=keywords,
        recalled_nodes=recalled_nodes,
        paths=paths,
        descriptions=descriptions,
        max_paths=llm_config.max_paths_in_context,
    )
    api_key, base_url, model = load_llm_config(env_path, model_override=llm_config.model or None)
    user_prompt = rag_context["context_text"]
    text, usage = chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=llm_config.temperature,
        timeout=llm_config.timeout_seconds,
    )
    payload = extract_json_object(text)
    styles, types, level_name, need_confirm, reasoning, confidence = _normalize_llm_payload(payload)

    car_level: Optional[CarLevel] = None
    if level_name and graph is not None and prediction_config is not None:
        type_cands = _to_candidates(types)
        type_scores = {c["name"]: c["score"] for c in type_cands}
        car_level = infer_level_from_type(
            graph=graph,
            car_type_candidates=type_cands,
            car_type_scores=type_scores,
            keywords=list(keywords),
            prediction_config=prediction_config,
        )
    elif level_name:
        car_level = {"name": level_name, "source": "llm_explicit"}

    return LLMPredictionResult(
        car_style=_to_candidates(styles, confidence or 1.0),
        car_type=_to_candidates(types, confidence or 1.0),
        car_level=car_level,
        need_user_confirmation=need_confirm,
        reasoning=reasoning,
        confidence=confidence,
        rag_context=rag_context,
        prediction_mode="llm",
        raw_response=text,
        usage=usage,
    )


def load_descriptions_for_prediction(features_jsonl: Path) -> Dict[str, str]:
    return load_node_descriptions(features_jsonl)
