import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from feature.benchmark_upstream_offline.constants import DIMENSION_FIELDS

from feature.benchmark_recommendation.config import PredictionConfig, RecallConfig, RecommendationConfig
from feature.benchmark_recommendation.graph_loader import GraphData
from feature.benchmark_recommendation.prediction_service import predict_from_main_paths, predict_style_and_type
from feature.benchmark_recommendation.path_narrative import build_case_rag_context, path_to_narrative
from feature.benchmark_recommendation.path_morphology import display_from_reverse_walk, format_path, inspect_path
from feature.benchmark_recommendation.pipeline import _inspect_paths, _paths_from_neo4j
from feature.benchmark_recommendation.neo4j_repository import (
    BATCH_NEIGHBOR_EVIDENCE_QUERY,
    DIRECT_STYLE_PATH_QUERY,
    DIRECT_TYPE_PATH_QUERY,
    MULTI_STYLE_PATH_QUERY,
    MULTI_TYPE_PATH_QUERY,
    BenchmarkNeo4jRepository,
)
from feature.parameter_recommendation.import_jsonl_to_neo4j import import_nodes
from feature.benchmark_recommendation.recall_service import apply_recall_config
from feature.benchmark_recommendation.recommend_types import parse_recommend_types
from feature.benchmark_recommendation.live_recall_service import build_retrieved_pool_from_scores
from feature.benchmark_recommendation.config import LiveRecallConfig
from feature.benchmark_recommendation.recommendation_strategies import (
    recommendation_router,
    style_guides_recommend_neo4j,
    style_type_level_recommend_neo4j,
    style_type_recommend_neo4j,
)
from feature.benchmark_recommendation.neo4j_repository import (
    style_guides_cypher,
    style_type_cypher,
    style_type_level_cypher,
)


class RecallFilteringTests(unittest.TestCase):
    def test_top_k_and_threshold_keeps_top_then_filters(self):
        retrieved = [
            {"node_id": "n1", "label": "L", "name": "A", "score": 0.9, "rank": 1, "matched_keywords": []},
            {"node_id": "n2", "label": "L", "name": "B", "score": 0.59, "rank": 2, "matched_keywords": []},
            {"node_id": "n3", "label": "L", "name": "C", "score": 0.8, "rank": 3, "matched_keywords": []},
        ]
        cfg = RecallConfig(mode="top_k_and_threshold", top_k=2, min_score=0.6, max_candidates=50)
        out = apply_recall_config(retrieved, cfg)
        self.assertEqual(["n1"], [row["node_id"] for row in out])


class RecommendTypesTests(unittest.TestCase):
    def test_parse_recommend_types_normalizes_aliases(self):
        self.assertEqual(
            ("style_guides", "style_type"),
            parse_recommend_types("style_parameter_guides, style_type"),
        )

    def test_parse_recommend_types_rejects_unknown(self):
        with self.assertRaises(ValueError):
            parse_recommend_types("style_guides,foo")


class LiveRecallPoolTests(unittest.TestCase):
    def test_live_pool_respects_threshold_mode(self):
        import numpy as np

        corpus = [
            {"node_id": "n1", "label": "L", "name": "A", "description": ""},
            {"node_id": "n2", "label": "L", "name": "B", "description": ""},
            {"node_id": "n3", "label": "L", "name": "C", "description": ""},
        ]
        scores = np.array([0.61, 0.59, 0.70], dtype=np.float32)
        cfg = RecallConfig(mode="threshold", min_score=0.60, max_candidates=50)
        live_cfg = LiveRecallConfig(pool_size=50)
        pool = build_retrieved_pool_from_scores(scores, corpus, ["A"], cfg, live_cfg)
        out = apply_recall_config(pool, cfg)
        self.assertEqual(["n3", "n1"], [row["node_id"] for row in out])


