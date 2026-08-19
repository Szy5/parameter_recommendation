import json
import tempfile
import unittest
from pathlib import Path

from feature.benchmark_upstream_offline import PROMPT_VERSION
from feature.benchmark_upstream_offline.analyze_smoke import estimated_cost, summarize_model, usage_tokens
from feature.benchmark_upstream_offline.build_edges import build_edges
from feature.benchmark_upstream_offline.constants import FEATURE_LABELS
from feature.benchmark_upstream_offline.extract_inputs import (
    allocate_sample_sizes,
    build_style_rubric,
    build_type_rubric,
    extract_graph,
    sample_features,
)
from feature.benchmark_upstream_offline.run_judge import normalize_result, successful_ids
from feature.benchmark_upstream_offline.prompts import prompt_cache_key, system_prompt, user_prompt


class NormalizeResultTests(unittest.TestCase):
    def setUp(self):
        self.feature = {"node_id": "n1", "labels": [FEATURE_LABELS[0]]}

    def test_accepts_empty_array(self):
        self.assertEqual(
            {"node_id": "n1", "styles": []},
            normalize_result("style", {"node_id": "n1", "styles": []}, self.feature),
        )

    def test_filters_low_confidence_and_deduplicates(self):
        raw = {
            "node_id": "n1",
            "styles": [
                {"name": "科技", "confidence": 0.64, "reason": "弱关联"},
                {"name": "运动", "confidence": 0.8, "reason": "低趴姿态稳定强化运动感"},
                {"name": "运动", "confidence": 0.9, "reason": "重复项"},
            ],
        }
        result = normalize_result("style", raw, self.feature)
        self.assertEqual(["运动"], [row["name"] for row in result["styles"]])

    def test_rejects_unknown_label(self):
        with self.assertRaisesRegex(ValueError, "unknown type label"):
            normalize_result(
                "type",
                {"node_id": "n1", "types": [{"name": "轿车", "confidence": 0.9, "reason": "非法粗类"}]},
                self.feature,
            )

    def test_rejects_node_id_mismatch_and_empty_reason(self):
        with self.assertRaisesRegex(ValueError, "node_id mismatch"):
            normalize_result("style", {"node_id": "n2", "styles": []}, self.feature)
        with self.assertRaisesRegex(ValueError, "reason is empty"):
            normalize_result(
                "style",
                {"node_id": "n1", "styles": [{"name": "科技", "confidence": 0.9, "reason": ""}]},
                self.feature,
            )


class SamplingTests(unittest.TestCase):
    def test_allocation_fills_requested_size_with_small_stratum(self):
        allocation = allocate_sample_sizes({FEATURE_LABELS[0]: 1, FEATURE_LABELS[1]: 9}, 8)
        self.assertEqual(8, sum(allocation.values()))
        self.assertEqual(1, allocation[FEATURE_LABELS[0]])

    def test_sampling_is_deterministic_and_balanced(self):
        rows = []
        for label_index, label in enumerate(FEATURE_LABELS):
            for index in range(10):
                rows.append({"node_id": "%d-%d" % (label_index, index), "labels": [label]})
        first = sample_features(rows, 50, 42)
        second = sample_features(rows, 50, 42)
        self.assertEqual(first, second)
        self.assertEqual(50, len(first))
        counts = {}
        for row in first:
            counts[row["sample_stratum"]] = counts.get(row["sample_stratum"], 0) + 1
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)


