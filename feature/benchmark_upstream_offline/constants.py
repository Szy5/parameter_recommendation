"""Closed vocabularies and graph labels for the offline pipeline."""

FEATURE_LABELS = (
    "AestheticConcept(美学概念)",
    "DesignAttribute(设计属性)",
    "UserTrend(用户与趋势)",
    "VehiclePosture(汽车姿态)",
    "DesignParameter(设计参数)",
    "FamilyDNA(家族DNA)",
    "AerodynamicFeature(空气动力学特征)",
)

STYLES = ("科技", "运动", "豪华", "硬派越野", "简约", "商务", "复古")

CAR_TYPES = (
    "小型SUV", "紧凑型SUV", "中型SUV", "中大型SUV", "大型SUV",
    "紧凑型MPV", "中型MPV", "中大型MPV", "大型MPV",
    "微型车", "小型车", "紧凑型车", "中型车", "中大型车", "大型车",
    "两厢轿车", "三厢轿车", "跑车", "皮卡", "微面", "轻客",
)

ROUTE_BY_CAR_TYPE = {
    "小型SUV": "SUV", "紧凑型SUV": "SUV", "中型SUV": "SUV",
    "中大型SUV": "SUV", "大型SUV": "SUV",
    "紧凑型MPV": "MPV", "中型MPV": "MPV", "中大型MPV": "MPV", "大型MPV": "MPV",
    "微型车": "两厢轿车", "小型车": "两厢轿车", "两厢轿车": "两厢轿车",
    "紧凑型车": "三厢轿车", "中型车": "三厢轿车", "中大型车": "三厢轿车",
    "大型车": "三厢轿车", "三厢轿车": "三厢轿车",
    "跑车": "跑车", "皮卡": "皮卡", "微面": "微面", "轻客": "轻客",
}

STYLE_LABEL = "汽车风格"
CAR_TYPE_LABEL = "汽车车型"
CAR_LEVEL_LABEL = "汽车级别"
CAR_INSTANCE_LABEL = "汽车实例"
DESIGN_PARAMETER_LABEL = "DesignParameter(设计参数)"
GUIDES_LABEL = "Guides(指导)"
CONTAINS_LABEL = "包含"
EXPRESSES_STYLE_LABEL = "EXPRESSES_STYLE"

DIMENSION_FIELDS = (
    "长度(mm)", "宽度(mm)", "高度(mm)", "轴距(mm)",
    "离地间隙", "接近角(°)", "离去角(°)",
)

# USD per one million tokens, retrieved from official OpenAI model pages on 2026-08-17.
# Cached input is deducted from ordinary input before pricing when usage exposes it.
MODEL_PRICES_USD_PER_MILLION = {
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
}