class PredictionVotingTests(unittest.TestCase):
    def test_candidate_score_is_sum(self):
        graph = GraphData(
            feature_node_by_id={},
            style_guides={},
            instances_by_type={},
            level_by_instance={},
            styles_by_instance={},
            instance_props={},
            style_edges_by_source={
                "f1": [{"target": "科技", "confidence": 0.8, "reason": "r1"}],
                "f2": [
                    {"target": "科技", "confidence": 0.5, "reason": "r2"},
                    {"target": "运动", "confidence": 0.6, "reason": "r3"},
                ],
            },
            type_edges_by_source={},
        )
        recalled_nodes = [
            {"node_id": "f1", "label": "X", "name": "x", "score": 0.7, "rank": 1, "matched_keywords": []},
            {"node_id": "f2", "label": "X", "name": "y", "score": 0.9, "rank": 2, "matched_keywords": []},
        ]
        style_cands, type_cands, need_confirm, _meta = predict_style_and_type(
            graph=graph,
            recalled_nodes=recalled_nodes,
            prediction_config=PredictionConfig(top_score_diff_ambiguity=1.0),
        )
        self.assertTrue(need_confirm)
        self.assertEqual([], type_cands)
        self.assertEqual("科技", style_cands[0]["name"])
        self.assertAlmostEqual(1.01, style_cands[0]["score"], places=6)
        self.assertEqual(2, style_cands[0]["support"])
        self.assertEqual("运动", style_cands[1]["name"])


