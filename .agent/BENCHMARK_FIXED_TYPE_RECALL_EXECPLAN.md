# 评估 Benchmark Keywords 对固定类型节点的向量召回

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

本阶段验证甲方 100 条 Benchmark 的 `keywords` 能否从当前汽车美学图中找回有意义的特征节点。完成后，使用者可以只输入关键词，在严格限定的七类、共 9,649 个节点中得到可复现的 Top 20 向量近邻，并查看每条 case 的节点、标签、相似度、文本证据，以及 100 条整体的阈值覆盖、唯一节点覆盖和标签分布。

本阶段只评估关键词进入图的检索层，不使用甲方旧图的 `result.nodes` 或 `paths`，不使用本轮新增的 AssociatedWith 边做投票，也不输出 style/type 分类准确率。当前没有本图节点级人工金标，因此“返回了多少节点”和“这些节点是否相关”必须分开报告；固定 10 条 Top 10 人工抽查用于给出方向性质量判断，但不能称为完整 recall accuracy。

## Progress

- [x] (2026-08-18) 核对 `benchmark/benchmark_100_inputs.jsonl`：100 条、100 个唯一 ID、每条 1–5 个非空关键词。
- [x] (2026-08-18) 核对检索语料：七类节点共 9,649 条，全部有 name，1,267 条缺少 description。
- [x] (2026-08-18) 确认本机有 `sentence-transformers`、NumPy、PyTorch 和 NVIDIA A800，但没有缓存 embedding 模型。
- [x] (2026-08-18) 选择官方 BAAI/BGE-M3 dense embedding，并固定 revision `5617a9f61b028005a4858fdac845db406aefb181`。
- [x] (2026-08-18) 实现语料构造、embedding、Top 20 召回、阈值汇总和可恢复产物。
- [x] (2026-08-18) 增加并通过 5 个不依赖模型下载的单元测试。
- [x] (2026-08-18) 生成 9,649×1,024 节点矩阵和 100×1,024 查询矩阵，float32 且标准化。
- [x] (2026-08-18) 生成 100 条 Top 20 召回明细、机器汇总和 B001–B010 固定人工抽查。
- [x] (2026-08-18) 独立重算排序并完成 11 项校验，生成含两张原生图表的 portable HTML 技术报告。

## Surprises & Discoveries

- Observation: 甲方 `benchmark_100_深思版.jsonl` 已带另一版图的 nodes 和 paths，但不能作为当前图节点级金标。
  Evidence: 技术方案明确要求在线输入只使用 `input.keywords`；旧结果中的标签、节点和多跳路径来自不同图版本，直接复用会造成数据泄漏和错误口径。
- Observation: 当前语料是中英混合，且部分节点只有英文 name/description。
  Evidence: `features_all.jsonl` 的首批 DesignAttribute 包含 `Wheel control precision` 等英文记录，同时也有中英双语和纯中文节点。
- Observation: 固定 Top K 会机械地为每条 case 返回 K 个节点，不能直接证明召回有效。
  Evidence: 对任何非空向量查询，排序都能产生 Top 20；必须同时查看相似度阈值、分数分布和人工语义抽查。
- Observation: Top 20 的节点类型明显偏离语料基线。
  Evidence: 汽车姿态和家族 DNA 的槽位富集倍数分别为 2.95 和 2.52；设计参数和美学概念只有 0.21 和 0.44。
- Observation: 全查询拼接会让多关键词中的强语义主导结果。
  Evidence: B004、B007 的 Top 10 各有 4 个无关节点；B001 对城市紧凑姿态召回很好，但像素灯语没有进入 Top 10。

## Decision Log

- Decision: 只检索 `AerodynamicFeature(空气动力学特征)`、`AestheticConcept(美学概念)`、`DesignAttribute(设计属性)`、`DesignParameter(设计参数)`、`FamilyDNA(家族DNA)`、`UserTrend(用户与趋势)`、`VehiclePosture(汽车姿态)`。
  Rationale: 这是 Benchmark 上游方案规定的在线召回范围，防止汽车实例、电池、电机等节点污染入口。
  Date/Author: 2026-08-18 / Codex
