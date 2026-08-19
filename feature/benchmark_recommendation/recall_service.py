from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from feature.benchmark_upstream_offline.io_utils import iter_jsonl

from .config import RecallConfig
from .schemas import RecalledNode


def load_recall_topk(recall_top20_jsonl: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load `recall_top20.jsonl` produced by feature/benchmark_fixed_type_recall/run_recall.py."""
    output: Dict[str, List[Dict[str, Any]]] = {}
    for record in iter_jsonl(recall_top20_jsonl):
        case_id = str(record.get("id"))
        output[case_id] = list(record.get("retrieved_nodes") or [])
    return output


def apply_recall_config(
    retrieved_nodes: List[Dict[str, Any]], recall_config: RecallConfig
) -> List[RecalledNode]:
    # Input nodes come sorted by rank asc.
    nodes = list(retrieved_nodes)

    if recall_config.mode == "top_k":
        nodes = nodes[: recall_config.top_k]
    elif recall_config.mode == "threshold":
        nodes = [node for node in nodes if float(node.get("score") or 0.0) >= recall_config.min_score]
        nodes = nodes[: recall_config.max_candidates]
    elif recall_config.mode == "top_k_and_threshold":
        nodes = nodes[: recall_config.top_k]
        nodes = [node for node in nodes if float(node.get("score") or 0.0) >= recall_config.min_score]
    else:
        raise ValueError("unknown recall mode: %s" % recall_config.mode)

    result: List[RecalledNode] = []
    for node in nodes:
        result.append(
            RecalledNode(
                node_id=str(node.get("node_id") or ""),
                label=str(node.get("label") or ""),
                name=str(node.get("name") or ""),
                score=float(node.get("score") or 0.0),
                rank=int(node.get("rank") or 0),
                matched_keywords=list(node.get("matched_keywords") or []),
            )
        )
    return result


def recall_score_by_id(recalled_nodes: Iterable[RecalledNode]) -> Dict[str, float]:
    return {row["node_id"]: float(row["score"]) for row in recalled_nodes}