class PathMorphologyTests(unittest.TestCase):
    def test_display_flips_reverse_walk(self):
        nodes, rels = display_from_reverse_walk(
            "简约",
            "StyleAssociatedWith",
            ["直立高坐姿", "紧凑占地尺寸", "极简家庭车"],
            ["Indicates(体现)", "ImplementedBy(由实现)"],
        )
        self.assertEqual(["简约", "极简家庭车", "紧凑占地尺寸", "直立高坐姿"], nodes)
        self.assertEqual(
            ["StyleAssociatedWith", "ImplementedBy(由实现)", "Indicates(体现)"],
            rels,
        )
        text = format_path(nodes, rels)
        self.assertTrue(text.startswith("简约 --StyleAssociatedWith-->"))
        self.assertTrue(text.endswith("直立高坐姿"))
        self.assertEqual(3, text.count("-->"))

    def test_predict_from_paths_prefers_shorter_hops(self):
        recalled = [
            {"node_id": "a", "score": 0.8},
            {"node_id": "b", "score": 0.8},
        ]
        rows = [
            {"head_kind": "style", "head_name": "简约", "recalled_id": "a", "hops": 1},
            {"head_kind": "style", "head_name": "运动", "recalled_id": "a", "hops": 5},
            {"head_kind": "style", "head_name": "运动", "recalled_id": "b", "hops": 5},
            {"head_kind": "type", "head_name": "两厢轿车", "recalled_id": "a", "hops": 2},
        ]
        styles, types, _need, _meta = predict_from_main_paths(
            rows, recalled, PredictionConfig(top_score_diff_ambiguity=0.01)
        )
        self.assertEqual("简约", styles[0]["name"])
        self.assertEqual("两厢轿车", types[0]["name"])
        self.assertAlmostEqual(0.8, styles[0]["score"], places=6)

    def test_cypher_keeps_associated_with_off_the_variable_walk(self):
        self.assertIn("StyleAssociatedWith", MULTI_STYLE_PATH_QUERY)
        self.assertIn("*1..4", MULTI_STYLE_PATH_QUERY)
        walk_part = MULTI_STYLE_PATH_QUERY.split("MATCH (head:")[0]
        self.assertNotIn("StyleAssociatedWith", walk_part)
        self.assertNotIn("TypeAssociatedWith", BATCH_NEIGHBOR_EVIDENCE_QUERY)
        self.assertIn("Indicates(体现)", BATCH_NEIGHBOR_EVIDENCE_QUERY)

    def test_path_queries_use_shared_graph_node_label(self):
        for query in (
            DIRECT_STYLE_PATH_QUERY,
            DIRECT_TYPE_PATH_QUERY,
            MULTI_STYLE_PATH_QUERY,
            MULTI_TYPE_PATH_QUERY,
            BATCH_NEIGHBOR_EVIDENCE_QUERY,
        ):
            self.assertIn("(recalled:GraphNode {_graph_id: nid})", query)
        for query in (MULTI_STYLE_PATH_QUERY, MULTI_TYPE_PATH_QUERY):
            self.assertIn(
                "ORDER BY recalled._graph_id, head.name, hops, walk_nodes, walk_rels",
                query,
            )

    def test_max_hops_is_pushed_into_cypher_and_empty_results_are_cached(self):
        class FakeInner:
            def __init__(self):
                self.calls = []

            def _read(self, query, **parameters):
                self.calls.append((query, parameters))
                return []

        inner = FakeInner()
        repo = BenchmarkNeo4jRepository(inner)

        repo.batch_main_paths(["n1"], max_hops=3)
        self.assertEqual(4, len(inner.calls))
        self.assertTrue(all("*1..2" in query for query, _ in inner.calls[2:]))
        self.assertTrue(all(params["node_ids"] == ["n1"] for _, params in inner.calls))

        repo.batch_main_paths(["n1"], max_hops=3)
        self.assertEqual(4, len(inner.calls))

        repo.batch_main_paths(["n1", "n2"], max_hops=3)
        self.assertEqual(8, len(inner.calls))
        self.assertTrue(all(params["node_ids"] == ["n2"] for _, params in inner.calls[4:]))

    def test_max_hops_one_skips_multi_path_queries(self):
        class FakeInner:
            def __init__(self):
                self.calls = []

            def _read(self, query, **parameters):
                self.calls.append((query, parameters))
                return []

        inner = FakeInner()
        repo = BenchmarkNeo4jRepository(inner)
        repo.batch_main_paths(["n1"], max_hops=1)

        self.assertEqual(2, len(inner.calls))
        self.assertTrue(all("*1.." not in query for query, _ in inner.calls))

    def test_no_neighbor_skips_neighbor_repository_call(self):
        class FakeRepo:
            def __init__(self):
                self.main_calls = 0
                self.neighbor_calls = 0

            def batch_main_paths(self, node_ids, max_hops=5):
                self.main_calls += 1
                return []

            def batch_neighbor_evidence(self, node_ids):
                self.neighbor_calls += 1
                return []

        repo = FakeRepo()
        main_rows, neighbor_rows = _paths_from_neo4j(
            [{"node_id": "n1"}],
            repo,
            max_hops=3,
            include_neighbor=False,
        )
        self.assertEqual([], main_rows)
        self.assertEqual([], neighbor_rows)
        self.assertEqual(1, repo.main_calls)
        self.assertEqual(0, repo.neighbor_calls)

    def test_neighbor_results_are_cached_by_recalled_id(self):
        class FakeInner:
            def __init__(self):
                self.calls = []

            def _read(self, query, **parameters):
                self.calls.append((query, parameters))
                return [
                    {
                        "recalled_id": node_id,
                        "recalled_name": node_id.upper(),
                        "rel": "Indicates(体现)",
                        "neighbor_name": "neighbor-" + node_id,
                    }
                    for node_id in parameters["node_ids"]
                ]

        inner = FakeInner()
        repo = BenchmarkNeo4jRepository(inner)
        first = repo.batch_neighbor_evidence(["n1"])
        second = repo.batch_neighbor_evidence(["n1", "n2"])

        self.assertEqual(2, len(inner.calls))
        self.assertEqual(["n1"], inner.calls[0][1]["node_ids"])
        self.assertEqual(["n2"], inner.calls[1][1]["node_ids"])
        self.assertEqual(["n1"], [row["recalled_id"] for row in first])
        self.assertEqual(["n1", "n2"], [row["recalled_id"] for row in second])
    def test_inspect_path_uses_bupt_arrows(self):
        self.assertEqual(
            "简约 -> StyleAssociatedWith -> 极简家庭车 -> ImplementedBy -> 直立高坐姿",
            inspect_path("简约 --StyleAssociatedWith--> 极简家庭车 --ImplementedBy(由实现)--> 直立高坐姿"),
        )
        rows = _inspect_paths(
            [
                {"path": "简约 --StyleAssociatedWith--> 紧凑城市姿态", "hops": 1},
                {"path": "运动 --StyleAssociatedWith--> a --Indicates--> b --Guides--> c", "hops": 3},
            ],
            [{"recalled_name": "紧凑城市姿态", "rel": "Indicates(体现)", "neighbor_name": "短悬"}],
            max_hops=1,
            include_neighbor=True,
        )
        self.assertEqual(["aesthetic_to_main_combined", "aesthetic_to_neighbor_evidence"], [p["template"] for p in rows])
        self.assertEqual(1, rows[0]["hop_count"])
        self.assertTrue(rows[0]["path"].startswith("简约 -> StyleAssociatedWith ->"))


