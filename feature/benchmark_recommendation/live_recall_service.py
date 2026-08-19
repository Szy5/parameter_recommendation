"""Online BGE-M3 recall for Benchmark keywords."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from feature.benchmark_fixed_type_recall.run_recall import (
    build_query_text,
    lexical_matches,
    prepare_corpus,
    rank_top_k,
)

from .config import LiveRecallConfig, RecallConfig


def _pool_size_for_recall(recall_config: RecallConfig, live_config: LiveRecallConfig) -> int:
    if recall_config.mode == "threshold":
        return recall_config.max_candidates
    return max(recall_config.top_k, live_config.pool_size, recall_config.max_candidates)


def build_retrieved_pool_from_scores(
    scores: np.ndarray,
    corpus: Sequence[Dict[str, Any]],
    keywords: Sequence[str],
    recall_config: RecallConfig,
    live_config: LiveRecallConfig,
) -> List[Dict[str, Any]]:
    """Build a retrieved_nodes list compatible with apply_recall_config."""
    if scores.ndim != 1 or scores.shape[0] != len(corpus):
        raise ValueError("score vector and corpus size mismatch")

    node_ids = [row["node_id"] for row in corpus]
    if recall_config.mode == "threshold":
        candidates = [
            (index, float(scores[index]))
            for index in range(len(corpus))
            if float(scores[index]) >= recall_config.min_score
        ]
        candidates.sort(key=lambda item: (-item[1], node_ids[item[0]]))
        indices = [index for index, _score in candidates[: recall_config.max_candidates]]
    else:
        pool_size = min(_pool_size_for_recall(recall_config, live_config), len(corpus))
        indices = rank_top_k(scores, node_ids, pool_size)

    retrieved: List[Dict[str, Any]] = []
    for rank, corpus_index in enumerate(indices, 1):
        node = corpus[corpus_index]
        retrieved.append(
            {
                "rank": rank,
                "node_id": node["node_id"],
                "label": node["label"],
                "name": node["name"],
                "description": node.get("description") or "",
                "score": round(float(scores[corpus_index]), 6),
                "matched_keywords": lexical_matches(keywords, node),
            }
        )
    return retrieved


@dataclass
class LiveRecallEngine:
    corpus: List[Dict[str, Any]]
    node_embeddings: np.ndarray
    live_config: LiveRecallConfig
    _model: Any = field(default=None, repr=False)

    @classmethod
    def from_config(cls, live_config: LiveRecallConfig) -> "LiveRecallEngine":
        import torch
        from sentence_transformers import SentenceTransformer

        features_path = Path(live_config.features_jsonl)
        corpus = prepare_corpus(features_path)
        device = live_config.device or ("cuda" if torch.cuda.is_available() else "cpu")
        model = SentenceTransformer(
            live_config.model,
            revision=live_config.revision,
            device=device,
            trust_remote_code=False,
        )
        model.max_seq_length = live_config.max_seq_length
        node_embeddings = model.encode(
            [row["text"] for row in corpus],
            batch_size=live_config.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
        ).astype(np.float32, copy=False)
        if node_embeddings.shape[0] != len(corpus):
            raise RuntimeError("node embedding row count mismatch")
        if not np.isfinite(node_embeddings).all():
            raise RuntimeError("node embeddings contain NaN or Infinity")
        return cls(corpus=corpus, node_embeddings=node_embeddings, live_config=live_config, _model=model)

    def _encode_queries(self, query_texts: Sequence[str]) -> np.ndarray:
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            device = self.live_config.device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._model = SentenceTransformer(
                self.live_config.model,
                revision=self.live_config.revision,
                device=device,
                trust_remote_code=False,
            )
            self._model.max_seq_length = self.live_config.max_seq_length
        return self._model.encode(
            list(query_texts),
            batch_size=self.live_config.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
        ).astype(np.float32, copy=False)

    def recall_by_case(
        self,
        cases: Sequence[Dict[str, Any]],
        recall_config: RecallConfig,
    ) -> Dict[str, List[Dict[str, Any]]]:
        query_texts = [build_query_text(case["keywords"]) for case in cases]
        query_embeddings = self._encode_queries(query_texts)
        scores_matrix = query_embeddings @ self.node_embeddings.T

        output: Dict[str, List[Dict[str, Any]]] = {}
        for case_index, case in enumerate(cases):
            output[str(case["id"])] = build_retrieved_pool_from_scores(
                scores=scores_matrix[case_index],
                corpus=self.corpus,
                keywords=case["keywords"],
                recall_config=recall_config,
                live_config=self.live_config,
            )
        return output
