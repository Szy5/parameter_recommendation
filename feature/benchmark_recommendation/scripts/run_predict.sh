#!/usr/bin/env bash
# Stage 2: LLM predict (RAG + concurrent API calls).
#
# Prerequisites:
#   - feature/.env with API_KEY, BASE_URL, MODEL_NAME
#   - artifacts/benchmark_evidence.json (run ./run_evidence.sh first)
#
# Outputs:
#   artifacts/benchmark_predict.json      prediction results
#   artifacts/benchmark_llm_audit.json    per-call LLM input/output audit log
#
# Examples:
#   ./run_predict.sh
#   LLM_WORKERS=8 ./run_predict.sh
#   INPUT_JSON=artifacts/benchmark_evidence.json LLM_WORKERS=4 ./run_predict.sh
#   PREDICTION_MODE=vote ./run_predict.sh   # no LLM, no audit log

set -euo pipefail
# shellcheck source=config.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"

export PREDICTION_MODE="${PREDICTION_MODE:-llm}"

OUTPUT_JSON="${OUTPUT_JSON:-${PREDICT_JSON}}"

if [[ "${PREDICTION_MODE}" == "llm" ]]; then
  echo "==> LLM predict: workers=${LLM_WORKERS}, audit=${LLM_AUDIT_JSON}"
  echo "==> progress on stderr: [LLM predict] N/100 Bxxx done"
fi

if [[ -n "${INPUT_JSON:-}" ]]; then
  benchmark_recommendation_run predict "${OUTPUT_JSON}" "${INPUT_JSON}"
elif [[ -f "${EVIDENCE_JSON}" ]]; then
  benchmark_recommendation_run predict "${OUTPUT_JSON}" "${EVIDENCE_JSON}"
else
  echo "No ${EVIDENCE_JSON}; running recall+path+predict inline..." >&2
  benchmark_recommendation_run predict "${OUTPUT_JSON}"
fi

echo "==> predict output: ${OUTPUT_JSON}"
if [[ "${PREDICTION_MODE}" == "llm" && -f "${LLM_AUDIT_JSON}" ]]; then
  echo "==> llm audit:      ${LLM_AUDIT_JSON}"
fi