class Neo4jImportPerformanceTests(unittest.TestCase):
    def test_imported_nodes_receive_shared_graph_node_label(self):
        class FakeResult:
            def consume(self):
                return None

        class FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **parameters):
                self.calls.append((query, parameters))
                return FakeResult()

        with TemporaryDirectory() as directory:
            graph_path = Path(directory) / "graph.jsonl"
            graph_path.write_text(
                '{"type":"node","id":"n1","labels":["L"],"properties":{"name":"A"}}\n',
                encoding="utf-8",
            )
            session = FakeSession()
            node_count, transaction_count = import_nodes(session, graph_path, 100)

        self.assertEqual(1, node_count)
        self.assertEqual(1, transaction_count)
        self.assertEqual(1, len(session.calls))
        self.assertIn("SET n:`GraphNode`", session.calls[0][0])


class PredictOutputShapeTests(unittest.TestCase):
    def test_public_predicted_is_name_lists(self):
        from feature.benchmark_recommendation.pipeline import _public_paths, _public_predicted

        predicted = _public_predicted(
            {
                "car_style": [{"name": "科技", "score": 0.9, "support": 1}],
                "car_type": ["紧凑型SUV"],
                "car_level": None,
                "prediction_mode": "llm",
                "reasoning": "ok",
                "confidence": 0.8,
            }
        )
        self.assertEqual(["科技"], predicted["car_style"])
        self.assertEqual(["紧凑型SUV"], predicted["car_type"])
        self.assertNotIn("prediction_mode", predicted)
        self.assertNotIn("confidence", predicted)
        paths = _public_paths(
            [{"path": "科技 -> StyleAssociatedWith -> 直立高坐姿", "template": "aesthetic_to_main_combined", "hop_count": 1}]
        )
        self.assertEqual([{"path": "科技 -> StyleAssociatedWith -> 直立高坐姿", "hop_count": 1}], paths)

    def test_skip_predict_when_recall_empty(self):
        from feature.benchmark_recommendation.pipeline import _skip_predict_for_empty_recall

        self.assertTrue(_skip_predict_for_empty_recall([]))
        self.assertTrue(_skip_predict_for_empty_recall([{"node_id": "", "name": ""}]))
        self.assertFalse(_skip_predict_for_empty_recall([{"node_id": "n1", "name": "A"}]))


class PathNarrativeTests(unittest.TestCase):
    def test_path_to_narrative_includes_descriptions(self):
        descriptions = {
            "直立高坐姿": "较高坐高，常见于城市SUV。",
            "科技": "强调数字化与灯语。",
        }
        path = "科技 -> StyleAssociatedWith -> 直立高坐姿"
        text = path_to_narrative(path, descriptions)
        self.assertIn("直立高坐姿", text)
        self.assertIn("较高坐高", text)
        self.assertIn("科技", text)

    def test_build_case_rag_context_has_keywords_and_paths(self):
        ctx = build_case_rag_context(
            keywords=["城市高坐姿"],
            recalled_nodes=[{"name": "直立高坐姿", "label": "VehiclePosture", "score": 0.8}],
            paths=[{"path": "科技 -> StyleAssociatedWith -> 直立高坐姿", "template": "aesthetic_to_main_combined", "hop_count": 1}],
            descriptions={"直立高坐姿": "较高坐高。"},
        )
        self.assertIn("城市高坐姿", ctx["context_text"])
        self.assertEqual(1, len(ctx["path_narratives"]))


