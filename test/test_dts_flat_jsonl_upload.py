import json
import tempfile
import unittest
from pathlib import Path

from feature.dts_llm_fulltable_my.upload_jsonl import load_graph, upload_graph


class FlatJsonlUploadTests(unittest.TestCase):
    def test_loads_flat_graph_and_serializes_nested_property_in_memory(self):
        rows = [
            {"type": "node", "id": "v1", "labels": ["VehicleType"],
             "properties": {"id": "v1", "name": "车型"}},
            {"type": "node", "id": "p1", "labels": ["Part", "侧围"],
             "properties": {"id": "p1", "name": "侧围", "location": "外部-侧部区域"}},
            {"type": "relationship", "label": "CONTAINS", "start_id": "v1", "end_id": "p1",
             "properties": {}},
            {"type": "relationship", "label": "DTS_POSITION_RELATION", "start_id": "p1", "end_id": "p1",
             "properties": {"relative_position": {"id": "P1", "relative_position": "左侧"},
                            "Gap": ["3±1"]}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.jsonl"
            original = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
            path.write_text(original, encoding="utf-8")
            nodes, relationships, stats = load_graph(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(relationships), 2)
        dts = next(row for row in relationships if row["label"] == "DTS_POSITION_RELATION")
        self.assertEqual(json.loads(dts["properties"]["relative_position"])["relative_position"], "左侧")
        self.assertEqual(dts["properties"]["Gap"], ["3±1"])
        self.assertEqual(stats["property_values_serialized_as_json"], 1)

    def test_rejects_missing_relationship_endpoint(self):
        rows = [
            {"type": "node", "id": "v1", "labels": ["VehicleType"], "properties": {}},
            {"type": "relationship", "label": "CONTAINS", "start_id": "v1", "end_id": "missing",
             "properties": {}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.jsonl"
            path.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "关系端点"):
                load_graph(path)

    def test_upload_uses_dynamic_labels_and_stable_relationship_ids(self):
        nodes = [
            {"id": "v1", "labels": ["VehicleType"], "properties": {"id": "v1"}},
            {"id": "p1", "labels": ["Part", "侧围"], "properties": {"id": "p1"}},
        ]
        relationships = [{
            "id": "flat-jsonl-rel:1", "label": "CONTAINS", "start_id": "v1", "end_id": "p1",
            "properties": {"_graph_id": "flat-jsonl-rel:1"},
        }]
        queries = []

        class Result:
            def consume(self): return None
            def data(self): return []
            def single(self): return {"count": 0}

        class Session:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def run(self, query, **_kwargs):
                queries.append(query)
                return Result()
            def execute_write(self, fn, *args): return fn(self, *args)

        class Driver:
            def verify_connectivity(self): return None
            def session(self, **_kwargs): return Session()
            def close(self): return None

        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("NEO4J_URI=bolt://test\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=p\n")
            result = upload_graph(nodes, relationships, env, driver_factory=lambda *_args, **_kwargs: Driver())
        self.assertEqual(result["relationships_processed"], 1)
        self.assertTrue(any("SET n:`Part`:`侧围`" in query for query in queries))
        self.assertTrue(any("[r:`CONTAINS` {_graph_id: row.id}]" in query for query in queries))


if __name__ == "__main__":
    unittest.main()
