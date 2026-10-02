# DTS 全表 LLM 抽取

这个包实现的是：**先确定 PDF schema，再让 LLM 根据 schema 抽取整份 XLSX**。
它与旧的 `feature.dts_schema_first` 抽取流程相互独立；只复用已经审核过的 PDF schema、工作簿读取器和 Neo4j 写入器。

节点、关系、属性、PDF 方位约束和数据实例之间的区别，见 [SCHEMA.md](../dts_schema_first/DTS_SCHEMA.md)。

## “1921 条”是什么意思

工作簿有四个车型工作表。程序把每个有“编号”的项目作为一条源记录，编号下面连续的 Gap、Flush 等行归到同一条记录。总计 **1921 条源记录**，其中外部 717 条、内部 1204 条。它们不是 1921 个部件，也不是 1921 条最终图谱边。

**全部 1921 条都会送给 LLM**。`--batch-size` 只控制一次请求装多少条，默认 12 条；模型每批都能看到同一份 PDF schema。PDF 只有外形关系，所以内部记录通常应返回空 `relations` 并进入待审文件，而不是编造方位。这会产生较多模型请求和费用。

## 运行

在仓库根目录，先配置 `feature/.env` 中的 `API_KEY`、`BASE_URL`、`MODEL_NAME`。不需要把 Neo4j 密码写入代码。

```bash
DTS_PDF='/Users/jiangzifeng/PycharmProjects/parameter_recommendation/DTS思维导图式逻辑图（外形）.pdf'
DTS_XLSX='/Users/jiangzifeng/PycharmProjects/parameter_recommendation/【20260519】DTS（无图版）.xlsx'

.venv/bin/python -m feature.dts_llm_fulltable_my \
  --pdf "$DTS_PDF" \
  --xlsx "$DTS_XLSX" \
  --out ./output/dts_llm_fulltable_my_test \
  --env ./feature/.env
```

终端会打印缓存数和当前批次。中断后用相同命令重跑：已通过校验的记录存在 `llm_cache.jsonl`，不会再次请求。不要把旧流程的输出目录当作本包的 `--out`。

缓存键同时绑定模型名称、PDF 指纹、PDF 部件类型清单和当前输入记录。任一内容变化时只会重新请求受影响的记录；类型不在 PDF 清单中、遗漏或重复 `source_id` 的结果不会写入缓存。每批校验完成后立即追加缓存，因此运行中断后，已经完成的批次仍可复用。源记录使用工作表与行号组成的唯一 `source_id`（如 `4:319`），避免相同业务编号在不同章节重复时串用缓存。

跑完先检查：

- `schema.json`：固定的 PDF 部件关系与方位。
- `extractions.jsonl`：每条源记录的模型输出或校验错误。
- `graph.jsonl`：可用节点与关系。
- `review.jsonl`：模型未匹配或未通过基本校验的源记录。
- `report.json`：数量统计。

默认**不上传**。确认 `graph.jsonl` 和 `review.jsonl` 后，才给同一命令增加 `--upload`。`--upload` 会使用 `feature/.env` 中的 `NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 和可选的 `NEO4J_DATABASE`。如需少量试跑，可把 `--batch-size` 改小，但这不会减少总记录数。

## 模型要做什么

每批请求包含原始编号、名称、分类、区域、Radii 特殊字段和七类 DTS 指标，以及固定的 PDF 关系清单。模型按指定 JSON 格式为每条记录返回具体部件、PDF 关系编号、方位和指标。程序只做格式、来源编号、指标原值和 schema 成员的基本检查，然后生成四个按车型隔离的子图。不能匹配的记录保留在 `review.jsonl`，不上传。

PDF schema 绑定到已审核 PDF 的 SHA-256；换一份 PDF 时程序会停止，需要先重新审核关系表。旧流程生成的缓存与新包不通用。

## 上传扁平 JSONL

`upload_jsonl.py` 可上传本包生成的扁平 JSONL：节点使用 `id/labels/properties`，关系使用 `label/start_id/end_id/properties`。它不要求 `report.json` 或 `locations.jsonl`，也不会修改源文件。

先只校验文件，不连接 Neo4j：

```bash
.venv/bin/python -m feature.dts_llm_fulltable_my.upload_jsonl \
  --graph ./output/dts_llm_fulltable_my/graph.jsonl
```

校验通过后上传并保存报告：

```bash
.venv/bin/python -m feature.dts_llm_fulltable_my.upload_jsonl \
  --graph ./output/dts_llm_fulltable_my/graph.jsonl \
  --env ./feature/.env \
  --report ./output/dts_llm_fulltable_my/upload_report.json \
  --upload
```

Neo4j 属性不支持嵌套对象。脚本只在上传时把嵌套对象或复杂数组序列化成 JSON 字符串；例如当前图中的 `relative_position` 对象会作为 JSON 字符串保存，原 `graph.jsonl` 保持不变。节点按输入 `id` 合并；没有显式 `id` 的关系根据关系类型、端点和原始属性生成稳定 ID，所以同一文件重复上传不会产生重复关系。
