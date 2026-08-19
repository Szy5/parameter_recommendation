#!/usr/bin/env python3
"""Extract auditable full inputs and deterministic pilot samples."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Set, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        BODY_LABEL,
        BODY_PARAMETER_UNITS,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        GUIDES_LABEL,
        first_text,
        iter_jsonl,
        labels_contain,
        parse_inner,
        write_jsonl,
    )
else:
    from .common import (
        AC_LABEL,
        BODY_LABEL,
        BODY_PARAMETER_UNITS,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        GUIDES_LABEL,
        first_text,
        iter_jsonl,
        labels_contain,
        parse_inner,
        write_jsonl,
    )


STYLE_KEYWORDS = {
    "科技": ["科技", "未来", "智能", "数字", "digital", "future", "technology", "tech", "cyber", "autonomous"],
    "运动": ["运动", "动感", "速度", "性能", "跑车", "sport", "dynamic", "performance", "racing", "speed"],
    "豪华": ["豪华", "奢华", "尊贵", "高端", "luxury", "premium", "opulent", "refined", "prestige"],
    "硬派越野": ["越野", "硬派", "粗犷", "rugged", "off-road", "offroad", "adventure", "terrain"],
    "简约": ["简约", "简洁", "极简", "克制", "minimal", "simple", "simplicity", "clean"],
    "商务": ["商务", "行政", "正式", "稳重", "business", "executive", "formal", "professional"],
    "复古": ["复古", "经典", "怀旧", "传统", "retro", "classic", "heritage", "nostalgia", "historical"],
}


def concept_fields(properties: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    inner = parse_inner(properties.get("properties"))
    name = first_text(
        properties.get("name"),
        inner.get("conceptName(概念名称)"),
        inner.get("conceptName"),
    )
    description = first_text(inner.get("description(描述)"), inner.get("description"))
    aliases_raw = (
        inner.get("aliases(别名)")
        or inner.get("aliases")
        or inner.get("synonyms(同义词)")
        or inner.get("synonyms")
        or []
    )
    if isinstance(aliases_raw, str):
        aliases = [part.strip() for part in aliases_raw.replace("；", ";").split(";") if part.strip()]
    elif isinstance(aliases_raw, list):
        aliases = [str(item).strip() for item in aliases_raw if str(item).strip()]
    else:
        aliases = []
    return name, description, aliases[:20]


def parameter_name(properties: Dict[str, Any]) -> str:
    inner = parse_inner(properties.get("properties"))
    return first_text(
        properties.get("name"),
        inner.get("parameterName(参数名称)"),
        inner.get("parameterName"),
    )


def guide_text(properties: Dict[str, Any]) -> str:
    inner = parse_inner(properties.get("properties"))
    return first_text(
        inner.get("howToGuide(指导方式)"),
        inner.get("howToGuide"),
        inner.get("guidanceStrategy(指导策略)"),
        inner.get("guidanceStrategy"),
    )


def extract_concepts(meixue_path: Path) -> List[Dict[str, Any]]:
    concepts: Dict[str, Dict[str, Any]] = {}
    guides: DefaultDict[str, List[Dict[str, str]]] = defaultdict(list)

    for record in iter_jsonl(meixue_path):
        if record.get("type") == "node" and AC_LABEL in (record.get("labels") or []):
            name, description, aliases = concept_fields(record.get("properties") or {})
            concepts[str(record["id"])] = {
                "concept_id": str(record["id"]),
                "name": name,
                "description": description,
                "aliases": aliases,
                "frequency_weight": (record.get("properties") or {}).get("frequency_weight"),
                "source_file": str(meixue_path),
            }
        elif record.get("type") == "relationship" and record.get("label") == GUIDES_LABEL:
            start = record.get("start") or {}
            end = record.get("end") or {}
            if labels_contain(start, AC_LABEL) and labels_contain(end, DP_LABEL):
                guides[str(start.get("id"))].append(
                    {
                        "parameter": parameter_name(end.get("properties") or {}),
                        "how_to_guide": guide_text(record.get("properties") or {}),
                    }
                )

    rows: List[Dict[str, Any]] = []
    for concept_id in sorted(concepts, key=lambda item: int(item) if item.isdigit() else item):
        row = concepts[concept_id]
        row["guides"] = guides.get(concept_id, [])[:20]
        row["guide_count"] = len(guides.get(concept_id, []))
        rows.append(row)
    return rows


def keyword_scores(record: Dict[str, Any]) -> Dict[str, int]:
    text = " ".join(
        [
            str(record.get("name") or ""),
            str(record.get("description") or ""),
            " ".join(record.get("aliases") or []),
        ]
    ).lower()
    return {
        style: sum(1 for keyword in keywords if keyword.lower() in text)
        for style, keywords in STYLE_KEYWORDS.items()
    }


def select_concept_pilot(
    records: List[Dict[str, Any]], per_style: int, ambiguous_count: int, negative_count: int, seed: int
) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    scored = [(record, keyword_scores(record)) for record in records]
    selected: List[Dict[str, Any]] = []
    used: Set[str] = set()

    for style in STYLE_KEYWORDS:
        candidates = sorted(
            scored,
            key=lambda pair: (
                -pair[1][style],
                -(len(pair[0].get("description") or "")),
                pair[0]["concept_id"],
            ),
        )
        taken = 0
        for record, scores in candidates:
            if scores[style] <= 0 or record["concept_id"] in used:
                continue
            copy = dict(record)
            copy["pilot_stratum"] = "candidate_" + style
            selected.append(copy)
            used.add(record["concept_id"])
            taken += 1
            if taken >= per_style:
                break

    ambiguous = [
        (record, scores)
        for record, scores in scored
        if sum(1 for score in scores.values() if score > 0) >= 2 and record["concept_id"] not in used
    ]
    rng.shuffle(ambiguous)
    for record, _ in ambiguous[:ambiguous_count]:
        copy = dict(record)
        copy["pilot_stratum"] = "ambiguous"
        selected.append(copy)
        used.add(record["concept_id"])

    negatives = [
        record
        for record, scores in scored
        if not any(scores.values()) and record["concept_id"] not in used
    ]
    rng.shuffle(negatives)
    for record in negatives[:negative_count]:
        copy = dict(record)
        copy["pilot_stratum"] = "unaligned_control"
        selected.append(copy)
        used.add(record["concept_id"])

    return selected


def extract_cars(cars_path: Path) -> List[Dict[str, Any]]:
    instances: Dict[str, Dict[str, Any]] = {}
    class_by_instance: Dict[str, str] = {}
    body_by_instance: Dict[str, Dict[str, Any]] = {}

    for record in iter_jsonl(cars_path):
        if record.get("type") == "node" and CAR_INSTANCE_LABEL in (record.get("labels") or []):
            props = record.get("properties") or {}
            instances[str(record["id"])] = {
                "instance_id": str(record["id"]),
                "model_name": props.get("车型名称"),
                "manufacturer": props.get("厂商"),
                "image_urls": props.get("image_urls") or [],
                "source_file": str(cars_path),
            }
        elif record.get("type") == "relationship" and record.get("label") == CONTAINS_LABEL:
            start = record.get("start") or {}
            end = record.get("end") or {}
            if labels_contain(start, CAR_CLASS_LABEL) and labels_contain(end, CAR_INSTANCE_LABEL):
                class_by_instance[str(end.get("id"))] = str((start.get("properties") or {}).get("name") or "")
            elif labels_contain(start, CAR_INSTANCE_LABEL) and labels_contain(end, BODY_LABEL):
                body_by_instance[str(start.get("id"))] = end.get("properties") or {}

    rows: List[Dict[str, Any]] = []
    for instance_id in sorted(instances, key=lambda item: int(item) if item.isdigit() else item):
        row = instances[instance_id]
        body = body_by_instance.get(instance_id, {})
        row["car_class"] = class_by_instance.get(instance_id, "")
        row["body_parameters"] = {
            key: body[key]
            for key in BODY_PARAMETER_UNITS
            if key in body and str(body[key]).strip() not in ("", "无", "-")
        }
        rows.append(row)
    return rows


CAR_PILOT_GROUPS = {
    "sports": {"跑车"},
    "suv": {"小型SUV", "紧凑型SUV", "中型SUV", "中大型SUV", "大型SUV"},
    "sedan": {"紧凑型车", "中型车", "中大型车", "大型车"},
    "mpv_business": {"紧凑型MPV", "中型MPV", "中大型MPV", "大型MPV"},
    "utility": {"皮卡", "轻客", "微面"},
    "small": {"微型车", "小型车"},
}


def select_car_pilot(records: List[Dict[str, Any]], per_group: int, seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    used: Set[str] = set()
    for group, classes in CAR_PILOT_GROUPS.items():
        candidates = [
            record
            for record in records
            if record.get("car_class") in classes
            and len(record.get("image_urls") or []) >= 3
            and record.get("body_parameters")
        ]
        rng.shuffle(candidates)
        candidates.sort(key=lambda row: (row.get("car_class") or "", row.get("model_name") or ""))
        # Shuffle once more so each class group is not biased toward alphabetic order.
        rng.shuffle(candidates)
        for record in candidates:
            if record["instance_id"] in used:
                continue
            copy = dict(record)
            copy["pilot_stratum"] = group
            selected.append(copy)
            used.add(record["instance_id"])
            if sum(1 for item in selected if item["pilot_stratum"] == group) >= per_group:
                break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract parameter-recommendation judge inputs")
    parser.add_argument("--meixue", type=Path, default=Path("meixue(1).json"))
    parser.add_argument("--cars", type=Path, default=Path("cars2.json"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--concept-per-style", type=int, default=4)
    parser.add_argument("--concept-ambiguous", type=int, default=7)
    parser.add_argument("--concept-negative", type=int, default=10)
    parser.add_argument("--car-per-group", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260802)
    args = parser.parse_args()

    concepts = extract_concepts(args.meixue)
    concept_pilot = select_concept_pilot(
        concepts,
        per_style=args.concept_per_style,
        ambiguous_count=args.concept_ambiguous,
        negative_count=args.concept_negative,
        seed=args.seed,
    )
    cars = extract_cars(args.cars)
    car_pilot = select_car_pilot(cars, per_group=args.car_per_group, seed=args.seed)

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    counts = {
        "concept_all": write_jsonl(args.artifact_dir / "01_concept_input_all.jsonl", concepts),
        "concept_pilot": write_jsonl(args.artifact_dir / "02_concept_input_pilot.jsonl", concept_pilot),
        "car_all": write_jsonl(args.artifact_dir / "03_style_input_all.jsonl", cars),
        "car_pilot": write_jsonl(args.artifact_dir / "04_style_input_pilot.jsonl", car_pilot),
    }
    (args.artifact_dir / "extraction_report.json").write_text(
        json.dumps(
            {
                **counts,
                "seed": args.seed,
                "concept_pilot_strata": dict(
                    (key, sum(1 for row in concept_pilot if row.get("pilot_stratum") == key))
                    for key in sorted({row.get("pilot_stratum") for row in concept_pilot})
                ),
                "car_pilot_strata": dict(
                    (key, sum(1 for row in car_pilot if row.get("pilot_stratum") == key))
                    for key in sorted({row.get("pilot_stratum") for row in car_pilot})
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

