import json
import tempfile
import unittest
from pathlib import Path

from feature.dts_llm_fulltable_my.pipeline import my_extract_all


class MyFullTableCacheTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "source_pdf_sha256": "pdf-test",
            "pdf_part_categories": [{"name": "侧围"}, {"name": "翼子板"}],
        }
        self.records = [
            {"source_id": "4:3", "source_part_name": "侧围", "target_part_name": "翼子板"},
            {"source_id": "4:7", "source_part_name": "翼子板", "target_part_name": "侧围"},
            {"source_id": "5:3", "source_part_name": "侧围", "target_part_name": "翼子板"},
        ]

    def test_completed_batches_are_cached_and_resumed(self):
        calls = []

        def completion(_system, prompt):
            data = json.loads(prompt)
            calls.append([row["source_id"] for row in data["records"]])
            return json.dumps({"items": [
                {"source_id": row["source_id"], "source_part_type": "侧围",
                 "target_part_type": "翼子板", "reason": "类型明确"}
                for row in data["records"]
            ]}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "llm_cache.jsonl"
            first = my_extract_all(self.records, self.schema, Path("unused"), cache,
                                   batch_size=2, completion=completion)
            self.assertEqual(calls, [["4:3", "4:7"], ["5:3"]])
            self.assertEqual(len(first), 3)
            self.assertEqual(len(cache.read_text(encoding="utf-8").splitlines()), 3)
            second = my_extract_all(self.records, self.schema, Path("unused"), cache,
                                    batch_size=2, completion=completion)
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 2)

    def test_invalid_category_is_not_cached(self):
        def completion(_system, prompt):
            source_id = json.loads(prompt)["records"][0]["source_id"]
            return json.dumps({"items": [{"source_id": source_id,
                                           "source_part_type": "不存在类型",
                                           "target_part_type": "翼子板", "reason": ""}]})

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "llm_cache.jsonl"
            result = my_extract_all(self.records[:1], self.schema, Path("unused"), cache,
                                    completion=completion)
            self.assertEqual(result, {})
            self.assertEqual(cache.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
