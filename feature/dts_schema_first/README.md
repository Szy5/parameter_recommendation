# DTS：先确定 PDF schema，再抽取 Excel

这个包对应你提出的顺序。旧的 `feature/DTS_extraction` 保留不动；这里不会让 LLM 先自由抽部件、再事后查一张方位表。

## 这份 schema 到底是什么

节点、关系、属性、零件身份和 PDF 方位规则的完整解释见 [SCHEMA.md](SCHEMA.md)；逐条可选方位边见 [schema.json](schema.json)。

外形 PDF 是一张有向思维导图：中心部件 → 方位 → 相邻/内含部件。`schema.py` 逐条列出了 12 个中心部件、152 条直接或嵌套的方位分支、61 个 PDF 部件类别和 10 种方位。运行 `--schema-only` 会先核对 PDF 的 SHA-256，再把完整清单输出为 `schema.json`。PDF 变化时程序会停止，要求重新人工核对，不会悄悄套用旧 schema。

PDF 写的“饰板”是类别，Excel 写的“散热器罩左饰板”是一个具体部件。两者不能混为一谈。LLM 的任务是从 Excel 原文找出具体名称，并选择一条 PDF 允许的类别关系。例如：

```text
PDF schema: 发动机盖 ──[前侧]──> 饰板       (关系 ID 固定)
Excel:       机盖至散热器罩左饰板          (具体部件来自原文)
图谱:        (:Part:发动机盖) ──[DTS_POSITION_RELATION {relative_position:"前侧", ...}]──> (:Part:散热器罩左饰板)
```

节点仍只用 `VehicleType` 和 `Part + 具体部件名` 两层 label。同名零件在不同车型、不同物理安装位置下是不同的 `Part` 节点；节点 ID 由车型、名称、安装位置一起确定。`Part.location` 和 `CONTAINS.location` 保存同一安装位置。部件到部件的 `DTS_POSITION_RELATION` 存 `relative_position`、`Gap`、`Flush`、`Ra`、`Rb`、`Alignment`、`Consistent`、`Radii`、`classification`（内外分类）、`area`（DTS 要求所在区域）。DTS 原值直接来自 Excel，不让 LLM 改写。关系不存技术 ID；只有相同实例端点和相同业务属性才合并。

从 v2 起，模型先选择 PDF 关系，再只对已选中的记录识别**两端具体部件各自的稳定安装位置**。这一步可用 `--selection-cache ./output/dts_schema_first/llm_cache.jsonl` 复用旧的关系选择结果，不重复请求已完成的 703 条关系选择；位置结果存到新输出目录的 `location_cache.jsonl`。表格的“前部区域”可能仅是测量区域，不直接等于某个零件的位置。例如“侧围”在前、侧、后区域的 DTS 要求中仍可能指同一个侧围。`L&R` 可能是左右两个同名部件，也可能是两个中央部件的左右测点；不能确定时进入待审，不合并或虚构节点。

PDF 中的 `×无DTS定义` 不产生边；`饰条n` 等是可匹配到具体 Excel 名称的模板，但不会建立字面为 `饰条n` 的节点。PDF 中 `后风挡→前风挡` 的可疑原文保留在 schema 并标为 `review_source`，不自动写入图谱。PDF 只有外形方位，内部关系与 PDF 未出现的类别关系都会进入待审文件；绝不让模型补造位置。

## 运行顺序

以下命令在仓库根目录执行。先使用含 `openpyxl` 的 Python 环境；只有上传时才需要 `neo4j` 包。模型配置和 Neo4j 配置从 `feature/.env` 读取，代码中不保存密钥。`schema-only` 不读 Excel、不调用付费模型、不连接数据库。

```bash
.venv/bin/python -m feature.dts_schema_first \
  --pdf '/Users/jiangzifeng/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_q8fwp9qkgzvf22_8380/msg/file/2026-09/DTS思维导图式逻辑图（外形）.pdf' \
  --out ./output/dts_schema_first_v2 \
  --schema-only
```

先打开 `./output/dts_schema_first_v2/schema.json`。每条 `edges` 都有 `id`、`source`（PDF 类别）、`target`（PDF 类别）、`relative_position` 和 `status`。确认这份清单后再跑 Excel：

```bash
.venv/bin/python -m feature.dts_schema_first \
  --pdf '/Users/jiangzifeng/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_q8fwp9qkgzvf22_8380/msg/file/2026-09/DTS思维导图式逻辑图（外形）.pdf' \
  --xlsx '/Users/jiangzifeng/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_q8fwp9qkgzvf22_8380/msg/file/2026-09/【20260519】DTS（无图版）.xlsx' \
  --out ./output/dts_schema_first_v2 \
  --env ./feature/.env \
  --selection-cache ./output/dts_schema_first/llm_cache.jsonl
```

