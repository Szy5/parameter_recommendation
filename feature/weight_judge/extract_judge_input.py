#!/usr/bin/env python3
"""Extract AestheticConcept --Guides--> DesignParameter edges into judge_input.jsonl."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROMPT_VERSION = "v1.1"

AC_LABEL = "AestheticConcept(美学概念)"
DP_LABEL = "DesignParameter(设计参数)"
GUIDES = "Guides(指导)"


def parse_inner(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def first_str(*values) -> str:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return ""


def aesthetic_fields(node_props: dict) -> dict:
    inner = parse_inner(node_props.get("properties"))
    name = first_str(
        node_props.get("name"),
        inner.get("conceptName(概念名称)"),
        inner.get("conceptName"),
    )
    description = first_str(
        inner.get("description(描述)"),
        inner.get("description"),
    )
    return {"name": name, "description": description}


def parameter_fields(node_props: dict) -> dict:
    inner = parse_inner(node_props.get("properties"))
    name = first_str(
        node_props.get("name"),
        inner.get("parameterName(参数名称)"),
        inner.get("parameterName"),
    )
    range_text = first_str(
        inner.get("range(范围)"),
        inner.get("range"),
    )
    unit = first_str(
        inner.get("unit(单位)"),
        inner.get("unit"),
    )
    return {"name": name, "range_text": range_text, "unit": unit}


def extract_how_to_guide(edge_props: dict) -> str:
    inner = parse_inner(edge_props.get("properties"))
    return first_str(
        inner.get("howToGuide(指导方式)"),
        inner.get("howToGuide"),
        inner.get("guidanceStrategy(指导策略)"),
        inner.get("guidanceStrategy"),
        edge_props.get("howToGuide(指导方式)"),
        edge_props.get("howToGuide"),
    )


def extract_edges(source_path: Path, source_label: str) -> list[dict]:
    records: list[dict] = []
    with source_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") != "relationship":
                continue
            if obj.get("label") != GUIDES:
                continue
            start = obj.get("start") or {}
            end = obj.get("end") or {}
            if AC_LABEL not in start.get("labels", []):
                continue
            if DP_LABEL not in end.get("labels", []):
                continue

            aesthetic = aesthetic_fields(start.get("properties") or {})
            parameter = parameter_fields(end.get("properties") or {})
            how_to_guide = extract_how_to_guide(obj.get("properties") or {})

            records.append(
                {
                    "edge_id": str(obj.get("id")),
                    "start_id": str(start.get("id")),
                    "end_id": str(end.get("id")),
                    "rel_label": GUIDES,
                    "aesthetic": aesthetic,
                    "parameter": parameter,
                    "how_to_guide": how_to_guide,
                    "source_file": source_label,
                    "prompt_version": PROMPT_VERSION,
                }
            )
    return records


def diversify_sample(records: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Prefer diverse aesthetics; fill remaining with round-robin."""
    import random

    rng = random.Random(seed)
    by_aes: dict[str, list[dict]] = {}
    for r in records:
        key = r["aesthetic"]["name"] or r["start_id"]
        by_aes.setdefault(key, []).append(r)

    keys = list(by_aes.keys())
    rng.shuffle(keys)
    for k in keys:
        rng.shuffle(by_aes[k])

    picked: list[dict] = []
    # first pass: one per aesthetic
    for k in keys:
        if len(picked) >= n:
            break
        picked.append(by_aes[k].pop())
    # fill
    pool = [item for k in keys for item in by_aes[k]]
    rng.shuffle(pool)
    for item in pool:
        if len(picked) >= n:
            break
        picked.append(item)
    return picked[:n]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Guides edges for LLM judge")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "6-25"
        / "meixue_cars2_with_corresponds_to.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "artifacts"
        / "weight_judge"
        / "full"
        / "01_judge_input.jsonl",
    )
    parser.add_argument("--limit", type=int, default=0, help="If >0, write only N diversified samples")
    parser.add_argument(
        "--pilot-out",
        type=Path,
        default=None,
        help="Optional separate path for limited pilot sample",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    source_label = str(args.source.relative_to(Path(__file__).resolve().parents[2]))
    records = extract_edges(args.source, source_label)
    write_jsonl(args.out, records)
    print(f"Wrote {len(records)} records -> {args.out}")

    if args.limit > 0:
        pilot = diversify_sample(records, args.limit, seed=args.seed)
        pilot_path = args.pilot_out or args.out.with_name("01_judge_input_pilot.jsonl")
        write_jsonl(pilot_path, pilot)
        empty_guide = sum(1 for r in pilot if not r["how_to_guide"])
        print(f"Wrote pilot {len(pilot)} records -> {pilot_path} (empty how_to_guide={empty_guide})")


if __name__ == "__main__":
    main()