class RecommendationAggregationTests(unittest.TestCase):
    def test_style_type_level_strategy_and_numeric_summary(self):
        instance_a = "v1"
        instance_b = "v2"
        field = "长度(mm)" if "长度(mm)" in DIMENSION_FIELDS else DIMENSION_FIELDS[0]

        graph = GraphData(
            feature_node_by_id={},
            style_guides={},
            instances_by_type={"SUV": {instance_a, instance_b}},
            level_by_instance={instance_a: {"A"}, instance_b: {"A"}},
            styles_by_instance={instance_a: {"科技"}, instance_b: {"科技"}},
            instance_props={
                instance_a: {field: 100},
                instance_b: {field: 300},
            },
            style_edges_by_source={},
            type_edges_by_source={},
        )

        style_candidates = [{"name": "科技", "score": 1.0, "support": 1}]
        type_candidates = [{"name": "SUV", "score": 1.0, "support": 1}]
        car_level = {"name": "A", "source": "type_instance_distribution"}
        cfg = RecommendationConfig(
            max_styles=2,
            max_types=2,
            max_combinations=4,
            small_sample_threshold=10,
            fallback_on_small_sample=False,
        )
        groups = recommendation_router(
            graph=graph,
            car_style_candidates=style_candidates,
            car_type_candidates=type_candidates,
            car_level=car_level,
            recommendation_config=cfg,
        )
        self.assertEqual(1, len(groups))
        self.assertEqual("style_type_level", groups[0]["strategy"])
        self.assertEqual(2, groups[0]["sample_count"])
        params = {p["parameter"]: p for p in groups[0]["parameters"]}
        self.assertIn(field, params)
        self.assertEqual(200.0, params[field]["median"])


class Neo4jRecommendationTests(unittest.TestCase):
    def test_cypher_templates_match_reference_shape(self):
        self.assertIn("CONTAINS '豪华'", style_guides_cypher("豪华"))
        self.assertIn("Guides(指导)", style_guides_cypher("豪华"))
        query = style_type_cypher("科技", "SUV")
        self.assertIn("汽车车型", query)
        self.assertIn("EXPRESSES_STYLE", query)
        self.assertIn("CONTAINS '科技'", query)
        self.assertIn("CONTAINS 'SUV'", query)
        level_query = style_type_level_cypher("科技", "SUV", "A级")
        self.assertIn("汽车级别", level_query)
        self.assertIn("CONTAINS 'A级'", level_query)

    def test_neo4j_style_type_aggregates_instance_properties(self):
        class FakeRepo:
            def style_guides(self, car_style, limit=250):
                return [
                    {
                        "style_name": car_style,
                        "parameter_properties": {
                            "name": "Wheelbase",
                            "properties": '{"parameterName(参数名称)": "轴距", "range(范围)": "2000-3000", "unit(单位)": "mm"}',
                        },
                        "guide_properties": {
                            "properties": '{"howToGuide(指导方式)": "拉长轴距"}'
                        },
                    }
                ]

            def style_type_instances(self, car_style, car_type, limit=800):
                return [
                    {
                        "vehicle_id": "car_1",
                        "model_name": "示例车A",
                        "vehicle_properties": {"长度(mm)": "4000", "车型名称": "示例车A"},
                    },
                    {
                        "vehicle_id": "car_2",
                        "model_name": "示例车B",
                        "vehicle_properties": {"长度(mm)": "4200", "车型名称": "示例车B"},
                    },
                ]

            def type_baseline_instances(self, car_type, limit=800):
                return self.style_type_instances("x", car_type)

            def style_type_level_instances(self, car_style, car_type, car_level, limit=800):
                return self.style_type_instances(car_style, car_type)[:1]

        repo = FakeRepo()
        guides = style_guides_recommend_neo4j(repo, "豪华")
        self.assertEqual("轴距", guides["parameters"][0]["parameter"])
        self.assertIn("CONTAINS '豪华'", guides["cypher"])

        group, count = style_type_recommend_neo4j(repo, "科技", "SUV", small_sample_threshold=10)
        self.assertEqual(2, count)
        self.assertEqual("style_type", group["strategy"])
        params = {row["parameter"]: row for row in group["parameters"]}
        self.assertEqual(4100.0, params["长度(mm)"]["median"])
        self.assertEqual(2, len(group["recommended_vehicles"]))

        level_group = style_type_level_recommend_neo4j(
            repo=repo,
            car_style="科技",
            car_type="SUV",
            car_level="A级",
            small_sample_threshold=10,
            fallback_on_small_sample=False,
            fallback_small_sample_threshold=8,
        )
        self.assertEqual("style_type_level", level_group["strategy"])
        self.assertEqual(1, level_group["sample_count"])
        self.assertIn("汽车级别", level_group["cypher"])


if __name__ == "__main__":
    unittest.main()