上例复用已存在的旧关系选择缓存，并将新版文件写到**另一个目录**，以保留原 `graph.jsonl` 供上传前核对。若没有旧缓存，去掉 `--selection-cache` 即可。

第二条命令会读取四个车型工作表，但只把外部且有编号名称/指标的记录交给模型选择 PDF 关系；随后仅对选中关系的记录再次调用模型识别物理位置，可能产生费用。内部记录直接进入待审（这份 PDF 没有内饰方位）。当前文件共 1921 个编号记录，其中 717 个为外部、1204 个为内部，按现有字段有 703 个外部记录适合关系选择。默认**不会上传**。先看这些文件：

运行时终端会先显示可复用的关系选择缓存数量，再显示每批的开始、完成数、待审数和耗时。单条关系选择记录校验不通过会直接进入待审，不再把整批拆开重复请求模型；只有整批响应格式无法读取时最多重试一次。中途按 `Ctrl+C` 后，已完成的关系选择和物理位置缓存仍可复用；不要同时启动两个写入同一输出目录的抽取进程。关系选择阶段默认连待审错误也缓存，以免下次运行重复计费；确实需要重新请求这些待审项时，加 `--retry-review`。物理位置阶段只缓存通过校验的结果，失败项下次会重试。

- `schema.json`：模型调用之前生成的完整 PDF schema。
- `extractions.jsonl`：每个 Excel 编号选中了哪些 PDF 关系 ID、具体部件原文证据、七类原始指标。
- `graph.jsonl`：可以上传的车型节点、具体部件节点和关系。
- `locations.jsonl`：每条已选 PDF 关系对应的具体部件位置，及无法判定的原因。
- `location_cache.jsonl`：仅存已验证的物理实例位置；中断后可续跑。

如果已有 `graph.jsonl`、`report.json`、`locations.jsonl`，但仍有物理位置待审，可以**只上传已通过校验的记录**，不重新请求模型：

```bash
.venv/bin/python -m feature.dts_schema_first.upload_approved \
  --out ./output/dts_schema_first_v2 \
  --env ./feature/.env \
  --upload
```

此命令先校验图谱与报告的数量及节点位置，再写入 Neo4j；不会上传 `locations.jsonl` 中待审的记录。上传成功后，`report.json` 的 `upload_scope` 为 `approved_only`，并记录未上传的待审位置条数。重复运行采用 `MERGE`，不会产生属性完全相同的重复关系。如果数据库里仍有不属于这份图谱的旧 DTS 节点，命令会拒绝上传，不会自动清库。
- `review.jsonl`：PDF 没定义、方位不唯一、模型失败或原文证据不合格的记录及原因。
- `report.json`：接受/待审数量、关系数、缓存/API 次数、是否上传。
- `llm_cache.jsonl`：可复用的模型结果；缓存键绑定 PDF 指纹、schema 版本、模型和表格行。

需要重新检查单条记录时，先用 `source_id` 在 `extractions.jsonl` 和 `review.jsonl` 找到它，再在 `schema.json` 查其关系 ID。Excel 的章节标题不是物理区域；例如 `Radii` 的“区域”列实际是第一个部件，程序沿用已验证的工作簿读取器单独处理。连续的 `Consistent` 行按原始换行保留。

复核图谱和待审项后，才在第二条命令末尾加 `--upload`。若任一已选 PDF 关系的部件实例位置仍待审，程序拒绝上传，以免误把同名零件合并。普通 `--upload` 不会删除旧数据；检测到旧 DTS 节点却无法和新图完全对应时也会拒绝上传。

如果以前已把旧版“同名合并”图谱上传到 Neo4j，使用**不同的新输出目录**先生成 v2 文件；保留旧 `graph.jsonl` 作为备份。确认后再加：

```text
--upload --replace-old-graph ./output/dts_schema_first/graph.jsonl
```

替换前会核对数据库中的 DTS 节点 ID 与旧备份完全相同，并确认旧节点没有无关类型的关系。随后在**同一个数据库事务**中删除这批旧 DTS 节点并写入新图；任何检查不符都会停止，不清理其他数据。旧输出文件不会被删除。此前在对话中出现过 Neo4j 密码，实际上传前建议轮换。

## 验证

```bash
.venv/bin/python -m unittest test.test_dts_schema_first -v
```

这些测试用假模型和假数据库检查 schema 先行、四车型隔离、同名左右实例、具体部件 label、`Radii` 方向、待审、缓存和重复上传语义，不会调用付费模型或真实 Neo4j。
