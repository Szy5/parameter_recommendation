# DTS 表格 → 知识图谱

这个包处理你提供的 DTS Excel 和“外形”思维导图 PDF。它读取工作表 `4`、`5`、`7`、`9`，让 LLM 从每个编号名称中识别两个部件，再根据 PDF 确定它们的相对位置。结果是四个互不相连的车型子图：硬派越野车、城市 SUV、豪华轿车、豪华 SUV。

先记住两个开关：**不加 `--upload` 会调用 LLM 并生成本地文件，但不会写 Neo4j**；加上 `--upload` 才会把 PDF 可确认的位置关系写入 Neo4j。LLM 调用可能产生费用，默认运行并非“零成本预览”。

## 1. 准备环境

在仓库根目录运行命令，使用项目的 Python 环境。至少需要下面两个依赖（仓库的 `requirements.txt` 也已包含它们）：

```bash
python -m pip install 'openpyxl>=3.1,<4' 'neo4j>=5'
```

默认读取 `feature/.env`。确认文件里有以下配置；这里的值只是格式示例，不要把真实密钥或密码写进 README、代码或 Git：

```dotenv
API_KEY=你的模型密钥
BASE_URL=你的模型接口地址
MODEL_NAME=仓库配置的模型名
NEO4J_URI=neo4j+s://你的实例.databases.neo4j.io
NEO4J_USERNAME=你的用户名
NEO4J_PASSWORD=你的新密码
NEO4J_DATABASE=你的数据库名
```

现有项目已使用 `feature/.env`。如果配置在别处，运行时加 `--env '/绝对路径/配置文件'`。此前在对话中发出的 Neo4j 密码建议先轮换。

## 2. 先抽取并检查，不上传

下面是这次提供的两份附件在本机的路径，可以直接复制运行；`--out` 是本次结果目录，可以自行命名。附件位于微信文件目录，若微信移动或清理了文件，需要把 `--xlsx`、`--pdf` 换成新的**绝对路径**。

```bash
python -m feature.DTS_extraction \
  --xlsx '/Users/jiangzifeng/PycharmProjects/parameter_recommendation/【20260519】DTS（无图版）.xlsx' \
  --pdf '/Users/jiangzifeng/PycharmProjects/parameter_recommendation/DTS思维导图式逻辑图（外形）.pdf' \
  --out './outputs/dts_extraction/first_run'
```

处理顺序是：

```text
Excel 按编号整理 Gap/Flush/Ra/Rb/Alignment/Consistent/Radii
  → LLM 识别“部件 A 至部件 B”
  → PDF 方位表核对 A → B 的位置
  → 可确认的关系写入 graph.jsonl；其余写入 review.jsonl
```

工作簿里约有 1,900 个编号，第一次运行会分批调用模型，可能需要一段时间。程序逐批写入 `llm_cache.jsonl`；中途中断后使用**相同的 `--out` 目录**重跑，已成功抽取且输入未变的记录会从缓存读取。`--batch-size` 默认 15，可设置为 1–40，例如 `--batch-size 10`。

## 3. 看懂输出，决定是否上传

运行结束后，先看 `--out` 目录中的这些文件：

| 文件 | 用途 | 重点看什么 |
| --- | --- | --- |
| `report.json` | 本次汇总 | `matched_source_records`、`review_records`、`llm.failed_batches`、`uploaded` |
| `extractions.jsonl` | 每个源编号一行的抽取记录 | 原编号名称 `raw_name` 对应的 `part_a`、`part_b` 是否正确 |
| `review.jsonl` | 未能形成位置关系的记录 | `reason` 是部件无法识别、PDF 无对应项，还是同一部件对有多个方位 |
| `graph.jsonl` | 待上传的节点与关系 | `VehicleType`、`Part`、`CONTAINS`、`DTS_POSITION_RELATION` 是否符合预期 |
| `schema.json` | 本次实际使用的 schema | `concrete_part_labels` 和 `observed_position_patterns` |
| `llm_cache.jsonl` | 成功抽取的模型缓存 | 通常不必手工修改；重跑时会自动使用 |

`review_records` 大于零是预期情况：给出的 PDF **只画了外形部件**，没有为所有内饰和所有部件对定义位置。同一部件对若同时出现“左侧”和“右侧”，程序也不会擅自选一个。已识别的部件仍可出现在图谱节点和 `CONTAINS` 中，但没有可靠方位时，不会生成对应的 `DTS_POSITION_RELATION`。

`report.json` 中 `llm.failed_batches > 0` 表示有记录在模型重试后仍抽取失败。先检查 `review.jsonl`，修复模型配置或输入问题后用同一命令重跑；存在这类失败时，程序会拒绝 `--upload`。

## 4. 确认后上传 Neo4j

检查结果后，**把上一步完全相同的命令再运行一次，只在末尾加 `--upload`**。例如：

```bash
python -m feature.DTS_extraction \
  --xlsx '/Users/jiangzifeng/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_q8fwp9qkgzvf22_8380/msg/file/2026-09/【20260519】DTS（无图版）.xlsx' \
  --pdf '/Users/jiangzifeng/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_q8fwp9qkgzvf22_8380/msg/file/2026-09/DTS思维导图式逻辑图（外形）.pdf' \
  --out './outputs/dts_extraction/first_run' \
  --upload
```

此时程序仍会先重建并检查结果，再连接 `feature/.env` 中的 Neo4j。成功后 `report.json` 的 `uploaded` 为 `true`。可在 Neo4j Browser 中查看各车型的部件数：

```cypher
MATCH (v:VehicleType)-[:CONTAINS]->(p:Part)
WHERE v.id STARTS WITH 'DTS:vehicle:'
RETURN v.name AS 车型, count(DISTINCT p) AS 部件数
ORDER BY 车型;
```

## 图谱究竟存什么

```text
(VehicleType {id, name})
    -[:CONTAINS {classification, area}]->
(Part:具体部件名 {id, name})
    -[:DTS_POSITION_RELATION {
         relative_position, Gap, Flush, Ra, Rb,
         Alignment, Consistent, Radii, classification, area
       }]->
(Part:另一个具体部件名 {id, name})
```

部件 ID 包含车型作用域，所以“硬派越野车的发动机盖”和“城市 SUV 的发动机盖”是不同节点，不会把四个子图接到一起。DTS 定义保留 Excel 原文，例如 `3.3±1//1`，不擅自拆成数值和公差；没有该指标或源表未提供物理区域时使用空字符串。

`Alignment`、`Consistent`、`Radii` 是独立编号，不会硬并入同一部件对的 Gap 记录。一个编号下连续出现的多条 `Consistent` 定义按原顺序保存在同一属性中。`Radii` 段的 B 列是主体部件，例如“翼子板”加“与发动机盖配合”会抽成 `翼子板 → 发动机盖`，不能把“翼子板”误记为区域。

关系不存 `relation_id`。相同端点且全部业务属性相同的关系在重复上传时由 Neo4j `MERGE` 合并；如果以后修改了 Excel 中某项 DTS，旧关系**不会自动删除**，需要人工核对。PDF 方位表在 `pdf_topology.json`：它是有向的，不会自行把 `A → B` 的方位反推成 `B → A`。如果 PDF 内容被修改，程序会停止，直到重新核对并更新方位表。
