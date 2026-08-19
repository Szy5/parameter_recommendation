# BUPT项目：汽车参数推荐需求梳理

## **1\. 需求目标**

基于现有汽车美学知识图谱和汽车参数知识图谱，支撑以下三类参数推荐：

1. 输入汽车风格，推荐与该风格相关的设计参数及参考范围。

2. 输入汽车级别，推荐该级别汽车的典型参数。

3. 输入汽车风格和汽车级别，推荐同时满足两项条件的汽车参数。

## **2\. 总体方案**

方案保持现有图谱的节点和关系名称不变：

```Plain Text
AestheticConcept(美学概念)
  ─[Guides(指导)]→ DesignParameter(设计参数)

汽车级别
  ─[包含]→ 汽车实例
  ─[包含]→ 车身
```

美学概念融合、消歧\(未被处理的美学概念节点保留不变\)后，把这些节点标签更改为 `汽车风格`，将节点内容整理为需要的主风格和其他风格，例如：

```Plain Text
科技、运动、豪华、硬派越野、简约、商务、复古、......
```

**两张图谱之间仅新增一种关系：**

```Plain Text
汽车实例 ─[EXPRESSES_STYLE]→ 汽车风格
```

## **3\. 不同类型的参数推荐**

### **3\.1 输入汽车风格**

**示例输入：**

```Plain Text
**科技 运动 豪华 简约 商务**

**请推荐 运动风格 相关的参数？**
```

**推理路径：**

```Plain Text
汽车风格(风格类型:豪华) ─[Guides(指导)]→ DesignParameter(设计参数)
```

**Cypher:**

```JSON


MATCH p=(s:汽车风格)-[:`Guides(指导)`]->(:`DesignParameter(设计参数)`)
WHERE s.name CONTAINS '豪华'
RETURN p LIMIT 250; 
```



**输出示例：**

*在设计参数节点当中会有这个设计参数是如何影响汽车风格的，以及这个设计参数的推荐范围，设计参数的单位等细粒度的信息\.*

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MTM1Y2QzYzU4ZjlkMDNmMmFjZWY5ZmVkMDgyMmViZjNfOWQ0Y2ZlYzA4OTJjMDBiZjQ0NGFiZTcxY2Q0NGJkOTVfSUQ6NzY3MDUyMzcwNjg0NTg5MTc4M18xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=ZjU2MTRlMjQxNzE4Yjg5OWUyNjFkNDBhZWU5MTQ4NGJfODM3MWM5MjgyOTFkZDkyMGZjNTNjNGI1MzE5MjRhYzZfSUQ6NzY3MDUyMjgwNDc2ODYwNzIwNF8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=NTBhYzNhMWQ5YTczMTc2NjQ0OWE2N2VmNTE3N2RhNjZfMTc5YWU5NzUzNTM0ZmYzNTM2ZWE0ZGJlNTBlOGEyZTlfSUQ6NzY3MDUyMjc1NTQzOTMxNTkzNl8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

### **4\.2 输入汽车级别**

**示例输入：**

```Plain Text
**请推荐紧凑型SUV相关的****汽车参数****？**
```

**推理路径：**

```Plain Text
汽车级别：跑车 ─[包含]→ 汽车实例(已经和车身节点进行融合)
```

**Cypher：**

```JSON
MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)
where n.name CONTAINS 'SUV'
RETURN p LIMIT 250;
```

**输出示例：**

*当要推荐某个汽车车型的参数时，通过推理路径可以直接读取这个车型的相关的参数，如果想得到更多细粒度的汽车参数知识，可以进一步探索这些实例节点*

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MmM0OGQ3NTk4MDNlYTEwNDU4NTliZWUwZWY4NWVlNDhfNGI1MWE3M2RiMzAwZWI5MzEzMGYzMTRkNGM2NDJhYzlfSUQ6NzY3MDUyOTUwNjM5MzkxODc1MV8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=NmQzNWZhOGI4MmE0MWY4ZjcxMjYzMjZlZjMwMTJkODlfY2ZjZjE0MmJjMzE1MDZiNTMzMDM3ZmY0YTg3NWJlZTRfSUQ6NzY3MDUyNTk1NDg4OTY0OTQxMV8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=Y2E3NWUyMGI5ZDkwNzc4MmEyYWZjMTgwMzRiOTAyNDFfMGYyOGYzZDJkNTNlZGRkOWE5YjcwOWNjNzJlM2I4MThfSUQ6NzY3MDUyNTkzNDc4NDU0Nzc5NF8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

### **4\.3 输入汽车风格和汽车级别**

**示例输入：**

