# 基于双图谱上下文的汽车风格 Judge 技术方案

## 1. 文档目的

本文档记录本轮汽车风格全量 Judge 的最终方案、数据来源、实现代码、运行命令、输出结构、验证结果和后续接入方式，供后续维护人员直接接手。

本轮采用：

- 单次 Judge，不做双次共识；
- 模型：`gpt-5-mini`；
- Prompt：`v2.0-graph-context-single-pass`；
- 不传汽车图片；
- LLM 只判断风格、分数、置信度和证据；
- `EXPRESSES_STYLE.parameters` 由程序从美学图谱读取，不由 LLM 生成；
- 全量汽车实例：800；
- 主批次并发：50；
- 失败任务按成功 ID 断点补跑。

## 2. 最终业务定义

### 2.1 风格判定标准

风格判定标准来自融合后的美学图谱：

```text
AestheticConcept(七个主风格)
  ─[Guides(指导)]→
DesignParameter(设计参数)
```

七个主风格为：

```text
科技、运动、豪华、硬派越野、简约、商务、复古
```

对每条有效 `Guides`，程序提取：

- `DesignParameter.name`；
- 范围；
- 单位；
- 描述；
- 所有源 `Guides` 指导内容。

只有尾节点标签为 `DesignParameter(设计参数)` 的关系进入风格标准。指向 `AerodynamicFeature` 等其他标签的 `Guides` 不写入 `EXPRESSES_STYLE.parameters`。

### 2.2 汽车证据

汽车证据来自汽车图谱：

```text
汽车实例 ─[包含]→ 关键尾节点
```

纳入的尾节点为：

```text
车身、车轮制动、底盘转向、变速箱、外观/防盗、车外灯光、
外后视镜、驾驶操控、四驱/越野、发动机、电动机、电池/充电、
屏幕/系统、驾驶硬件、驾驶功能、智能化配置、方向盘/内后视镜、
座椅配置、音响/车内灯光、天窗/玻璃、空调/冰箱、特色配置、选装包
```

不传 `image_urls`，不构造任何图片消息块。

### 2.3 LLM 的职责

LLM 只输出：

```json
{
  "styles": [
    {
      "style": "科技",
      "score": 0.86,
      "confidence": 0.90,
      "evidence": "汽车实例-包含-屏幕/系统中的中控屏幕尺寸=15.4英寸，与科技风格的 Infotainment/Central Screen Size 相匹配。"
    }
  ]
}
```

LLM 不得输出：

- `parameters`；
- `parameter_names`；
- 汽车具体参数值列表；
- 七个主风格以外的标签。

### 2.4 程序的职责

LLM 判定风格后，程序按风格查询固定标准，将该风格所有 `DesignParameter.name` 写入最终结果：

```json
{
  "style": "科技",
  "score": 0.86,
  "confidence": 0.90,
  "evidence": "……",
  "parameters": [
    "Infotainment/Central Screen Size",
    "Sensor integration packaging",
    "SoftwareUpgradabilityLevel"
  ],
  "parameter_source": "AestheticConcept-[Guides]->DesignParameter"
}
```

同一个风格的 `parameters` 对所有汽车相同；汽车之间不同的是 `score`、`confidence` 和 `evidence`。

## 3. 数据来源

### 3.1 美学图谱

使用单次全量概念 Judge 后的融合图：

```text
feature_v2/artifacts/parameter_recommendation/01_concept_fusion/
  02_meixue_fused_single_pass.jsonl
```

概念覆盖为 `1342/1342`，置信度门槛为 `0.80`。

### 3.2 汽车图谱恢复来源

工作区根目录的 `cars2.json` 已在最后一条记录中截断：

```text
可解析完整记录：110552
下一条记录：JSON 字符串未结束
```

之前生成 Pilot 融合预览时，完整汽车图谱已作为带 `car_` 前缀的记录写入。整理 artifacts 时已将这部分正式依赖提取为独立源文件，旧 Pilot 目录不再保留：

```text
feature_v2/artifacts/parameter_recommendation/00_sources/
  automobile_graph_complete.jsonl
```

本轮从该文件提取汽车子图：

