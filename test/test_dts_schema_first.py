from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from feature.dts_schema_first.__main__ import main
from feature.dts_schema_first.extract import _parse, extract
from feature.dts_schema_first.graph import build_graph, part_id, write_jsonl
from feature.dts_schema_first.locations import resolve_locations, validate_instances
from feature.dts_schema_first.schema import PDF_BRANCHES, build_schema
from feature.dts_schema_first.upload import dts_query, upload_graph
from feature.dts_schema_first.workbook import SourceRecord


def fixture_schema(root: Path):
    pdf = root / "source.pdf"
    pdf.write_bytes(b"reviewed-pdf-test")
    with patch("feature.dts_schema_first.schema.PDF_SHA256", hashlib.sha256(pdf.read_bytes()).hexdigest()):
        return pdf, build_schema(pdf)


def edge(schema, source, target, position):
    return next(e["id"] for e in schema["edges"] if
                e["source"] == source and e["target"] == target and e["relative_position"] == position)


def record(source_id, name, sheet="4", metric="Gap", value="3±1", base="", area="前部区域"):
    return SourceRecord(
        source_id=source_id, sheet=sheet,
        vehicle={"4": "硬派越野车", "5": "城市SUV"}.get(sheet, "豪华轿车"),
        row=int(source_id.split(":")[1]), code="A-001", name=name,
        classification="外部", area=area, section="补充圆角定义" if metric == "Radii" else "",
        radii_base_part=base, metrics={metric: [value]},
    )


