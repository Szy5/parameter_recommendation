# 汽车美学 Benchmark：召回 → 路径 → 预测 → 参数推荐

本仓库只保留这一条交付链路。关键词来自 100 条 Benchmark，在美学特征节点上做向量召回，沿知识图谱走到汽车风格 / 汽车车型，再按风格和车型查参数与实例。

## 技术方案

### 问题

甲方 Benchmark 的主路径形态是：

    科技 -> StyleAssociatedWith -> 极简家庭车 -> ImplementedBy -> 紧凑占地尺寸 -> Indicates -> 直立高坐姿

头是 7 个汽车风格或粗粒度车型，第一跳永远是 `StyleAssociatedWith` / `TypeAssociatedWith`，尾巴是召回的美学特征。AssociatedWith 是稀疏的签名桥，不是每个特征都直接挂风格。

### 图怎么构造

- `data/kgdata_0804.jsonl`：原始图谱。没有 AssociatedWith。
- `data/judge/style_edges.jsonl`、`type_edges.jsonl`：Judge 置信度 > 0.8 的特征→风格 / 特征→车型边。
- 不能把全部 Judge 边灌进图，否则主路径会塌成 1 跳。
- 只把桥接类型上的边反过来写成 `(风格|车型)-[:AssociatedWith]->(桥)`。桥接类型是 `VehiclePosture`、`FamilyDNA`、`AestheticConcept`。
- 合并结果是 `data/kgdata_0804_assoc_bridges.jsonl`，也是导入 Neo4j 的图。

原图关系（中间跳）仍是：`Indicates`、`ImplementedBy`、`Guides`、`Influences`、`Prefers`、`Constrains`、`DefinesDNA`。

### 运行时四步

1. **召回**  
   查询文本是关键词用中文分号拼接。语料是七类美学特征（`data/features_all.jsonl`，9649 个节点），不用风格/车型/实例。BGE-M3 算余弦相似度，取 Top-K 再按 `min_score` 过滤。离线可直接读 `data/recall_top20.jsonl`。

2. **路径**  
   用召回节点的 `_graph_id` 在 Neo4j 上反查：原图关系 0–4 跳，最后一跳 AssociatedWith 落到风格或车型，再翻成甲方展示顺序。邻接证据只用原图 1 跳，不含 AssociatedWith。

3. **预测**  
   对路径头投票：每个 (头, 召回节点) 取最短跳数，`score = 召回分 / hops` 再求和。目前没有 LLM 意图过滤，票数多的头不一定等于甲方 `predicted`。

4. **参数推荐**  
   用预测出的风格 / 车型（可选级别）查 `Guides` 参数，以及风格+车型下的汽车实例尺寸分位。

路径展示若太多，可后处理：每条路径 `召回分/跳数`，每个 (头, 召回节点) 只留最短一条，风格保留 Top 3、车型保留 Top 4。默认不截总条数。

## 仓库结构

```text
data/
  kgdata_0804.jsonl                 原始知识图谱
  kgdata_0804_assoc_bridges.jsonl   原图 + 桥接 AssociatedWith（主图）
  judge/style_edges.jsonl           Judge 风格边（conf > 0.8）
  judge/type_edges.jsonl            Judge 车型边
  features_all.jsonl                召回语料（七类特征）
  recall_top20.jsonl                已算好的 BGE-M3 Top-20
benchmark/
  benchmark_100_inputs.jsonl        100 条关键词
  benchmark_100_深思版.jsonl        甲方参考结果
feature/benchmark_fixed_type_recall/   向量召回
feature/benchmark_recommendation/      路径 / 预测 / 推荐 CLI
feature/benchmark_upstream_offline/    从 Judge 生成桥接边并合并图谱
feature/parameter_recommendation/      Neo4j 导入与在线推荐底层
```

## 环境

```bash
pip install -r requirements.txt
cp feature/.env.example feature/.env   # 填 Neo4j
```

在仓库根目录设置 `PYTHONPATH`：

