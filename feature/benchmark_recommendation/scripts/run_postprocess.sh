#!/usr/bin/env bash
# Post-filter inspect paths: unique (head, recalled), keep top 3 styles and top 4 types.
# Does not cap total path count.
#
#   ./run_postprocess.sh
#   INPUT_JSON=... OUTPUT_JSON=... ./run_postprocess.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

INPUT_JSON="${INPUT_JSON:-${ARTIFACTS_DIR}/benchmark_path_results.json}"
OUTPUT_JSON="${OUTPUT_JSON:-${ARTIFACTS_DIR}/benchmark_path_results_post.json}"
MAX_STYLE_HEADS="${MAX_STYLE_HEADS:-3}"
MAX_TYPE_HEADS="${MAX_TYPE_HEADS:-4}"

mkdir -p "$(dirname "${OUTPUT_JSON}")"
cd "${REPO_ROOT}"
python3 - << PY
import json
from pathlib import Path
from feature.benchmark_recommendation.path_postprocess import postprocess_payload
from feature.benchmark_recommendation.run_benchmark_recommendation import _render_json

src = Path("${INPUT_JSON}")
dst = Path("${OUTPUT_JSON}")
payload = json.loads(src.read_text(encoding="utf-8"))
out = postprocess_payload(
    payload,
    max_style_heads=int("${MAX_STYLE_HEADS}"),
    max_type_heads=int("${MAX_TYPE_HEADS}"),
)
dst.write_text(_render_json(out) + "\n", encoding="utf-8")
print(json.dumps({"input": str(src), "output": str(dst), "cases": len(out.get("cases") or [])}, ensure_ascii=False, indent=2))
PY
