"""Conservative directed relative positions transcribed from the supplied PDF."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Optional, Set, Tuple

from .schema import POSITION_VALUES, canonical_part


class PdfTopology:
    def __init__(self, pdf_path: Path, topology_path: Optional[Path] = None) -> None:
        mapping_path = topology_path or Path(__file__).with_name("pdf_topology.json")
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if digest != data["source_pdf_sha256"]:
            raise ValueError("PDF differs from the reviewed exterior mind map; update the topology before extraction")
        pairs: DefaultDict[Tuple[str, str], Set[str]] = defaultdict(set)
        for entry in data["relations"]:
            if not isinstance(entry, list) or len(entry) != 3 or entry[2] not in POSITION_VALUES:
                raise ValueError("Invalid PDF topology entry: %r" % entry)
            first, second = canonical_part(entry[0]), canonical_part(entry[1])
            if first == second:
                raise ValueError("A PDF topology pair points at itself: %r" % entry)
            pairs[(first, second)].add(entry[2])
        self.pairs = dict(pairs)
        self.digest = digest

    def lookup(self, first: str, second: str, classification: str) -> Tuple[Optional[str], str]:
        if classification != "外部":
            return None, "PDF仅覆盖外部部件"
        positions = self.pairs.get((canonical_part(first), canonical_part(second)), set())
        if not positions:
            return None, "PDF中没有可确认的有向部件对"
        if len(positions) != 1:
            return None, "PDF中该部件对存在多个方位: " + "、".join(sorted(positions))
        return next(iter(positions)), ""
