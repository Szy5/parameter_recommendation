import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feature.dts_schema_first.upload_approved import load_approved_graph, main


class PartialUploadTests(unittest.TestCase):
    def fixture(self, root):
        graph = [
            {"type": "node", "id": f"DTS:vehicle:{i}", "labels": ["VehicleType"],
             "properties": {"id": f"DTS:vehicle:{i}", "name": str(i)}} for i in range(4)
        ]
        graph.extend([
            {"type": "node", "id": f"DTS:part:{i}", "labels": ["Part", "侧围"],
             "properties": {"id": f"DTS:part:{i}", "name": "侧围", "location": "侧部左侧"}}
            for i in range(2)
        ])
        graph.extend([
            {"type": "relationship", "label": "CONTAINS", "start_id": "DTS:vehicle:0",
             "end_id": f"DTS:part:{i}", "properties": {"location": "侧部左侧"}}
            for i in range(2)
        ])
        graph.append({"type": "relationship", "label": "DTS_POSITION_RELATION",
                      "start_id": "DTS:part:0", "end_id": "DTS:part:1", "properties": {"Gap": "3"}})
        (root / "graph.jsonl").write_text("\n".join(map(json.dumps, graph)) + "\n")
        (root / "locations.jsonl").write_text(json.dumps({
            "source_id": "4:3", "instances": [], "reason": "side unknown",
        }) + "\n")
        report = {"vehicle_nodes": 4, "part_nodes": 2, "contains_relationships": 2,
                  "dts_relationships": 1, "accepted_source_records": 1, "location": {"review": 1},
                  "uploaded": False}
        (root / "report.json").write_text(json.dumps(report))
        return graph

    def test_only_validated_graph_is_uploaded_without_model_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self.fixture(root)
            with patch("feature.dts_schema_first.upload_approved.upload_graph",
                       return_value={"dts_processed": 1}) as upload, patch.object(
                           sys, "argv", ["upload_approved", "--out", str(root), "--env", str(root / "env"), "--upload"]):
                main()
            self.assertEqual(upload.call_args.args[0], graph)
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["uploaded"])
            self.assertEqual(report["pending_location_records_not_uploaded"], 1)

    def test_rejects_mismatched_graph_and_missing_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = self.fixture(root)
            graph[4]["properties"]["location"] = ""
            (root / "graph.jsonl").write_text("\n".join(map(json.dumps, graph)) + "\n")
            with self.assertRaisesRegex(ValueError, "without confirmed"):
                load_approved_graph(root)
            graph.pop()
            (root / "graph.jsonl").write_text("\n".join(map(json.dumps, graph)) + "\n")
            with self.assertRaisesRegex(ValueError, "Graph/report mismatch"):
                load_approved_graph(root)


if __name__ == "__main__":
    unittest.main()
