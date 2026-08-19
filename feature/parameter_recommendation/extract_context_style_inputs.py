#!/usr/bin/env python3
"""Extract graph-grounded style criteria and per-vehicle Judge contexts."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        GUIDES_LABEL,
        MAIN_STYLES,
        iter_jsonl,
        parse_inner,
        write_jsonl,
    )
else:
    from .common import (
        AC_LABEL,
        CAR_CLASS_LABEL,
        CAR_INSTANCE_LABEL,
        CONTAINS_LABEL,
        DP_LABEL,
        GUIDES_LABEL,
        MAIN_STYLES,
        iter_jsonl,
        parse_inner,
        write_jsonl,
    )


PROMPT_VERSION = "v2.0-graph-context-single-pass"
STYLE_ID_PREFIX = "meixue_style_"

RELEVANT_TAIL_LABELS = [
    "车身",
    "车轮制动",
    "底盘转向",
    "变速箱",
    "外观/防盗",
    "车外灯光",
    "外后视镜",
    "驾驶操控",
    "四驱/越野",
    "发动机",
    "电动机",
    "电池/充电",
    "屏幕/系统",
    "驾驶硬件",
    "驾驶功能",
    "智能化配置",
    "方向盘/内后视镜",
    "座椅配置",
    "音响/车内灯光",
    "天窗/玻璃",
    "空调/冰箱",
    "特色配置",
    "选装包",
]

VEHICLE_BASE_FIELDS = [
    "车型名称",
    "厂商",
    "级别",
    "能源类型",
    "车身结构",
    "长*宽*高(mm)",
    "厂商指导价(元)",
    "整备质量(kg)",
    "最大满载质量(kg)",
    "最大功率(kW)",
    "最大扭矩(N·m)",
    "官方0-100km/h加速(s)",
    "官方0-50km/h加速(s)",
    "最高车速(km/h)",
    "发动机",
    "电动机(Ps)",
    "变速箱",
    "CLTC纯电续航里程(km)",
    "WLTC纯电续航里程(km)",
]

EMPTY_VALUES = {"", "无", "-", "--", "暂无", "暂无数据", "null", "None"}


def first_label(endpoint: Dict[str, Any]) -> Optional[str]:
    labels = endpoint.get("labels") or []
    return str(labels[0]) if labels else None


def clean_properties(properties: Dict[str, Any], drop_name: bool = True) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in properties.items():
        if key == "image_urls" or (drop_name and key == "name"):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip() in EMPTY_VALUES:
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        cleaned[str(key)] = value.strip() if isinstance(value, str) else value
    return cleaned


def guidance_payloads(edge_properties: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    merged = edge_properties.get("merged_source_relationships")
    if isinstance(merged, str):
        try:
            sources = json.loads(merged)
        except json.JSONDecodeError:
            sources = []
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                properties = source.get("properties") or {}
                yield parse_inner(properties.get("properties"))
            return
    yield parse_inner(edge_properties.get("properties"))


def extract_guidance_text(edge_properties: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for inner in guidance_payloads(edge_properties):
        for key in (
            "howToGuide(指导方式)",
            "howToGuide",
            "guidanceStrategy(指导策略)",
            "guidanceStrategy",
        ):
            value = inner.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in values:
                values.append(value.strip())
    return values


def parameter_fields(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    properties = endpoint.get("properties") or {}
    inner = parse_inner(properties.get("properties"))
    return {
        "name": properties.get("name")
        or inner.get("parameterName(参数名称)")
        or inner.get("parameterName"),
        "range": inner.get("range(范围)") if "range(范围)" in inner else inner.get("range"),
        "unit": inner.get("unit(单位)") if "unit(单位)" in inner else inner.get("unit"),
        "description": inner.get("description(描述)") or inner.get("description"),
    }


def extract_style_criteria(aesthetic_graph: Path) -> Dict[str, Any]:
    criteria: Dict[str, Dict[str, Dict[str, Any]]] = {
        style: {} for style in MAIN_STYLES
    }
    for record in iter_jsonl(aesthetic_graph):
        if record.get("type") != "relationship" or record.get("label") != GUIDES_LABEL:
            continue
        start = record.get("start") or {}
        end = record.get("end") or {}
        if AC_LABEL not in (start.get("labels") or []) or DP_LABEL not in (end.get("labels") or []):
            continue
        style = str((start.get("properties") or {}).get("name") or "")
        if style not in criteria:
            continue
        fields = parameter_fields(end)
        name = str(fields.get("name") or "").strip()
        if not name:
            continue
        existing = criteria[style].get(name)
        guidance = extract_guidance_text(record.get("properties") or {})
        if existing is None:
            existing = {
                "name": name,
                "range": fields.get("range"),
                "unit": fields.get("unit"),
                "description": fields.get("description"),
                "guidance": [],
            }
            criteria[style][name] = existing
        for text in guidance:
            if text not in existing["guidance"]:
                existing["guidance"].append(text)

    return {
        "prompt_version": PROMPT_VERSION,
        "source_graph": str(aesthetic_graph),
        "styles": {
            style: list(criteria[style].values()) for style in MAIN_STYLES
        },
    }


def car_record(record: Dict[str, Any]) -> bool:
    return str(record.get("id") or "").startswith("car_")


def raw_car_id(value: Any) -> str:
    text = str(value)
    return text[len("car_") :] if text.startswith("car_") else text


def extract_vehicle_contexts(car_graph: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    instances: Dict[str, Dict[str, Any]] = {}
    classes: Dict[str, str] = {}
    tails: DefaultDict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    car_records = 0

    for record in iter_jsonl(car_graph):
        if not car_record(record):
            continue
        car_records += 1
        if record.get("type") == "node" and CAR_INSTANCE_LABEL in (record.get("labels") or []):
            instances[raw_car_id(record["id"])] = record.get("properties") or {}
            continue
        if record.get("type") != "relationship" or record.get("label") != CONTAINS_LABEL:
            continue
        start = record.get("start") or {}
        end = record.get("end") or {}
        start_labels = start.get("labels") or []
        end_labels = end.get("labels") or []
        if CAR_CLASS_LABEL in start_labels and CAR_INSTANCE_LABEL in end_labels:
            classes[raw_car_id(end.get("id"))] = str(
                (start.get("properties") or {}).get("name") or ""
            )
        elif CAR_INSTANCE_LABEL in start_labels:
            label = first_label(end)
            if label in RELEVANT_TAIL_LABELS:
                tails[raw_car_id(start.get("id"))][str(label)] = clean_properties(
                    end.get("properties") or {}
                )

    rows: List[Dict[str, Any]] = []
    tail_coverage = Counter()
    context_lengths: List[int] = []
    for instance_id in sorted(
        instances, key=lambda value: int(value) if value.isdigit() else value
    ):
        properties = instances[instance_id]
        vehicle = {
            key: properties[key]
            for key in VEHICLE_BASE_FIELDS
            if key in properties
            and properties[key] is not None
            and (not isinstance(properties[key], str) or properties[key].strip() not in EMPTY_VALUES)
        }
        car_class = classes.get(instance_id) or str(properties.get("级别") or "")
        if car_class:
            vehicle["级别"] = car_class
        contains_context = {
            label: tails[instance_id][label]
            for label in RELEVANT_TAIL_LABELS
            if tails[instance_id].get(label)
        }
        tail_coverage.update(contains_context.keys())
        row = {
            "instance_id": instance_id,
            "model_name": properties.get("车型名称"),
            "car_class": car_class,
            "vehicle": vehicle,
            "contains_context": contains_context,
            "source_graph": str(car_graph),
        }
        context_lengths.append(len(json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
        rows.append(row)

    report = {
        "source_graph": str(car_graph),
        "source_car_records": car_records,
        "vehicle_instances": len(rows),
        "relevant_tail_labels": RELEVANT_TAIL_LABELS,
        "tail_coverage": {label: tail_coverage[label] for label in RELEVANT_TAIL_LABELS},
        "context_json_characters": {
            "mean": round(statistics.mean(context_lengths), 1) if context_lengths else 0,
            "median": round(statistics.median(context_lengths), 1) if context_lengths else 0,
            "min": min(context_lengths) if context_lengths else 0,
            "max": max(context_lengths) if context_lengths else 0,
        },
    }
    return rows, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract graph-context automobile style Judge inputs")
    parser.add_argument("--aesthetic-graph", type=Path, required=True)
    parser.add_argument("--car-graph", type=Path, required=True)
    parser.add_argument("--criteria-output", type=Path, required=True)
    parser.add_argument("--vehicle-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    criteria = extract_style_criteria(args.aesthetic_graph)
    vehicles, vehicle_report = extract_vehicle_contexts(args.car_graph)
    if len(vehicles) != 800:
        raise SystemExit("Expected 800 automobile instances, found %d" % len(vehicles))

    args.criteria_output.parent.mkdir(parents=True, exist_ok=True)
    args.criteria_output.write_text(
        json.dumps(criteria, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    vehicle_count = write_jsonl(args.vehicle_output, vehicles)
    report = {
        "prompt_version": PROMPT_VERSION,
        "style_parameter_counts": {
            style: len(criteria["styles"][style]) for style in MAIN_STYLES
        },
        "style_criteria_characters": len(
            json.dumps(criteria["styles"], ensure_ascii=False, separators=(",", ":"))
        ),
        "vehicle_output_records": vehicle_count,
        **vehicle_report,
        "criteria_output": str(args.criteria_output),
        "vehicle_output": str(args.vehicle_output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
