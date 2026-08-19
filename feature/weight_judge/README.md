# Weight Judge（LLM as Judge）

按 `docs/llm-judge-weight-backfill-plan.md` 实施：给美学→设计参数 `Guides` 边回填 `weight`。

当前 Prompt：`v1.1`（领域专家知识 + 反模板化 reason）

## 目录

```text
feature_v2/
  .env
  weight_judge/
    prompt_version.md
    extract_judge_input.py
    run_judge.py
    merge_backfill.py
  artifacts/weight_judge/
    full/
      01_judge_input.jsonl
      02_judge_output.jsonl
      03_meixue_cars2_with_guides_weight.jsonl
      merge_report.json
      run_meta.json
    pilot_25/
    pilot_25_v1.1/
```

## 全量跑批 + 合并

```bash
cd feature_v2

python -u weight_judge/run_judge.py \
  --input artifacts/weight_judge/full/01_judge_input.jsonl \
  --output artifacts/weight_judge/full/02_judge_output.jsonl \
  --meta artifacts/weight_judge/full/run_meta.json \
  --temperature 0.2 --workers 8 --sleep 0.05

python weight_judge/merge_backfill.py \
  --judge-output artifacts/weight_judge/full/02_judge_output.jsonl \
  --out artifacts/weight_judge/full/03_meixue_cars2_with_guides_weight.jsonl \
  --report artifacts/weight_judge/full/merge_report.json
```
