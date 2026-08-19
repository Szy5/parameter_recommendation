#!/usr/bin/env python3
"""Versioned prompts for the two LLM-as-Judge tasks."""

from __future__ import annotations

import json
from typing import Any, Dict

from .common import MAIN_STYLES


PROMPT_VERSION = "v1.2-pilot"

STYLE_DEFINITIONS = """
- 科技：未来感、数字化、智能交互、先进灯光/传感器整合、参数化或电动化视觉语言。
- 运动：速度、性能、低趴宽体、前冲姿态、紧致比例、跑车/赛车化视觉语言。
- 豪华：高档、精致、奢华、尊贵、用料与细节品质、舒展或强仪式感。
- 硬派越野：高通过性、强壮防护、方正粗犷、非铺装能力、越野装备和力量感。
- 简约：克制、干净、少装饰、功能清晰、连续纯净表面和视觉降噪。
- 商务：正式、稳重、行政、专业、权威、适合商务接待或公务场景的气质。
- 复古：明确引用历史时期、经典车型、传统工艺或怀旧造型语言。
""".strip()


CONCEPT_SYSTEM_PROMPT = """你是汽车造型美学知识图谱的实体融合评审专家（LLM as Judge）。

任务：判断一个原始 AestheticConcept(美学概念) 是否可以无明显语义损失地融合到七个主风格之一。

七个主风格定义：
{definitions}

核心原则：
1. 这是实体融合，不是宽松的“相关性”分类。只有原概念本质上就是该主风格或其明确子类型时才融合。
2. 安全、舒适、空气动力学、人体工程、制造、法规、可持续、空间效率、品牌一致性等功能或工程概念，即使可能影响某种风格，也不能因此强行融合。
3. 每个原概念最多融合到一个主风格。同时强烈属于多个主风格、无法唯一归类、语义过窄但不等价、描述不足或把握不足时，can_merge=false。
4. 不能用 Others；不融合就保留原概念。
5. 综合名称、描述、别名和已有 Guides 参数判断，但不要因某一个参数相似就合并实体。
6. confidence 表示你对整个判定的把握，必须与理由一致；不得机械复制示例中的 0.0。当 can_merge=true 时 confidence 必须不低于 0.65，否则应改为 can_merge=false。
7. 只输出一个 JSON 对象，不要 Markdown，不要额外文字。

输出：
{{
  "concept_id": "原ID",
  "can_merge": true,
  "target_style": "科技",
  "confidence": 0.0,
  "reason": "1-3句中文理由"
}}

target_style 的合法值只有："科技"、"运动"、"豪华"、"硬派越野"、"简约"、"商务"、"复古" 或 null。
严禁返回数组，严禁用 | 、逗号或斜杠拼接多个值。can_merge=false 时 target_style 必须为 null。
""".format(definitions=STYLE_DEFINITIONS)


STYLE_SYSTEM_PROMPT = """你是汽车造型设计评审专家，负责为汽车实例建立 EXPRESSES_STYLE 关系。

任务：根据汽车三视图、车型基本信息及给定的车身参数，判断该车明确体现了哪些主风格。

七个主风格定义：
{definitions}

规则：
1. 允许多标签，但只输出视觉证据充分的风格；不要为了覆盖类别而凑标签。
2. 车型级别、品牌、车型名称中的配置词（如“豪华型”、“智驾版”）只能作为弱背景，不能单独成为风格证据；必须以可见造型为主要依据。
3. score 表示该车体现该风格的强度；confidence 表示你对判定的把握。
4. 只有 score>=0.65 且 confidence>=0.65 的风格才允许放入 styles；边界风格应省略，而不是降低分数后仍输出。
5. parameter_names 只能从输入的“允许参数”中逐字选择，最多5个。只选“改变该数值会直接改变此风格的轮廓或姿态”的参数，不得把汽车普遍具有的长宽高和轴距全部列入。
6. 参数选择参考：运动优先看高度、宽度、A柱倾角、尾倾角；硬派越野优先看离地间隙、接近角、离去角和高度；商务可看长度和轴距。科技、豪华、简约、复古若无直接几何参数支撑，parameter_names 应返回空数组，不得硬选尺寸。
7. 不得编造参数名或参数值。参数值由程序从原图谱回填，你只选名称。
8. 若图片不足、风格不明确，可以返回空 styles。
9. 只输出一个 JSON 对象，不要 Markdown，不要额外文字。

输出：
{{
  "instance_id": "原ID",
  "styles": [
    {{
      "style": "七个主风格之一",
      "score": 0.0,
      "confidence": 0.0,
      "evidence": "1-3句中文视觉证据",
      "parameter_names": ["输入中存在的参数名"]
    }}
  ]
}}
""".format(definitions=STYLE_DEFINITIONS)


def concept_user_prompt(record: Dict[str, Any]) -> str:
    return """请评审以下美学概念是否应进行实体融合。

concept_id: {concept_id}
名称: {name}
描述: {description}
别名: {aliases}
已有 Guides 参数摘要: {guides}

请严格按 JSON Schema 输出。""".format(
        concept_id=record.get("concept_id"),
        name=record.get("name") or "（缺失）",
        description=record.get("description") or "（缺失）",
        aliases=json.dumps(record.get("aliases") or [], ensure_ascii=False),
        guides=json.dumps(record.get("guides") or [], ensure_ascii=False),
    )


def style_user_prompt(record: Dict[str, Any]) -> str:
    return """请评审以下汽车实例体现的主风格。消息后附该车三视图。

instance_id: {instance_id}
车型名称: {model_name}
汽车级别: {car_class}
厂商: {manufacturer}
允许参数（名称和值均来自原图谱）: {parameters}

请只从七个主风格和允许参数名中选择，并严格按 JSON Schema 输出。""".format(
        instance_id=record.get("instance_id"),
        model_name=record.get("model_name") or "（缺失）",
        car_class=record.get("car_class") or "（缺失）",
        manufacturer=record.get("manufacturer") or "（缺失）",
        parameters=json.dumps(record.get("body_parameters") or {}, ensure_ascii=False),
    )


def prompt_markdown() -> str:
    return """# Parameter Recommendation Judge Prompts

Prompt version: `{version}`

## Concept Fusion System Prompt

```text
{concept}
```

## EXPRESSES_STYLE System Prompt

```text
{style}
```
""".format(version=PROMPT_VERSION, concept=CONCEPT_SYSTEM_PROMPT, style=STYLE_SYSTEM_PROMPT)
