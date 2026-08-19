# Parameter Recommendation Pipeline

当前实现是“融合美学标准 + 汽车图谱上下文 + 单次无图片 LLM Judge”的正式流程。一辆车可以命中多个风格，程序再把每个风格的全部 `Guides(指导) → DesignParameter` 名称写入关系。

完整的产物导航、每个文件用途、数据规模、重跑和 Neo4j 导入说明，请先阅读：

```text
feature_v2/artifacts/parameter_recommendation/README.md
```

完整技术方案与质量边界：

```text
feature_v2/parameter_recommendation/GRAPH_CONTEXT_STYLE_JUDGE_TECHNICAL_PLAN.md
```

正式最终图谱：

```text
feature_v2/artifacts/parameter_recommendation/03_final_delivery/
  07_unified_automobile_aesthetic_graph.jsonl
```

正式图谱中的 `EXPRESSES_STYLE` 只包含业务属性：

```text
style、score、confidence、parameters
```

`evidence`、`parameter_source`、`model`、`prompt_version` 只用于中间审计，不进入正式图谱。

运行自动化测试：

```bash
python3 -m unittest discover -s test -p 'test_parameter_recommendation.py'
```

## Neo4j 在线参数推荐

`neo4j_recommend.py` 从 `.env` 读取以下配置：

```text
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
NEO4J_DATABASE       # 可选
```

默认优先读取项目根目录的 `.env`；若不存在，则读取当前项目已有的
`feature_v2/.env`。同名系统环境变量的优先级高于文件。

三种推荐模式：

```bash
# 1. 汽车风格：AestheticConcept -[Guides]-> DesignParameter
python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --style 运动

# 2. 汽车级别：汽车级别 -> 汽车实例 -> 车身 -> 数值参数
python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --car-class 紧凑型SUV

# 3. 汽车风格 + 汽车级别
python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --style 运动 \
  --car-class 跑车 \
  --score-threshold 0.65 \
  --confidence-threshold 0.65
```

组合模式不是直接返回离线关系内容。`EXPRESSES_STYLE` 是离线生成的候选实例
筛选依据，而在线代码会继续读取命中实例当前的 `车身` 数值，聚合中位数、
实际范围和 P25-P75；同时读取该汽车级别的全量实例作为基线，输出
`class_baseline_median`、`median_delta_from_class` 和
`median_delta_percent`。这样既满足“风格 + 级别”的交集，又不会把关系中的
设计参数名称误当成汽车实测数值。

也可以在 Python 代码中复用服务层：

```python
from pathlib import Path

from feature_v2.parameter_recommendation.neo4j_recommend import (
    Neo4jConfig,
    Neo4jRecommendationRepository,
    RecommendationService,
)

repository = Neo4jRecommendationRepository.connect(
    Neo4jConfig.from_env(Path("feature_v2/.env"))
)
try:
    service = RecommendationService(repository)
    result = service.recommend(style="运动", car_class="跑车")
finally:
    repository.close()
```