- Decision: 节点 embedding 文本固定为 `名称：name` 加非空的 `描述：description`，标签只作为过滤和报告元数据，不进入向量文本。
  Rationale: 标签中包含“姿态”“属性”等词，如果写入文本会人为抬高含这些词的查询分数；name+description 是每个节点自身的语义证据。description 缺失时仍保留 name，不补造内容。
  Date/Author: 2026-08-18 / Codex
- Decision: 查询文本用中文分号按原顺序拼接 keywords，不添加模型 instruction。
  Rationale: 技术方案要求关键词拼成一句查询；BGE-M3 官方模型卡明确该模型不需要 query instruction。保留顺序和明确分隔符可使输入可审计。
  Date/Author: 2026-08-18 / Codex
- Decision: 使用 `BAAI/bge-m3` dense 1024 维向量，固定 revision `5617a9f61b028005a4858fdac845db406aefb181`，标准化后用点积计算余弦相似度，Top K 固定为 20。
  Rationale: BGE-M3 支持 100+ 语言和短句到长文档的多粒度检索，适合中文查询与中英混合语料；固定 revision、保存矩阵与哈希才能保证结果可重复。
  Date/Author: 2026-08-18 / Codex
- Decision: 主报告同时给出无阈值 Top 1/5/10/15/20 覆盖和 0.3–0.7 的阈值敏感性，不预先宣布某个阈值是生产阈值。
  Rationale: 余弦分数依赖模型和语料，尚无节点金标可调阈值；先展示敏感性和固定人工抽查，避免用任意阈值制造结论。
  Date/Author: 2026-08-18 / Codex
- Decision: 交付一个由 canonical `artifact.json` 构建的 portable `report.html`，同时保留 JSONL、NPY、JSON 和 SHA-256 审计产物。
  Rationale: HTML 适合快速阅读，机器产物支持逐条核查、独立重算和后续实验复用。
  Date/Author: 2026-08-18 / Codex

## Outcomes & Retrospective

本阶段完成。模型下载与全量编码、排序共耗时 360.475 秒，使用 NVIDIA A800 80GB PCIe。100 条查询固定 Top 20 共形成 2,000 个槽位，去重得到 843 个节点，覆盖 9,649 节点语料的 8.7367%。阈值 0.60 时 98 条 case 仍有至少一个节点，共 1,622 个槽位、662 个唯一节点；阈值 0.70 时下降到 38 条、239 个槽位、130 个唯一节点。

B001–B010 的固定 Top 10 人工抽查共 100 个槽位：71 个相关、17 个部分相关、12 个无关；严格相关率为 0.71，计入部分相关为 0.88。该样本不代表全量准确率。11 项独立校验全部通过，包括从保存的 embedding 矩阵逐条重算 Top 20 和核对原运行 SHA-256。

进入后续 edges/P2 前，应先补节点级人工标注，并验证三项改进：单关键词分别召回后融合、按类型独立召回或配额、dense+sparse/reranker。尤其需要解决汽车姿态/家族 DNA 过度召回，以及设计参数/美学概念召回不足。

## Context and Orientation

仓库根目录为 `/home/sunzongyuan/Project/extraction`。甲方输入在 `benchmark/benchmark_100_inputs.jsonl`，每行包含 `id` 和 `keywords`。当前图的固定类型语料快照在 `feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl`，每行包含 `node_id`、`labels`、`name`、`description` 和源属性。

新代码放在 `feature/benchmark_fixed_type_recall/`，产物放在 `feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f/`。embedding 是把文本转换成固定长度数字向量；余弦相似度衡量两个标准化向量方向的接近程度，值越高通常表示语义越近，但不是正确性概率。

`recall_top20.jsonl` 是主审计产物，每条 Benchmark case 保存原始关键词、拼接查询、Top 20 节点和分数。`summary.json` 汇总数量和分布。`node_embeddings.npy` 与 `query_embeddings.npy` 保存 float32 标准化向量，后续可在不重新下载或运行模型的情况下复核排序。

## Plan of Work

先实现纯函数：规范化空白、构造节点文本、构造查询文本、验证七类输入、对标准化矩阵做 Top K 排序、按 K 和阈值汇总。模型仅在 CLI 主流程中延迟加载，使单元测试不依赖网络和 GPU。