```text
汽车图谱记录：124230
汽车实例：800
汽车级别 ─[包含]→ 汽车实例：800
汽车实例 ─[包含]→ 车身：800
汽车实例 ─[包含]→ 车轮制动：800
汽车实例 ─[包含]→ 底盘转向：800
```

后续如从原电脑取得完整 `cars2.json`，应重新执行上下文抽取并对比结果；不要继续把当前截断文件当作完整源图。

## 4. 实现文件

### 4.1 上下文抽取

```text
feature_v2/parameter_recommendation/extract_context_style_inputs.py
```

功能：

- 提取七种风格的固定判定标准；
- 提取800辆汽车的图谱上下文；
- 删除图片字段；
- 清理空值；
- 统计尾节点覆盖和上下文长度。

### 4.2 Judge 运行器

```text
feature_v2/parameter_recommendation/run_context_style_judge.py
```

功能：

- 将固定风格标准放入 System Prompt；
- 每辆汽车上下文放入 User Prompt；
- 不发送图片；
- 并发调用；
- 结构与业务门禁；
- 保存原始响应、usage、Prompt hash、调用历史；
- 按成功 `instance_id` 断点续跑。

门禁包括：

- `style` 必须属于七个主风格；
- `score >= 0.65`；
- `confidence >= 0.65`；
- `evidence` 至少引用一个输入尾节点标签；
- `evidence` 至少逐字引用一个目标风格自己的 `DesignParameter.name`；
- LLM 输出中不得出现 `parameters` 或 `parameter_names`。

### 4.3 参数名称补齐

```text
feature_v2/parameter_recommendation/enrich_style_parameters.py
```

功能：根据目标风格，将固定标准中的全部 `DesignParameter.name` 写入 `parameters`。

### 4.4 关系文件生成

```text
feature_v2/parameter_recommendation/build_expresses_style_edges.py
```

功能：生成可与两张图谱合并的 `EXPRESSES_STYLE` JSONL 关系；`parameters` 保存为 DesignParameter 名称数组的 JSON 字符串。

## 5. 可复现运行命令

以下命令从仓库根目录执行。

### 5.1 抽取标准和汽车上下文

```bash
python3 feature_v2/parameter_recommendation/extract_context_style_inputs.py \
  --aesthetic-graph feature_v2/artifacts/parameter_recommendation/01_concept_fusion/02_meixue_fused_single_pass.jsonl \
  --car-graph feature_v2/artifacts/parameter_recommendation/00_sources/automobile_graph_complete.jsonl \
  --criteria-output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/01_style_criteria.json \
  --vehicle-output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/02_vehicle_context_all.jsonl \
  --report feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/02_extraction_report.json
```

### 5.2 小样本门禁

必须写入单独 Pilot 输出，避免污染正式结果：

```bash
python3 -u feature_v2/parameter_recommendation/run_context_style_judge.py \
  --criteria feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/01_style_criteria.json \
  --input feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/02_vehicle_context_all.jsonl \
  --output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/PILOT_OUTPUT.jsonl \
  --meta feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/PILOT_META.json \
  --env feature_v2/.env --model gpt-5-mini \
  --workers 3 --limit 3 --temperature 0.1 --max-retries 2 --timeout 240
```

### 5.3 全量单次 Judge

```bash
python3 -u feature_v2/parameter_recommendation/run_context_style_judge.py \
  --criteria feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/01_style_criteria.json \
  --input feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/02_vehicle_context_all.jsonl \
  --output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/03_style_judge_single_pass.jsonl \
  --meta feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/03_style_judge_single_pass_meta.json \
  --env feature_v2/.env --model gpt-5-mini \
  --workers 50 --temperature 0.1 --max-retries 2 --timeout 240
```

重复执行同一命令时，运行器只处理没有成功结果的 `instance_id`。

### 5.4 补齐图谱参数名称

```bash
python3 feature_v2/parameter_recommendation/enrich_style_parameters.py \
  --criteria feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/01_style_criteria.json \
  --judge-output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/03_style_judge_single_pass.jsonl \
  --output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/04_style_judge_with_graph_parameters.jsonl \
  --report feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/04_style_judge_with_graph_parameters_report.json \
  --expected-instances 800
```

