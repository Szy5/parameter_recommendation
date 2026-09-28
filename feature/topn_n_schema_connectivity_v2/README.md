# Top-N Schema 按起点标签聚合连通性

本版本不再分别评价每条 Schema，而是把 Top-N Schema 按最左侧的起点标签分组。

对于某个起点标签，图谱中的一个节点只要能通过该组任意一条 Schema 到达 `汽车风格` 或 `汽车车型`，就记为一次可连通。即使同一节点命中多条 Schema 或多条具体路径，分子仍只加 1。

```text
connectivity = connected_start_nodes / total_start_nodes
```

正式运行：

```bash
python3 -m feature.topn_n_schema_connectivity_v2.calculate_connectivity \
  --schemas-json data/top25_schema_connectivity_500.json \
  --top-n 25 \
  --direction-mode path_extraction \
  --env feature/.env \
  --output data/top25_schema_connectivity_by_label_500.json
```

只生成 Cypher、不连接 Neo4j：

```bash
python3 -m feature.topn_n_schema_connectivity_v2.calculate_connectivity \
  --dry-run \
  --output data/top25_schema_connectivity_by_label_500_dry_run.json
```

默认 `path_extraction` 方向模式与原始路径提取逻辑一致：普通中间关系无向匹配，最后的 `StyleAssociatedWith` 或 `TypeAssociatedWith` 保留图谱方向。

默认输入使用已经完成节点标签修正的 V1 连通性结果。程序也兼容 `pass_rate_500.json` 的 `SCHEMAS` 格式，但旧文件若尚未重新生成，可能仍包含历史上由同名节点标签覆盖产生的错误 Schema。

本次结果及指标合理性说明见 [CONNECTIVITY_REPORT.md](CONNECTIVITY_REPORT.md)。
