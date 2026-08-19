#!/usr/bin/env bash
# Run the Benchmark upstream Offline Style/Type judges with safe defaults.
# Re-running the same command resumes from successful task + node IDs.

set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "${script_dir}/../.." && pwd)

usage() {
  cat <<'EOF'
Run the Benchmark upstream Offline Style/Type judges with safe defaults.
Re-running the same command resumes from successful task + node IDs.

Usage:
  ./feature/benchmark_upstream_offline/run_offline_judge.sh <smoke|full> <gpt-4o-mini|gpt-5-mini|both> [style|type|both]

Examples:
  # Recommended first check: 500 nodes × two Judges with gpt-4o-mini.
  ./feature/benchmark_upstream_offline/run_offline_judge.sh smoke gpt-4o-mini both

  # Run the same 500 nodes with gpt-5-mini.
  ./feature/benchmark_upstream_offline/run_offline_judge.sh smoke gpt-5-mini both

  # Full 9,649-node run. This is 19,298 LLM tasks when task=both.
  ./feature/benchmark_upstream_offline/run_offline_judge.sh full gpt-5-mini both

  # Compare both models on the smoke sample.
  ./feature/benchmark_upstream_offline/run_offline_judge.sh smoke both both

Optional environment overrides:
  PYTHON_BIN=python3       Python executable.
  ENV_FILE=feature/.env   LLM configuration file, relative to project root or absolute.
  WORKERS=4               Override model-specific worker count.
  MAX_RETRIES=4           Record-level retry count.
  TIMEOUT=240             Per-request timeout in seconds.
  SAMPLE_SIZE=500         Smoke sample size; extraction uses a matching file name.
  SAMPLE_SEED=20260817    Deterministic stratified sampling seed.
  FORCE_EXTRACT=1         Regenerate full inputs and Rubrics before running.
  USE_ENV_PROXY=1         Honor HTTP(S)_PROXY; default directly accesses BASE_URL.
  DRY_RUN=1               Print commands without making LLM calls.

Notes:
  - The script never prints API credentials.
  - Outputs are append-only under feature/artifacts/benchmark_upstream_offline/runs/<scope>/<prompt-version>/.
  - Re-run the exact same command after interruption; successful tasks are skipped.
  - Current smoke results show over-linking. Review SMOKE_REPORT.md before a full run.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" || $# -lt 2 || $# -gt 3 ]]; then
  usage
  if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    exit 0
  fi
  exit 2
fi

scope=$1
model_selection=$2
task_selection=${3:-both}

case "$scope" in
  smoke|full) ;;
  *) printf 'Error: scope must be smoke or full, got %s\n\n' "$scope" >&2; usage >&2; exit 2 ;;
esac
case "$model_selection" in
  gpt-4o-mini|gpt-5-mini|both) ;;
  *) printf 'Error: model must be gpt-4o-mini, gpt-5-mini, or both, got %s\n\n' "$model_selection" >&2; usage >&2; exit 2 ;;
esac
case "$task_selection" in
  style|type|both) ;;
  *) printf 'Error: task must be style, type, or both, got %s\n\n' "$task_selection" >&2; usage >&2; exit 2 ;;
esac

python_bin=${PYTHON_BIN:-python3}
sample_size=${SAMPLE_SIZE:-500}
sample_seed=${SAMPLE_SEED:-20260817}
max_retries=${MAX_RETRIES:-4}
request_timeout=${TIMEOUT:-240}
force_extract=${FORCE_EXTRACT:-0}
use_env_proxy=${USE_ENV_PROXY:-0}
dry_run=${DRY_RUN:-0}