然后加载固定 revision 的 BGE-M3。节点和查询使用同一模型、同一最大长度和 float32 输出，并显式 `normalize_embeddings=True`。保存向量后使用 NumPy 矩阵乘法得到 100×9,649 分数矩阵；每条 case 按分数降序、node_id 升序作为并列稳定规则输出 Top 20。

最后生成汇总和人工抽查材料。数量报告区分检索槽位、跨 case 唯一节点和 corpus 覆盖；阈值报告给出至少命中一个节点的 case 数、阈值以上总槽位和唯一节点。固定审查 B001–B010 的 Top 10，逐项标记相关、部分相关或无关，并陈述没有节点级金标的限制。

## Concrete Steps

所有命令从仓库根目录执行：

    python3 -m unittest discover -s test -p 'test_benchmark_fixed_type_recall.py' -v

运行全量召回：

    python3 -u -m feature.benchmark_fixed_type_recall.run_recall \
      --features feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl \
      --benchmark benchmark/benchmark_100_inputs.jsonl \
      --output-dir feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f \
      --model BAAI/bge-m3 \
      --revision 5617a9f61b028005a4858fdac845db406aefb181 \
      --top-k 20 \
      --batch-size 32 \
      --max-seq-length 512

预期产物至少包括：

    corpus.jsonl
    benchmark_queries.jsonl
    node_embeddings.npy
    query_embeddings.npy
    recall_top20.jsonl
    summary.json
    run_manifest.json

## Validation and Acceptance

静态验收要求输入恰好有 9,649 个唯一 node_id、七种且仅七种标签，Benchmark 恰好有 100 个唯一 case ID，关键词全部为非空字符串数组。语料文本不得包含为了检索而新增的风格或车型标签。

向量验收要求 node matrix 为 `(9649, 1024)`，query matrix 为 `(100, 1024)`，dtype 为 float32，所有向量范数约等于 1，分数无 NaN/Inf。每个 case 恰有 20 个不同节点，rank 连续为 1–20，分数不升序，所有节点属于七类固定范围。

汇总验收要求 Top 1/5/10/15/20 的槽位数、唯一节点数和标签分布可从明细独立重算；阈值分母明确是 100 个 case 或 K×100 个槽位。人工审查不得把固定 Top 20 的非空覆盖称为准确率。

## Idempotence and Recovery

同一模型 revision、输入哈希和参数对应同一输出目录。程序写可再生产物，不修改 Benchmark、输入节点、Neo4j 或 AssociatedWith 边。embedding 完成后若只需重算汇总，可直接读取保存的矩阵和 JSONL；若模型下载中断，Hugging Face 缓存支持继续下载。

若输出目录已经含不匹配的 manifest，程序应拒绝混写；本轮执行时先使用全新目录。任何凭据都不写入产物。

## Artifacts and Notes

BGE-M3 官方模型卡说明其支持 dense/sparse/multi-vector、100+ 语言、1024 维、最长 8,192 tokens，并说明 M3 查询无需添加 instruction。本轮只使用 dense 模式，最大长度收紧为 512，因为当前节点文本远短于 8,192，降低运行成本且不改变绝大多数输入。

## Interfaces and Dependencies

`feature.benchmark_fixed_type_recall.run_recall.build_document_text(record) -> str` 构造固定节点文本；`build_query_text(keywords) -> str` 构造查询；`rank_top_k(scores, node_ids, top_k)` 返回确定性索引；`summarize_recall(rows, corpus_size, thresholds)` 返回可 JSON 序列化汇总。

运行依赖 Python、NumPy、PyTorch、sentence-transformers 和 huggingface-hub。模型下载自 `BAAI/bge-m3` 的固定 commit，计算使用本地 GPU；不调用 Judge API。

Revision note (2026-08-18): 建立 P0 固定类型节点召回 ExecPlan，固定数据范围、BGE-M3 revision、文本构造、Top K 与阈值口径，并明确无节点金标时不得报告准确率。

Revision note (2026-08-18): 完成全量运行、阈值/类型诊断、B001–B010 固定人工抽查、embedding 独立复算与 portable HTML 报告；记录实际覆盖和 P2 前门禁。
