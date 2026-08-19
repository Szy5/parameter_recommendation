#!/usr/bin/env bash
# Run recall only (keywords -> recalled_nodes).
#
# Examples:
#   ./run_recall.sh
#   MIN_SCORE=0.55 ./run_recall.sh
#   RECALL_SOURCE=live OUTPUT_JSON=/tmp/live_recall.json ./run_recall.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${ARTIFACTS_DIR}/benchmark_recall_results.json}"
benchmark_recommendation_run recall "${OUTPUT_JSON}"
