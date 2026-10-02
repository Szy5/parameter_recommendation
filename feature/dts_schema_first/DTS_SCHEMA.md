# DTS 知识图谱 Schema

## 1. Schema 总览

按照 `node / rel` 形式，可以将本图谱写成：

```text
node: [
  VehicleType: {
    name: "车型",
    des: "一张车型工作表对应一个车型节点，也是一个独立子图的根节点",
    属性: {
      id: "车型节点唯一标识",
      name: "车型名称"
    }
  },

  Part:<具体部件Label>: {
    name: "具体部件",
    des: "从 XLSX 原文抽取的具体汽车部件；具体部件名同时作为第二个 Neo4j Label",
    属性: {
      id: "部件节点唯一标识",
      name: "XLSX 中的具体部件名称"
      location: "部件在汽车中的具体位置，用于区分同名部件"
    }
  }
]

rel: [
  CONTAINS: {
    head: "VehicleType",
    tail: "Part:<具体部件Label>",
    des: "表示某车型包含某个具体部件",
    属性: {}
  },

  DTS_POSITION_RELATION: {
    head: "Part:<具体部件Label>",
    tail: "Part:<具体部件Label>",
    des: "表示两个具体部件之间由 PDF schema 允许的有向位置关系及其 DTS 要求",
    属性: {
      relative_position: "PDF 中的相对方位",
      Gap: "间隙要求",
      Flush: "面差要求",
      Ra: "A 侧圆角要求",
      Rb: "B 侧圆角要求",
      Alignment: "对齐度要求",
      Consistent: "一致性要求",
      Radii: "补充圆角要求",
      classification: "XLSX 中的内外分类",
      area: "XLSX 中该 DTS 要求所属区域"
    }
  }
]
```

图结构可以简写为：

```text
(:VehicleType)
      │
      └──[:CONTAINS]──> (:Part:具体部件Label)
                                  │
                                  ├──[:DTS_POSITION_RELATION {
                                  │      relative_position,
                                  │      Gap, Flush, Ra, Rb,
                                  │      Alignment, Consistent, Radii,
                                  │      classification, area
                                  │   }]──>
                                  │
                                  ▼
                           (:Part:具体部件Label)
```

## 2. 节点 Schema

当前实现只有两种基础节点 schema。四种车型是四个 `VehicleType` 实例，不是四种不同的节点类型；不同具体部件则通过第二个 Label 进行区分。

| 节点 | Neo4j Label | 属性                      | 唯一性                                           | 数据来源 |
| --- | --- |---------------------------|--------------------------------------------------| --- |
| 车型 | `VehicleType` | `id`, `name`              | 工作表编号唯一                                   | XLSX 工作表与代码中的车型映射 |
| 具体部件 | `Part` + 具体部件名 | `id`, `name` , `location` | 当前包按“工作表编号 + 具体部件名 + 所在位置”唯一 | LLM 从 XLSX 原文抽取 |

### 2.1 VehicleType

固定包含四个实例：

| 工作表 | `name` | 子图 |
| --- | --- | --- |
| `4` | `硬派越野车` | 子图 1 |
| `5` | `城市SUV` | 子图 2 |
| `7` | `豪华轿车` | 子图 3 |
| `9` | `豪华SUV` | 子图 4 |

四个车型之间不建立关系。同名部件也不会跨车型复用，因此最终形成四个互不相连的子图。

### 2.2 Part + 具体部件 Label

每个部件节点至少有 `Part` Label，同时把 XLSX 中抽取出的具体名称作为第二个 Label。例如：

```cypher
(:Part:发动机盖 {
  id: "DTS:part:4:...",
  name: "发动机盖",
  location: "..."
})

(:Part:散热器罩左饰板 {
  id: "DTS:part:4:...",
  name: "散热器罩左饰板",
  location: "..."
})
```

>PDF 中的“饰板”“饰条”等是用于约束关系的**类别词**；如果 XLSX 写的是“散热器罩左饰板”，最终节点使用具体名称“散热器罩左饰板”，不会退化成宽泛的“饰板”节点。


## 3. 关系 Schema

| 关系 | Head | Tail | 属性 | 方向 |
| --- | --- | --- | --- | --- |
| `CONTAINS` | `VehicleType` | `Part:<具体部件Label>` | 无 | 车型指向部件 |
| `DTS_POSITION_RELATION` | `Part:<具体部件Label>` | `Part:<具体部件Label>` | `relative_position`、七类 DTS 指标、`classification`、`area` | 与所选 PDF schema 边一致 |

### 3.1 CONTAINS

表示“该具体部件属于该车型”。例如：

```text
(:VehicleType {name: "硬派越野车"})
  -[:CONTAINS]->
(:Part:发动机盖 {name: "发动机盖"})
```

同一车型和同一部件之间只保留一条 `CONTAINS`。

### 3.2 DTS_POSITION_RELATION

表示两个具体部件之间的 DTS 位置关系。示例：

```text
(:Part:发动机盖 {name: "发动机盖"})
  -[:DTS_POSITION_RELATION {
      relative_position: "前侧",
      Gap: "3±1",
      Flush: "0±0.5",
      Ra: "R2",
      Rb: "R1",
      Alignment: "",
      Consistent: "",
      Radii: "",
      classification: "外部",
      area: "前部区域"
   }]->
(:Part:前保险杠 {name: "前保险杠"})
```

## 4. 属性说明

