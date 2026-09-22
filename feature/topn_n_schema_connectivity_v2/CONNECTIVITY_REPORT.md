# Top-N Schema 按起点标签聚合连通性报告

## 技术结论

本次将修正后的 Top 25 Schema 按最左侧的召回节点标签分为 6 组，并计算每组的联合连通性。一个起点节点只要能通过组内任意一条 Schema 到达 `汽车风格` 或 `汽车车型`，就记为可连通，命中多条 Schema 或多条具体路径仍只计一次。

6 类标签的统计均成功完成。连通性从 `36.78%` 到 `94.93%` 不等：`FamilyDNA(家族DNA)` 最高，`DesignParameter(设计参数)` 最低。结果说明，按标签聚合后能够更准确地描述“某类召回节点是否具备至少一种可用推理路径”，避免用单条、可能高度专用的 Schema 代表整类节点的可用性。

该指标适合作为**标签级知识图谱可用性指标**，但不等于预测准确率，也不保证同一节点能同时到达风格和车型。

## 六类起点标签的连通性结果

| 排名 | 起点标签 | 组内 Schema 数 | 可连通节点数 | 该标签总结点数 | connectivity |
|---:|---|---:|---:|---:|---:|
| 1 | FamilyDNA（家族DNA） | 3 | 468 | 493 | 94.93% |
| 2 | VehiclePosture（汽车姿态） | 2 | 844 | 1,005 | 83.98% |
| 3 | AestheticConcept（美学概念） | 3 | 801 | 987 | 81.16% |
| 4 | UserTrend（用户与趋势） | 7 | 687 | 1,048 | 65.55% |
| 5 | DesignAttribute（设计属性） | 6 | 1,800 | 3,107 | 57.93% |
| 6 | DesignParameter（设计参数） | 4 | 1,007 | 2,738 | 36.78% |

从结果看，前三类标签中超过八成的节点至少具备一条通向风格或车型的 Top-N Schema 路径。`DesignParameter` 的联合连通性相对较低，说明当前 Top 25 Schema 对设计参数节点的覆盖仍较集中；这可以作为后续补充 Schema 或知识图谱关系的优先检查方向。

本报告不计算一个跨标签的“总连通性”。不同标签的节点数量和语义不同，直接混合可能掩盖具体哪一类节点存在结构短板，因此以标签级结果作为最终口径。

## 指标定义：衡量一类节点是否至少有一种可用路径

对于起点标签 `L`，设：

- `V_L`：知识图谱中所有标签为 `L` 的起点节点集合；
- `S_L`：Top 25 中所有以 `L` 开头的 Schema；
- `C_L`：`V_L` 中至少能通过 `S_L` 的任意一条 Schema 到达 `汽车风格` 或 `汽车车型` 的节点集合。

最终指标为：

```text
connectivity(L) = |C_L| / |V_L|
```

节点级判定可以写成：

```text
connected(v) = EXISTS(schema_1)
            OR EXISTS(schema_2)
            OR ...
            OR EXISTS(schema_k)
```

这里使用的是集合并集，而不是把每条 Schema 的连通节点数直接相加。假设 Schema A 能连接 60 个节点，Schema B 能连接 50 个节点，其中有 30 个节点重复，则联合可连通节点数为：

```text
60 + 50 - 30 = 80
```

而不是 110。这样可以保证同一节点最多贡献一次分子，最终连通性不会超过 100%。

## 为什么按标签聚合比逐条 Schema 更符合当前目标

### 1. 当前关心的是节点有没有可用通路，而不是每条模板是否通用

召回阶段返回的是具体知识图谱节点。后续系统真正需要判断的是：该节点能否沿至少一条合法路径推导到汽车风格或汽车车型。

单条 Schema 连通性较低，只能说明这一条固定路径较专用；它不能直接证明该类节点整体不可用。只要该节点能够通过同组另一条 Schema 到达目标，系统仍然可以继续完成预测。

### 2. 多条 Schema 本来就是同一类节点的替代路径

同一个起点标签可能通过不同中间概念到达目标。例如 `DesignAttribute` 可以经过 `VehiclePosture`，也可以经过 `UserTrend`、`AestheticConcept` 或 `DesignParameter`。这些路径对系统而言是可替代的推理通道。

按标签取 Schema 并集，衡量的是这组替代路径共同提供的能力，和系统实际使用方式更加一致。

### 3. 分母稳定且解释直接

每组的分母固定为该标签在知识图谱中的全部节点数，分子固定为至少命中一条组内 Schema 的不同起点节点数。因此可以直接回答：

> 对于这一类可能被召回的节点，有多少比例至少具备一条通向风格或车型的推理路径？

### 4. 节点去重避免高频节点或多路径节点夸大结果

某个节点可能同时命中多条 Schema，也可能沿同一 Schema 找到多条具体实例路径。算法只判断 `EXISTS`，不会因为路径多而重复计数，因此指标反映覆盖广度，而不是路径数量。

## 与逐条 Schema 连通性的关系

为了验证聚合计算符合并集逻辑，将每类标签的新连通性与该组内最高的单条 Schema 连通性进行了对比：

| 起点标签 | 组内最高单条连通性 | 标签聚合连通性 | 增加 |
|---|---:|---:|---:|
| FamilyDNA（家族DNA） | 91.48% | 94.93% | +3.45 个百分点 |
| VehiclePosture（汽车姿态） | 74.93% | 83.98% | +9.05 个百分点 |
| AestheticConcept（美学概念） | 71.63% | 81.16% | +9.52 个百分点 |
| UserTrend（用户与趋势） | 44.27% | 65.55% | +21.28 个百分点 |
| DesignAttribute（设计属性） | 25.88% | 57.93% | +32.06 个百分点 |
| DesignParameter（设计参数） | 24.65% | 36.78% | +12.13 个百分点 |

