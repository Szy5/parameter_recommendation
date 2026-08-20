#!/usr/bin/env bash
# Main entry: run pipeline stages 1, 1+2, or 1+2+3 with hyperparameters from config.sh.
#
# Stage 1 (evidence): keyword recall + Neo4j path search (+ optional postprocess)
# Stage 2 (predict):  RAG path narratives + LLM reasoning -> car_style / car_type
# Stage 3 (recommend): parameter recommendation from predicted style/type
#
# Examples:
#   ./run.sh              # full pipeline (PIPELINE=123)
#   PIPELINE=1 ./run.sh   # evidence only
#   PIPELINE=12 ./run.sh  # evidence + predict
#   MIN_SCORE=0.60 ENABLE_PATH_POSTPROCESS=1 PIPELINE=1 ./run.sh
#   PREDICTION_MODE=vote PIPELINE=12 ./run.sh
#   INPUT_JSON=artifacts/benchmark_evidence.json PIPELINE=2 ./run.sh   # predict from saved evidence

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

run_stage_1() {
  benchmark_recommendation_run evidence "${EVIDENCE_JSON}"
}

run_stage_2() {
  local input="${INPUT_JSON:-}"
  if [[ -z "${input}" && -f "${EVIDENCE_JSON}" ]]; then
    input="${EVIDENCE_JSON}"
  fi
  if [[ -n "${input}" ]]; then
    benchmark_recommendation_run predict "${PREDICT_JSON}" "${input}"
  else
    benchmark_recommendation_run predict "${PREDICT_JSON}"
  fi
}

run_stage_3() {
  local input="${INPUT_JSON:-}"
  if [[ -z "${input}" && -f "${PREDICT_JSON}" ]]; then
    input="${PREDICT_JSON}"
  fi
  if [[ -n "${input}" ]]; then
    benchmark_recommendation_run recommend "${RECOMMEND_JSON}" "${input}"
  else
    benchmark_recommendation_run recommend "${RECOMMEND_JSON}"
  fi
}

case "${PIPELINE}" in
  1)
    run_stage_1
    ;;
  2)
    run_stage_2
    ;;
  3)
    run_stage_3
    ;;
  12)
    run_stage_1
    INPUT_JSON="${EVIDENCE_JSON}" run_stage_2
    ;;
  23)
    run_stage_2
    INPUT_JSON="${PREDICT_JSON}" run_stage_3
    ;;
  123|"")
    run_stage_1
    INPUT_JSON="${EVIDENCE_JSON}" run_stage_2
    INPUT_JSON="${PREDICT_JSON}" run_stage_3
    ;;
  *)
    echo "Unknown PIPELINE=${PIPELINE}. Use 1, 2, 3, 12, 23, or 123." >&2
    exit 2
    ;;
esac

echo "==> pipeline ${PIPELINE} complete"