class SchemaFirstTests(unittest.TestCase):
    def test_pdf_schema_is_complete_fixed_and_directed(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        self.assertEqual(len(PDF_BRANCHES), 12)
        self.assertEqual(len(schema["edges"]), 152)
        self.assertEqual(len(schema["pdf_part_categories"]), 61)
        self.assertIn("饰板", {item["name"] for item in schema["pdf_part_categories"]})
        self.assertTrue(any(e["source"] == "前保险杠" and e["target"] == "前车灯" and e["relative_position"] == "内含" for e in schema["edges"]))
        self.assertTrue(any(e["source"] == "前保险杠" and e["target"] == "前车灯" and e["relative_position"] == "上侧" for e in schema["edges"]))
        self.assertFalse(any(e["target"] == "×无DTS定义" for e in schema["edges"]))
        self.assertEqual({e["status"] for e in schema["edges"] if e["source"] == "后风挡" and e["target"] == "前风挡"}, {"review_source"})
        self.assertEqual(schema["relationship_types"]["CONTAINS"]["properties"], ["location"])

    def test_llm_can_only_select_pdf_edges_and_preserves_specific_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
            source = record("4:3", "机盖至散热器罩左饰板")
            selected_id = edge(schema, "发动机盖", "饰板", "前侧")
            answer = {"items": [{
                "source_id": source.source_id, "edge_ids": [selected_id],
                "source_evidence": "机盖", "target_evidence": "散热器罩左饰板",
            }]}
            parsed = _parse(json.dumps(answer, ensure_ascii=False), [source], schema)
            self.assertEqual(parsed[source.source_id]["part_a"], "发动机盖")
            self.assertEqual(parsed[source.source_id]["part_b"], "散热器罩左饰板")
            calls = []
            def completion(_system, user):
                calls.append(json.loads(user))
                return json.dumps(answer, ensure_ascii=False)
            cache = Path(directory) / "cache.jsonl"
            first, errors, counts = extract([source], schema, Path("unused"), cache, completion=completion)
            second, _, cache_counts = extract([source], schema, Path("unused"), cache, completion=completion)
            self.assertEqual(first, second)
            self.assertEqual(errors, {})
            self.assertEqual(counts["api_batches"], 1)
            self.assertEqual(cache_counts["cached"], 1)
            self.assertEqual(len(calls), 1)
            self.assertTrue(any(row[0] == selected_id for row in calls[0]["approved_edges"]))
            answer["items"][0]["edge_ids"] = ["not-in-pdf"]
            with self.assertRaisesRegex(ValueError, "non-approved"):
                _parse(json.dumps(answer, ensure_ascii=False), [source], schema)

    def test_bilateral_edges_four_subgraphs_and_radii_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        left = edge(schema, "前风挡", "侧围", "左侧")
        right = edge(schema, "前风挡", "侧围", "右侧")
        bilateral = record("4:3", "前风挡玻璃至侧围(L&R)")
        choice = _parse(json.dumps({"items": [{
            "source_id": bilateral.source_id, "edge_ids": [left, right],
            "source_evidence": "前风挡玻璃", "target_evidence": "侧围",
        }]}, ensure_ascii=False), [bilateral], schema)
        duplicate = record("4:7", bilateral.name)
        other_vehicle = record("5:3", bilateral.name, sheet="5")
        radii = record("5:10", "与发动机盖配合", sheet="5", metric="Radii", value="1.5", base="翼子板", area="")
        radii_id = edge(schema, "翼子板", "发动机盖", "上侧")
        radii_choice = _parse(json.dumps({"items": [{
            "source_id": radii.source_id, "edge_ids": [radii_id],
            "source_evidence": "翼子板", "target_evidence": "发动机盖",
        }]}, ensure_ascii=False), [radii], schema)
        selections = {bilateral.source_id: choice[bilateral.source_id], duplicate.source_id: choice[bilateral.source_id],
                      other_vehicle.source_id: choice[bilateral.source_id], **radii_choice}
        left_right = {"instances": [
            {"edge_id": left, "part_a_location": "前部中央", "part_b_location": "侧部左侧"},
            {"edge_id": right, "part_a_location": "前部中央", "part_b_location": "侧部右侧"},
        ]}
        located = {r.source_id: left_right for r in (bilateral, duplicate, other_vehicle)}
        located[radii.source_id] = {"instances": [
            {"edge_id": radii_id, "part_a_location": "侧部左侧", "part_b_location": "前部中央"},
        ]}
        graph, review, report = build_graph([bilateral, duplicate, other_vehicle, radii], selections, {}, schema, located)
        self.assertEqual(review, [])
        self.assertEqual(report["vehicle_nodes"], 4)
        self.assertEqual(report["dts_relationships"], 5)
        self.assertEqual(report["collapsed_identical_dts_records"], 2)
        self.assertNotEqual(part_id("4", "侧围", "侧部左侧"), part_id("5", "侧围", "侧部左侧"))
        self.assertNotEqual(part_id("4", "侧围", "侧部左侧"), part_id("4", "侧围", "侧部右侧"))
        self.assertTrue(any(row.get("label") == "DTS_POSITION_RELATION" and row["properties"]["Radii"] == "1.5" and row["start_id"] == part_id("5", "翼子板", "侧部左侧") for row in graph))
        self.assertTrue(all(row["properties"].get("location") for row in graph if row.get("label") == "CONTAINS"))
        self.assertTrue(any(row["labels"] == ["Part", "前风挡"] for row in graph if row["type"] == "node"))

    def test_unknown_and_pdf_anomaly_go_to_review(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        source = record("4:3", "内部扶手至仪表板", area="前部区域")
        anomaly = record("4:7", "后风挡至前风挡")
        anomaly_id = edge(schema, "后风挡", "前风挡", "前侧")
        with self.assertRaisesRegex(ValueError, "non-approved"):
            _parse(json.dumps({"items": [{"source_id": anomaly.source_id, "edge_ids": [anomaly_id],
                                          "source_evidence": "后风挡", "target_evidence": "前风挡"}]}, ensure_ascii=False), [anomaly], schema)
        graph, review, report = build_graph([source, anomaly], {}, {}, schema)
        self.assertEqual(report["review_records"], 2)
        self.assertEqual(report["dts_relationships"], 0)
        self.assertEqual(len(graph), 4)

    def test_location_pass_splits_same_name_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pdf, schema = fixture_schema(root)
            source = record("4:27", "机盖至翼子板(L&R)")
            left = edge(schema, "发动机盖", "翼子板", "左侧")
            right = edge(schema, "发动机盖", "翼子板", "右侧")
            choice = {source.source_id: {"edge_ids": [left, right],
                                         "part_a": "发动机盖", "part_b": "翼子板"}}
            calls = []

            def completion(_system, prompt):
                calls.append(json.loads(prompt))
                return json.dumps({"items": [{"source_id": source.source_id, "instances": [
                    {"edge_id": left, "part_a_location": "前部中央", "part_b_location": "侧部左侧"},
                    {"edge_id": right, "part_a_location": "前部中央", "part_b_location": "侧部右侧"},
                ]}]}, ensure_ascii=False)

            cache = root / "locations.jsonl"
            located, errors, counts = resolve_locations([source], choice, schema, Path("unused"), cache,
                                                       completion=completion)
            self.assertFalse(errors)
            self.assertEqual(counts["api_batches"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["records"][0]["part_b"], "翼子板")
            self.assertIn("侧部左侧", calls[0]["allowed_locations"])
            graph, review, report = build_graph([source], choice, {}, schema, located)
            self.assertEqual(review, [])
            self.assertEqual(report["part_nodes"], 3)
            self.assertEqual(report["dts_relationships"], 2)
            located_again, _, resumed = resolve_locations([source], choice, schema, Path("unused"), cache,
                                                           completion=completion)
            self.assertEqual(located_again, located)
            self.assertEqual(resumed["api_batches"], 0)
            self.assertEqual(len(calls), 1)

    def test_multiple_dts_edges_use_instance_endpoints_not_names_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        chosen = edge(schema, "发动机盖", "翼子板", "左侧")
        first = record("4:27", "机盖至左翼子板", value="3±1")
        second = record("4:31", "机盖至左翼子板", value="4±1")
        duplicate = record("4:35", "机盖至左翼子板", value="3±1")
        other_vehicle = record("5:27", "机盖至左翼子板", sheet="5", value="3±1")
        records = [first, second, duplicate, other_vehicle]
        choice = {r.source_id: {"edge_ids": [chosen], "part_a": "发动机盖", "part_b": "翼子板"}
                  for r in records}
        located = {r.source_id: {"instances": [{"edge_id": chosen,
            "part_a_location": "前部中央", "part_b_location": "侧部左侧"}]}
            for r in records}
        graph, review, report = build_graph(records, choice, {}, schema, located)
        self.assertFalse(review)
        self.assertEqual(report["dts_relationships"], 3)
        self.assertEqual(report["collapsed_identical_dts_records"], 1)
        dts = [row for row in graph if row.get("label") == "DTS_POSITION_RELATION"]
        same_pair = [row for row in dts if row["start_id"] == part_id("4", "发动机盖", "前部中央")
                     and row["end_id"] == part_id("4", "翼子板", "侧部左侧")]
        self.assertEqual(len(same_pair), 2)
        self.assertEqual({row["properties"]["Gap"] for row in same_pair}, {"3±1", "4±1"})
        self.assertNotEqual(part_id("4", "翼子板", "侧部左侧"),
                            part_id("5", "翼子板", "侧部左侧"))

    def test_location_validation_does_not_split_central_pair_or_accept_one_bilateral_side(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        source = record("4:435", "尾门至后保(L&R)")
        chosen = edge(schema, "尾门/后备箱盖", "后保险杠", "后侧/下侧")
        choice = {"edge_ids": [chosen], "part_a": "尾门/后备箱盖", "part_b": "后保险杠"}
        central = {"source_id": source.source_id, "instances": [
            {"edge_id": chosen, "part_a_location": "后部中央", "part_b_location": "后部中央"},
        ]}
        self.assertEqual(len(validate_instances(central, source, choice,
                                                {e["id"]: e for e in schema["edges"]})["instances"]), 1)
        wrong = {"source_id": source.source_id, "instances": [
            {"edge_id": chosen, "part_a_location": "后部中央", "part_b_location": "后部左侧"},
        ]}
        with self.assertRaisesRegex(ValueError, "中心部件的位置"):
            validate_instances(wrong, source, choice, {e["id"]: e for e in schema["edges"]})
        no_side = record("4:200", "翼子板与发动机盖配合", metric="Radii", base="翼子板")
        radii_id = edge(schema, "翼子板", "发动机盖", "上侧")
        radii_choice = {"edge_ids": [radii_id], "part_a": "翼子板", "part_b": "发动机盖"}
        unsupported = {"source_id": no_side.source_id, "instances": [
            {"edge_id": radii_id, "part_a_location": "侧部左侧", "part_b_location": "前部中央"},
        ]}
        with self.assertRaisesRegex(ValueError, "没有支持"):
            validate_instances(unsupported, no_side, radii_choice, {e["id"]: e for e in schema["edges"]})

    def test_internal_row_and_ambiguous_side_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            _pdf, schema = fixture_schema(Path(directory))
        left = edge(schema, "前风挡", "侧围", "左侧")
        internal = record("4:3", "前风挡玻璃至侧围(L)")
        internal.classification = "内部"
        answer = {"items": [{"source_id": internal.source_id, "edge_ids": [left],
                             "source_evidence": "前风挡玻璃", "target_evidence": "侧围"}]}
        with self.assertRaisesRegex(ValueError, "non-exterior"):
            _parse(json.dumps(answer, ensure_ascii=False), [internal], schema)
        external = record("4:7", "前风挡玻璃至侧围")
        answer["items"][0]["source_id"] = external.source_id
        with self.assertRaisesRegex(ValueError, "side evidence"):
            _parse(json.dumps(answer, ensure_ascii=False), [external], schema)

    def test_invalid_item_is_reviewed_without_recalling_valid_batch_peers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pdf, schema = fixture_schema(root)
            good = record("4:3", "机盖至前保")
            ambiguous = record("4:7", "前保险杠至前车灯")
            answer = {"items": [
                {"source_id": good.source_id,
                 "edge_ids": [edge(schema, "发动机盖", "前保险杠", "前侧")],
                 "source_evidence": "机盖", "target_evidence": "前保"},
                {"source_id": ambiguous.source_id,
                 "edge_ids": [edge(schema, "前保险杠", "前车灯", "上侧")],
                 "source_evidence": "前保险杠", "target_evidence": "前车灯"},
            ]}
            calls = []

            def completion(_system, _user):
                calls.append(1)
                return json.dumps(answer, ensure_ascii=False)

            progress = io.StringIO()
            cache = root / "cache.jsonl"
            with redirect_stderr(progress):
                selected, errors, counts = extract(
                    [good, ambiguous], schema, Path("unused"), cache,
                    completion=completion, batch_size=2,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(counts["api_batches"], 1)
            self.assertEqual(counts["failed_records"], 1)
            self.assertIn(good.source_id, selected)
            self.assertIn(ambiguous.source_id, errors)
            self.assertEqual(len(cache.read_text(encoding="utf-8").splitlines()), 2)
            self.assertIn("完成 2/2", progress.getvalue())
            with redirect_stderr(io.StringIO()):
                resumed, resumed_errors, resumed_counts = extract(
                    [good, ambiguous], schema, Path("unused"), cache,
                    completion=completion, batch_size=2,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(resumed, selected)
            self.assertEqual(resumed_errors, errors)
            self.assertEqual(resumed_counts["cached_reviews"], 1)

            def recheck(_system, user):
                self.assertEqual([item["source_id"] for item in json.loads(user)["records"]], [ambiguous.source_id])
                return json.dumps({"items": [{"source_id": ambiguous.source_id, "edge_ids": []}]})

            with redirect_stderr(io.StringIO()):
                _selected, retried_errors, retried_counts = extract(
                    [good, ambiguous], schema, Path("unused"), cache,
                    completion=recheck, batch_size=2, retry_review=True,
                )
            self.assertEqual(retried_errors, {})
            self.assertEqual(retried_counts["api_batches"], 1)

    def test_invalid_whole_response_retries_once_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pdf, schema = fixture_schema(root)
            records = [record("4:3", "机盖至前保"), record("4:7", "机盖至前保")]
            calls = []

            def completion(_system, _user):
                calls.append(1)
                return "not JSON"

            with redirect_stderr(io.StringIO()):
                selected, errors, counts = extract(
                    records, schema, Path("unused"), root / "cache.jsonl",
                    completion=completion, batch_size=2,
                )
            self.assertEqual(selected, {})
            self.assertEqual(set(errors), {record.source_id for record in records})
            self.assertEqual(len(calls), 2)
            self.assertEqual(counts["api_batches"], 2)
            self.assertEqual(counts["failed_records"], 2)

    def test_upload_merges_business_properties_without_technical_edge_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pdf, schema = fixture_schema(root)
            source = record("4:3", "机盖至前保")
            selected = {source.source_id: {"edge_ids": [edge(schema, "发动机盖", "前保险杠", "前侧")],
                                           "part_a": "发动机盖", "part_b": "前保险杠"}}
            located = {source.source_id: {"instances": [{
                "edge_id": selected[source.source_id]["edge_ids"][0],
                "part_a_location": "前部中央", "part_b_location": "前部中央",
            }]}}
            graph, _, _ = build_graph([source], selected, {}, schema, located)
            env = root / ".env"
            env.write_text("NEO4J_URI=bolt://test\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=p\n", encoding="utf-8")
            queries = []
            class Result:
                def consume(self): pass
                def value(self): return []
            class Session:
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def run(self, query, **kwargs):
                    queries.append(query)
                    return Result()
                def execute_write(self, fn, query, rows): fn(self, query, rows)
            class Driver:
                def verify_connectivity(self): pass
                def session(self, **_kwargs): return Session()
                def close(self): pass
            with patch.dict("os.environ", {}, clear=True):
                upload_graph(graph, env, driver_factory=lambda *_args, **_kwargs: Driver())
                upload_graph(graph, env, driver_factory=lambda *_args, **_kwargs: Driver())
            self.assertEqual(sum("DTS_POSITION_RELATION" in query for query in queries), 2)
            self.assertIn("MERGE", dts_query())
            self.assertNotIn("source_id", dts_query())
            self.assertNotIn("relation_id", dts_query())

    def test_replacement_checks_old_ids_and_runs_delete_and_create_in_one_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pdf, schema = fixture_schema(root)
            source = record("4:3", "机盖至前保")
            chosen = edge(schema, "发动机盖", "前保险杠", "前侧")
            selections = {source.source_id: {"edge_ids": [chosen],
                                             "part_a": "发动机盖", "part_b": "前保险杠"}}
            located = {source.source_id: {"instances": [{"edge_id": chosen,
                "part_a_location": "前部中央", "part_b_location": "前部中央"}]}}
            graph, _, _ = build_graph([source], selections, {}, schema, located)
            old_graph = root / "legacy.jsonl"
            old_ids = ["DTS:vehicle:4", "DTS:vehicle:5", "DTS:vehicle:7", "DTS:vehicle:9",
                       "DTS:part:4:old-a", "DTS:part:4:old-b"]
            write_jsonl(old_graph, [{"type": "node", "id": identifier} for identifier in old_ids])
            env = root / ".env"
            env.write_text("NEO4J_URI=bolt://test\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=p\n", encoding="utf-8")
            queries = []

            class Result:
                def __init__(self, values=None): self.values = values or []
                def consume(self): pass
                def value(self): return self.values
            class Session:
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def run(self, query, **_kwargs):
                    queries.append(query)
                    if "RETURN n.id AS id" in query: return Result(old_ids)
                    if "RETURN DISTINCT type(r)" in query: return Result(["CONTAINS", "DTS_POSITION_RELATION"])
                    if "RETURN count(r) AS count" in query: return Result([0])
                    return Result()
                def execute_write(self, fn, *args): fn(self, *args)
            class Driver:
                def verify_connectivity(self): pass
                def session(self, **_kwargs): return Session()
                def close(self): pass
            with patch.dict("os.environ", {}, clear=True):
                result = upload_graph(graph, env, driver_factory=lambda *_args, **_kwargs: Driver(),
                                      replace_old_graph_path=old_graph)
            self.assertEqual(result["old_nodes_replaced"], len(old_ids))
            self.assertEqual(sum("DETACH DELETE" in q for q in queries), 1)
            self.assertTrue(any("CONTAINS {location:" in q for q in queries))

    def test_schema_only_never_opens_workbook_or_calls_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, _schema = fixture_schema(root)
            out = root / "out"
            with patch("feature.dts_schema_first.schema.PDF_SHA256", hashlib.sha256(pdf.read_bytes()).hexdigest()), \
                 patch("feature.dts_schema_first.__main__.read_workbook") as workbook_reader, \
                 patch("feature.dts_schema_first.__main__.extract") as llm, \
                 patch.object(sys, "argv", ["dts", "--pdf", str(pdf), "--out", str(out), "--schema-only"]), \
                 patch("builtins.print"):
                main()
            workbook_reader.assert_not_called()
            llm.assert_not_called()
            self.assertTrue((out / "schema.json").exists())

    def test_full_cli_freezes_schema_before_xlsx_and_filters_interior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, schema = fixture_schema(root)
            xlsx = root / "source.xlsx"
            xlsx.write_bytes(b"fixture workbook")
            out = root / "out"
            external = record("4:3", "机盖至前保")
            internal = record("4:7", "仪表板至扶手")
            internal.classification = "内部"
            chosen = edge(schema, "发动机盖", "前保险杠", "前侧")

            def read_stub(_path):
                self.assertTrue((out / "schema.json").exists())
                return [external, internal]

            def extract_stub(records, fixed_schema, *_args, **_kwargs):
                self.assertEqual([r.source_id for r in records], [external.source_id])
                self.assertEqual(fixed_schema["source_pdf_sha256"], schema["source_pdf_sha256"])
                return ({external.source_id: {"edge_ids": [chosen], "part_a": "发动机盖", "part_b": "前保险杠"}},
                        {}, {"cached": 0, "api_batches": 1, "failed_records": 0})

            with patch("feature.dts_schema_first.schema.PDF_SHA256", hashlib.sha256(pdf.read_bytes()).hexdigest()), \
                 patch("feature.dts_schema_first.__main__.read_workbook", side_effect=read_stub), \
                 patch("feature.dts_schema_first.__main__.extract", side_effect=extract_stub), \
                 patch("feature.dts_schema_first.__main__.resolve_locations", return_value=(
                     {external.source_id: {"instances": [{"edge_id": chosen,
                         "part_a_location": "前部中央", "part_b_location": "前部中央"}]}},
                     {}, {"eligible": 1, "cached": 0, "api_batches": 1, "review": 0})), \
                 patch("feature.dts_schema_first.__main__.upload_graph") as uploader, \
                 patch.object(sys, "argv", ["dts", "--pdf", str(pdf), "--xlsx", str(xlsx), "--out", str(out)]), \
                 patch("builtins.print"):
                main()
            uploader.assert_not_called()
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["accepted_source_records"], 1)
            self.assertEqual(report["internal_records_without_pdf_schema"], 1)
            self.assertFalse(report["uploaded"])


if __name__ == "__main__":
    unittest.main()