```Plain Text
**请给我推荐运动风格的跑车相关的参数？**
```

**推理路径：**

```Plain Text
汽车车型：跑车 ─[包含]→ 汽车实例─[EXPRESSES_STYLE]→汽车风格(风格类型：运动)
```

**Cypher：**

```SQL
MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
where style.name CONTAINS '豪华' and n.name='跑车'
RETURN p LIMIT 150;

MATCH p=(n:汽车车型)-[:包含]-(:汽车实例)-[:EXPRESSES_STYLE]-(style:`汽车风格`)
where style.name CONTAINS '科技' and n.name contains 'SUV'
RETURN p LIMIT 50;
```

**输出示例过程：**

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=Y2Q3ZjkzZTJlZjU0ZTJjNzI3NmE0ZjAzNTcxYzNkNGFfZGIwMzhiMDc2ZmFmNmEyMGQyNDEwYzIwYzkzN2Y2ZWVfSUQ6NzY2OTQ2MTk2NjkxNzczMzMxNF8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=YTRiZDI0OWE0MzkwODkyZGU1Y2YyYmM3YTgwNzI3MjVfMzZiODg1NWFhYjZmZmQwMzUzZjkzODQ0MWY0ZTFkMDNfSUQ6NzY2OTQ2MjEyNTIxNDk3NzIyNl8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=ZGU3MWJlYzA3NmI1YTI2MDcyNTNkMWM2ZmIyNjc5ZDRfYjc1N2JlYmM1ZWMwMDQ2OGY1ZmNkYzA5OGIxNzZkMzVfSUQ6NzY2OTQ2MTcxODc2NTk4MDg3MV8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MzkyZjJjYzRhYTFiYWU1YmUwNzhjNzA5ODY1ZTQ2Y2FfNGI1MWNmMmM3MzE3ZWY4YWEwZTAzNWNmNDE0M2Y3NWNfSUQ6NzY3MDUzMTU1MTA2MjQ2MTQwMl8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=ZTM2ZTQ3NWZkZGNhYzQ2ZGQ5ZmUyNzZlNjZmZWYzMWZfN2Y1YTlkY2ViYjc5MjNiZGMzMTE3NTA4MzQ3MWZjZmRfSUQ6NzY3MDUzMTYzNjI0NDg1OTg5MV8xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

## 交付文件：



\[07\_graph\.jsonl\]

## 需求整改

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=ZGI0MzRkZDUzMDBiNTEyNjQxMDM1YzlmOTkzOTI5M2ZfOTdjNmNmMTU2ZmJmZDBkYTVkMmI2ZDY2ZjkxZWM4ZGJfSUQ6NzY3MDQxNzcxOTA0NTQ0Mjg0M18xNzg2MDIyMzU2OjE3ODYxMDg3NTZfVjM)

1. 汽车级别改名为汽车车型，多余的跟汽车实例连接的节点全部删掉；

    1. 汽车车型目前不包含两厢轿车，三厢轿车  

    2. 汽车级别目前得改成汽车车型。

2. 目前美学概念中包含了他说的需要的汽车风格，把这些美学概念改成汽车风格，然后其他美学概念不管就行了。

    1. 需要注意的是运动有多个，把那个连接节点多的放到汽车风格节点中；

    2. 复古、溜背 新增节点

    3. （Car】）目前图谱中没有意见中说的这种汽车级别可能需要做Judge 或者看看 A0，A B C D级都是啥意思  把这些级别跟每个具体的汽车实例相连接，

3. 理论情况：6\*5\*8=240个复合节点   

4. 删掉就行

5. 按照说的处理

```Shell
python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --style 运动 \
  --car-class 紧凑型SUV \
  --score-threshold 0.85 \
  --confidence-threshold 0.85 \
  --output result.json
  
  
  
  python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --style 科技 \
  --output result.json
  
  python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --car-class 紧凑型SUV \
  --output feature_v2/artifacts/recommendations/compact_suv.json
  
  
  python3 feature_v2/parameter_recommendation/neo4j_recommend.py \
  --style 运动 \
  --car-class 跑车 \
  --output result.json
```

## 技术路线选择

完整口径见：[`feature/parameter_recommendation/LLM_as_Rubrics技术方案.md`](feature/parameter_recommendation/LLM_as_Rubrics技术方案.md)

两条链路：

1. **实体融合**：从 `AestheticConcept` 收敛出甲方所需的 `汽车风格`，并保留 `Guides → DesignParameter`；
2. **LLM as Rubrics**：用双图谱证据判定汽车实例风格归属，写入 `EXPRESSES_STYLE`；后续用多强模型自一致性共识降低偏差。






