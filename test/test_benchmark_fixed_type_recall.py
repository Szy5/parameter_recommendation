import unittest
from pathlib import Path

import numpy as np

from feature.benchmark_fixed_type_recall.run_recall import (
    ALLOWED_LABELS,
    build_document_text,
    build_query_text,
    prepare_corpus,
    prepare_queries,
    rank_top_k,
    summarize_recall,
)


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl"
BENCHMARK = ROOT / "benchmark/benchmark_100_inputs.jsonl"


class TextContractTests(unittest.TestCase):
    def test_document_text_excludes_label_and_omits_empty_description(self):
        record = {"node_id": "n1", "labels": [ALLOWED_LABELS[0]], "name": " 高坐姿 ", "description": "  "}
        self.assertEqual(build_document_text(record), "名称：高坐姿")
        record["description"] = " 更好的视野\n与通过性 "
        self.assertEqual(build_document_text(record), "名称：高坐姿\n描述：更好的视野 与通过性")
        self.assertNotIn(ALLOWED_LABELS[0], build_document_text(record))

    def test_query_preserves_order_and_rejects_empty(self):
        self.assertEqual(build_query_text(["高坐姿", "短前悬"]), "高坐姿；短前悬")
        with self.assertRaises(ValueError):
            build_query_text([])
        with self.assertRaises(ValueError):
            build_query_text(["高坐姿", ""])


class RankingAndSummaryTests(unittest.TestCase):
    def test_ranking_is_score_desc_then_node_id(self):
        scores = np.asarray([0.8, 0.9, 0.9, 0.1], dtype=np.float32)
        node_ids = ["n4", "n2", "n1", "n3"]
        self.assertEqual(rank_top_k(scores, node_ids, 3), [2, 1, 0])

    def test_summary_counts_unique_nodes_and_thresholds(self):
        rows = []
        for case_index in range(2):
            nodes = []
            for rank in range(1, 21):
                nodes.append({
                    "rank": rank,
                    "node_id": "n%d" % (rank if case_index == 0 else rank + 10),
                    "label": ALLOWED_LABELS[0],
                    "score": round(1.0 - rank / 100.0, 6),
                    "matched_keywords": ["x"] if rank == 1 else [],
                })
            rows.append({"id": "B%d" % case_index, "retrieved_nodes": nodes})
        summary = summarize_recall(rows, corpus_size=100, thresholds=(0.9,))
        self.assertEqual(summary["by_k"]["20"]["retrieval_slots"], 40)
        self.assertEqual(summary["by_k"]["20"]["unique_nodes"], 30)
        self.assertEqual(summary["by_threshold"]["0.9"]["retrieval_slots"], 20)
        self.assertEqual(summary["by_threshold"]["0.9"]["cases_with_at_least_one"], 2)


class RealInputContractTests(unittest.TestCase):
    def test_real_fixed_corpus_and_benchmark_contract(self):
        corpus = prepare_corpus(FEATURES)
        queries = prepare_queries(BENCHMARK)
        self.assertEqual(len(corpus), 9649)
        self.assertEqual(len({row["node_id"] for row in corpus}), 9649)
        self.assertEqual({row["label"] for row in corpus}, set(ALLOWED_LABELS))
        self.assertEqual(len(queries), 100)
        self.assertEqual(len({row["id"] for row in queries}), 100)
        self.assertTrue(all(row["query_text"] for row in queries))


if __name__ == "__main__":
    unittest.main()
