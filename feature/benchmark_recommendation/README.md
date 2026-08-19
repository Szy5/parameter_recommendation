# Benchmark 关键词到模块化参数推荐

流程：

```text
keywords（Benchmark 100）
  → 固定节点召回（offline: recall_top20；live: BGE-M3 在线 embedding）
  → AssociatedWith 加权投票预测 car_style / car_type / car_level（离线图）
  → 推荐阶段默认直接查 Neo4j
```

## Bash 脚本（推荐）

目录：`feature/benchmark_recommendation/scripts/`

| 脚本 | 作用 |
|------|------|
| `run_recall.sh` | 只跑召回 |
| `run_predict.sh` | 召回 + 预测 |
| `run_recommend.sh` | 召回 + 预测 + 推荐（默认 Neo4j） |

所有超参数集中在 `config.sh`，也可在命令前用环境变量临时覆盖：

```bash
cd feature/benchmark_recommendation/scripts

# 只召回，降低阈值
MIN_SCORE=0.55 ./run_recall.sh

# 在线召回 + 全量推荐
RECALL_SOURCE=live ./run_recommend.sh

# 只要风格指导 + style/type 范围推荐
RECOMMEND_TYPES="style_guides,style_type" ./run_recommend.sh

# 自定义输出路径
OUTPUT_JSON=/tmp/my_predict.json AMBIGUITY_MARGIN=0.05 ./run_predict.sh
```

常用可覆盖变量：

- 召回：`RECALL_SOURCE`、`RECALL_MODE`、`TOP_K`、`MIN_SCORE`、`MAX_CANDIDATES`
- live：`FEATURES_JSONL`、`EMBED_MODEL`、`LIVE_POOL_SIZE`、`EMBED_DEVICE`
- 预测：`AMBIGUITY_MARGIN`、`LEVEL_AMBIGUITY_MARGIN`
- 推荐：`RECOMMEND_TYPES`（如 `style_guides,style_type`）、`RECOMMEND_SOURCE`、`MAX_STYLES`、`MAX_TYPES`、`FALLBACK_ON_SMALL_SAMPLE`

## 召回来源

- `--recall-source offline`（默认）：读取预计算的 `recall_top20.jsonl`
- `--recall-source live`：每次运行加载 BGE-M3，对 9649 个固定节点做在线向量召回

离线模式示例：

```bash
python3 -m feature.benchmark_recommendation.run_benchmark_recommendation \
  --stage recommend \
  --recall-source offline \
  --recall-top20-jsonl "feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f/recall_top20.jsonl" \
  ...
```

在线召回示例：

```bash
python3 -m feature.benchmark_recommendation.run_benchmark_recommendation \
  --stage recall \
  --recall-source live \
  --features-jsonl "feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl" \
  --model BAAI/bge-m3 \
  --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --graph-jsonl "feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/07_merged_graph/kgdata_0804_with_associated_edges_confidence_gt_0.8.jsonl" \
  --benchmark-inputs-jsonl "benchmark/benchmark_100_inputs.jsonl" \
  --output-json "feature/benchmark_recommendation/artifacts/benchmark_recommendation_live_recall.json" \
  --recall-mode top_k_and_threshold \
  --min-score 0.60 --top-k 20 --live-pool-size 50
```

`live` 模式额外参数：

- `--features-jsonl`：9649 个固定召回节点语料
- `--model` / `--revision`：BGE-M3 模型与固定 revision
- `--batch-size` / `--max-seq-length` / `--device`
- `--live-pool-size`：过滤前先保留的候选池大小（非 threshold 模式）

## 推荐用的 Cypher

1. 汽车风格 → 设计参数（`style_parameter_guides`）

```cypher
MATCH p=(s:汽车风格)-[:`Guides(指导)`]->(:`DesignParameter(设计参数)`)
WHERE s.name CONTAINS '豪华'
RETURN p LIMIT 250;
```

2. 风格 + 车型 → 参数范围 + 汽车实例（`range_recommendation.style_type`）

```cypher
MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
WHERE style.name CONTAINS '科技' AND n.name CONTAINS 'SUV'
RETURN p LIMIT 50;
```

3. 风格 + 车型 + 级别 → 参数范围 + 汽车实例（`range_recommendation.style_type_level`）

在上面第二条上再挂 `汽车级别-[:包含]->汽车实例`。

每条推荐结果里都会带回对应的 `cypher`。数值聚合读取汽车实例节点上的尺寸属性（`长度(mm)` 等），并推荐若干 `recommended_vehicles`。

## 一键运行

```bash
python3 -m feature.benchmark_recommendation.run_benchmark_recommendation \
  --stage recommend \
  --recall-source offline \
  --graph-jsonl "feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/07_merged_graph/kgdata_0804_with_associated_edges_confidence_gt_0.8.jsonl" \
  --recall-top20-jsonl "feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f/recall_top20.jsonl" \
  --benchmark-inputs-jsonl "benchmark/benchmark_100_inputs.jsonl" \
  --output-json "feature/benchmark_recommendation/artifacts/benchmark_recommendation_results_v8.json" \
  --env "feature/.env" \
  --recommend-source neo4j \
  --recall-mode top_k_and_threshold \
  --min-score 0.60 --top-k 20 --max-candidates 50 \
  --ambiguity-margin 0.02 --min-candidate-score 1e-9 --level-ambiguity-margin 0.05 \
  --max-styles 2 --max-types 2 --max-combinations 4 \
  --small-sample-threshold 10 --fallback-on-small-sample --fallback-small-sample-threshold 8
```

`--stage recall` / `--stage predict` 不连 Neo4j；`--stage recommend` 默认连 Neo4j。  
若要完全离线推荐，加 `--recommend-source offline`。

## 超参数

- 召回：`--recall-source`、`--recall-mode`、`--top-k`、`--min-score`、`--max-candidates`
- live 召回：`--features-jsonl`、`--model`、`--revision`、`--live-pool-size`、`--device`
- 预测：`--ambiguity-margin`、`--min-candidate-score`、`--level-ambiguity-margin`
- 推荐：`--max-styles`、`--max-types`、`--max-combinations`、`--small-sample-threshold`、`--fallback-on-small-sample`

## 输出字段顺序

每个 case：`id → input → recalled_nodes → predicted → paths → recommendation`

- `recalled_nodes` / `predicted.*` / `paths` / `parameters`：每个 dict 占一行
- `recommendation.style_parameter_guides`：Cypher + 风格指导参数
- `recommendation.range_recommendation.style_type`：Cypher + 参数范围 + 汽车实例
- `recommendation.range_recommendation.style_type_level`：Cypher + 参数范围 + 汽车实例
