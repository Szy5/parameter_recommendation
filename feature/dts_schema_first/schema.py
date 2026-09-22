"""PDF-derived exterior position schema. No workbook or LLM can extend its edges.

The tuples below transcribe the *direct* branches of the supplied XMind PDF.
An ``n`` suffix is a drawing template, not a concrete part label. The two
odd 后风挡→前风挡 branches are kept as printed but require human review.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PDF_SHA256 = "ed246ab0d57745b3d185581cda1981bab186c2cb26b098d84dfde4c70337d606"
SCHEMA_VERSION = "pdf-exterior-v1"
METRICS = ("Gap", "Flush", "Ra", "Rb", "Alignment", "Consistent", "Radii")
POSITIONS = ("前侧", "后侧", "上侧", "下侧", "左侧", "右侧", "内含", "安装侧", "前侧/上侧", "后侧/下侧")
SHEET_VEHICLES = {
    "4": "硬派越野车", "5": "城市SUV", "7": "豪华轿车", "9": "豪华SUV",
}
PART_ALIASES = {
    "机盖": "发动机盖", "引擎盖": "发动机盖",
    "前保": "前保险杠", "前保杠": "前保险杠",
    "前大灯": "前车灯", "前组合灯": "前车灯",
    "后组合灯": "后车灯", "尾灯": "后车灯",
    "前风挡玻璃": "前风挡", "前挡风玻璃": "前风挡",
    "后风挡玻璃": "后风挡", "后挡风玻璃": "后风挡",
    "前门": "前车门", "后门": "后车门",
    "背门": "尾门/后备箱盖", "尾门": "尾门/后备箱盖",
    "后背门": "尾门/后备箱盖", "后备箱盖": "尾门/后备箱盖",
    "尾门/后背门": "尾门/后备箱盖", "车顶": "顶盖",
}

# Every PDF hub and every named, direct position branch. Repeated identical
# branches in the drawing are represented once; left/right are not collapsed.
PDF_BRANCHES = {
    "前保险杠": {
        "上侧": ("饰条", "前车灯", "饰板", "发动机盖"),
        "左侧": ("翼子板", "饰板"),
        "右侧": ("翼子板", "饰板"),
        "内含": ("前车灯", "前雾灯", "日间行车灯", "格栅1", "格栅2", "饰条1", "饰条2", "饰条n", "装饰件1", "装饰件2", "装饰件n", "超声波雷达", "前牌照安装板", "毫米波雷达", "前拖车钩盖", "摄像头"),
    },
    "发动机盖": {
        "前侧": ("饰条", "前车灯", "饰板", "前保险杠"),
        "右侧": ("翼子板",), "左侧": ("翼子板",), "内含": ("装饰件",),
    },
    "前风挡": {
        "后侧": ("顶盖", "饰板"),
        "左侧": ("侧围", "饰条"), "右侧": ("侧围", "饰条"),
    },
    "顶盖": {
        "前侧": ("前风挡",),
        "后侧": ("后风挡", "饰板", "扰流板"),
        "左侧": ("侧围", "饰条"), "右侧": ("侧围", "饰条"),
        "内含": ("天窗", "饰条", "饰板", "鲨鱼鳍天线", "扰流板"),
    },
    "后风挡": {
        "前侧": ("前风挡", "饰板", "扰流板"),
        "后侧": ("前风挡", "饰板"),
        "左侧": ("侧围", "饰条"), "右侧": ("侧围", "饰条"),
    },
    "尾门/后备箱盖": {
        "前侧/上侧": ("饰板", "后风挡"),
        "后侧/下侧": ("后保险杠",),
        "左侧": ("侧围", "饰板"), "右侧": ("侧围", "饰板"),
        "内含": ("饰条", "饰板", "后车灯", "尾门把手", "储物盒"),
    },
    "后保险杠": {
        "上侧": ("尾门/后背门",),
        "左侧": ("侧围", "轮眉饰板"), "右侧": ("侧围", "轮眉饰板"),
        "内含": ("后车灯", "后雾灯", "回复反射器", "饰条1", "饰条2", "饰条n", "装饰件1", "装饰件2", "装饰件n", "超声波雷达", "后牌照安装板", "后牌照灯", "后拖车钩盖", "摄像头"),
    },
    "翼子板": {
        "前侧": ("前保险杠", "前车灯"), "后侧": ("前车门",),
        "下侧": ("饰板",), "上侧": ("发动机盖",),
        "内含": ("轮眉饰板", "装饰件"),
    },
    "侧围": {
        "前侧": ("前角窗", "前风挡", "发动机盖", "翼子板"),
        "后侧": ("后风挡", "尾门/后备箱盖", "后保险杠", "后车灯", "饰板"),
        "上侧": ("顶盖", "饰条"), "下侧": ("轮眉饰板",),
        "内含": ("轮眉饰板", "加油口门", "前角窗", "后角窗"),
    },
    "前车门": {
        "前侧": ("翼子板", "前角窗"), "后侧": ("后车门",),
        "上侧": ("侧围",), "下侧": ("侧围", "饰板"),
        "内含": ("前门玻璃", "车门把手", "前角窗", "B柱饰板"),
    },
    "后车门": {
        "前侧": ("前车门", "B柱饰板"),
        "后侧": ("侧围", "轮眉饰板", "C柱饰板", "后角窗"),
        "上侧": ("侧围",), "下侧": ("侧围", "饰板"),
        "内含": ("后门玻璃", "轮眉饰板", "车门把手", "后角窗"),
    },
    "后视镜": {
        "安装侧": ("前车门", "前角窗"),
        "内含": ("本体上壳", "本体下壳", "镜臂上壳", "镜臂下壳", "转向灯", "镜片", "摄像头1", "摄像头2", "摄像头n"),
    },
}

# Additional *nested* position branches in the PDF, not inferred reversals.
NESTED_BRANCHES = (
    ("扰流板", "高位制动灯", "内含", "顶盖"),
    ("扰流板", "摄像头", "内含", "顶盖"),
    ("装饰件", "摄像头1", "内含", "翼子板"),
    ("装饰件", "摄像头2", "内含", "翼子板"),
    ("装饰件", "摄像头n", "内含", "翼子板"),
)

TEMPLATE_NAMES = frozenset({"饰条n", "装饰件n", "摄像头n"})
PDF_ALIASES = {"尾门/后背门": "尾门/后备箱盖"}
SOURCE_ANOMALIES = frozenset({
    ("后风挡", "前风挡", "前侧"),
    ("后风挡", "前风挡", "后侧"),
})


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    position: str
    context: str = ""
    status: str = "approved"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "source": self.source, "target": self.target,
            "relative_position": self.position, "context": self.context,
            "status": self.status,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_schema(pdf: Path) -> dict:
    """Verify the exact PDF, then materialize the position schema before XLSX work."""
    digest = sha256_file(pdf)
    if digest != PDF_SHA256:
        raise ValueError("PDF SHA-256 differs from the reviewed PDF; re-audit schema before extraction")
    edges = []
    for source, branches in PDF_BRANCHES.items():
        for position, targets in branches.items():
            assert position in POSITIONS
            for target in targets:
                status = "template" if target in TEMPLATE_NAMES else "review_source" if (source, target, position) in SOURCE_ANOMALIES else "approved"
                edges.append(Edge(f"P{len(edges)+1:03}", source, PDF_ALIASES.get(target, target), position, context="PDF 原文：" + target if target in PDF_ALIASES else "", status=status))
    for source, target, position, context in NESTED_BRANCHES:
        status = "template" if target in TEMPLATE_NAMES else "approved"
        edges.append(Edge(f"P{len(edges)+1:03}", source, target, position, context, status))
    parts = sorted({name for e in edges for name in (e.source, e.target)})
    return {
        "schema_version": SCHEMA_VERSION,
        "graph_version": "part-instance-v2",
        "source_pdf_sha256": digest,
        "scope": "外形图：不定义内饰关系；未定义的 XLSX 记录进入 review",
        "node_types": ["VehicleType", "Part + XLSX 原文中的具体部件 label；节点 id 由车型、名称、物理位置共同确定"],
        "relationship_types": {
            "CONTAINS": {"from": "VehicleType", "to": "Part", "properties": ["location"]},
            "DTS_POSITION_RELATION": {"from": "Part", "to": "Part", "properties": ["relative_position", *METRICS, "classification", "area"]},
        },
        "vehicle_types": list(SHEET_VEHICLES.values()),
        "allowed_relative_positions": list(POSITIONS),
        "pdf_part_categories": [{"name": name, "status": "template" if name in TEMPLATE_NAMES else "approved"} for name in parts],
        "edges": [edge.as_dict() for edge in edges],
        "notes": ["×无DTS定义不是边", "方位有向，不推断反向", "PDF 名称是部件类别；最终节点 label 从 XLSX 的具体部件名称取", "n 后缀为模板，仅在 XLSX 给出具体名称时可匹配，不能建字面 n 节点", "后风挡→前风挡按 PDF 原文保留，需人工确认"],
    }


def approved_edges(schema: dict) -> dict[str, dict]:
    return {edge["id"]: edge for edge in schema["edges"] if edge["status"] in {"approved", "template"}}
