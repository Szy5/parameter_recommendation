"""解析预测结果，统计每个 Benchmark case 的路径 schema。"""

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KGDATA = PROJECT_ROOT / "data" / "kgdata_0820.jsonl"
DEFAULT_BENCHMARK_PREDICT = (
    PROJECT_ROOT
    / "feature"
    / "benchmark_recommendation"
    / "artifacts"
    / "benchmark_predict.json"
)
DEFAULT_OUTPUT_SCHEMAS = PROJECT_ROOT / "data" / "path_schemas.json"


# 解析图谱文件
def read_jsonl(path: Path):
    json_list = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            json_list.append(json.loads(line))
    return json_list


# 获取 name -> label 字典
def make_dict_by_name(json_list):
    dict_by_name = {}
    for record in json_list:
        if record["type"] != "node":
            continue
        name = record.get("properties", {}).get("name")
        labels = record.get("labels") or []
        if not name or not labels:
            continue
        dict_by_name[str(name)] = str(labels[0])
    return dict_by_name


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kgdata", type=Path, default=DEFAULT_KGDATA)
    parser.add_argument(
        "--benchmark-predict",
        type=Path,
        default=DEFAULT_BENCHMARK_PREDICT,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_SCHEMAS)
    return parser.parse_args()


def main():
    args = parse_args()
    dict_by_name = make_dict_by_name(read_jsonl(args.kgdata))
    with args.benchmark_predict.open("r", encoding="utf-8") as f:
        result = json.load(f)
    cases = result['cases']
    outputs = []

    #处理每一个case
    for case in cases:
        paths = case['paths']
        schema_set = set()
        for record in paths:
            path = record['path']
            values = path.strip().split(' -> ')
            schema = dict_by_name[values[0]]
            for i , value in enumerate(values[1:]):
                if i % 2 == 0:
                    schema += ' -> ' + value
                else:
                    schema += ' -> ' + dict_by_name[value]
            schema_set.add(schema)
        outputs.append(
            {
                "id": case["id"],
                "keywords": case["input"]["keywords"],
                "path_schemas": sorted(schema_set),
                "schemas_count": len(schema_set),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(outputs, f, ensure_ascii=False, indent=4)
        f.write("\n")


if __name__ == "__main__":
    main()