| 属性 | 所属 | 来源 | 说明 |
| --- | --- | --- | --- |
| `id` | 节点 | 程序生成 | Neo4j 唯一约束使用的技术标识 |
| `name` | 节点 | 车型映射或 XLSX 原文 | 车型或具体部件名称 |
| `relative_position` | DTS 关系 | PDF schema | 有向相对方位，不能由 LLM 自由创造 |
| `Gap` | DTS 关系 | XLSX | 两部件间隙要求 |
| `Flush` | DTS 关系 | XLSX | 两部件面差要求 |
| `Ra` | DTS 关系 | XLSX | 第一个部件一侧的圆角要求 |
| `Rb` | DTS 关系 | XLSX | 第二个部件一侧的圆角要求 |
| `Alignment` | DTS 关系 | XLSX | 对齐度要求 |
| `Consistent` | DTS 关系 | XLSX | 一致性要求；同一编号下连续值以换行保留 |
| `Radii` | DTS 关系 | XLSX | 补充圆角要求；按 Radii 段特殊列结构读取 |
| `classification` | DTS 关系 | XLSX | `外部` 或 `内部` |
| `area` | DTS 关系 | XLSX | DTS 要求所在区域，不等同于零件安装位置 |

七类 DTS 指标、`classification` 和 `area` 必须与输入记录完全一致。LLM 负责识别部件和选择 schema 边，但不能改写这些值。

## 5. PDF 方位约束

PDF 描述的形式是：

```text
起点部件类别 ──[相对方位]──> 终点部件类别
```

例如：

```text
发动机盖 ──[前侧]──> 饰板
```

如果 XLSX 原文是“机盖至散热器罩左饰板”，LLM 可以将其匹配到这条 PDF 类别边，但输出的具体节点仍是：

```text
发动机盖 ──[DTS_POSITION_RELATION {relative_position: "前侧"}]──> 散热器罩左饰板
```

允许的 `relative_position` 共 10 种：

| 方位值 | 含义 |
| --- | --- |
| `前侧` | PDF 中的前侧分支 |
| `后侧` | PDF 中的后侧分支 |
| `上侧` | PDF 中的上侧分支 |
| `下侧` | PDF 中的下侧分支 |
| `左侧` | PDF 中的左侧分支 |
| `右侧` | PDF 中的右侧分支 |
| `内含` | 起点部件包含终点部件 |
| `安装侧` | 后视镜等部件的安装侧关系 |
| `前侧/上侧` | PDF 合并表达的复合方位 |
| `后侧/下侧` | PDF 合并表达的复合方位 |

方位关系是有向的。程序不会因为存在 `A ──[前侧]──> B`，就自动推断 `B ──[后侧]──> A`。

## 6. PDF 类别词表

当前审核后的 PDF schema 含 12 个主要起点类别、61 个类别词和 152 条方位边，其中 150 条可供 LLM 自动选择。

12 个主要起点类别：

```text
前保险杠、发动机盖、前风挡、顶盖、后风挡、尾门/后备箱盖、
后保险杠、翼子板、侧围、前车门、后车门、后视镜
```

完整类别词表：

```text
B柱饰板、C柱饰板、侧围、储物盒、前保险杠、前拖车钩盖、前牌照安装板、
前角窗、前车灯、前车门、前门玻璃、前雾灯、前风挡、加油口门、发动机盖、
后保险杠、后拖车钩盖、后牌照安装板、后牌照灯、后视镜、后角窗、后车灯、
后车门、后门玻璃、后雾灯、后风挡、回复反射器、天窗、尾门/后备箱盖、
尾门把手、扰流板、摄像头、摄像头1、摄像头2、摄像头n、日间行车灯、
本体上壳、本体下壳、格栅1、格栅2、毫米波雷达、翼子板、装饰件、装饰件1、
装饰件2、装饰件n、超声波雷达、车门把手、转向灯、轮眉饰板、镜片、
镜臂上壳、镜臂下壳、顶盖、饰条、饰条1、饰条2、饰条n、饰板、高位制动灯、
鲨鱼鳍天线
```

其中 `饰条n`、`装饰件n`、`摄像头n` 是模板类别，只用于匹配 XLSX 中的具体名称，不创建字面为 `n` 的部件节点。PDF 中的“×无DTS定义”不产生关系。两条 PDF 原文中可疑的“后风挡 → 前风挡”边保留用于审计，但不允许自动选择。

逐条边的 `edge_id`、head 类别、tail 类别、方位和状态，以程序运行时生成的 `schema.json` 为准。

## 7. 抽取及待审边界

LLM 对每条 XLSX 编号记录只能执行两种操作：

1. 从 PDF 允许边中选择关系，输出具体 `part_a`、`part_b` 和相应方位；
2. 无法确定时返回空 `relations` 和原因。

代码随后校验：来源编号、内外分类、区域、七类指标、PDF `edge_id` 和方位是否一致。以下记录不会生成图谱关系，而是进入 `review.jsonl`：

- PDF 外形 schema 未覆盖的内部关系；
- PDF 中找不到相应类别边的外部记录；
- 同一部件对存在多个方位而 XLSX 无法消歧；
- LLM 漏项、重复、改变指标值或使用非法 `edge_id`；
- 无法确定具体部件的记录。

因此，`review.jsonl` 表示“当前 schema 和证据不足以安全入图”，不代表原始 DTS 数据无效。

