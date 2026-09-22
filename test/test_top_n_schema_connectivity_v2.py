import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from feature.topn_n_schema_connectivity_v2.connectivity import (
    SchemaGroup,
    build_label_connectivity_query,
    calculate_label_connectivity,
    group_schemas_by_start_label,
    load_selected_schema_strings,
    sort_label_results,
)
from feature.topn_n_schema_connectivity.connectivity import parse_schema


STYLE_SCHEMA = (
    "VehiclePosture(汽车姿态) <- StyleAssociatedWith <- 汽车风格"
)
TYPE_SCHEMA = (
    "VehiclePosture(汽车姿态) <- TypeAssociatedWith <- 汽车车型"
)
USER_SCHEMA = (
    "UserTrend(用户与趋势) <- Prefers <- AestheticConcept(美学概念) "
    "<- StyleAssociatedWith <- 汽车风格"
)


class FakeRepository:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def _read(self, query, **parameters):
        self.queries.append((query, parameters))
        return [self.row]


class SchemaGroupingTests(unittest.TestCase):
    def test_groups_schemas_by_leftmost_label_in_input_order(self):
        groups = group_schemas_by_start_label(
            [STYLE_SCHEMA, USER_SCHEMA, TYPE_SCHEMA]
        )
        self.assertEqual(2, len(groups))
        self.assertEqual("VehiclePosture(汽车姿态)", groups[0].start_label)
        self.assertEqual(2, len(groups[0].schemas))
        self.assertEqual("UserTrend(用户与趋势)", groups[1].start_label)

    def test_rejects_a_schema_whose_label_does_not_match_the_group(self):
        group = SchemaGroup(
            start_label="VehiclePosture(汽车姿态)",
            schemas=(parse_schema(USER_SCHEMA),),
        )
        with self.assertRaises(ValueError):
            build_label_connectivity_query(group)

    def test_loads_corrected_schemas_from_v1_connectivity_results(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "connectivity.json"
            path.write_text(
                json.dumps(
                    {
                        "results": [
                            {"schema": STYLE_SCHEMA},
                            {"schema": TYPE_SCHEMA},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [STYLE_SCHEMA, TYPE_SCHEMA],
                load_selected_schema_strings(path),
            )


class LabelConnectivityQueryTests(unittest.TestCase):
    def setUp(self):
        self.group = SchemaGroup(
            start_label="VehiclePosture(汽车姿态)",
            schemas=(parse_schema(STYLE_SCHEMA), parse_schema(TYPE_SCHEMA)),
        )

    def test_query_uses_or_between_schema_exists_branches(self):
        query = build_label_connectivity_query(self.group)
        self.assertEqual(2, query.count("EXISTS {"))
        self.assertIn("\n  OR EXISTS {", query)
        self.assertIn("StyleAssociatedWith", query)
        self.assertIn("TypeAssociatedWith", query)

    def test_calculates_union_connectivity_at_start_node_grain(self):
        repository = FakeRepository(
            {"total_start_nodes": 100, "connected_start_nodes": 60}
        )
        result = calculate_label_connectivity(repository, self.group)
        self.assertEqual(2, result["schema_count"])
        self.assertEqual(60, result["connected_start_nodes"])
        self.assertAlmostEqual(0.6, result["connectivity"])
        self.assertAlmostEqual(60.0, result["connectivity_percent"])

    def test_zero_start_nodes_returns_null_connectivity(self):
        repository = FakeRepository(
            {"total_start_nodes": 0, "connected_start_nodes": 0}
        )
        result = calculate_label_connectivity(repository, self.group)
        self.assertIsNone(result["connectivity"])
        self.assertEqual("no_start_nodes", result["status"])

    def test_rejects_connected_count_larger_than_denominator(self):
        repository = FakeRepository(
            {"total_start_nodes": 10, "connected_start_nodes": 11}
        )
        with self.assertRaises(RuntimeError):
            calculate_label_connectivity(repository, self.group)


class ResultSortingTests(unittest.TestCase):
    def test_sorts_connectivity_descending_and_null_last(self):
        rows = [
            {"start_label": "B", "connectivity": 0.2, "status": "ok"},
            {"start_label": "A", "connectivity": 0.8, "status": "ok"},
            {
                "start_label": "C",
                "connectivity": None,
                "status": "no_start_nodes",
            },
        ]
        sorted_rows = sort_label_results(rows)
        self.assertEqual(["A", "B", "C"], [r["start_label"] for r in sorted_rows])


if __name__ == "__main__":
    unittest.main()
