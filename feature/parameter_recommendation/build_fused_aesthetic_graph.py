#!/usr/bin/env python3
"""Build an aesthetic-only graph from concept-fusion Judge decisions.

This stage is intentionally independent from automobile style judging. It
repoints every relationship touching a merged AestheticConcept to one of the
seven canonical style nodes and preserves all source relationship properties
when duplicate relationships collapse to the same start/label/end tuple.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.build_fused_graph import (  # type: ignore
        build_meixue_records,
        merge_decisions,
    )
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        AC_LABEL,
        MAIN_STYLES,
        iter_jsonl,
        write_jsonl,
    )
else:
    from .build_fused_graph import build_meixue_records, merge_decisions
    from .common import AC_LABEL, MAIN_STYLES, iter_jsonl, write_jsonl


def mapping_records(
    judged: Dict[str, Dict[str, Any]], mapping: Dict[str, str], confidence_threshold: float
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for concept_id in sorted(
        judged, key=lambda value: int(value) if value.isdigit() else value
    ):
        row = judged[concept_id]
        action = "MERGE" if concept_id in mapping else "KEEP_ORIGINAL"
        exclusion_reason = None
        if action == "KEEP_ORIGINAL" and row.get("can_merge"):
            if row.get("target_style") not in MAIN_STYLES:
                exclusion_reason = "target_style_outside_allowed_styles"
            elif float(row.get("confidence", 0)) < confidence_threshold:
                exclusion_reason = "below_confidence_threshold"
        elif action == "KEEP_ORIGINAL":
            exclusion_reason = "judge_keep_original"
        records.append(
            {
                "concept_id": concept_id,
                "concept_name": row.get("concept_name"),
                "action": action,
                "target_style": row.get("target_style") if action == "MERGE" else None,
                "target_node_id": mapping.get(concept_id),
                "confidence": row.get("confidence"),
                "confidence_threshold": confidence_threshold,
                "reason": row.get("reason"),
                "exclusion_reason": exclusion_reason,
                "model": row.get("model"),
                "prompt_version": row.get("prompt_version"),
                "judged_at": row.get("judged_at"),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a concept-fused aesthetic graph")
    parser.add_argument("--meixue", type=Path, default=Path("meixue(1).json"))
    parser.add_argument("--concept-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--concept-confidence", type=float, default=0.80)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    mapping, grouped, judged = merge_decisions(
        args.concept_output, args.concept_confidence
    )
    total_concepts = sum(
        1
        for record in iter_jsonl(args.meixue)
        if record.get("type") == "node" and AC_LABEL in (record.get("labels") or [])
    )
    complete = len(judged) == total_concepts
    if not complete and not args.allow_partial:
        raise SystemExit(
            "Concept Judge coverage is partial (%d/%d); pass --allow-partial only "
            "for an explicitly labeled preview" % (len(judged), total_concepts)
        )

    records, _nodes, graph_stats = build_meixue_records(args.meixue, mapping, grouped)
    output_count = write_jsonl(args.output, records)
    mapping_rows = mapping_records(judged, mapping, args.concept_confidence)
    mapping_count = write_jsonl(args.mapping_output, mapping_rows)

    report = {
        "complete_full_concept_coverage": complete,
        "concept_judge_coverage": [len(judged), total_concepts],
        "concept_confidence_threshold": args.concept_confidence,
        "judge_passes_used": 1,
        "decision_counts": {
            "merge": len(mapping),
            "keep_original": len(judged) - len(mapping),
            "below_confidence_threshold": sum(
                1
                for row in mapping_rows
                if row.get("exclusion_reason") == "below_confidence_threshold"
            ),
        },
        "target_style_counts": {
            style: len(grouped.get(style, [])) for style in MAIN_STYLES
        },
        "graph": graph_stats,
        "output_records": output_count,
        "mapping_records": mapping_count,
        "output": str(args.output),
        "mapping_output": str(args.mapping_output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
