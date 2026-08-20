"""Path morphology helpers: original-relation middles, AssociatedWith last hop."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

ORIG_REL_TYPES = (
    "Indicates(体现)",
    "ImplementedBy(由实现)",
    "Guides(指导)",
    "Influences(影响)",
    "Prefers(偏好)",
    "Constrains(约束)",
    "DefinesDNA(定义DNA)",
)

ORIG_REL_CYPHER = "|".join("`%s`" % rel for rel in ORIG_REL_TYPES)

ASSOCIATED_STYLE = "StyleAssociatedWith"
ASSOCIATED_TYPE = "TypeAssociatedWith"


def short_rel(rel: str) -> str:
    return rel.split("(")[0] if "(" in rel else rel


def format_path(path_nodes: Sequence[str], path_rels: Sequence[str]) -> str:
    if not path_nodes:
        return ""
    parts = [str(path_nodes[0])]
    for rel, node in zip(path_rels, path_nodes[1:]):
        parts.append("--%s-->" % short_rel(str(rel)))
        parts.append(str(node))
    return " ".join(parts)


def inspect_path(path: str) -> str:
    """Rewrite `--Rel-->` to ` -> Rel -> ` for Benchmark inspect JSON."""
    text = re.sub(
        r"\s*--([^>]+)-->\s*",
        lambda match: " -> %s -> " % short_rel(match.group(1)),
        path or "",
    ).strip()
    return re.sub(r"\s+->\s+", " -> ", text)


def display_from_reverse_walk(
    head_name: str,
    associated_rel: str,
    walk_nodes: Sequence[str],
    walk_rels: Sequence[str],
) -> Tuple[List[str], List[str]]:
    """walk_nodes runs recalled -> ... -> bridge. Flip to head -> bridge -> ... -> recalled."""
    if not walk_nodes:
        return [head_name], []
    if len(walk_nodes) == 1:
        return [head_name, str(walk_nodes[0])], [associated_rel]
    flipped_nodes = [head_name] + [str(name) for name in reversed(walk_nodes)]
    flipped_rels = [associated_rel] + [str(rel) for rel in reversed(walk_rels)]
    return flipped_nodes, flipped_rels
