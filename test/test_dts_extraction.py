from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from feature.DTS_extraction.graph import build_graph
from feature.DTS_extraction.__main__ import main
from feature.DTS_extraction.llm_extract import _parse_batch, extract_parts
from feature.DTS_extraction.schema import METRICS, part_id
from feature.DTS_extraction.topology import PdfTopology
from feature.DTS_extraction.upload import dts_merge_query, upload_graph
from feature.DTS_extraction.workbook import SourceRecord, read_workbook


class DummyTopology:
    def lookup(self, first, second, classification):
        if classification == "外部" and (first, second) == ("发动机盖", "前保险杠"):
            return "前侧", ""
        return None, "no PDF match"


def record(source_id, sheet="4", area="前部区域", metric="Gap", value="3±1"):
    return SourceRecord(
        source_id=source_id, sheet=sheet,
        vehicle={"4": "硬派越野车", "5": "城市SUV"}.get(sheet, "豪华轿车"),
        row=int(source_id.split(":")[1]), code="A-001", name="机盖至前保",
        classification="外部", area=area, section="", metrics={metric: [value]},
    )


class GraphTests(unittest.TestCase):
    def test_four_isolated_vehicle_subgraphs_and_duplicate_policy(self):
        records = [
            record("4:3"), record("4:7", area="侧部区域"),
            record("4:11"), record("4:15", metric="Alignment", value="0±1"),
            record("5:3", sheet="5"),
        ]
        endpoints = {item.source_id: ("发动机盖", "前保险杠") for item in records}
        graph, review, report = build_graph(records, endpoints, {}, DummyTopology())
        self.assertEqual(len(review), 0)
        self.assertEqual(report["vehicle_nodes"], 4)
        self.assertEqual(report["part_nodes"], 4)
        self.assertEqual(report["position_relationships"], 4)
        self.assertEqual(report["collapsed_identical_relationships"], 1)
        self.assertNotEqual(part_id("4", "发动机盖"), part_id("5", "发动机盖"))
        part_nodes = [row for row in graph if row["type"] == "node" and "Part" in row["labels"]]
        self.assertTrue(all(len(row["labels"]) == 2 for row in part_nodes))
        dts = [row for row in graph if row.get("label") == "DTS_POSITION_RELATION"]
        self.assertTrue(all(set(METRICS).issubset(row["properties"]) for row in dts))
        self.assertTrue(all("relation_id" not in row["properties"] for row in dts))

    def test_unknown_position_is_reviewed_but_parts_are_kept(self):
        source = record("4:3")
        graph, review, report = build_graph(
            [source], {source.source_id: ("发动机盖", "翼子板")}, {}, DummyTopology()
        )
        self.assertEqual(report["part_nodes"], 2)
        self.assertEqual(report["position_relationships"], 0)
        self.assertEqual(len(review), 1)

    def test_pdf_lookup_is_directed_and_ambiguous_pairs_are_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            pdf.write_bytes(b"test pdf bytes")
            mapping = root / "map.json"
            mapping.write_text(json.dumps({
                "source_pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "relations": [
                    ["发动机盖", "前保险杠", "前侧"],
                    ["前保险杠", "翼子板", "左侧"],
                    ["前保险杠", "翼子板", "右侧"],
                ],
            }, ensure_ascii=False), encoding="utf-8")
            topology = PdfTopology(pdf, mapping)
            self.assertEqual(topology.lookup("机盖", "前保", "外部")[0], "前侧")
            self.assertIsNone(topology.lookup("前保", "机盖", "外部")[0])
            self.assertIsNone(topology.lookup("前保", "翼子板", "外部")[0])
            self.assertIsNone(topology.lookup("机盖", "前保", "内部")[0])