### 5.5 生成 `EXPRESSES_STYLE`

```bash
python3 feature_v2/parameter_recommendation/build_expresses_style_edges.py \
  --results feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/04_style_judge_with_graph_parameters.jsonl \
  --car-graph feature_v2/artifacts/parameter_recommendation/00_sources/automobile_graph_complete.jsonl \
  --aesthetic-graph feature_v2/artifacts/parameter_recommendation/01_concept_fusion/02_meixue_fused_single_pass.jsonl \
  --output feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/05_expresses_style_relationships.jsonl \
  --report feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/05_expresses_style_relationships_report.json \
  --expected-instances 800
```

## 6. 本轮交付产物

目录：

```text
feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/
```

文件：

```text
01_style_criteria.json
02_vehicle_context_all.jsonl
02_extraction_report.json
03_style_judge_single_pass.jsonl
03_style_judge_single_pass_meta.json
03_style_judge_run_history.json
04_style_judge_with_graph_parameters.jsonl
04_style_judge_with_graph_parameters_report.json
05_expresses_style_relationships.jsonl
05_expresses_style_relationships_report.json
06_validation_report.json
prompt_version_graph_context.md
```

`03_style_judge_single_pass.jsonl` 保留8条主批次 HTTP 503 失败审计行以及后续8条成功补跑行，所以物理行数为808；读取时以每个 `instance_id` 的 `status=ok` 结果为准，唯一成功结果为800。

断点补跑会覆盖单一 `meta` 文件，因此 `03_style_judge_run_history.json` 额外保存主批次与补跑两次运行记录；综合质量和用量以 `06_validation_report.json` 为准。

## 7. 实际全量结果

### 7.1 调用和覆盖

```text
汽车实例：800
成功实例：800
唯一成功覆盖：100%
主批次并发：50
主批次耗时：558.675秒
补跑：8条，8/8成功
```

成功结果 Token：

```text
输入 Token：21,820,890
输出 Token：2,738,777
总 Token：24,559,667
平均每辆：30,699.6
```

### 7.2 风格分布

```text
科技：772
运动：374
豪华：472
硬派越野：211
简约：8
商务：28
复古：0
```

每辆汽车平均 `2.331` 个风格，中位数为2，最少1，最多4。最终生成1,865条 `EXPRESSES_STYLE`。

### 7.3 参数名称数量

只统计 `Guides → DesignParameter`：

```text
科技：48
运动：95
豪华：43
硬派越野：29
简约：21
商务：7
复古：39
```

## 8. 验证结果

已验证：

- 800个成功实例 ID 唯一且完整；
- LLM 原始规范化结果不含 `parameters`；
- 每条 evidence 引用输入尾节点；
- 每条 evidence 引用目标风格自身的 DesignParameter；
- 程序补齐参数与固定风格标准逐项完全相同；
- 参数列表不含汽车实例具体值；
- 1,865条关系的 `parameters` 均为合法 JSON 名称数组；
- 关系端点缺失为0；
- 汽车上下文没有 `image_urls`；
- Prompt 没有图片块；
- 新产物未发现 API Key。

自动化测试：

```bash
python3 -m unittest discover -s test -p 'test_parameter_recommendation.py'
```

## 9. 结果解释与质量边界

### 9.1 不应把分布当作准确率

本轮没有人工 gold set。程序门禁证明结果结构正确、证据可追溯、参数来源正确，但不能证明风格语义准确率。

### 9.2 科技风格覆盖率很高

科技为 `772/800`。原因是融合美学图明确把以下内容定义为科技风格参数：

```text
屏幕尺寸、ADAS、传感器集成、语音控制、OTA、软件升级、车联网等
```

这些配置在当前800辆现代汽车中十分普遍。若业务认为“科技风格”必须比普通现代配置更强，需要：

- 提高科技风格的 `score/confidence` 写图门槛；
- 要求更高规格证据，例如大屏、高算力、多雷达、高等级辅助驾驶的组合；
- 人工清理科技风格的 Guides 参数集合；
- 建立人工 gold set 后校准阈值。

### 9.3 复古为0

结构化汽车图谱缺少复古风格所需的灯具造型、曲面、装饰、轮廓历史语言等明确字段。不传图片的前提下，返回0比根据车型品牌或名称猜测更符合证据原则。

