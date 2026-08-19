# parameter_recommendation

汽车美学知识图谱上的关键词召回、风格/车型预测与模块化参数推荐。

## 模块

| 目录 | 说明 |
|------|------|
| `feature/benchmark_recommendation/` | Benchmark 推荐主流程（召回 → 预测 → 推荐） |
| `feature/benchmark_upstream_offline/` | 上游 LLM Judge 离线链路（Style/Type 关联边生成） |
| `feature/benchmark_fixed_type_recall/` | BGE-M3 固定类型召回 |
| `feature/parameter_recommendation/` | 参数推荐与 Neo4j 导入脚本 |
| `benchmark/` | 100 条 benchmark 输入与期望标签 |
| `data/` | 知识图谱 JSONL（`kgdata_0804.jsonl`、`kgdata_0819.jsonl`） |
| `test/` | 单元测试 |

## 快速开始

```bash
# 1. 配置凭据
cp feature/.env.example feature/.env
# 编辑 feature/.env，填入 LLM API 与 Neo4j 连接信息

# 2. 安装依赖（按各模块 README 补充）
pip install neo4j sentence-transformers

# 3. 运行 benchmark 推荐（见 feature/benchmark_recommendation/README.md）
cd feature/benchmark_recommendation/scripts
./run_recommend.sh
```

## 数据说明

- `data/kgdata_0819.jsonl`：0819 版合并图谱（含 Style/Type 关联边）
- `feature/artifacts/` 为本地生成的召回/评测产物，未纳入版本库；需按各模块 README 自行生成
- 图谱导入 Neo4j：`python feature/parameter_recommendation/import_jsonl_to_neo4j.py --graph data/kgdata_0819.jsonl --env feature/.env --report /tmp/import_report.json`

## 文档

- `BUPT项目：汽车参数推荐需求梳理.md` — 需求梳理
- `feature/parameter_recommendation/BENCHMARK关键词到模块化参数推荐技术方案.md` — 技术方案