class LLMTests(unittest.TestCase):
    def test_batch_cache_and_radii_direction(self):
        source = record("4:3")
        radii = SourceRecord(
            source_id="5:10", sheet="5", vehicle="城市SUV", row=10,
            code="A-1", name="与发动机盖配合", classification="外部",
            area="", section="补充圆角定义", radii_base_part="翼子板",
            metrics={"Radii": ["1.5（角半径）"]},
        )
        calls = []
        def completion(_system, user):
            calls.append(user)
            ids = [item["source_id"] for item in json.loads(user)]
            answers = {
                "4:3": {"source_id": "4:3", "part_a": "机盖", "part_b": "前保"},
                "5:10": {"source_id": "5:10", "part_a": "翼子板", "part_b": "发动机盖"},
            }
            return json.dumps({"items": [answers[key] for key in ids]}, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.jsonl"
            first, errors, counts = extract_parts([source, radii], Path("unused"), cache, completion=completion)
            self.assertEqual(errors, {})
            self.assertEqual(first["4:3"], ("发动机盖", "前保险杠"))
            self.assertEqual(first["5:10"], ("翼子板", "发动机盖"))
            self.assertEqual(counts["api_batches"], 1)
            second, _, counts = extract_parts([source, radii], Path("unused"), cache, completion=completion)
            self.assertEqual(second, first)
            self.assertEqual(counts["cached"], 2)
            self.assertEqual(len(calls), 1)

    def test_radii_cannot_change_the_base_part(self):
        source = SourceRecord(
            source_id="5:10", sheet="5", vehicle="城市SUV", row=10,
            code="A-1", name="与发动机盖配合", classification="外部",
            area="", section="补充圆角定义", radii_base_part="翼子板",
            metrics={"Radii": ["1.5"]},
        )
        with self.assertRaisesRegex(ValueError, "Radii source part"):
            _parse_batch('{"items":[{"source_id":"5:10","part_a":"发动机盖","part_b":"翼子板"}]}', [source])

    def test_one_bad_item_does_not_discard_its_batch_peer(self):
        good = record("4:3")
        bad = SourceRecord(
            source_id="5:10", sheet="5", vehicle="城市SUV", row=10,
            code="A-1", name="与发动机盖配合", classification="外部",
            area="", section="补充圆角定义", radii_base_part="翼子板",
            metrics={"Radii": ["1.5"]},
        )
        def completion(_system, user):
            items = []
            for item in json.loads(user):
                items.append({"source_id": item["source_id"], "part_a": "机盖", "part_b": "前保"})
            return json.dumps({"items": items}, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as directory, patch(
            "feature.DTS_extraction.llm_extract.retry_delay", lambda _attempt: None
        ):
            extracted, errors, counts = extract_parts(
                [good, bad], Path("unused"), Path(directory) / "cache.jsonl",
                completion=completion,
            )
        self.assertEqual(extracted["4:3"], ("发动机盖", "前保险杠"))
        self.assertIn("5:10", errors)
        self.assertEqual(counts["failed_batches"], 1)


class WorkbookTests(unittest.TestCase):
    def test_seven_metrics_and_special_sections(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is not installed")
        with tempfile.TemporaryDirectory() as directory:
            workbook = openpyxl.Workbook()
            workbook.active.title = "4"
            for title in ("5", "7", "9"):
                workbook.create_sheet(title)
            sheet = workbook["4"]
            sheet.append(["title"])
            sheet.append(["内外分类", "区域", "编号", "编号名称", "数值类别", "数值定义"])
            for row in [
                ["外部", "前部区域", "A-001", "机盖至前保", "Gap", "3±1"],
                [None, None, None, None, "Flush", "0.5"],
                [None, None, None, None, "Ra", "R1"],
                [None, None, None, None, "Rb", "R2"],
                [None, "对齐度要求", "E-001", "机盖至前保", "Alignment", "0±1"],
                [None, "一致性要求", "F-001", "机盖至前保", "Consistent", "间隙一致"],
                [None, None, None, None, "Consistent", "面差一致"],
            ]:
                sheet.append(row)
            sheet = workbook["5"]
            sheet.append(["title"])
            sheet.append(["内外分类", "区域", "编号", "编号名称", "数值类别", "数值定义"])
            sheet.append(["外部", "前部区域", "A-001", "机盖至前保", "Gap", 3])
            sheet.append([None, "补充圆角定义", None, None, None, None])
            sheet.append([None, "翼子板", "A-1", "与发动机盖配合", "Radii", "1.5（角半径）"])
            for title in ("7", "9"):
                sheet = workbook[title]
                sheet.append(["title"])
                sheet.append(["内外分类", "区域", "编号", "编号名称", "数值类别", "数值定义"])
                sheet.append(["外部", "前部区域", "A-001", "机盖至前保", "Gap", 3])
            path = Path(directory) / "fixture.xlsx"
            workbook.save(path)
            records = read_workbook(path)
        self.assertEqual(len(records), 7)
        self.assertEqual(set(records[0].metrics), {"Gap", "Flush", "Ra", "Rb"})
        self.assertEqual(records[1].area, "")
        self.assertEqual(records[2].metric_values()["Consistent"], "间隙一致\n面差一致")
        radii = next(item for item in records if "Radii" in item.metrics)
        self.assertEqual(radii.radii_base_part, "翼子板")
        self.assertEqual(radii.area, "")


class UploadTests(unittest.TestCase):
    def test_merge_uses_only_business_properties_and_is_repeatable(self):
        graph, _, _ = build_graph(
            [record("4:3")], {"4:3": ("发动机盖", "前保险杠")}, {}, DummyTopology()
        )
        queries = []
        class Result:
            def consume(self):
                return None
        class Session:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def run(self, query, **kwargs):
                queries.append((query, kwargs))
                return Result()
            def execute_write(self, fn, query, rows):
                fn(self, query, rows)
        class Driver:
            def verify_connectivity(self):
                pass
            def session(self, **_kwargs):
                return Session()
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("NEO4J_URI=bolt://test\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=p\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                for _ in range(2):
                    result = upload_graph(graph, env, driver_factory=lambda *_args, **_kwargs: Driver())
        self.assertEqual(result["dts_relationships_processed"], 1)
        self.assertIn("MERGE (a)-[r:DTS_POSITION_RELATION", dts_merge_query())
        self.assertNotIn("relation_id", dts_merge_query())
        self.assertNotIn("source_id", dts_merge_query())
        self.assertEqual(sum("MERGE (a)-[r:DTS_POSITION_RELATION" in query for query, _ in queries), 2)

    def test_cli_upload_requires_explicit_flag(self):
        class CLIStubTopology(DummyTopology):
            digest = "test-pdf-digest"
        source = record("4:3")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xlsx = root / "source.xlsx"
            pdf = root / "source.pdf"
            xlsx.write_bytes(b"fixture workbook")
            pdf.write_bytes(b"fixture pdf")
            out = root / "out"
            args = ["dts", "--xlsx", str(xlsx), "--pdf", str(pdf), "--out", str(out)]
            with patch("feature.DTS_extraction.__main__.PdfTopology", return_value=CLIStubTopology()), \
                 patch("feature.DTS_extraction.__main__.read_workbook", return_value=[source]), \
                 patch("feature.DTS_extraction.__main__.extract_parts", return_value=({"4:3": ("发动机盖", "前保险杠")}, {}, {"cached": 0, "api_batches": 1, "failed_batches": 0})), \
                 patch("feature.DTS_extraction.__main__.upload_graph", return_value={"dts_relationships_processed": 1}) as uploader, \
                 patch.object(sys, "argv", args), \
                 patch("builtins.print"):
                main()
                uploader.assert_not_called()
                self.assertFalse(json.loads((out / "report.json").read_text(encoding="utf-8"))["uploaded"])
                self.assertTrue((out / "extractions.jsonl").exists())
                self.assertIn("发动机盖", (out / "schema.json").read_text(encoding="utf-8"))
            with patch("feature.DTS_extraction.__main__.PdfTopology", return_value=CLIStubTopology()), \
                 patch("feature.DTS_extraction.__main__.read_workbook", return_value=[source]), \
                 patch("feature.DTS_extraction.__main__.extract_parts", return_value=({"4:3": ("发动机盖", "前保险杠")}, {}, {"cached": 1, "api_batches": 0, "failed_batches": 0})), \
                 patch("feature.DTS_extraction.__main__.upload_graph", return_value={"dts_relationships_processed": 1}) as uploader, \
                 patch.object(sys, "argv", args + ["--upload"]), \
                 patch("builtins.print"):
                main()
                uploader.assert_called_once()
                self.assertTrue(json.loads((out / "report.json").read_text(encoding="utf-8"))["uploaded"])


if __name__ == "__main__":
    unittest.main()