```bash
export PYTHONPATH=/path/to/extraction
```

脚本会自己切到仓库根，不必 export 也能跑 `scripts/*.sh`。

## 使用

脚本目录：`feature/benchmark_recommendation/scripts/`。超参数在 `config.sh`，也可用环境变量覆盖。默认离线召回、`TOP_K=10`、`MIN_SCORE=0.65`、最多 5 跳。

```bash
cd feature/benchmark_recommendation/scripts

# 只看召回节点
./run_recall.sh

# 召回 + Cypher 路径（不按预测裁剪，适合检查）
./run_path.sh

# 路径后处理：去重 + 风格 Top3 / 车型 Top4
./run_postprocess.sh

# 召回 + 路径头投票预测风格/车型
./run_predict.sh

# 预测后再查参数与实例（默认连 Neo4j）
./run_recommend.sh
```

常用覆盖：

```bash
MIN_SCORE=0.60 TOP_K=20 MAX_HOPS=3 ./run_path.sh
RECALL_SOURCE=live ./run_recall.sh          # 重新跑 BGE-M3，需要 GPU
OUTPUT_JSON=/tmp/paths.json ./run_path.sh
MAX_STYLE_HEADS=3 MAX_TYPE_HEADS=4 ./run_postprocess.sh
```

输出默认写到 `feature/benchmark_recommendation/artifacts/`（已 gitignore）。JSON 里每案是 `id`、`input.keywords`、`recalled_nodes`、`paths`；`predict` / `recommend` 还会带 `predicted` 和 `recommendation`。

等价 Python（路径阶段）：

```bash
python3 -m feature.benchmark_recommendation.run_benchmark_recommendation \
  --stage path \
  --recall-source offline \
  --graph-jsonl data/kgdata_0804_assoc_bridges.jsonl \
  --recall-top20-jsonl data/recall_top20.jsonl \
  --benchmark-inputs-jsonl benchmark/benchmark_100_inputs.jsonl \
  --env feature/.env \
  --min-score 0.65 --top-k 10 --max-hops 5 \
  --output-json feature/benchmark_recommendation/artifacts/benchmark_path_results.json
```

`--stage` 可选：`recall` | `path` | `predict` | `recommend`。

### 重新算向量召回

```bash
python3 -m feature.benchmark_fixed_type_recall.run_recall \
  --features data/features_all.jsonl \
  --benchmark benchmark/benchmark_100_inputs.jsonl \
  --output-dir /tmp/bge_recall \
  --model BAAI/bge-m3 \
  --revision 5617a9f61b028005a4858fdac845db406aefb181
```

输出目录必须为空。完成后把 `recall_top20.jsonl` 拷回 `data/` 即可给离线流水线用。

### 从 Judge 重建桥接图并导入 Neo4j

```bash
python3 -m feature.benchmark_upstream_offline.build_bridge_associated_graph \
  --graph data/kgdata_0804.jsonl \
  --style-edges data/judge/style_edges.jsonl \
  --type-edges data/judge/type_edges.jsonl \
  --output-dir /tmp/bridge_associated \
  --merged-graph data/kgdata_0804_assoc_bridges.jsonl

python3 -m feature.parameter_recommendation.import_jsonl_to_neo4j \
  --graph data/kgdata_0804_assoc_bridges.jsonl \
  --env feature/.env \
  --report /tmp/neo4j_import_report.json
```

导入会写入当前 Aura/Neo4j 库，请确认连的是对的实例。

## 测试

```bash
cd test
PYTHONPATH=.. python3 -m unittest test_benchmark_recommendation test_benchmark_fixed_type_recall
```

## 已知边界

- 路径头覆盖多种风格/车型是图连通性导致的，甲方深思版展示路径同样如此，最终答案靠 `predicted` 收口；本仓库尚未做 LLM 意图过滤。
- `min_score` 过高会出现空召回、空路径案。
- 后处理压的是头的种类，不是语义对错。B001 仍可能留下「跑车」这类靠多跳投票进来的头。
