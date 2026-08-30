# Benchmark 路径阶段运行速度优化方案

## 1. 目标与范围

本次只优化 `evidence / predict / recommend` 阶段从 Neo4j 查询证据路径的运行速度，不改变召回、路径展示、预测和推荐的业务规则，也暂不处理自动重试、断点恢复等稳定性功能。

验收边界：

- 相同输入和配置下，优化前后的公开 `paths` 语义保持一致。
- `--max-hops` 在 Neo4j 查询阶段生效，不再查询必然会被 Python 丢弃的更长路径。
- `--no-neighbor` 不再执行邻居证据查询。
- 同一轮任务中，相同召回节点、相同路径配置只查询一次 Neo4j。
- Neo4j 能通过统一标签上的 `_graph_id` 唯一索引定位召回节点。

## 2. 当前瓶颈

### 2.1 召回节点起点没有命中索引

当前路径查询使用：

```cypher
UNWIND $node_ids AS nid
MATCH (recalled {_graph_id: nid})
```

`recalled` 没有标签，而导入程序只为各业务标签分别建立 `_graph_id` 约束。Neo4j 无法稳定地使用这些标签索引定位起点。

### 2.2 `max_hops` 只在查询结束后过滤

多跳查询固定搜索原始关系 `1..4` 跳，`--max-hops` 仅在 Python 中过滤结果。因此配置较小的 `max_hops` 仍会执行不必要的图扩张。

### 2.3 `--no-neighbor` 没有省掉数据库查询

当前代码始终执行邻居证据查询，`--no-neighbor` 只控制是否把查询结果放入最终 `paths`。

### 2.4 跨 case 重复查询严重

500 条离线召回结果在 `top_k=20、min_score=0.60` 下共有 7,804 次节点引用，但只有 1,411 个不同节点；6,393 次引用是重复的，约占 81.9%。当前实现会为这些重复节点反复搜索路径。

## 3. 修改步骤

### 步骤一：建立统一 `GraphNode` 标签与索引

修改图谱导入程序，使所有导入节点同时带有 `GraphNode` 标签，并创建：

```cypher
CREATE CONSTRAINT graph_node_graph_id IF NOT EXISTS
FOR (n:GraphNode)
REQUIRE n._graph_id IS UNIQUE
```

新增幂等迁移脚本，用于给已经导入的节点补充标签和约束。迁移前必须检查 `_graph_id` 是否为空或重复；存在异常时停止，不修改数据库。

当前 `data/kgdata_0821.jsonl` 已验证：12,130 个节点对应 12,130 个唯一 `_graph_id`，不存在重复。

### 步骤二：路径查询通过统一索引定位起点

五类路径查询统一改为：

```cypher
UNWIND $node_ids AS nid
MATCH (recalled:GraphNode {_graph_id: nid})
```

修改范围：直接风格、直接车型、多跳风格、多跳车型和邻居证据查询。

多跳查询在截取每组前 `per_pair` 条路径之前，按照召回节点、目标节点、跳数、路径节点和关系稳定排序。原因是原查询只按跳数排序，多个等长候选的选择取决于执行计划；索引生效后执行计划变化可能导致随机换成另一条等长路径。稳定排序保证重复运行得到一致结果。

### 步骤三：将 `max_hops` 下推到 Neo4j

`batch_main_paths()` 接收 `max_hops`。总路径跳数包含最后一条 `AssociatedWith`，因此原始关系搜索深度为 `max_hops - 1`：

- `max_hops=1`：只查询直接路径，完全跳过多跳查询。
- `max_hops=3`：原始关系最多搜索 2 跳。
- `max_hops=5`：保持现有原始关系最多 4 跳的语义。

动态深度只允许经过整数校验并限制在 `1..4` 后写入 Cypher，不能接受任意字符串。

### 步骤四：让 `--no-neighbor` 真正跳过查询

`pipeline._paths_from_neo4j()` 接收 `include_neighbor`，为 `False` 时不调用 `batch_neighbor_evidence()`。

### 步骤五：增加跨 case 路径缓存

在 `BenchmarkNeo4jRepository` 生命周期内缓存：

- 主路径：键包含 `node_id、max_hops、per_pair`。
- 邻居路径：键包含 `node_id、per_node`。

每次只把未缓存的节点 ID 发给 Neo4j，再按输入节点顺序组合缓存结果。没有查到路径的节点也要缓存为空，避免下一条 case 再次查询。

邻居查询补充返回 `recalled_id`，避免使用可能重名的 `recalled_name` 作为缓存键。

## 4. 不在本次范围内

- Neo4j 自动重试。
- case 级 checkpoint / resume。
- 调大网络或事务超时时间。
- 修改召回阈值、Top K 或业务上的路径评分与筛选规则。

这些属于稳定性或业务配置，后续单独处理。

## 5. 验证方案

### 单元测试

- 查询必须包含 `:GraphNode`。
- `max_hops=1` 不执行多跳查询。
- `max_hops=3` 生成 `*1..2` 的原始关系查询。
- `include_neighbor=False` 不调用邻居仓库方法。
- 第二次查询相同节点时不再访问底层 Neo4j。
- 部分节点已缓存时只查询缺失节点。

### 数据库验证

- `GraphNode` 节点数等于图谱节点数 12,130。
- `_graph_id` 无空值、无重复。
- `SHOW CONSTRAINTS` 能看到 `graph_node_graph_id`。
- `EXPLAIN` 的召回节点起点应使用唯一索引查找，而不是无标签全节点扫描。

### 结果验证

- 先运行现有 16 个单元测试。
- 使用 10 条 benchmark 对比优化前后的 case、召回节点和公开路径集合。
- 最后运行 500 条 evidence，确认包含 M001 至 M500，并记录总耗时。

## 6. 实施与验收结果

本方案已于 2026-08-30 实施：

- 当前 Neo4j 的 12,130 个节点已全部添加 `GraphNode` 标签。
- `graph_node_graph_id` 唯一约束已创建，执行计划出现 `NodeUniqueIndexSeek`。
- 10 条 evidence 独立运行两次，`cases` 完全一致，分别耗时 11.09 秒和 11.00 秒。
- 完整 500 条 evidence 成功输出 M001 至 M500，共 28,868 条路径，468 个 case 有路径，总耗时 322.89 秒。
- 500 条任务共有 7,804 次有效召回节点引用，其中只有 1,411 个唯一节点，缓存避免了 6,393 次重复图扩张。
- 按每个需要新节点的 case 执行 5 类路径查询估算，Neo4j 查询往返由约 2,500 次降至 1,545 次，减少约 38.2%。
- 项目全部 27 个单元测试通过。

本次没有实现自动重试、checkpoint 或 resume，符合“只优化运行速度”的范围约束。
