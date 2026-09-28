# Top-N Schema 连通性统计

## 统计定义

对每条 Top-N Schema：

```text
连通性 = 至少存在一条完整 Schema 路径的起点节点数 / 该起点类型的总结点数
```

例如，起点标签 `A` 有 100 个节点，其中只有 1 个节点能按 Schema 到达终点 `B`，则连通性为 `1/100 = 1%`。

当前 `path_schemas_500_reversed.json` 中 Schema 的左侧是起点，右侧是 `汽车风格` 或 `汽车车型`。例如：

```text
VehiclePosture(汽车姿态) <- StyleAssociatedWith <- 汽车风格
```

对应 Cypher 从左侧的 `VehiclePosture(汽车姿态)` 节点出发，沿入边到达右侧的 `汽车风格`。

需要注意：现有 Top-N Schema 的箭头主要表示反转后的展示顺序，不能全部当作真实关系方向。生成这些路径时：

- `Indicates/Prefers/Guides/Influences/...` 等中间关系采用无向匹配；
- `StyleAssociatedWith/TypeAssociatedWith` 保留图谱方向，均为汽车风格或车型指向 bridge 节点。

因此默认的 `--direction-mode path_extraction` 会复现原路径提取语义：中间关系无向，最后的 AssociatedWith 从左向右按入边遍历。若使用 `--direction-mode literal`，程序会把 Schema 中每一个 `<-` 都作为真实入边；该模式仅适合对照，或未来已经正确保存每条边方向的 Schema 文件。

## 实现说明

- Top-25 默认读取自 `data/pass_rate_500.json` 第一项的 `SCHEMAS`；
- Schema 中的短关系名会转换为 Neo4j 的完整关系类型，例如 `Indicates` 转为 `Indicates(体现)`；
- 默认按原路径提取语义处理方向，中间关系无向、AssociatedWith 保留方向；
- 每条 Schema 只执行一次聚合 Cypher，而不是为每个起点节点单独发送请求；
- 查询使用 `EXISTS` 判断每个起点是否至少有一条完整路径，因此统计的是不同起点节点数，不是路径总数；
- 起点类型没有任何节点时，连通性返回 `null`，不会误记为 0%。

## 运行

先生成并检查 Cypher，不连接 Neo4j：

```bash
python3 -m feature.topn_n_schema_connectivity.calculate_connectivity \
  --dry-run \
  --output data/top25_schema_connectivity_dry_run.json
```

连接 Neo4j 正式统计：

```bash
python3 -m feature.topn_n_schema_connectivity.calculate_connectivity \
  --schemas-json data/pass_rate_500.json \
  --top-n 25 \
  --direction-mode path_extraction \
  --env feature/.env \
  --output data/top25_schema_connectivity_500.json
```

若希望某一条 Schema 查询失败后继续执行其余 Schema，可增加：

```text
--continue-on-error
```

正式运行前需要确认 Neo4j 中加载的是本次需要评估的 KG 版本。连通性反映数据库当前图谱内容；如果 Neo4j 与生成 Top-25 时使用的 KG 版本不同，结果需要注明版本差异。
