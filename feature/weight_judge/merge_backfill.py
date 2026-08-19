#!/usr/bin/env python3
"""Merge LLM judge weights back into source graph JSONL (delivery artifact C)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

AC_LABEL = "AestheticConcept(美学概念)"
DP_LABEL = "DesignParameter(设计参数)"
GUIDES = "Guides(指导)"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_weight_map(judge_output: Path) -> dict[str, dict]:
    """edge_id -> best ok result (last ok wins)."""
    mapping: dict[str, dict] = {}
    errors = []
    with judge_output.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            eid = str(obj.get("edge_id"))
            if obj.get("status") == "ok" and obj.get("weight") is not None:
                mapping[eid] = obj
            else:
                errors.append(obj)
    return mapping, errors


def is_ac_dp_guides(obj: dict) -> bool:
    if obj.get("type") != "relationship" or obj.get("label") != GUIDES:
        return False
    start = obj.get("start") or {}
    end = obj.get("end") or {}
    return AC_LABEL in start.get("labels", []) and DP_LABEL in end.get("labels", [])


def merge(
    source: Path,
    judge_output: Path,
    out_path: Path,
    report_path: Path,
) -> dict:
    weight_map, judge_errors = load_weight_map(judge_output)

    total_lines = 0
    ac_dp_guides = 0
    merged = 0
    missing_weight = []
    non_target_guides = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            raw = line.strip()
            if not raw:
                continue
            total_lines += 1
            obj = json.loads(raw)

            if is_ac_dp_guides(obj):
                ac_dp_guides += 1
                eid = str(obj.get("id"))
                judged = weight_map.get(eid)
                if judged:
                    props = obj.setdefault("properties", {})
                    props["weight"] = judged["weight"]
                    props["reason"] = judged.get("reason")
                    props["weight_source"] = "llm_judge"
                    props["prompt_version"] = judged.get("prompt_version")
                    props["judge_model"] = judged.get("model")
                    if judged.get("confidence") is not None:
                        props["confidence"] = judged["confidence"]
                    merged += 1
                else:
                    missing_weight.append(eid)
            elif obj.get("type") == "relationship" and obj.get("label") == GUIDES:
                non_target_guides += 1

            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

    weights = [v["weight"] for v in weight_map.values()]
    hist = Counter(weights)
    report = {
        "source": str(source),
        "judge_output": str(judge_output),
        "delivery": str(out_path),
        "total_source_lines": total_lines,
        "ac_dp_guides_edges": ac_dp_guides,
        "merged_with_weight": merged,
        "missing_weight_count": len(missing_weight),
        "missing_weight_edge_ids_sample": missing_weight[:50],
        "judge_ok_unique_edges": len(weight_map),
        "judge_error_rows": len(judge_errors),
        "other_guides_untouched": non_target_guides,
        "weight_hist": {str(k): hist[k] for k in sorted(hist)},
        "weight_mean": round(sum(weights) / len(weights), 3) if weights else None,
        "success": len(missing_weight) == 0 and merged == ac_dp_guides,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Merge judge weights into graph JSONL")
    parser.add_argument(
        "--source",
        type=Path,
        default=repo / "6-25" / "meixue_cars2_with_corresponds_to.json",
    )
    parser.add_argument(
        "--judge-output",
        type=Path,
        default=root / "artifacts" / "weight_judge" / "full" / "02_judge_output.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=root
        / "artifacts"
        / "weight_judge"
        / "full"
        / "03_meixue_cars2_with_guides_weight.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=root / "artifacts" / "weight_judge" / "full" / "merge_report.json",
    )
    args = parser.parse_args()

    report = merge(args.source, args.judge_output, args.out, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["success"]:
        raise SystemExit(
            f"Merge incomplete: missing={report['missing_weight_count']} "
            f"merged={report['merged_with_weight']}/{report['ac_dp_guides_edges']}"
        )


if __name__ == "__main__":
    main()
