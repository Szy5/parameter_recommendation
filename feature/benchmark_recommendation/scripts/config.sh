#!/usr/bin/env bash
# Shared defaults for benchmark recommendation scripts.
# Override any variable before running, e.g.:
#   MIN_SCORE=0.55 RECALL_SOURCE=live ./run.sh 1
#   PIPELINE=12 PREDICTION_MODE=llm ./run.sh

# Resolve repo root (extraction/) no matter where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# ---------------------------------------------------------------------------
# Pipeline stage selector (main entry: run.sh)
#   1   = evidence only (recall + path, optional postprocess)
#   12  = evidence + predict (LLM RAG by default)
#   123 = evidence + predict + recommend (full)
# ---------------------------------------------------------------------------
PIPELINE="${PIPELINE:-123}"

# ---------------------------------------------------------------------------
# Input / output paths
# ---------------------------------------------------------------------------
GRAPH_JSONL="${GRAPH_JSONL:-${REPO_ROOT}/data/kgdata_0804_assoc_bridges.jsonl}"
RECALL_TOP20_JSONL="${RECALL_TOP20_JSONL:-${REPO_ROOT}/data/recall_top20.jsonl}"
BENCHMARK_INPUTS_JSONL="${BENCHMARK_INPUTS_JSONL:-${REPO_ROOT}/benchmark/benchmark_100_inputs.jsonl}"
FEATURES_JSONL="${FEATURES_JSONL:-${REPO_ROOT}/data/features_all.jsonl}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${REPO_ROOT}/feature/benchmark_recommendation/artifacts}"
NEO4J_ENV="${NEO4J_ENV:-${REPO_ROOT}/feature/.env}"

EVIDENCE_JSON="${EVIDENCE_JSON:-${ARTIFACTS_DIR}/benchmark_evidence.json}"
PREDICT_JSON="${PREDICT_JSON:-${ARTIFACTS_DIR}/benchmark_predict.json}"
LLM_AUDIT_JSON="${LLM_AUDIT_JSON:-${ARTIFACTS_DIR}/benchmark_llm_audit.json}"
RECOMMEND_JSON="${RECOMMEND_JSON:-${ARTIFACTS_DIR}/benchmark_recommendation_results.json}"

# Optional: reuse prior stage output when running predict/recommend alone
INPUT_JSON="${INPUT_JSON:-}"

# ---------------------------------------------------------------------------
# Recall source: offline | live
# ---------------------------------------------------------------------------
RECALL_SOURCE="${RECALL_SOURCE:-offline}"   # offline=read recall_top20.jsonl, live=BGE-M3 online

# Live recall only
EMBED_MODEL="${EMBED_MODEL:-BAAI/bge-m3}"
EMBED_REVISION="${EMBED_REVISION:-5617a9f61b028005a4858fdac845db406aefb181}"
EMBED_BATCH_SIZE="${EMBED_BATCH_SIZE:-32}"
EMBED_MAX_SEQ_LENGTH="${EMBED_MAX_SEQ_LENGTH:-2048}"
EMBED_DEVICE="${EMBED_DEVICE:-}"              # empty = auto (cuda if available)
LIVE_POOL_SIZE="${LIVE_POOL_SIZE:-50}"

# ---------------------------------------------------------------------------
# Recall filtering
# ---------------------------------------------------------------------------
RECALL_MODE="${RECALL_MODE:-top_k_and_threshold}"   # top_k | threshold | top_k_and_threshold
TOP_K="${TOP_K:-20}"
MIN_SCORE="${MIN_SCORE:-0.65}"
MAX_CANDIDATES="${MAX_CANDIDATES:-50}"
MAX_HOPS="${MAX_HOPS:-5}"
INCLUDE_NEIGHBOR="${INCLUDE_NEIGHBOR:-1}"   # 1=include neighbor 1-hop, 0=main paths only

# ---------------------------------------------------------------------------
# 后处理 (evidence stage)
# ---------------------------------------------------------------------------
ENABLE_PATH_POSTPROCESS="${ENABLE_PATH_POSTPROCESS:-1}"   # 1=dedupe + top style/type heads
MAX_STYLE_HEADS="${MAX_STYLE_HEADS:-2}"
MAX_TYPE_HEADS="${MAX_TYPE_HEADS:-2}"

# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------
PREDICTION_MODE="${PREDICTION_MODE:-llm}"   # llm | vote
LLM_MODEL="${LLM_MODEL:-}"                  # empty = MODEL_NAME from .env
LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.1}"
LLM_TIMEOUT="${LLM_TIMEOUT:-180}"
MAX_PATHS_IN_CONTEXT="${MAX_PATHS_IN_CONTEXT:-30}"
LLM_WORKERS="${LLM_WORKERS:-4}"               # concurrent LLM requests
LLM_SHOW_PROGRESS="${LLM_SHOW_PROGRESS:-1}" # 1=print progress to stderr, 0=quiet

AMBIGUITY_MARGIN="${AMBIGUITY_MARGIN:-0.02}"
MIN_CANDIDATE_SCORE="${MIN_CANDIDATE_SCORE:-1e-9}"
LEVEL_AMBIGUITY_MARGIN="${LEVEL_AMBIGUITY_MARGIN:-0.05}"