所有标签的聚合连通性都不低于其组内最高单条 Schema 连通性，符合集合并集应有的单调性。其中 `DesignAttribute` 提升最明显，说明单条 Schema 之间存在较强的互补覆盖；仅展示任意一条 Schema 都会明显低估这一类节点的整体可用性。

## 算法实现：每个标签执行一次聚合查询

程序的执行过程如下：

1. 从修正后的 V1 结果中读取前 25 条唯一 Schema。
2. 解析每条 Schema 的节点标签和关系类型。
3. 按最左侧 `start_label` 分组，本次得到 6 组。
4. 将短关系名转换成 Neo4j 中的完整关系名，例如 `Prefers` 转为 `Prefers(偏好)`。
5. 对组内每条 Schema 生成一个 `EXISTS` 路径条件。
6. 使用逻辑 `OR` 合并组内全部 `EXISTS` 条件。
7. 对该标签的所有起点节点执行一次聚合，得到分母和去重后的分子。
8. 计算 `connectivity`，并按连通性从高到低生成排序字段。

生成的 Cypher 结构可概括为：

```cypher
MATCH (start:`某起点标签`)
WITH start, (
  EXISTS {
    MATCH <Schema 1 对应的完整路径>
  }
  OR EXISTS {
    MATCH <Schema 2 对应的完整路径>
  }
  OR ...
) AS connected
RETURN count(start) AS total_start_nodes,
       sum(CASE WHEN connected THEN 1 ELSE 0 END) AS connected_start_nodes
```

虽然业务定义是“遍历标签中的每一个节点”，实现上不需要对每个节点单独向 Neo4j 发送请求。数据库在一条聚合 Cypher 内完成节点遍历和路径存在性判断。本次由原来的 25 条逐 Schema 查询减少为 6 条标签聚合查询，同时保持相同的节点级判定语义。

## 路径方向沿用原始路径提取语义

本次继续使用 `path_extraction` 模式：

- `Indicates`、`Prefers`、`Guides`、`Influences` 等中间关系按无向方式匹配；
- 最后一跳 `StyleAssociatedWith` 或 `TypeAssociatedWith` 保留图谱中的实际方向；
- 反转 Schema 中的 `<-` 主要表达从召回节点到目标节点的展示顺序，不能直接理解为所有中间关系的真实方向。

保持这一规则是为了让连通性查询和产生 Top-N Schema 时的路径查询口径一致，避免因展示方向和存储方向不同而漏掉原本能够命中的路径。

## 数据一致性与验证结果

本次默认读取修正后的 `top25_schema_connectivity_500.json`，而不是仍包含历史错误标签的旧版 `pass_rate_500.json`。这样可以避免此前由同名节点覆盖造成的错误 Schema 再次进入计算。

验证情况如下：

- 修正后的 25 条 Schema 均为唯一值；
- 成功分为 6 个起点标签组；
- 6 条 Neo4j 聚合查询全部成功，错误数为 0；
- 每组均满足 `0 ≤ connected_start_nodes ≤ total_start_nodes`；
- 每类聚合连通性均不低于组内最高单条 Schema 连通性；
- 自动化测试共 18 项，覆盖分组、方向、查询生成、节点去重、空分母和结果排序，全部通过。

## 指标边界：高连通性不等于预测正确

这个指标只回答知识图谱的结构问题：节点是否存在至少一条 Top-N Schema 路径通向风格或车型。它不直接衡量：

- 路径的语义是否与输入关键词真正相关；
- 最终预测的汽车风格或车型是否准确；
- 到达的是风格还是车型；
- 是否能够同时到达风格和车型；
- Schema 的路径长度、可解释性和业务质量。

另外，联合连通性会随着组内 Schema 数量增加而单调不减。因此比较不同版本时，应固定相同的 `Top-N` 和图谱版本；否则连通性提升可能只是因为加入了更多 Schema，而不一定代表图谱本身变得更完善。

## 建议的使用方式

建议将本次标签聚合 `connectivity` 作为对甲方展示的主连通性指标，用它回答“各类召回节点是否具备至少一条可用推理路径”。

逐条 Schema 连通性仍可保留为内部诊断指标。当某类标签连通性较低时，再查看该组各条 Schema 的覆盖和重叠情况，判断问题来自 Schema 数量不足、Schema 之间高度重叠，还是图谱关系缺失。

现阶段优先关注 `DesignParameter` 和 `DesignAttribute` 两类：前者联合连通性最低，后者虽然通过多条 Schema 的互补覆盖提升明显，但仍有约四成节点无法通过当前 Top 25 到达目标。

## 运行方式与产物

正式计算命令：

```bash
python3 -m feature.topn_n_schema_connectivity_v2.calculate_connectivity \
  --schemas-json data/top25_schema_connectivity_500.json \
  --top-n 25 \
  --direction-mode path_extraction \
  --env feature/.env \
  --output data/top25_schema_connectivity_by_label_500.json
```

主要产物：

- 实现代码：`feature/topn_n_schema_connectivity_v2/`
- 计算结果：`data/top25_schema_connectivity_by_label_500.json`
- 自动化测试：`test/test_top_n_schema_connectivity_v2.py`

结果 JSON 中：

- `results` 保存每类标签的 Schema、分子、分母、连通性和实际 Cypher；
- `labels_sorted_by_connectivity` 仅保存起点标签和对应连通性，并按连通性降序排列；
- 指标名称统一为 `connectivity`，不再使用 `any_connectivity`。
