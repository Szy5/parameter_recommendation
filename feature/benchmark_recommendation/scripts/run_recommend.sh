#!/usr/bin/env bash
# Run recall + predict + recommend (full pipeline, Neo4j by default).
#
# Examples:
#   ./run_recommend.sh
#   MIN_SCORE=0.55 MAX_STYLES=3 ./run_recommend.sh
#   RECOMMEND_SOURCE=offline ./run_recommend.sh
#   RECALL_SOURCE=live RECOMMEND_SOURCE=neo4j ./run_recommend.sh

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

OUTPUT_JSON="${OUTPUT_JSON:-${ARTIFACTS_DIR}/benchmark_recommendation_results.json}"
benchmark_recommendation_run recommend "${OUTPUT_JSON}"
