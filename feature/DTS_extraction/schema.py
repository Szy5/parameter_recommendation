"""The deliberately small, vehicle-scoped DTS graph schema."""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List


SHEET_VEHICLES = {
    "4": "硬派越野车",
    "5": "城市SUV",
    "7": "豪华轿车",
    "9": "豪华SUV",
}

METRICS = ("Gap", "Flush", "Ra", "Rb", "Alignment", "Consistent", "Radii")
POSITION_VALUES = (
    "前侧", "后侧", "上侧", "下侧", "左侧", "右侧", "内含", "安装侧",
    "前侧/上侧", "后侧/下侧",
)

# These are spelling aliases, not rules for extracting an A-to-B relation.
PART_ALIASES = {
    "机盖": "发动机盖",
    "引擎盖": "发动机盖",
    "前保": "前保险杠",
    "前保杠": "前保险杠",
    "前大灯": "前车灯",
    "前组合灯": "前车灯",
    "后组合灯": "后车灯",
    "尾灯": "后车灯",
    "前风挡玻璃": "前风挡",
    "后风挡玻璃": "后风挡",
    "前挡风玻璃": "前风挡",
    "后挡风玻璃": "后风挡",
    "前门": "前车门",
    "后门": "后车门",
    "背门": "尾门/后备箱盖",
    "尾门": "尾门/后备箱盖",
    "后背门": "尾门/后备箱盖",
    "后备箱盖": "尾门/后备箱盖",
    "尾门/后背门": "尾门/后备箱盖",
    "车顶": "顶盖",
}

GRAPH_SCHEMA: Dict[str, object] = {
    "nodes": {
        "VehicleType": ["id", "name"],
        "Part+<canonical concrete part label>": ["id", "name"],
    },
    "relationships": {
        "CONTAINS": ["classification", "area"],
        "DTS_POSITION_RELATION": [
            "relative_position", "Gap", "Flush", "Ra", "Rb",
            "Alignment", "Consistent", "Radii", "classification", "area",
        ],
    },
    "relative_position_values": list(POSITION_VALUES),
    "notes": [
        "Part ids are scoped to a vehicle; the four vehicle subgraphs never share Part nodes.",
        "No relationship id is stored. Identical endpoint and business-property tuples merge.",
        "Empty string means the source has no value for that optional property.",
    ],
}


def canonical_part(raw: str) -> str:
    """Normalize harmless typography and a small set of verified name aliases."""
    value = re.sub(r"\s+", "", str(raw or ""))
    value = value.strip("：:、，,。.;；（）()")
    if not value or len(value) > 80 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid concrete part name: %r" % raw)
    if value in {"Part", "VehicleType", "未知", "部件", "饰板", "饰条", "装饰件", "装饰件n", "饰条n"}:
        raise ValueError("Non-concrete part name: %r" % raw)
    return PART_ALIASES.get(value, value)


def vehicle_id(sheet: str) -> str:
    return "DTS:vehicle:" + sheet


def part_id(sheet: str, name: str) -> str:
    digest = hashlib.sha256((sheet + "\0" + name).encode("utf-8")).hexdigest()[:24]
    return "DTS:part:" + sheet + ":" + digest


def blank_metrics() -> Dict[str, str]:
    return {metric: "" for metric in METRICS}


def labels_for_part(name: str) -> List[str]:
    return ["Part", canonical_part(name)]
