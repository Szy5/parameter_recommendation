from __future__ import annotations

from typing import Dict, Iterable, Tuple

RECOMMEND_TYPE_ALIASES: Dict[str, str] = {
    "style_guides": "style_guides",
    "style_parameter_guides": "style_guides",
    "style_type": "style_type",
    "style_type_level": "style_type_level",
}

VALID_RECOMMEND_TYPES = ("style_guides", "style_type", "style_type_level")


def parse_recommend_types(raw: str) -> Tuple[str, ...]:
    if not str(raw or "").strip():
        raise ValueError("recommend_types must not be empty")

    seen = set()
    normalized: list[str] = []
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        key = RECOMMEND_TYPE_ALIASES.get(token)
        if key is None:
            allowed = ", ".join(sorted(set(RECOMMEND_TYPE_ALIASES.keys())))
            raise ValueError("unknown recommend type %r; allowed: %s" % (token, allowed))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)

    if not normalized:
        raise ValueError("recommend_types must contain at least one type")
    return tuple(normalized)


def recommend_types_include(recommend_types: Iterable[str], recommend_type: str) -> bool:
    return recommend_type in set(recommend_types)
