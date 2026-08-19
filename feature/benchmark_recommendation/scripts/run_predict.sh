#!/usr/bin/env bash
# Run recall + predict (keywords -> recalled_nodes -> car_style/car_type/car_level).
#
# Examples:
#   ./run_predict.sh
#   AMBIGUITY_MARGIN=0.05 ./run_predict.sh
#   RECALL_SOURCE=live ./run_predict.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${ARTIFACTS_DIR}/benchmark_predict_results.json}"
benchmark_recommendation_run predict "${OUTPUT_JSON}"
