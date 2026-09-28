import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from feature.topn_n_schema_connectivity.connectivity import (
    build_connectivity_query,
    calculate_schema_connectivity,
    edge_directions,
    load_schema_strings,
    parse_schema,
)


class FakeRepository:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def _read(self, query, **parameters):
        self.queries.append((query, parameters))
        return [self.row]


class SchemaParsingTests(unittest.TestCase):
    def test_parses_reversed_top_n_schema_and_expands_relationship_type(self):
        parsed = parse_schema(
            "DesignAttribute(设计属性) <- Indicates <- "
            "VehiclePosture(汽车姿态) <- TypeAssociatedWith <- 汽车车型"
        )
        self.assertEqual("DesignAttribute(设计属性)", parsed.start_label)
        self.assertEqual("汽车车型", parsed.end_label)
        self.assertEqual("incoming", parsed.direction)
        self.assertEqual(
            ("Indicates(体现)", "TypeAssociatedWith"),
            parsed.relationship_types,
        )

    def test_parses_outgoing_schema(self):
        parsed = parse_schema(
            "DesignAttribute(设计属性) -> Indicates -> "
            "VehiclePosture(汽车姿态)",
            require_supported_endpoint=False,
        )
        self.assertEqual("outgoing", parsed.direction)
        query = build_connectivity_query(parsed)
        self.assertIn(
            "(start:`DesignAttribute(设计属性)`)-[:`Indicates(体现)`]-",
            query,
        )

    def test_literal_mode_follows_outgoing_schema_arrow(self):
        parsed = parse_schema(
            "DesignAttribute(设计属性) -> Indicates -> "
            "VehiclePosture(汽车姿态)",
            require_supported_endpoint=False,
        )
        query = build_connectivity_query(parsed, direction_mode="literal")
        self.assertIn(
            "(start:`DesignAttribute(设计属性)`)-[:`Indicates(体现)`]->",
            query,
        )

    def test_path_extraction_mode_keeps_middle_edges_undirected(self):
        parsed = parse_schema(
            "UserTrend(用户与趋势) <- Prefers <- "
            "AestheticConcept(美学概念) <- StyleAssociatedWith <- 汽车风格"
        )
        self.assertEqual(
            ("undirected", "incoming"),
            edge_directions(parsed, "path_extraction"),
        )

    def test_rejects_mixed_arrows(self):
        with self.assertRaises(ValueError):
            parse_schema(
                "A <- Indicates <- B -> TypeAssociatedWith -> 汽车车型"
            )

    def test_rejects_non_target_endpoint(self):
        with self.assertRaises(ValueError):
            parse_schema("A <- Indicates <- B")


class ConnectivityQueryTests(unittest.TestCase):
    def test_query_follows_incoming_relationships_and_uses_exists(self):
        parsed = parse_schema(
            "VehiclePosture(汽车姿态) <- StyleAssociatedWith <- 汽车风格"
        )
        query = build_connectivity_query(parsed)
        self.assertIn("EXISTS {", query)
        self.assertIn(
            "(start:`VehiclePosture(汽车姿态)`)<-[:`StyleAssociatedWith`]-"
            "(n1:`汽车风格`)",
            query,
        )
        self.assertIn("count(start) AS total_start_nodes", query)

    def test_calculates_one_percent_from_distinct_start_node_counts(self):
        repository = FakeRepository(
            {"total_start_nodes": 100, "connected_start_nodes": 1}
        )
        result = calculate_schema_connectivity(
            repository,
            parse_schema(
                "VehiclePosture(汽车姿态) <- StyleAssociatedWith <- 汽车风格"
            ),
        )
        self.assertEqual(100, result["total_start_nodes"])
        self.assertEqual(1, result["connected_start_nodes"])
        self.assertAlmostEqual(0.01, result["connectivity"])
        self.assertAlmostEqual(1.0, result["connectivity_percent"])

    def test_zero_start_nodes_returns_null_connectivity(self):
        repository = FakeRepository(
            {"total_start_nodes": 0, "connected_start_nodes": 0}
        )
        result = calculate_schema_connectivity(
            repository,
            parse_schema(
                "VehiclePosture(汽车姿态) <- TypeAssociatedWith <- 汽车车型"
            ),
        )
        self.assertIsNone(result["connectivity"])
        self.assertEqual("no_start_nodes", result["status"])


class SchemaFileTests(unittest.TestCase):
    def test_loads_schemas_from_pass_rate_shape(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pass_rate.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "TOP_N_SCHEMA": 2,
                            "SCHEMAS": [
                                "A <- StyleAssociatedWith <- 汽车风格",
                                "B <- TypeAssociatedWith <- 汽车车型",
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(2, len(load_schema_strings(path)))


if __name__ == "__main__":
    unittest.main()
