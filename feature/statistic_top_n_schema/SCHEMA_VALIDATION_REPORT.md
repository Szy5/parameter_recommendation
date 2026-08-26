# 路径 Schema 方向与 Top-N 覆盖率验证说明

## 一、结论摘要

本次验证针对 100 条 Benchmark 样本完成了两项检查：

1. 已将路径按“召回节点在前、风格/车型候选在后”的顺序展示。反转后的路径端点没有发散到大量候选：全部 100 条样本的风格端点不超过 2 个，车型端点也不超过 2 个。
2. 对能够到达汽车风格或汽车车型的 schema 按样本覆盖次数排序，暂取 Top-25。按当前代码“命中至少 1 条 Top-25 schema 即通过”的口径，共 67 条样本通过，通过率为 **67%**。

需要特别说明：当前第二项指标衡量的是“Top-N 高频 schema 对样本的覆盖率”，并不是“每条样本都能同时到达车型和级别”。当前 schema 中验证的目标端点是汽车风格和汽车车型，汽车级别尚未纳入该指标。

## 二、疑问一：路径方向及候选是否发散

### 2.1 路径方向调整

原路径示例：

```text
简约 -> StyleAssociatedWith -> Friendly Compact Stance
```

调整后：

```text
Friendly Compact Stance <- StyleAssociatedWith <- 简约
```

调整只改变路径的展示顺序和箭头方向，不改变节点、关系及其图谱语义。反转后左侧为关键词召回命中的图谱节点，右侧为能够关联到的汽车风格或汽车车型，符合“从输入证据查看候选输出”的分析顺序。

该调整解决的是“路径展示倒置”问题。它证明某个召回节点能够通过哪些关系到达候选，并不表示知识图谱中只有这一条正确路径。

### 2.2 候选端点收敛验证

对 `benchmark_predict_reversed_paths.json` 中每条样本的全部路径进行统计：

- 路径以 `StyleAssociatedWith` 结束时，统计右侧不同汽车风格的数量；
- 路径以 `TypeAssociatedWith` 结束时，统计右侧不同汽车车型的数量；
- 同一样本、同一名称重复出现时只计 1 个候选。

统计结果如下：

| 指标 | 结果 |
|---|---:|
| Benchmark 样本数 | 100 |
| 风格端点为 0 个 | 25 |
| 风格端点为 1 个 | 1 |
| 风格端点为 2 个 | 74 |
| 风格端点超过 2 个 | 0 |
| 车型端点为 0 个 | 28 |
| 车型端点为 1 个 | 1 |
| 车型端点为 2 个 | 71 |
| 车型端点超过 2 个 | 0 |
| 风格、车型端点均不超过 2 个 | 100/100（100%） |

由此可见，召回节点对应的路径候选会集中在 1～2 个汽车风格和 1～2 个汽车车型，并未出现大量风格或车型端点同时发散的情况。端点为 0 表示该样本在相应维度没有形成可用候选，应理解为证据不足，而不是形成了更多候选。

该指标验证的是“候选数量是否收敛”，不直接等同于候选语义是否正确；候选正确性仍需结合人工标注或目标答案另行评估。

## 三、疑问二：Top-N Schema 统计与通过率

### 3.1 当前实现思路

当前代码按以下步骤计算：

1. 将每条具体路径抽象为节点类型和关系类型组成的 schema；
2. 将 schema 反转，使召回节点类型位于左侧，汽车风格或汽车车型位于右侧；
3. 只保留最终能够关联到“汽车风格”或“汽车车型”的 schema；
4. 统计每条 schema 被多少个 Benchmark 样本命中；
5. 按命中样本数从高到低排序，暂取前 25 条作为 Top-25 schema；
6. 对每个样本计算其 schema 集合与 Top-25 的交集；
7. 交集非空，且在Top-25 schema 中同时命中风格类和车型类，记为通过。

由于每个样本内部的 `hit_path_schemas` 已经去重，因此 schema 的 `count` 表示“覆盖了多少个样本”，而不是同一路径在单个样本中重复出现了多少次。

### 3.2 指标定义

设第 `i` 条样本的 schema 集合为 `S_i`，全局频次最高的前 `N` 条 schema 集合为 `TopN`：

```text
hit_count_i = |S_i ∩ TopN|
hit_style_count + 1 if s in |S_i ∩ TopN| and s.end == '汽车风格'
hit_type_count + 1 if s in |S_i ∩ TopN| and s.end == '汽车车型'
```

当前代码的单样本通过条件为：

```text
pass_count + 1 if 交集非空，且在Top-25 schema 中同时命中风格类和车型类
```

总体通过率为：

```text
pass_rate = 通过样本数 / Benchmark 总样本数
```

### 3.3 Top-25 结果

当前暂取 `N=25`，结果如下：

| 指标 |    结果 |
|---|--------:|
| Top-N |      25 |
| Benchmark 样本数 |     100 |
| 当前口径通过率 | **62%** |
| Top-25 中风格类 schema |      15 |
| Top-25 中车型类 schema |      10 |

作为补充观察，在 Top-25 中：

- 67 条样本命中至少 1 条风格类 schema；
- 62 条样本命中至少 1 条车型类 schema；
- 62 条样本同时命中风格类和车型类 schema。

当前代码使用“同时命中风格类和车型类即可通过”的定义，因此最终通过数是 62。

### 3.4 N 的暂定依据

不同 N 下的当前覆盖率如下：

| N | 通过样本数 | 通过率 |
|---:|---:|-------:|
| 3 | 63 |    51% |
| 5 | 66 |    51% |
| 10 | 66 |    59% |
| 15 | 66 |    60% |
| 20 | 66 |    61% |
| 25 | 67 |    62% |
| 30 | 68 |    62% |
| 50 | 70 |    64% |

Top-5 已覆盖 51% 的样本，继续扩大 N 后提升较缓。当前选择 Top-25 是在保留更多常见路径形态和控制 schema 数量之间的暂定折中；后续可结合业务可解释性或目标覆盖率再确定最终 N。


## 四、对应代码与数据

- 路径 schema 提取：`feature/statistic_path_schema/extract_path_info.py`
- 反转后的 schema 数据：`data/path_schemas_reversed.json`
- schema 频次排序：`feature/statistic_top_n_schema/sort_schema.py`
- Top-N 通过率计算：`feature/statistic_top_n_schema/calculatie_pass_rate.py`
- schema 排序结果：`data/sort_schemas.json`
- 当前通过率明细：`data/pass_rate.json`
- 反转后的预测路径：`feature/benchmark_recommendation/artifacts/benchmark_predict_reversed_paths.json`