if [[ ${ENV_FILE:-feature/.env} = /* ]]; then
  env_path=${ENV_FILE:-feature/.env}
else
  env_path="${project_root}/${ENV_FILE:-feature/.env}"
fi

graph_path="${project_root}/kgdata_0804.jsonl"
input_dir="${project_root}/feature/artifacts/benchmark_upstream_offline/inputs"
report_path="${project_root}/feature/artifacts/benchmark_upstream_offline/SMOKE_REPORT.md"

if [[ ! -x $(command -v "$python_bin" 2>/dev/null || true) ]]; then
  printf 'Error: Python executable not found: %s\n' "$python_bin" >&2
  exit 1
fi
prompt_version=$(PYTHONPATH="${project_root}${PYTHONPATH:+:${PYTHONPATH}}" \
  "$python_bin" -c 'from feature.benchmark_upstream_offline import PROMPT_VERSION; print(PROMPT_VERSION)')
run_root="${project_root}/feature/artifacts/benchmark_upstream_offline/runs/${scope}/${prompt_version}"
if [[ ! -f "$graph_path" ]]; then
  printf 'Error: graph input not found: %s\n' "$graph_path" >&2
  exit 1
fi
if [[ ! -f "$env_path" ]]; then
  printf 'Error: LLM env file not found: %s\n' "$env_path" >&2
  exit 1
fi
if ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?API_KEY[[:space:]]*=' "$env_path" ||
   ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?BASE_URL[[:space:]]*=' "$env_path"; then
  printf 'Error: %s must define API_KEY and BASE_URL.\n' "$env_path" >&2
  exit 1
fi

run_command() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  if [[ "$dry_run" != "1" ]]; then
    "$@"
  fi
}

smoke_input="${input_dir}/features_smoke_${sample_size}.jsonl"
full_input="${input_dir}/features_all.jsonl"
if [[ "$force_extract" == "1" || ! -f "$full_input" || ! -f "$smoke_input" ||
      ! -f "${input_dir}/style_rubric.json" || ! -f "${input_dir}/type_rubric.json" ]]; then
  printf '\n[1/3] Extracting graph-backed inputs and Rubrics...\n'
  run_command "$python_bin" -m feature.benchmark_upstream_offline.extract_inputs \
    --graph "$graph_path" \
    --output-dir "$input_dir" \
    --sample-size "$sample_size" \
    --seed "$sample_seed"
else
  printf '\n[1/3] Reusing existing inputs in %s\n' "$input_dir"
fi

if [[ "$scope" == "smoke" ]]; then
  judge_input=$smoke_input
else
  judge_input=$full_input
fi
if [[ ! -f "$judge_input" && "$dry_run" != "1" ]]; then
  printf 'Error: Judge input was not created: %s\n' "$judge_input" >&2
  exit 1
fi

if [[ "$scope" == "full" ]]; then
  printf '\nWARNING: full mode schedules up to 9,649 nodes per selected Judge.\n'
  printf 'Current Prompt v1 smoke candidates were NOT approved for production write-back.\n'
  printf 'Read the quality report before continuing: %s\n' "$report_path"
  if [[ ${CONFIRM_FULL_RUN:-0} != "1" ]]; then
    printf '\nFull mode requires explicit confirmation. Re-run with:\n'
    printf '  CONFIRM_FULL_RUN=1 %q %q %q %q\n' "$0" "$scope" "$model_selection" "$task_selection"
    exit 3
  fi
fi

if [[ "$model_selection" == "both" ]]; then
  models=(gpt-4o-mini gpt-5-mini)
else
  models=("$model_selection")
fi
if [[ "$task_selection" == "both" ]]; then
  tasks=(style type)
else
  tasks=("$task_selection")
fi

mkdir -p "$run_root"
printf '\n[2/3] Running Offline Judge: scope=%s models=%s tasks=%s\n' \
  "$scope" "${models[*]}" "${tasks[*]}"

for model_name in "${models[@]}"; do
  if [[ -n ${WORKERS:-} ]]; then
    worker_count=$WORKERS
  elif [[ "$model_name" == "gpt-5-mini" ]]; then
    worker_count=4
  else
    worker_count=20
  fi

  if [[ "$model_name" == "gpt-5-mini" ]]; then
    temperature_value=none
  else
    temperature_value=0.1
  fi

  model_output="${run_root}/${model_name}"
  log_dir="${model_output}/logs"
  mkdir -p "$log_dir"

  for judge_task in "${tasks[@]}"; do
    proxy_args=()
    if [[ "$use_env_proxy" == "1" ]]; then
      proxy_args+=(--use-env-proxy)
    fi
    command_args=(
      "$python_bin" -u -m feature.benchmark_upstream_offline.run_judge
      --task "$judge_task"
      --input "$judge_input"
      --rubric-dir "$input_dir"
      --output-dir "$model_output"
      --env "$env_path"
      --model "$model_name"
      --workers "$worker_count"
      --temperature "$temperature_value"
      --max-retries "$max_retries"
      --timeout "$request_timeout"
      "${proxy_args[@]}"
    )
    timestamp=$(date '+%Y%m%d_%H%M%S')
    log_path="${log_dir}/${judge_task}_${timestamp}.log"
    printf '\nRunning model=%s task=%s workers=%s\n' "$model_name" "$judge_task" "$worker_count"
    printf 'Log: %s\n' "$log_path"
    if [[ "$dry_run" == "1" ]]; then
      run_command "${command_args[@]}"
    else
      printf '+ '
      printf '%q ' "${command_args[@]}"
      printf '\n'
      "${command_args[@]}" 2>&1 | tee "$log_path"
    fi
  done
done

printf '\n[3/3] Building summaries and candidate edge files...\n'
for model_name in "${models[@]}"; do
  model_output="${run_root}/${model_name}"
  for judge_task in "${tasks[@]}"; do
    results_path="${model_output}/${judge_task}_results.jsonl"
    rubric_path="${input_dir}/${judge_task}_rubric.json"
    edges_path="${model_output}/${judge_task}_edges.jsonl"
    if [[ -f "$results_path" || "$dry_run" == "1" ]]; then
      run_command "$python_bin" -m feature.benchmark_upstream_offline.build_edges \
        --task "$judge_task" \
        --results "$results_path" \
        --features "$judge_input" \
        --rubric "$rubric_path" \
        --output "$edges_path"
    fi
  done
done

if [[ ${#models[@]} -eq 2 && "$task_selection" == "both" ]]; then
  run_command "$python_bin" -m feature.benchmark_upstream_offline.analyze_smoke \
    --input-root "$run_root" \
    --models "${models[@]}" \
    --output "${run_root}/comparison.json"
elif [[ ${#models[@]} -eq 1 && "$task_selection" == "both" ]]; then
  run_command "$python_bin" -m feature.benchmark_upstream_offline.analyze_smoke \
    --input-root "$run_root" \
    --models "${models[0]}" \
    --output "${run_root}/${models[0]}_summary.json"
fi

printf '\nDone. Outputs: %s\n' "$run_root"
printf 'Re-run the same command to resume or verify completed task keys.\n'
