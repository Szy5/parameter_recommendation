#!/usr/bin/env bash
# Shared defaults for benchmark recommendation scripts.
# Override any variable before running, e.g.:
#   MIN_SCORE=0.55 RECALL_SOURCE=live ./run_recall.sh

# Resolve repo root (extraction/) no matter where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# ---------------------------------------------------------------------------
# Input / output paths
# ---------------------------------------------------------------------------
GRAPH_JSONL="${GRAPH_JSONL:-${REPO_ROOT}/feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/07_merged_graph/kgdata_0804_with_associated_edges_confidence_gt_0.8.jsonl}"
RECALL_TOP20_JSONL="${RECALL_TOP20_JSONL:-${REPO_ROOT}/feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f/recall_top20.jsonl}"
BENCHMARK_INPUTS_JSONL="${BENCHMARK_INPUTS_JSONL:-${REPO_ROOT}/benchmark/benchmark_100_inputs.jsonl}"
FEATURES_JSONL="${FEATURES_JSONL:-${REPO_ROOT}/feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${REPO_ROOT}/feature/benchmark_recommendation/artifacts}"
NEO4J_ENV="${NEO4J_ENV:-${REPO_ROOT}/feature/.env}"

# ---------------------------------------------------------------------------
# Recall source: offline | live
# ---------------------------------------------------------------------------
RECALL_SOURCE="${RECALL_SOURCE:-live}"   # offline=read recall_top20.jsonl, live=BGE-M3 online

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
MIN_SCORE="${MIN_SCORE:-0.62}"
MAX_CANDIDATES="${MAX_CANDIDATES:-50}"

# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------
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

  if [[ -z "${stage}" || -z "${output_json}" ]]; then
    echo "usage: benchmark_recommendation_run <recall|predict|recommend> <output_json>" >&2
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
    --ambiguity-margin "${AMBIGUITY_MARGIN}"
    --min-candidate-score "${MIN_CANDIDATE_SCORE}"
    --level-ambiguity-margin "${LEVEL_AMBIGUITY_MARGIN}"
    --max-styles "${MAX_STYLES}"
    --max-types "${MAX_TYPES}"
    --max-combinations "${MAX_COMBINATIONS}"
    --small-sample-threshold "${SMALL_SAMPLE_THRESHOLD}"
    --fallback-small-sample-threshold "${FALLBACK_SMALL_SAMPLE_THRESHOLD}"
    --recommend-types "${RECOMMEND_TYPES}"
  )

  if [[ "${RECALL_SOURCE}" == "offline" ]]; then
    cmd+=(--recall-top20-jsonl "${RECALL_TOP20_JSONL}")
  else
    cmd+=(
      --features-jsonl "${FEATURES_JSONL}"
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
    if [[ "${RECOMMEND_SOURCE}" == "neo4j" ]]; then
      cmd+=(--env "${NEO4J_ENV}")
    fi
  fi

  if [[ "${FALLBACK_ON_SMALL_SAMPLE}" == "1" ]]; then
    cmd+=(--fallback-on-small-sample)
  fi

  echo "==> stage=${stage} recall_source=${RECALL_SOURCE} recommend_source=${RECOMMEND_SOURCE:-n/a} recommend_types=${RECOMMEND_TYPES}"
  echo "==> output=${output_json}"
  echo "==> cwd=${REPO_ROOT}"
  (
    cd "${REPO_ROOT}" || exit 1
    "${cmd[@]}"
  )
}
