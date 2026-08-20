"""Post-filter inspect paths: score, unique (head, recalled), keep top style/type heads."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _parts(path: str) -> List[str]:
    return [part.strip() for part in (path or "").split(" -> ") if part.strip()]


def _main_fields(path: str) -> Optional[Tuple[str, str, str]]:
    parts = _parts(path)
    if len(parts) < 3:
        return None
    rel = parts[1]
    if rel not in ("StyleAssociatedWith", "TypeAssociatedWith"):
        return None
    return parts[0], rel, parts[-1]


def _score_map(recalled_nodes: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for row in recalled_nodes:
        name = str(row.get("name") or "")
        if not name:
            continue
        score = float(row.get("score") or 0.0)
        scores[name] = max(score, scores.get(name, 0.0))
    return scores


def postprocess_case_paths(
    recalled_nodes: Sequence[Dict[str, Any]],
    paths: Sequence[Dict[str, Any]],
    *,
    max_style_heads: int = 3,
    max_type_heads: int = 4,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Steps 1–3 and 5. Does not cap total path count."""
    scores = _score_map(recalled_nodes)
    name_order = {str(row.get("name") or ""): idx for idx, row in enumerate(recalled_nodes)}

    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    neighbors: List[Dict[str, Any]] = []
    raw_main = 0
    for rec in paths:
        template = rec.get("template") or ""
        if template == "aesthetic_to_neighbor_evidence":
            neighbors.append(dict(rec))
            continue
        if template != "aesthetic_to_main_combined":
            continue
        raw_main += 1
        parsed = _main_fields(str(rec.get("path") or ""))
        if parsed is None:
            continue
        head, rel, tail = parsed
        hops = int(rec.get("hop_count") or 0) or 1
        recall_score = scores.get(tail, 0.0)
        path_score = recall_score / hops
        key = (rel, head, tail)
        row = {
            "path": rec.get("path"),
            "template": template,
            "hop_count": hops,
            "head_name": head,
            "head_kind": "style" if rel == "StyleAssociatedWith" else "type",
            "recalled_name": tail,
            "recall_score": recall_score,
            "path_score": path_score,
        }
        prev = best.get(key)
        if prev is None or hops < prev["hop_count"] or (hops == prev["hop_count"] and path_score > prev["path_score"]):
            best[key] = row

    unique_rows = list(best.values())
    head_scores: Dict[Tuple[str, str], float] = defaultdict(float)
    for row in unique_rows:
        head_scores[(row["head_kind"], row["head_name"])] += float(row["path_score"])

    def top_names(kind: str, limit: int) -> List[str]:
        ranked = sorted(
            ((name, score) for (k, name), score in head_scores.items() if k == kind),
            key=lambda item: (-item[1], item[0]),
        )
        return [name for name, _score in ranked[: max(0, limit)]]

    keep_styles = set(top_names("style", max_style_heads))
    keep_types = set(top_names("type", max_type_heads))
    kept_main = [
        row
        for row in unique_rows
        if (row["head_kind"] == "style" and row["head_name"] in keep_styles)
        or (row["head_kind"] == "type" and row["head_name"] in keep_types)
    ]

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in kept_main:
        grouped[row["recalled_name"]].append(row)
    recalled_order = sorted(
        grouped,
        key=lambda name: (-scores.get(name, 0.0), name_order.get(name, 10**9), name),
    )
    ordered: List[Dict[str, Any]] = []
    for name in recalled_order:
        styles = sorted(
            [row for row in grouped[name] if row["head_kind"] == "style"],
            key=lambda row: (-row["path_score"], row["hop_count"], row["head_name"]),
        )
        types = sorted(
            [row for row in grouped[name] if row["head_kind"] == "type"],
            key=lambda row: (-row["path_score"], row["hop_count"], row["head_name"]),
        )
        for idx in range(max(len(styles), len(types))):
            if idx < len(styles):
                ordered.append(styles[idx])
            if idx < len(types):
                ordered.append(types[idx])

    display = [
        {"path": row["path"], "template": row["template"], "hop_count": row["hop_count"]}
        for row in ordered
    ] + neighbors
    stats = {
        "raw_main": raw_main,
        "unique_main": len(unique_rows),
        "kept_main": len(ordered),
        "neighbor": len(neighbors),
        "style_heads": sorted(keep_styles),
        "type_heads": sorted(keep_types),
    }
    return display, stats


def postprocess_payload(
    payload: Dict[str, Any],
    *,
    max_style_heads: int = 3,
    max_type_heads: int = 4,
) -> Dict[str, Any]:
    cases = []
    for case in payload.get("cases") or []:
        paths, stats = postprocess_case_paths(
            case.get("recalled_nodes") or [],
            case.get("paths") or [],
            max_style_heads=max_style_heads,
            max_type_heads=max_type_heads,
        )
        row = dict(case)
        row["paths"] = paths
        row["path_count"] = int(stats["raw_main"]) + int(stats["neighbor"])
        row["postprocess"] = stats
        cases.append(row)
    out = dict(payload)
    out["stage"] = "path_postprocess"
    config = dict(payload.get("config") or {})
    config["postprocess"] = {
        "max_style_heads": max_style_heads,
        "max_type_heads": max_type_heads,
        "cap_total_paths": False,
    }
    out["config"] = config
    out["cases"] = cases
    return out
