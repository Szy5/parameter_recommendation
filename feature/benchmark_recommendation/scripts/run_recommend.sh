#!/usr/bin/env bash
# Stage 3: parameter recommendation from predicted style/type.
#
# Examples:
#   ./run_recommend.sh
#   INPUT_JSON=artifacts/benchmark_predict.json ./run_recommend.sh
#   MIN_SCORE=0.55 MAX_STYLES=3 ./run_recommend.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${RECOMMEND_JSON}}"
if [[ -n "${INPUT_JSON:-}" ]]; then
  benchmark_recommendation_run recommend "${OUTPUT_JSON}" "${INPUT_JSON}"
elif [[ -f "${PREDICT_JSON}" ]]; then
  benchmark_recommendation_run recommend "${OUTPUT_JSON}" "${PREDICT_JSON}"
else
  benchmark_recommendation_run recommend "${OUTPUT_JSON}"
fi