class ResumeAndCostTests(unittest.TestCase):
    def test_resume_key_is_task_model_and_prompt_version_scoped(self):
        rows = [
            {"node_id": "a", "task": "style", "status": "ok", "model": "m", "prompt_version": PROMPT_VERSION},
            {"node_id": "b", "task": "type", "status": "ok", "model": "m", "prompt_version": PROMPT_VERSION},
            {"node_id": "c", "task": "style", "status": "error", "model": "m", "prompt_version": PROMPT_VERSION},
            {"node_id": "d", "task": "style", "status": "ok", "model": "other", "prompt_version": PROMPT_VERSION},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            self.assertEqual({"a"}, successful_ids(path, "style", "m"))

    def test_usage_and_cached_token_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 400}}
        self.assertEqual((1000, 100, 400), usage_tokens(usage))
        expected = (600 * 0.15 + 400 * 0.075 + 100 * 0.60) / 1_000_000
        self.assertAlmostEqual(expected, estimated_cost("gpt-4o-mini", 1000, 100, 400))

    def test_summary_counts_usage_from_every_retry_attempt(self):
        latest = {
            ("style", "n1"): {
                "task": "style", "node_id": "n1", "status": "ok", "styles": [],
            }
        }
        audit = [{
            "task": "style", "node_id": "n1", "status": "ok",
            "attempt_history": [
                {"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                {"usage": {"prompt_tokens": 11, "completion_tokens": 3}},
            ],
        }]
        report = summarize_model("gpt-4o-mini", latest, audit)
        self.assertEqual(21, report["tasks"]["style"]["input_tokens"])
        self.assertEqual(5, report["tasks"]["style"]["output_tokens"])
        self.assertEqual(2, report["tasks"]["style"]["api_call_count"])


class PromptCachingTests(unittest.TestCase):
    def setUp(self):
        self.rubric = {"styles": {"科技": {"guides": [{"parameter": "像素灯"}]}}}
        self.feature_a = {"node_id": "a", "name": "节点A"}
        self.feature_b = {"node_id": "b", "name": "节点B"}

    def test_rubric_is_in_fixed_system_and_feature_is_only_in_dynamic_user(self):
        fixed = system_prompt("style", self.rubric)
        user_a = user_prompt("style", self.feature_a)
        user_b = user_prompt("style", self.feature_b)
        self.assertIn("像素灯", fixed)
        self.assertNotIn("节点A", fixed)
        self.assertIn("节点A", user_a)
        self.assertNotIn("像素灯", user_a)
        self.assertNotEqual(user_a, user_b)

    def test_cache_key_is_stable_and_scoped_by_model_task_version_and_rubric(self):
        first = prompt_cache_key("gpt-5-mini", "style", self.rubric)
        second = prompt_cache_key("gpt-5-mini", "style", dict(self.rubric))
        changed = prompt_cache_key("gpt-5-mini", "style", {"styles": {}})
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertIn(PROMPT_VERSION, first)


class GraphExtractionTests(unittest.TestCase):
    def test_real_graph_contract(self):
        graph = Path(__file__).resolve().parents[1] / "kgdata_0804.jsonl"
        extracted = extract_graph(graph)
        self.assertEqual(9649, len(extracted["features"]))
        style = build_style_rubric(extracted)
        self.assertEqual(7, len(style["styles"]))
        self.assertEqual(96, style["styles"]["运动"]["guide_count"])
        self.assertTrue(all(
            set(guide) == {"parameter", "description"} and bool(guide["parameter"])
            for row in style["styles"].values()
            for guide in row["guides"]
        ))
        type_rubric = build_type_rubric(extracted)
        self.assertEqual(21, len(type_rubric["car_types"]))
        self.assertEqual(800, sum(row["sample_count"] for row in type_rubric["car_types"].values()))
        self.assertEqual(0, type_rubric["car_types"]["两厢轿车"]["sample_count"])
        self.assertEqual(0, type_rubric["car_types"]["三厢轿车"]["sample_count"])

    def test_edge_builder_is_deterministic_and_keeps_only_edge_properties(self):
        features = {
            "n1": {"node_id": "n1", "labels": [FEATURE_LABELS[0]], "name": "低趴", "source_properties": {"name": "低趴"}}
        }
        targets = {"运动": {"node_id": "style-sport"}}
        result = {
            "node_id": "n1", "task": "style", "status": "ok",
            "styles": [{"name": "运动", "confidence": 0.9, "reason": "低趴稳定强化运动姿态"}],
        }
        edges = build_edges("style", [result, result], features, targets)
        self.assertEqual(1, len(edges))
        self.assertEqual("StyleAssociatedWith", edges[0]["label"])
        self.assertEqual({"confidence", "reason"}, set(edges[0]["properties"]))


if __name__ == "__main__":
    unittest.main()
