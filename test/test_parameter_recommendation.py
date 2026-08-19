import json
import tempfile
import unittest
from pathlib import Path

from feature_v2.parameter_recommendation.build_fused_graph import build_meixue_records
from feature_v2.parameter_recommendation.common import AC_LABEL, DP_LABEL, GUIDES_LABEL
from feature_v2.parameter_recommendation.neo4j_recommend import RecommendationService
from feature_v2.parameter_recommendation.run_context_style_judge import normalize


class RelationshipMergeTests(unittest.TestCase):
    def test_duplicate_guides_preserve_every_source_property_payload(self):
        concept_a = {
            "type": "node",
            "id": "1",
            "labels": [AC_LABEL],
            "properties": {"name": "Sportiness"},
        }
        concept_b = {
            "type": "node",
            "id": "2",
            "labels": [AC_LABEL],
            "properties": {"name": "Dynamic Stance"},
        }
        parameter = {
            "type": "node",
            "id": "3",
            "labels": [DP_LABEL],
            "properties": {"name": "A柱倾角"},
        }
        edges = [
            {
                "type": "relationship",
                "id": "e1",
                "label": GUIDES_LABEL,
                "properties": {
                    "name": "guide A",
                    "properties": json.dumps({"howToGuide(指导方式)": "指导A"}, ensure_ascii=False),
                },
                "start": concept_a,
                "end": parameter,
            },
            {
                "type": "relationship",
                "id": "e2",
                "label": GUIDES_LABEL,
                "properties": {
                    "name": "guide B",
                    "properties": json.dumps({"howToGuide(指导方式)": "指导B"}, ensure_ascii=False),
                },
                "start": concept_b,
                "end": parameter,
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "meixue.jsonl"
            source.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in [concept_a, concept_b, parameter, *edges])
                + "\n",
                encoding="utf-8",
            )
            records, _nodes, stats = build_meixue_records(
                source,
                mapping={"1": "meixue_style_运动", "2": "meixue_style_运动"},
                grouped={
                    "运动": [
                        {"concept_id": "1", "concept_name": "Sportiness"},
                        {"concept_id": "2", "concept_name": "Dynamic Stance"},
                    ]
                },
            )

        guides = [row for row in records if row.get("label") == GUIDES_LABEL]
        self.assertEqual(1, len(guides))
        properties = guides[0]["properties"]
        self.assertEqual(["e1", "e2"], properties["merged_source_edge_ids"])
        sources = json.loads(properties["merged_source_relationships"])
        self.assertEqual({"e1", "e2"}, {row["source_edge_id"] for row in sources})
        guidance = {
            json.loads(row["properties"]["properties"])["howToGuide(指导方式)"]
            for row in sources
        }
        self.assertEqual({"指导A", "指导B"}, guidance)
        self.assertEqual(1, stats["deduplicated_relationships"])
        self.assertEqual(1, stats["merged_relationship_groups"])
        self.assertEqual(2, stats["preserved_source_relationships"])


class GraphContextStyleJudgeTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "instance_id": "100",
            "model_name": "测试车型",
            "car_class": "中型车",
            "contains_context": {
                "屏幕/系统": {"中控屏幕尺寸": "15.4英寸"},
                "驾驶硬件": {"摄像头数量": "10个"},
            },
        }
        self.criteria = {
            "styles": {
                "科技": [
                    {"name": "Infotainment/Central Screen Size"},
                    {"name": "Sensor integration packaging"},
                ]
            }
        }

    def test_normalize_accepts_graph_grounded_style_without_parameters(self):
        result = normalize(
            {
                "styles": [
                    {
                        "style": "科技",
                        "score": 0.88,
                        "confidence": 0.9,
                        "evidence": (
                            "汽车实例-包含-屏幕/系统中的中控屏幕尺寸=15.4英寸，"
                            "与 Infotainment/Central Screen Size 相匹配。"
                        ),
                    }
                ]
            },
            self.record,
            self.criteria,
        )
        self.assertEqual("科技", result["styles"][0]["style"])
        self.assertNotIn("parameters", result["styles"][0])

    def test_normalize_rejects_evidence_without_target_style_parameter(self):
        with self.assertRaisesRegex(ValueError, "DesignParameter name"):
            normalize(
                {
                    "styles": [
                        {
                            "style": "科技",
                            "score": 0.88,
                            "confidence": 0.9,
                            "evidence": "汽车实例-包含-屏幕/系统中的中控屏幕尺寸=15.4英寸，体现科技感。",
                        }
                    ]
                },
                self.record,
                self.criteria,
            )

    def test_normalize_rejects_llm_generated_parameters(self):
        with self.assertRaisesRegex(ValueError, "must not output parameters"):
            normalize(
                {
                    "styles": [
                        {
                            "style": "科技",
                            "score": 0.88,
                            "confidence": 0.9,
                            "evidence": (
                                "汽车实例-包含-屏幕/系统中的中控屏幕尺寸=15.4英寸，"
                                "与 Infotainment/Central Screen Size 相匹配。"
                            ),
                            "parameters": ["错误参数"],
                        }
                    ]
                },
                self.record,
                self.criteria,
            )


