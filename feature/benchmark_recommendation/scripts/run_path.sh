#!/usr/bin/env bash
# Inspect Cypher paths from recalled nodes (no prediction filter).
#
# Examples:
#   ./run_path.sh
#   MIN_SCORE=0.65 TOP_K=10 MAX_HOPS=1 ./run_path.sh
#   MAX_HOPS=2 INCLUDE_NEIGHBOR=0 OUTPUT_JSON=/tmp/paths.json ./run_path.sh
#   RECALL_SOURCE=offline ./run_path.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${ARTIFACTS_DIR}/benchmark_path_results.json}"
benchmark_recommendation_run path "${OUTPUT_JSON}"