### 9.4 汽车源字段质量

部分车身字段可能存在业务口径问题，例如 `离地间隙` 与 `空载最小离地间隙(mm)` 同时存在且值可能不一致。当前 Judge 使用图谱原值，没有自行修正。正式业务上线前应建立字段可信度和优先级规则。

## 10. 统一图谱与 Neo4j 接入

Judge 原始关系文件 `05_expresses_style_relationships.jsonl` 保留了审计字段；正式融合时运行：

```bash
python3 feature_v2/parameter_recommendation/build_unified_graph.py \
  --aesthetic-graph feature_v2/artifacts/parameter_recommendation/01_concept_fusion/02_meixue_fused_single_pass.jsonl \
  --car-graph feature_v2/artifacts/parameter_recommendation/00_sources/automobile_graph_complete.jsonl \
  --style-relationships feature_v2/artifacts/parameter_recommendation/02_vehicle_style_judge/05_expresses_style_relationships.jsonl \
  --output feature_v2/artifacts/parameter_recommendation/03_final_delivery/07_unified_automobile_aesthetic_graph.jsonl \
  --report feature_v2/artifacts/parameter_recommendation/03_final_delivery/07_unified_automobile_aesthetic_graph_report.json
```

融合后的 `EXPRESSES_STYLE` 只保留下游需要的业务属性：

```json
{
  "type": "relationship",
  "label": "EXPRESSES_STYLE",
  "properties": {
    "style": "科技",
    "score": 0.86,
    "confidence": 0.90,
    "parameters": "[\"Infotainment/Central Screen Size\",\"Sensor integration packaging\"]"
  }
}
```

以下 Judge 过程属性明确不进入正式图谱：

```text
evidence、parameter_source、model、prompt_version
```

统一图谱的实际规模为：

```text
总记录：148,620
节点：28,878
关系：119,742
EXPRESSES_STYLE：1,865
缺失端点：0
重复节点 ID：0
重复关系 ID：0
```

使用 `.env` 导入 Neo4j：

```bash
python3 feature_v2/parameter_recommendation/import_jsonl_to_neo4j.py \
  --graph feature_v2/artifacts/parameter_recommendation/03_final_delivery/07_unified_automobile_aesthetic_graph.jsonl \
  --env feature_v2/.env \
  --report feature_v2/artifacts/parameter_recommendation/03_final_delivery/08_neo4j_import_report.json \
  --batch-size 1000
```

导入器通过 `_graph_id` 和每类节点的唯一约束执行幂等 `MERGE`；重复运行同一份图谱不会重复创建节点或关系。`_graph_id` 是 Neo4j 内部导入标识，不属于 `EXPRESSES_STYLE` 的业务属性。

本次实际导入后的数据库校验结果：

```text
节点：28,878
关系：119,742
EXPRESSES_STYLE：1,865
涉及汽车实例：800
EXPRESSES_STYLE 禁用字段关系数：0
缺少 _graph_id 的节点数：0
```

在线查询 `parameters` 时先解析 JSON 字符串。其含义是“该风格受哪些设计参数指导”，不是汽车实例具体数值，因此不得再据此计算汽车数值中位数和范围。

如果仍需汽车级别数值统计，应独立走：

```text
汽车级别 ─[包含]→ 汽车实例 ─[包含]→ 车身
```

## 11. 后续继续工作的建议顺序

1. 从原电脑重新取得完整 `cars2.json`，与本轮恢复汽车子图对比。
2. 抽取人工 gold set，至少覆盖七种风格、负样本和边界样本。
3. 优先复核科技、豪华、硬派越野的高覆盖率。
4. 确认正式写图门槛；当前 Judge 输出门槛是 `score/confidence >= 0.65`。
5. 统一图谱和 Neo4j 已完成；若上游数据变化，重新运行第10节的构建与幂等导入命令。
6. 修改在线“风格+级别”推荐逻辑：返回设计参数名称和 Guides，而不是从关系上计算汽车数值中位数。
7. 美学概念、Guides 或汽车关键属性变化后，重新抽取标准和汽车上下文，再运行受影响实例。
