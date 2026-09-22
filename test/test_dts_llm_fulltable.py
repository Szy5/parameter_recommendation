import json
import tempfile
import unittest
from pathlib import Path

from feature.dts_llm_fulltable.pipeline import build_graph, extract_all, record_input, validate_item
from feature.dts_schema_first.schema import approved_edges
from feature.dts_schema_first.workbook import SourceRecord, read_workbook


class FullTableTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "schema_version": "test", "source_pdf_sha256": "test",
            "edges": [{"id": "P001", "source": "发动机盖", "target": "前保险杠",
                       "relative_position": "前侧", "context": "", "status": "approved"}],
        }
        self.records = [
            SourceRecord("4:10", "4", "硬派越野车", 10, "A1", "发动机盖至前保险杠", "外部", "前部区域", "",
                         metrics={"Gap": ["3.0"]}),
            SourceRecord("5:10", "5", "城市SUV", 10, "A1", "发动机盖至前保险杠", "外部", "前部区域", "",
                         metrics={"Gap": ["3.0"]}),
            SourceRecord("7:20", "7", "豪华轿车", 20, "B1", "内部部件", "内部", "仪表板区域", "",
                         metrics={"Ra": ["1.0"]}),
        ]

    def _item(self, record, matched=True):
        item = {"source_id": record.source_id, "classification": record.classification,
                "area": record.area, "metrics": record.metric_values(), "relations": [], "reason": ""}
        if matched:
            item["relations"] = [{"edge_id": "P001", "part_a": "发动机盖", "part_b": "前保险杠",
                                   "relative_position": "前侧"}]
        else:
            item["reason"] = "PDF 没有内部方位"
        return item

    def test_complete_input_and_resume(self):
        batches = []

        def completion(_system, prompt):
            data = json.loads(prompt)
            batches.append(data)
            ids = {r["source_id"] for r in data["records"]}
            return json.dumps({"items": [self._item(r, r.source_id in ids and r.classification == "外部")
                                         for r in self.records if r.source_id in ids]}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.jsonl"
            items, errors, counts = extract_all(self.records, self.schema, Path("unused"), cache,
                                                batch_size=2, completion=completion)
            self.assertEqual(len(batches), 2)
            self.assertEqual([len(b["records"]) for b in batches], [2, 1])
            self.assertTrue(all(b["approved_edges"] for b in batches))
            self.assertEqual(len(items), 3)
            self.assertFalse(errors)
            self.assertEqual(counts["api_batches"], 2)
            again, _, second_counts = extract_all(self.records, self.schema, Path("unused"), cache,
                                                  batch_size=2, completion=completion)
            self.assertEqual(len(again), 3)
            self.assertEqual(second_counts["api_batches"], 0)

    def test_schema_and_metric_checks(self):
        record = self.records[0]
        valid = self._item(record)
        self.assertEqual(validate_item(valid, record, approved_edges(self.schema))["relations"][0]["edge_id"], "P001")
        bad = json.loads(json.dumps(valid))
        bad["relations"][0]["relative_position"] = "后侧"
        with self.assertRaises(ValueError):
            validate_item(bad, record, approved_edges(self.schema))
        bad = json.loads(json.dumps(valid))
        bad["metrics"]["Gap"] = "9.0"
        with self.assertRaises(ValueError):
            validate_item(bad, record, approved_edges(self.schema))
        internal_with_exterior_edge = {**self._item(self.records[2]), "relations": valid["relations"]}
        with self.assertRaises(ValueError):
            validate_item(internal_with_exterior_edge, self.records[2], approved_edges(self.schema))

    def test_radii_and_all_metric_values_are_in_model_input(self):
        record = SourceRecord("9:100", "9", "豪华SUV", 100, "R1", "配合部件", "外部", "", "补充圆角定义",
                              radii_base_part="第一个部件",
                              metrics={name: [name + "-value"] for name in
                                       ("Gap", "Flush", "Ra", "Rb", "Alignment", "Consistent", "Radii")})
        data = record_input(record)
        self.assertEqual(data["radii_base_part"], "第一个部件")
        self.assertEqual(data["metrics"]["Radii"], "Radii-value")
        self.assertEqual(len(data["metrics"]), 7)

    def test_graph_vehicle_isolation_and_review(self):
        items = {r.source_id: self._item(r, r.classification == "外部") for r in self.records}
        graph, review, report = build_graph(self.records, items, {})
        self.assertEqual(report["source_records"], 3)
        self.assertEqual(report["dts_relationships"], 2)
        self.assertEqual(len(review), 1)
        part_nodes = [r for r in graph if r["type"] == "node" and "Part" in r["labels"]]
        self.assertEqual(len(part_nodes), 4)
        self.assertEqual({r["properties"]["name"] for r in part_nodes}, {"发动机盖", "前保险杠"})

    def test_real_workbook_count(self):
        root = Path(__file__).resolve().parents[1]
        workbook = root / "【20260519】DTS（无图版）.xlsx"
        if not workbook.exists():
            self.skipTest("sample workbook not present")
        records = read_workbook(workbook)
        self.assertEqual(len(records), 1921)
        self.assertEqual(sum(r.classification == "外部" for r in records), 717)
        self.assertEqual(sum(r.classification == "内部" for r in records), 1204)


if __name__ == "__main__":
    unittest.main()
