"""Turn recalled nodes and graph paths into natural-language RAG context."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from feature.benchmark_upstream_offline.io_utils import iter_jsonl

from .path_morphology import short_rel


_REL_NL: Dict[str, str] = {
    "StyleAssociatedWith": "关联汽车风格",
    "TypeAssociatedWith": "关联汽车车型",
    "Indicates": "体现",
    "ImplementedBy": "由实现",
    "Guides": "指导",
    "Influences": "影响",
    "Prefers": "偏好",
    "Constrains": "约束",
    "DefinesDNA": "定义家族DNA",
}


def _rel_nl(rel: str) -> str:
    return _REL_NL.get(short_rel(rel), short_rel(rel))


def load_node_descriptions(features_jsonl: Path) -> Dict[str, str]:
    """Map node name -> description from the feature corpus."""
    descriptions: Dict[str, str] = {}
    for record in iter_jsonl(features_jsonl):
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        description = str(record.get("description") or "").strip()
        if description:
            descriptions[name] = description
    return descriptions


def _parse_inspect_path(path: str) -> List[str]:
    return [part.strip() for part in re.split(r"\s*->\s*", path or "") if part.strip()]


def describe_node(name: str, descriptions: Mapping[str, str]) -> str:
    text = descriptions.get(name, "").strip()
    if text:
        return "「%s」（%s）" % (name, text)
    return "「%s」" % name


def path_to_narrative(path: str, descriptions: Mapping[str, str]) -> str:
    """Convert an inspect-style path into one Chinese sentence."""
    parts = _parse_inspect_path(path)
    if len(parts) < 3:
        return path or ""

    segments: List[str] = []
    idx = 0
    while idx < len(parts):
        node = parts[idx]
        if idx + 1 >= len(parts):
            segments.append(describe_node(node, descriptions))
            break
        rel = parts[idx + 1]
        next_node = parts[idx + 2] if idx + 2 < len(parts) else ""
        if rel in ("StyleAssociatedWith", "TypeAssociatedWith"):
            head_kind = "汽车风格" if rel == "StyleAssociatedWith" else "汽车车型"
            segments.append(
                "%s 通过「%s」%s %s"
                % (describe_node(node, descriptions), _rel_nl(rel), head_kind, describe_node(next_node, descriptions))
            )
            idx += 3
            continue
        segments.append(
            "%s 经「%s」连接到 %s"
            % (describe_node(node, descriptions), _rel_nl(rel), describe_node(next_node, descriptions))
        )
        idx += 2
    return "；".join(segments) + "。"


def neighbor_to_narrative(path: str, descriptions: Mapping[str, str]) -> str:
    """Neighbor evidence: recalled --Rel--> neighbor."""
    match = re.match(r"^(.+?)\s*--([^>]+)-->\s*(.+)$", (path or "").strip())
    if not match:
        return path or ""
    recalled, rel, neighbor = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    return "召回节点 %s 与邻接证据 %s 存在「%s」关系。" % (
        describe_node(recalled, descriptions),
        describe_node(neighbor, descriptions),
        _rel_nl(rel),
    )


def build_case_rag_context(
    *,
    keywords: Sequence[str],
    recalled_nodes: Sequence[Dict[str, Any]],
    paths: Sequence[Dict[str, Any]],
    descriptions: Mapping[str, str],
    max_paths: int = 30,
) -> Dict[str, Any]:
    """Build structured RAG context for one benchmark case."""
    recalled_summaries: List[str] = []
    for row in recalled_nodes:
        name = str(row.get("name") or "")
        score = float(row.get("score") or 0.0)
        label = str(row.get("label") or "")
        recalled_summaries.append(
            "- %s [label=%s, score=%.4f]" % (describe_node(name, descriptions), label, score)
        )

    path_narratives: List[str] = []
    for rec in paths[: max(0, int(max_paths))]:
        template = str(rec.get("template") or "")
        path = str(rec.get("path") or "")
        if not path:
            continue
        if template == "aesthetic_to_neighbor_evidence":
            narrative = neighbor_to_narrative(path, descriptions)
        else:
            narrative = path_to_narrative(path, descriptions)
        hop_count = int(rec.get("hop_count") or 0)
        path_narratives.append("- [hop=%d] %s" % (hop_count, narrative))

    context_text = "\n".join(
        [
            "用户关键词：%s" % "；".join(keywords),
            "",
            "召回的美学特征节点：",
            *recalled_summaries,
            "",
            "知识图谱路径证据（自然语言）：",
            *path_narratives,
        ]
    )
    return {
        "keywords": list(keywords),
        "recalled_summaries": recalled_summaries,
        "path_narratives": path_narratives,
        "context_text": context_text,
    }
