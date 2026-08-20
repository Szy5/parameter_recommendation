#!/usr/bin/env bash
# Stage 1: recall + path (+ optional postprocess) -> benchmark_evidence.json
#
# Examples:
#   ./run_evidence.sh
#   ENABLE_PATH_POSTPROCESS=1 MIN_SCORE=0.65 ./run_evidence.sh
#   RECALL_SOURCE=live ./run_evidence.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${EVIDENCE_JSON}}"
benchmark_recommendation_run evidence "${OUTPUT_JSON}"