# ---------------------------------------------------------------------------
# Recommendation (recommend stage only)
# ---------------------------------------------------------------------------
RECOMMEND_SOURCE="${RECOMMEND_SOURCE:-neo4j}"   # neo4j | offline
MAX_STYLES="${MAX_STYLES:-2}"
MAX_TYPES="${MAX_TYPES:-2}"
MAX_COMBINATIONS="${MAX_COMBINATIONS:-4}"
SMALL_SAMPLE_THRESHOLD="${SMALL_SAMPLE_THRESHOLD:-10}"
FALLBACK_ON_SMALL_SAMPLE="${FALLBACK_ON_SMALL_SAMPLE:-1}"   # 1=enable, 0=disable
FALLBACK_SMALL_SAMPLE_THRESHOLD="${FALLBACK_SMALL_SAMPLE_THRESHOLD:-8}"
# Comma-separated recommendation types:
#   style_guides          -> 汽车风格 -> 设计参数
#   style_type            -> 风格 + 车型 -> 参数范围 + 实例
#   style_type_level      -> 风格 + 车型 + 级别 -> 参数范围 + 实例
RECOMMEND_TYPES="${RECOMMEND_TYPES:-style_guides,style_type}"

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
benchmark_recommendation_run() {
  local stage="$1"
  local output_json="$2"
  local input_json="${3:-}"

  if [[ -z "${stage}" || -z "${output_json}" ]]; then
    echo "usage: benchmark_recommendation_run <recall|evidence|path|predict|recommend> <output_json> [input_json]" >&2
    return 1
  fi

  mkdir -p "$(dirname "${output_json}")"

  local -a cmd=(
    python3 -m feature.benchmark_recommendation.run_benchmark_recommendation
    --stage "${stage}"
    --graph-jsonl "${GRAPH_JSONL}"
    --benchmark-inputs-jsonl "${BENCHMARK_INPUTS_JSONL}"
    --output-json "${output_json}"
    --recall-source "${RECALL_SOURCE}"
    --recall-mode "${RECALL_MODE}"
    --min-score "${MIN_SCORE}"
    --top-k "${TOP_K}"
    --max-candidates "${MAX_CANDIDATES}"
    --max-hops "${MAX_HOPS}"
    --prediction-mode "${PREDICTION_MODE}"
    --llm-temperature "${LLM_TEMPERATURE}"
    --llm-timeout "${LLM_TIMEOUT}"
    --max-paths-in-context "${MAX_PATHS_IN_CONTEXT}"
    --llm-workers "${LLM_WORKERS}"
    --llm-audit-json "${LLM_AUDIT_JSON}"
    --max-style-heads "${MAX_STYLE_HEADS}"
    --max-type-heads "${MAX_TYPE_HEADS}"
    --ambiguity-margin "${AMBIGUITY_MARGIN}"
    --min-candidate-score "${MIN_CANDIDATE_SCORE}"
    --level-ambiguity-margin "${LEVEL_AMBIGUITY_MARGIN}"
    --max-styles "${MAX_STYLES}"
    --max-types "${MAX_TYPES}"
    --max-combinations "${MAX_COMBINATIONS}"
    --small-sample-threshold "${SMALL_SAMPLE_THRESHOLD}"
    --fallback-small-sample-threshold "${FALLBACK_SMALL_SAMPLE_THRESHOLD}"
    --recommend-types "${RECOMMEND_TYPES}"
    --features-jsonl "${FEATURES_JSONL}"
    --env "${NEO4J_ENV}"
  )

  if [[ -n "${input_json}" ]]; then
    cmd+=(--input-json "${input_json}")
  fi

  if [[ -n "${LLM_MODEL}" ]]; then
    cmd+=(--llm-model "${LLM_MODEL}")
  fi

  if [[ "${LLM_SHOW_PROGRESS}" == "0" ]]; then
    cmd+=(--no-llm-progress)
  fi

  if [[ "${ENABLE_PATH_POSTPROCESS}" == "1" ]]; then
    cmd+=(--enable-path-postprocess)
  fi

  if [[ "${RECALL_SOURCE}" == "offline" ]]; then
    cmd+=(--recall-top20-jsonl "${RECALL_TOP20_JSONL}")
  else
    cmd+=(
      --model "${EMBED_MODEL}"
      --revision "${EMBED_REVISION}"
      --batch-size "${EMBED_BATCH_SIZE}"
      --max-seq-length "${EMBED_MAX_SEQ_LENGTH}"
      --live-pool-size "${LIVE_POOL_SIZE}"
    )
    if [[ -n "${EMBED_DEVICE}" ]]; then
      cmd+=(--device "${EMBED_DEVICE}")
    fi
  fi

  if [[ "${stage}" == "recommend" ]]; then
    cmd+=(--recommend-source "${RECOMMEND_SOURCE}")
  fi

  if [[ "${INCLUDE_NEIGHBOR}" != "1" ]]; then
    cmd+=(--no-neighbor)
  fi

  if [[ "${FALLBACK_ON_SMALL_SAMPLE}" == "1" ]]; then
    cmd+=(--fallback-on-small-sample)
  fi

  echo "==> stage=${stage} pipeline=${PIPELINE} recall_source=${RECALL_SOURCE} prediction_mode=${PREDICTION_MODE} llm_workers=${LLM_WORKERS}"
  echo "==> min_score=${MIN_SCORE} top_k=${TOP_K} max_hops=${MAX_HOPS} postprocess=${ENABLE_PATH_POSTPROCESS} neighbor=${INCLUDE_NEIGHBOR}"
  echo "==> output=${output_json}"
  if [[ -n "${input_json}" ]]; then
    echo "==> input=${input_json}"
  fi
  echo "==> cwd=${REPO_ROOT}"
  (
    cd "${REPO_ROOT}" || exit 1
    "${cmd[@]}"
  )
}