class FakeRecommendationRepository:
    def style_parameters(self, style):
        if style != "运动":
            return []
        return [
            {
                "style_properties": {"name": "运动"},
                "parameter_properties": {
                    "name": "A柱倾角",
                    "properties": json.dumps(
                        {
                            "range(范围)": "145-155",
                            "unit(单位)": "°",
                            "description(描述)": "较大后倾有助于形成运动姿态",
                        },
                        ensure_ascii=False,
                    ),
                },
                "guide_properties": {
                    "properties": json.dumps(
                        {"howToGuide(指导方式)": "适当增加后倾"}, ensure_ascii=False
                    )
                },
            }
        ]

    def class_body_parameters(self, car_class):
        if car_class != "跑车":
            return []
        return [
            {"vehicle_id": "1", "model_name": "车型A", "parameter": "高度(mm)", "value": "1400"},
            {"vehicle_id": "1", "model_name": "车型A", "parameter": "宽度(mm)", "value": "1800"},
            {"vehicle_id": "2", "model_name": "车型B", "parameter": "高度(mm)", "value": "1500"},
            {"vehicle_id": "2", "model_name": "车型B", "parameter": "宽度(mm)", "value": "1900"},
        ]

    def style_class_body_parameters(
        self, style, car_class, score_threshold, confidence_threshold
    ):
        if style != "运动" or car_class != "跑车":
            return []
        common = {
            "vehicle_id": "1",
            "model_name": "车型A",
            "style_score": 0.9,
            "style_confidence": 0.88,
            "style_parameters": '["A柱倾角","车身高度"]',
        }
        return [
            dict(common, parameter="高度(mm)", value="1400"),
            dict(common, parameter="宽度(mm)", value="1800"),
        ]


class Neo4jRecommendationServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RecommendationService(FakeRecommendationRepository())

    def test_style_mode_reads_design_parameter_details(self):
        result = self.service.recommend(style="运动")
        self.assertEqual("汽车风格", result["mode"])
        self.assertEqual(1, result["matched_style_nodes"])
        self.assertEqual("A柱倾角", result["recommendations"][0]["parameter"])
        self.assertEqual(
            "适当增加后倾",
            result["recommendations"][0]["guidance"]["howToGuide(指导方式)"],
        )

    def test_class_mode_aggregates_vehicle_body_values(self):
        result = self.service.recommend(car_class="跑车")
        self.assertEqual(2, result["matched_instances"])
        by_name = {row["parameter"]: row for row in result["recommendations"]}
        self.assertEqual(1450.0, by_name["高度(mm)"]["recommended_median"])
        self.assertEqual([1800.0, 1900.0], by_name["宽度(mm)"]["observed_range"])

    def test_combined_mode_uses_matched_cohort_and_adds_class_comparison(self):
        result = self.service.recommend(style="运动", car_class="跑车")
        self.assertEqual("汽车风格+汽车级别", result["mode"])
        self.assertEqual(2, result["class_instances"])
        self.assertEqual(1, result["matched_instances"])
        self.assertEqual(0.5, result["match_rate"])
        self.assertEqual(["A柱倾角", "车身高度"], result["style_design_parameter_basis"])
        by_name = {row["parameter"]: row for row in result["recommendations"]}
        self.assertEqual(1400.0, by_name["高度(mm)"]["recommended_median"])
        self.assertEqual(1450.0, by_name["高度(mm)"]["class_baseline_median"])
        self.assertEqual(-50.0, by_name["高度(mm)"]["median_delta_from_class"])

    def test_rejects_missing_or_invalid_inputs(self):
        with self.assertRaisesRegex(ValueError, "cannot both be empty"):
            self.service.recommend()
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            self.service.recommend(style="运动", score_threshold=1.1)


if __name__ == "__main__":
    unittest.main()
