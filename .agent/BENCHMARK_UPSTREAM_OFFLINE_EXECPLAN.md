# 实现 Benchmark 上游 Offline AssociatedWith 图处理

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

本阶段为 `kgdata_0804.jsonl` 补齐 Benchmark 上游分类所需的离线语义关系。完成后，使用者可以从同一份图数据自动抽出七类可在线召回的特征节点，为每个节点分别运行汽车风格 Judge 和汽车车型 Judge，并得到带 `confidence` 与中文 `reason` 的 `StyleAssociatedWith`、`TypeAssociatedWith` 关系候选。运行器支持并发、严格 JSON 校验、失败重试和按“任务类型 + 节点 ID”断点续跑；审计产物保留原始响应、模型、Prompt 版本、token usage 与耗时。

本轮先固定抽取约 9,649 个特征节点和由现图生成的两份 Rubric，再在同一批确定性抽样的约 500 个节点上分别运行 `gpt-4o-mini` 与 `gpt-5-mini`。两次 smoke test 将比较成功率、空关系率、非法输出、标签分布、两模型一致性、token 使用和按官方单价估算的成本。此 ExecPlan 当前只承诺 Offline 图处理，不实现在线向量召回、Benchmark 100 条分类或 Neo4j 正式写入。

## Progress

- [x] (2026-08-17 18:06+08:00) 完整阅读 `.agent/PLANS.md` 与 `feature/parameter_recommendation/BENCHMARK上游链路改造方案.md`。
- [x] (2026-08-17 18:06+08:00) 核对 `kgdata_0804.jsonl` 的真实规模与关键关系方向：31,642 条记录、9,649 个目标特征节点、800 个车型实例、800 条车型包含关系、800 条级别包含关系、1,865 条实例风格关系。
- [x] (2026-08-17 18:06+08:00) 核对现有 LLM runner 的凭据读取、并发、重试、JSONL 审计和续跑实现；确认 `feature/.env` 提供 `MODEL_NAME`、`BASE_URL`、`API_KEY`，且未输出秘密值。
- [x] (2026-08-17 18:13+08:00) 在新目录 `feature/benchmark_upstream_offline/` 实现常量、图抽取、Rubric 构造和确定性 500 条分层抽样；实测输出 9,649 个完整节点和 500 个样本。
- [x] (2026-08-17 18:18+08:00) 实现两类 Prompt、直连/代理可选的 LLM 客户端、结构化校验、并发、重试、task 级断点续跑和调用审计。
- [x] (2026-08-17 19:11+08:00) 实现模型输出汇总、两模型一致性、空结果/多标签/跨路由质量代理指标和按 usage 估算成本。
- [x] (2026-08-17 19:11+08:00) 增加并通过 11 个单元测试，覆盖真实图抽取契约、Rubric 数量、过滤门槛、续跑键、重试 usage 成本、稳定关系 ID 和边属性。
- [x] (2026-08-17 18:22+08:00) 使用 `gpt-4o-mini` 完成同一批 500 个节点的 500 Style + 500 Type smoke test；1,000/1,000 成功，成本约 $3.043605。
- [x] (2026-08-17 19:09+08:00) 使用 `gpt-5-mini` 对相同 500 节点完成 500 Style + 500 Type smoke test；经降低并发和断点补跑后 1,000/1,000 成功，成本约 $7.222817。
- [x] (2026-08-17 19:12+08:00) 生成四套候选边、机器可读对比和 `feature/artifacts/benchmark_upstream_offline/SMOKE_REPORT.md`；确认当前两模型结果均不应直接写正式图。
- [x] (2026-08-17 20:20+08:00) 增加统一可执行入口 `feature/benchmark_upstream_offline/run_offline_judge.sh`，支持 smoke/full、单/双模型、单/双任务、模型安全并发、日志、断点续跑、自动边构建、汇总和 full 显式确认门禁；通过 `bash -n`、帮助输出及 smoke/full dry-run 验证。
- [x] (2026-08-17 20:51+08:00) 完成 Prompt v1.1 缓存前缀改造：固定 System + 固定 Rubric、动态 User feature、按模型/任务/版本/Rubric 哈希生成稳定 `prompt_cache_key`，并在并发池启动前逐任务执行单请求预热；13 个相关测试全部通过。
- [x] (2026-08-17 20:51+08:00) 使用 `gpt-4o-mini` 做真实缓存探针：Style 第二个不同 feature 请求缓存 35,072/35,356 input tokens；Type 在三个不同 feature 及一个完全相同 feature 请求中均报告 0 cached tokens，记录为当前网关的短前缀缓存限制待确认。
- [x] (2026-08-17 21:02+08:00) 按确认后的最终字段口径将 Prompt v1.2 Style Rubric 缩减为每条 Guide 的 `parameter + description`，重新生成 9,649 条完整输入、500 条 smoke 输入及两份 Rubric；284 条 Guide 数量保持不变，13 个测试及编译、shell 语法检查全部通过，shell 输出按 Prompt 版本隔离，未发起新的 LLM 对比请求。
- [x] (2026-08-18 12:03+08:00) 对 9,649 个节点实际运行 `v1.2-style-rubric-lean/gpt-5-mini` 的 Style 与 Type Judge；当前唯一成功任务键分别为 Style 8,898/9,649、Type 9,154/9,649，尚余 751 和 495 个失败键，因此全量调用已执行但 Offline 候选边批次尚未达到完整验收。
- [x] (2026-08-18 复核) 重新汇总全量审计结果并运行 13 个相关单元测试；测试全部通过，但 `build_edges.py` 会按设计拒绝当前含失败键的批次。
- [x] (2026-08-18 用户决策) 不再补跑剩余 1,246 个失败键；将现有成功任务键作为允许 partial 的审计候选集，失败键继续保留在汇总中，不伪装成成功或删除审计记录。
- [x] (2026-08-18 12:18+08:00) 生成 `gpt-5-mini_summary.json`、17,078 条 `style_edges.jsonl` 和 9,662 条 `type_edges.jsonl`；两份边文件共 26,740 条，均通过关系 ID/端点对唯一性、属性 Schema、置信度门槛和关系标签校验，未合并源图或写入 Neo4j。
- [x] (2026-08-18 用户决策) 对两份候选边执行严格 `confidence > 0.8` 筛选：Style 保留 9,697/17,078 条，Type 保留 4,915/9,662 条；置信度等于 0.8 的边也被排除，筛选前文件另存为可恢复备份，未合图或写入 Neo4j。
- [x] (2026-08-18) 创建独立校验包 `feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/`，按代码、输入、Judge、未筛选构边、`confidence > 0.8` 构边、汇总和验证七个阶段保存 106 MB 快照；加入无凭据复现说明、49 文件 SHA-256 和一键只读语义校验，实测输出 `VALIDATION PASSED`。
- [x] (2026-08-18 用户授权) 将原始 `kgdata_0804.jsonl` 与筛选后的 9,697 条 Style、4,915 条 Type 边按字节顺序生成独立合并图；原文件保持不变。合并图为 46,254 行、13,424 节点、32,830 关系，SHA-256 为 `05e07d42383577abe77ca9011f63691ef32ecd5139c61725fdb38a8a035b6ef7`。
- [x] (2026-08-18 12:43+08:00) 在确认 Neo4j 基线恰为 13,424 节点、18,218 关系且无 AssociatedWith 后，使用幂等 MERGE 将完整合并图上传到数据库 `neo4j`；上传后节点数不变、关系增至 32,830，Style=9,697、Type=4,915。
- [x] (2026-08-18) 对数据库中新边做逐条在线比对：关系 ID、起止 `_graph_id`、confidence、reason 与两份 JSONL 完全一致，missing/unexpected/duplicate/mismatched 均为 0；校验包扩展至 Step 08、59 个哈希文件并再次通过 `VALIDATION PASSED`。
- [x] (2026-08-18) 修复 `feature/parameter_recommendation/import_jsonl_to_neo4j.py` 直接脚本入口仍引用 `feature_v2` 及旧默认 env 路径的问题；`--help`、编译和 13 个 Offline 单元测试通过。
- [ ] 建立 40–100 条人工金标并评审全量输出质量；在质量门禁通过前，不把 v1.2 候选边合并进 `kgdata_0804.jsonl` 或 Neo4j。
- [ ] 独立启动后续 P0：为七类特征节点建立向量召回原型，用 Benchmark 100 条关键词空跑并人工验收固定 10 条 Top 10；该步骤不依赖把 AssociatedWith 候选边写入正式图。

## Surprises & Discoveries

- Observation: 技术方案估算的目标规模与源图精确一致，为 9,649 个节点，而非泛指的“约 9k”。
  Evidence: 节点数分别是 `DesignAttribute=3107`、`DesignParameter=2738`、`UserTrend=1048`、`VehiclePosture=1005`、`AestheticConcept=987`、`FamilyDNA=493`、`AerodynamicFeature=271`，合计 9,649。
- Observation: `kgdata_0804.jsonl` 没有独立的“汽车实例 → 车身”关系；车身数值已合并进关系端点中的汽车实例属性。
  Evidence: 3,484 条 `包含` 由汽车→车型 19、车型→实例 800、级别→实例 800、产品定义→实例 1,865 构成。级别→实例端点已包含长度、宽度、高度、轴距、离地间隙、接近角和离去角等值。
- Observation: standalone 主风格节点标签是 `汽车风格`，部分关系端点快照仍保留 `AestheticConcept(美学概念)`，因此关联必须优先按稳定节点 ID 和名称解析，不能只依赖端点标签。
  Evidence: 主风格节点 ID 为 `meixue_style_<风格>`；`EXPRESSES_STYLE` 端点的同 ID 快照标签与 standalone 节点不同。
- Observation: 工作目录本身不是 Git 仓库，因此本任务无法按提交粒度保存版本。
  Evidence: `git status --short` 返回 `fatal: not a git repository`。
- Observation: 环境通用 HTTP(S) 代理会截断 `.env` 网关的 TLS 连接，直连同一地址则握手正常。
  Evidence: 经代理的 urllib/curl 均返回 `SSL: UNEXPECTED_EOF_WHILE_READING`；`curl --noproxy '*'` 得到预期 HTTP 401，携带凭据的 Judge 随后成功。新客户端默认对显式 `BASE_URL` 直连，保留 `--use-env-proxy`。
- Observation: `gpt-4o-mini` 结构稳定且便宜，但有强科技/SUV 偏置。
  Evidence: 500 条中 Style 科技边 371/771；Type 紧凑型 SUV 247 次、中型 SUV 222 次；Type 空结果率仅 27.8%，并出现 `Seat belt system` 输出全部 21 车型。
- Observation: `gpt-5-mini` 的 Type 拒绝率更合理，但非空输出会用大量候选表达不确定性；Style 比 `gpt-4o-mini` 更扩张。
  Evidence: Type 空结果率 63.6%，但非空平均 3.53 条边、50.0% 跨路由、最高 15 个车型；Style 共 1,046 条边，多标签率 67.6%，4 个节点输出全部 7 风格。
- Observation: `gpt-5-mini` 在当前网关高并发不稳定，且即使省略 temperature 也存在判定波动。
  Evidence: workers 8–20 产生 16 个最终 HTTP 500 记录；workers 4 的 348 Style 续跑和 500 Type 均零最终失败。同一 `Underfloor Battery Pack` 的 Type pilot 返回空，完整批次独立调用输出全部 4 个 MPV。
- Observation: 当前固定 Style Rubric 很大且代理未命中缓存，是费用的主因。
  Evidence: `style_rubric.json` 约 163 KB；每个 Style 请求约 35k input tokens。`gpt-4o-mini` smoke 总成本 $3.043605，`gpt-5-mini` $7.222817，后者还包含约 112 万 output/reasoning tokens。
- Observation: Style Rubric 的约 35k tokens 来自 284 条完整 Guide 记录，而不是七个风格标签或单纯参数名。
  Evidence: 紧凑 JSON 为 106,454 chars / 34,519 `o200k_base` tokens；其中 237 个 description 值约 11,337 tokens、284 个 guidance 值约 8,365 tokens、274 个 range 值约 7,312 tokens。若只保留各风格的 parameter 列表，约为 2,355 tokens。
- Observation: 固定前缀改造在当前网关上能使大 Style Rubric 命中，但不能保证约 4k Type 前缀命中。
  Evidence: `gpt-4o-mini` Style 的第二条响应报告 `cached_tokens=35072`，固定 System 约 35,075 tokens；Type 固定 System 约 4,282 tokens，连续不同请求和完全相同请求均报告 `cached_tokens=0`。稳定 key 是缓存路由提示而非强制写缓存指令，服务端门槛仍需由网关确认。
- Observation: 只保留 `parameter + description` 后，Style Rubric 在不减少 Guide 条数的情况下压缩了一半以上。
  Evidence: 新 Rubric 仍含 284 条 Guide，且每条 Guide 都严格具有 `parameter`、`description` 两个字段；紧凑 JSON 从 34,519 降至 15,118 tokens，完整固定 Style System 为 15,677 tokens，Rubric token 数降低约 56.2%。源图中 47 条没有 description 的参数保留空字符串，未补造内容。
- Observation: `v1.2-style-rubric-lean/gpt-5-mini` 的全量结果文件存在全部 9,649 个任务键，但并非全部成功，不能把“调用跑完”视为“Offline 后处理验收完成”。
  Evidence: 按每个 task + node_id 保留成功结果汇总后，Style 为 8,898 ok / 751 error，Type 为 9,154 ok / 495 error；`build_edges.py` 默认会拒绝带失败键的 partial batch。
- Observation: v1.2 缩短 Style Rubric 并未消除过度关联，Type 的跨路由发散也仍然明显。
  Evidence: Style 的 8,898 个成功节点生成 17,078 条关系，60.13% 的成功节点为多标签；Type 的 9,154 个成功节点生成 9,662 条关系，非空节点平均 3.72 条边，54.39% 的非空结果跨越多个路由类型，且 3 个节点同时输出全部 21 个车型。
- Observation: 全量 Type 运行中使用过 `workers=500`，并发生磁盘写满和 JSONL 中断损坏，虽然后续文件已能完整解析，但该并发不适合作为收口配置。
  Evidence: 日志记录 `OSError: [Errno 28] No space left on device`、随后一次 `UnicodeDecodeError` 和无效 JSONL 第 828 行；当前 `/home` 文件系统仅约 19 GB 可用且使用率显示为 100%。
- Observation: 原始图的关系 ID 值不是全局唯一，但完整关系签名唯一，不能用单独的 relationship `id` 判断原图重复。
  Evidence: 18,218 条原始关系中有 653 个 ID 值被不同端点复用；按 `(label, id, start.id, end.id)` 统计没有重复。现有 Neo4j 导入器的 MERGE 同时包含起点、终点、关系类型和 `_graph_id`，数据库基线完整保留 18,218 条。
- Observation: Neo4j 在上传前与原始图精确一致，因此本轮不需要清库或删除旧 AssociatedWith。
  Evidence: 上传前只读查询返回 13,424 节点、18,218 关系、13,424 个唯一节点 `_graph_id`，StyleAssociatedWith 和 TypeAssociatedWith 均为 0。
- Observation: 导入器的直接脚本 fallback 尚沿用不存在的 `feature_v2` 包，但模块入口正常。
  Evidence: `python3 feature/parameter_recommendation/import_jsonl_to_neo4j.py ...` 在连接数据库前抛出 `ModuleNotFoundError: feature_v2`；改用 `python3 -m feature.parameter_recommendation.import_jsonl_to_neo4j` 成功上传。随后已把 fallback 与默认 env 路径修正为 `feature`。

## Decision Log

- Decision: 新实现放在 `feature/benchmark_upstream_offline/`，不改写已有 `feature/parameter_recommendation/run_judge.py`。
  Rationale: 用户要求新文件夹；新任务的输入、输出 Schema 和双 Judge 续跑键与旧任务不同，隔离可避免破坏已完成的汽车风格链路。
  Date/Author: 2026-08-17 / Codex
- Decision: 500 条 smoke 样本采用按七种目标特征标签分层、固定随机种子的确定性抽样，并让两个模型复用同一输入文件。
  Rationale: 源图各标签规模差异明显；分层能覆盖不同语义层级，相同样本才能公平比较模型质量和成本。
  Date/Author: 2026-08-17 / Codex
- Decision: 每个样本仍执行两个独立请求，而不是把 Style 与 Type 合并为一次请求。
  Rationale: 这是技术方案的核心要求，可防止车型证据与风格证据互相污染，也能分别报告失败率与成本。
  Date/Author: 2026-08-17 / Codex
- Decision: smoke 阶段只生成候选边 JSONL，不直接修改 `kgdata_0804.jsonl` 或远程 Neo4j。
  Rationale: 500 条只是模型选型与质量门禁；在结论明确前写图会混入不同模型和不完整覆盖，且不利于安全重跑。
  Date/Author: 2026-08-17 / Codex
- Decision: 当前四套 smoke 边全部标记为审计候选，不选择任一模型进入 9,649 节点正式全量或写图。
  Rationale: 两模型都违反技术方案的稳定关系门槛；`gpt-4o-mini` 偏置严重，`gpt-5-mini` Style 过度多标签且 Type 非空时跨路由发散。全量运行只会放大错误并产生约 $58.73 或 $139.39 的预计费用。
  Date/Author: 2026-08-17 / Codex
- Decision: 若进行 Prompt v2 复测，Type 优先使用 `gpt-5-mini` 且 workers=4，并新增先拒绝、再路由、最后细分的层级门禁；Style 不直接沿用任一模型结果。
  Rationale: `gpt-5-mini` Type 对 `Seat belt system` 等通用属性的拒绝明显优于 `gpt-4o-mini`，但必须禁止跨路由和过多细类。Style 两模型空结果率均低于 7%，尚无可靠底座。
  Date/Author: 2026-08-17 / Codex
- Decision: shell 入口不带参数时不发起调用，full 模式还要求 `CONFIRM_FULL_RUN=1`。
  Rationale: 一次 full both 会产生 19,298 个 LLM 任务，而当前 Prompt v1 尚未通过生产质量门禁；显式确认可以避免误触发费用，同时不妨碍用户主动启动或断点续跑。
  Date/Author: 2026-08-17 / Codex
- Decision: Prompt v1.1 将完整 Rubric 放入固定 System，把单条 feature 放入动态 User，并默认在任何并发请求前为每个 task 同步预热一次。
  Rationale: 缓存复用要求大段相同内容位于消息前缀；稳定 key 按模型、任务、Prompt 版本和 Rubric 哈希隔离，可避免不同判定语义争用同一缓存路由。
  Date/Author: 2026-08-17 / Codex
- Decision: 本轮不通过复制或填充 Type Rubric 来人为跨过未知的服务端缓存门槛，也不擅自删减 Style Guide 字段。
  Rationale: 填充会浪费 token，删减字段会改变 Judge 的可用证据和质量；两者都超出纯缓存修复，应在确认网关能力及小样本质量对比后单独决策。
  Date/Author: 2026-08-17 / Codex
- Decision: 经用户确认，Prompt v1.2 的 Style Guide 固定只包含 `parameter` 和 `description`，不再保留 `range`、`unit`、`guidance`，且不再做三个 Rubric 版本的 LLM 对比。
  Rationale: 参数名与详细描述足以提供当前需要的语义锚点，删除其余长字段显著降低固定输入；版本升级和 Rubric 哈希变化会同时隔离旧续跑记录与旧缓存前缀。
  Date/Author: 2026-08-17 / Codex
- Decision: 将当前 v1.2 全量产物视为“已运行、待收口和待质量验收”的审计候选，不视为可正式写图的 Offline 完成态。
  Rationale: 尚有 1,246 个失败任务键，且 Style 多标签率与 Type 跨路由率仍明显违反“稳定、足以写入图谱”的关系口径；允许 partial 构边会让在线 Benchmark 的覆盖与结果发生不可追踪偏差。
  Date/Author: 2026-08-18 / Codex
- Decision: 后续先用 `workers=4` 断点补齐失败键并建立人工金标质量门禁；同时可开展不写图的 P0 向量召回空跑，但在门禁通过前不执行正式合图、Neo4j 写入或依赖这些边的 P2 投票评分。
  Rationale: 低并发是此前 `gpt-5-mini` 已验证的稳定配置；P0 只验证关键词能否召回相关特征节点，与 AssociatedWith 边是否正式入图解耦，可以提前暴露在线入口问题而不扩散候选边噪声。
  Date/Author: 2026-08-18 / Codex
- Decision: 经用户明确选择，停止补跑剩余失败键，使用 `build_edges.py --allow-partial` 从当前唯一成功任务键生成候选边；汇总必须继续显式报告 Style 751 和 Type 495 个失败键，候选边不得合并正式图。
  Rationale: 用户接受当前覆盖缺口并要求先产出完整审计汇总和候选 edge JSONL；保留失败统计与隔离候选文件可以满足后续分析需要，同时避免把 partial 状态误认为完整图数据。
  Date/Author: 2026-08-18 / Codex
- Decision: 当前交付的 `style_edges.jsonl` 和 `type_edges.jsonl` 只保留严格大于 0.8 的置信度，原始候选边以 `*.pre-confidence-gt-0.8.jsonl` 备份；原 `gpt-5-mini_summary.json` 继续描述过滤前 Judge 结果，另用 `confidence_gt_0.8_filter_summary.json` 描述派生筛选集。
  Rationale: 用户明确要求使用 `confidence > 0.8`，而不是 `>= 0.8`；分离 Judge 汇总与筛选汇总能够保留完整审计口径，并防止后续把过滤后的边数误认为模型原始输出边数。
  Date/Author: 2026-08-18 / Codex
- Decision: 用真实文件快照而非符号链接构造分步骤校验包，并排除 `.env` 等凭据；同时保存代码快照、实际错误日志、原始 Judge JSONL、过滤前后两套边、复现命令、SHA-256 和只读语义验证器。
  Rationale: 独立快照不会随原产物后续修改而漂移，能复核失败/重试/partial/筛选的完整因果链；排除凭据可以安全交接，哈希与语义校验结合既能发现字节篡改，也能发现任务覆盖、边结构或过滤关系错误。
  Date/Author: 2026-08-18 / Codex
- Decision: 经用户明确授权，将仍属 partial、未完成人工金标的 `confidence > 0.8` 候选边与原图合并并上传 Neo4j；不原地修改 `kgdata_0804.jsonl`，而是在校验包中生成具名合并快照，并保存上传前后和逐边比对证据。
  Rationale: 用户要求将这两份筛选边实际加入之前图数据并重新上传；独立合并文件保留原始图回退能力，数据库基线无旧 AssociatedWith，使用幂等 MERGE 可以只新增目标关系且安全重跑。partial 和未人工验收的质量边界继续明确记录，不因上传而消失。
  Date/Author: 2026-08-18 / Codex

## Outcomes & Retrospective

Offline 工程链路与 500 条双模型 smoke 里程碑已经完成。代码能从源图可重复地产生 9,649 条 Judge 输入、真实图上 Style/Type Rubric、同样本双 Judge 审计结果和确定性候选边；当前 13 个相关测试全部通过。两个模型各补齐 1,000 个 Prompt v1 成功任务，成本、延迟、标签分布、一致性和典型失败均已记录在 `feature/artifacts/benchmark_upstream_offline/SMOKE_REPORT.md`。

本轮最重要的结果不是选出一个可直接全量的模型，而是用真实数据证明当前 Prompt 的关系门槛还不够强。`gpt-4o-mini` 预计全量约 $58.73，但 Type 被 SUV 偏置污染；`gpt-5-mini` 预计全量约 $139.39，Type 拒绝更好但非空时发散，Style 也更容易为多个风格寻找理由。遵循 smoke 先行的目的，本轮没有把候选边追加到源图或 Neo4j，从而避免约 9k 节点的错误扩散。下一里程碑应建立 40–100 条人工金标并做 Prompt v2 小样本复测，而不是立即全量。

2026-08-18 的 v1.2 全量运行进一步证明工程吞吐与缓存成本可控：当前审计汇总的估算成本约 $45.28，Style 固定前缀大量命中缓存。但这批结果尚余 Style 751、Type 495 个失败键，并且成功结果仍表现为 Style 过度多标签和 Type 非空时跨路由发散。故当前正确的后续顺序是：先断点补齐并生成完整审计汇总，再以人工金标决定是否修订 Prompt/过滤规则；与此同时可独立验证 P0 向量召回。只有边质量门禁通过，才进入候选边合图/Neo4j 临时库、P2 在线投票、P3 参数推荐和 P4 Benchmark 100 条报告。

用户随后决定不补跑失败键。当前已按 partial 候选口径固化汇总和两份边文件：Style 候选覆盖 8,052 个有非空成功结果的源节点并生成 17,078 条边，Type 候选覆盖 2,596 个有非空成功结果的源节点并生成 9,662 条边；关系 ID 和源-目标对均无重复，边属性严格只有 `confidence` 与 `reason`。`kgdata_0804.jsonl` 未修改，Neo4j 未写入。后续使用这些文件时必须把缺失的 751 个 Style 和 495 个 Type 任务键视为未知，而不是空关系。

候选边随后按用户要求执行严格 `confidence > 0.8` 筛选。当前主文件中 Style 为 9,697 条、覆盖 7,659 个源节点，Type 为 4,915 条、覆盖 2,061 个源节点，保留置信度范围均为 0.81–0.95；两份文件共移除 12,128 条边，其中恰好等于 0.8 的 2,576 条也被移除。筛选后重复关系 ID、重复源-目标对和非法 Schema 记录均为 0，筛选前候选边可从同目录备份恢复。

为方便后续独立校验，上述过程已整理成按步骤编号的 106 MB 审计快照。校验包保存当时执行代码和测试、全量输入/Rubric、append-only Judge 结果和所有运行日志、未筛选与筛选后边、两类汇总以及明确的复现命令。包内不含凭据；49 个快照文件全部通过 SHA-256，语义验证进一步证明过滤后的每条边都是未筛选边的未改写子集，最终输出 `VALIDATION PASSED`。该结论只证明流水线完整性，不替代人工关系准确率验收。

用户随后明确要求把筛选边加入原图并重新上传 Neo4j。原始 `kgdata_0804.jsonl` 未被覆盖；新合并图和 merge manifest 已进入校验包 Step 07，上传前基线、失败入口日志、成功导入报告与逐边在线验证进入 Step 08。数据库最终为 13,424 节点、32,830 关系，两类新增边共 14,612 条；逐边字段级比较完全一致。校验包现有 59 个哈希文件并通过包含合并图和上传报告的新版语义验证。数据库写入已完成，但 751 个 Style 与 495 个 Type 失败键仍为未知，模型关系仍未经过人工金标准确率验收。

## Context and Orientation

仓库根目录为 `/home/sunzongyuan/Project/extraction`。输入图 `kgdata_0804.jsonl` 每行是一个 JSON 对象，`type=node` 表示节点，`type=relationship` 表示关系。关系对象内的 `start`、`end` 带端点快照，因此离线抽取可单次顺序读取完成，不需要连接 Neo4j。

“特征节点”是在线阶段允许向量召回的节点，标签严格限定为 `AestheticConcept(美学概念)`、`DesignAttribute(设计属性)`、`UserTrend(用户与趋势)`、`VehiclePosture(汽车姿态)`、`DesignParameter(设计参数)`、`FamilyDNA(家族DNA)`、`AerodynamicFeature(空气动力学特征)`。“Rubric”是给 Judge 的图谱参考摘要：Style Rubric 是七个汽车风格经 `Guides(指导)` 指向的设计参数；Type Rubric 是 21 个汽车车型的实例数量、路由类型、级别分布、风格 Top3 和典型尺寸中位数。

新目录 `feature/benchmark_upstream_offline/` 将包含可直接运行的 Python 3 标准库程序。`extract_inputs.py` 读取源图并输出完整特征 JSONL、style/type Rubric JSON 和 500 条样本。`prompts.py` 保存技术方案 6.5 节的版本化 System Prompt 与模板。`llm_client.py` 只负责 OpenAI-compatible `chat/completions` 请求。`run_judge.py` 负责两任务并发、重试、校验、过滤、审计与续跑。`analyze_smoke.py` 负责单模型和双模型对比报告。`build_edges.py` 将成功 Judge 输出转换为可追加到图数据的关系 JSONL，但 smoke 阶段不执行正式合并。

产物放在 `feature/artifacts/benchmark_upstream_offline/`。`inputs/` 保存可复用输入与 Rubric；`smoke/gpt-4o-mini/` 和 `smoke/gpt-5-mini/` 分别保存模型原始审计 JSONL、元信息与摘要；`smoke/comparison.json` 保存同样本对比结果。所有程序只从 `feature/.env` 加载 `API_KEY`、`BASE_URL` 和默认模型名，日志不得包含凭据。

## Plan of Work

先实现图数据抽取器。它顺序读取全部记录，保留七类特征节点的 ID、标签、名称、解析后的内层 `properties` 和适合 Judge 的描述字段。它同时建立车型→实例、级别→实例、实例→风格索引，并从实例端点属性解析数值。Style Rubric 按七个主风格收集直接 `Guides(指导)` 的参数名与指导信息。Type Rubric 严格输出全部 21 个车型，包括没有直接实例的两厢轿车和三厢轿车，并为缺失统计输出空分布而非编造值。

然后实现确定性抽样。目标总数默认 500，按七种标签尽量均衡分配；标签不足时将余量按剩余容量补齐。每个标签内部用固定 seed 打乱后选取，最终按节点 ID 稳定排序。抽取报告记录源图 SHA-256、各标签总量与抽样量，保证两个模型使用完全相同输入。

接着实现两个独立 Judge。每个请求只包含一个节点和对应完整 Rubric。程序要求响应为单个 JSON 对象、`node_id` 原样匹配、数组字段存在、标签属于封闭词表、置信度是 0 到 1 的数值、理由非空。程序去重并丢弃低于 0.65 的边。验证失败会附加简短纠错指令后重试；最终失败也写入输出，以便审计。续跑时只跳过相同 task、node_id、model、prompt version 且状态为 `ok` 的记录，不能因为 Style 成功而误跳过 Type。

再实现统计。单模型报告应给出两个任务的请求数、成功/失败、重试数、空数组率、边数、每节点平均标签数、标签分布、输入/输出/总 token、耗时和成本。双模型报告按 task+node_id 对齐，比较 exact label set agreement、Jaccard、空/非空分歧、每标签支持数差异和成本倍数。质量判断使用结构成功率、一致性、标签集中度与固定人工抽查清单作为代理，明确没有人工金标时不能把模型一致性称为准确率。

最后先运行 3 条最小门禁，再运行 `gpt-4o-mini` 的 500×2 请求；输出通过自动校验后，以相同命令和相同输入运行 `gpt-5-mini`。如 API 不接受 `temperature`，客户端应在明确的参数错误时允许用 `--temperature none` 重跑。成本优先使用响应 usage 与用户指定或官方当前单价计算，若代理未返回 cached token 或 reasoning token，则在报告中明确缺失而非假设为零成本。

## Concrete Steps

所有命令从 `/home/sunzongyuan/Project/extraction` 执行。抽取和测试命令为：

    python3 -m feature.benchmark_upstream_offline.extract_inputs \
      --graph kgdata_0804.jsonl \
      --output-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --sample-size 500 --seed 20260817

    python3 -m unittest discover -s test -p 'test_benchmark_upstream_offline.py' -v

预期抽取报告中的 `feature_count` 为 9649、`sample_count` 为 500、车型数为 21、风格数为 7。最小在线门禁使用单独目录：

    python3 -u -m feature.benchmark_upstream_offline.run_judge \
      --task both \
      --input feature/artifacts/benchmark_upstream_offline/inputs/features_smoke_500.jsonl \
      --rubric-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --output-dir feature/artifacts/benchmark_upstream_offline/smoke/gpt-4o-mini-pilot \
      --env feature/.env --model gpt-4o-mini --workers 2 --limit 3

3 条通过后，运行两个完整 smoke：

    python3 -u -m feature.benchmark_upstream_offline.run_judge \
      --task both \
      --input feature/artifacts/benchmark_upstream_offline/inputs/features_smoke_500.jsonl \
      --rubric-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --output-dir feature/artifacts/benchmark_upstream_offline/smoke/gpt-4o-mini \
      --env feature/.env --model gpt-4o-mini --workers 20

    python3 -u -m feature.benchmark_upstream_offline.run_judge \
      --task both \
      --input feature/artifacts/benchmark_upstream_offline/inputs/features_smoke_500.jsonl \
      --rubric-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --output-dir feature/artifacts/benchmark_upstream_offline/smoke/gpt-5-mini \
      --env feature/.env --model gpt-5-mini --workers 4 --temperature none

汇总命令为：

    python3 -m feature.benchmark_upstream_offline.analyze_smoke \
      --input-root feature/artifacts/benchmark_upstream_offline/smoke \
      --models gpt-4o-mini gpt-5-mini \
      --output feature/artifacts/benchmark_upstream_offline/smoke/comparison.json

## Validation and Acceptance

静态验收要求抽取器精确识别 9,649 个目标节点，且没有汽车实例、电池、电机等非特征节点；Style Rubric 恰有 7 个风格，每个条目来自真实 `Guides(指导)`；Type Rubric 恰有 21 个车型，样本总数之和为 800，实例级别统计总数也为 800；两厢轿车与三厢轿车允许为零实例。

Judge 单元测试必须覆盖合法空数组、低置信度过滤、非法标签拒绝、重复标签去重、node_id 不匹配拒绝、错误响应重试和 task 级断点续跑。实际 3 条门禁必须在两个 task 都产出 `status=ok`，且审计记录包含 model、prompt_version、prompt_hash、usage、raw_response、elapsed_seconds 和 attempt_history。

500 条模型验收要求每个模型都有 1,000 个成功任务键，或对所有失败给出可定位错误并在续跑后补齐；两个模型的报告必须按同一批 500 个 node_id 计算。最终建议不得只比较输出边数量：至少人工查看两类 task 各 20 个固定样本，特别关注通用工程属性是否正确返回空数组、只有粗品类证据时是否避免武断选择尺寸细类，以及理由是否引用输入真实语义。

## Idempotence and Recovery

抽取命令覆盖的是完全可再生输入文件，使用临时文件后原子替换，重复运行结果应在相同源图和 seed 下字节稳定。Judge 输出使用 append-only JSONL；程序启动时扫描成功任务键，只提交未成功项。进程中断后重复同一命令即可续跑。失败记录不视为完成，因此会再次尝试；报告按每个任务键最后一次成功结果汇总，历史失败仍保留供审计。

模型、Prompt 版本或输入源哈希变化时必须写入新的输出目录，避免混合不可比较的批次。`build_edges.py` 默认拒绝包含失败、混合模型、混合 Prompt 版本或重复任务键的输入；只有显式输出到新文件，不原地修改源图。任何情况下都不删除用户现有产物。

## Artifacts and Notes

截至计划建立时的源数据证据：

    $ wc -l kgdata_0804.jsonl
    31642 kgdata_0804.jsonl

    目标特征节点 = 3107 + 2738 + 1048 + 1005 + 987 + 493 + 271 = 9649
    汽车车型 → 汽车实例 = 800
    汽车级别 → 汽车实例 = 800
    汽车实例 → EXPRESSES_STYLE = 1865

`.env` 仅确认存在以下键，值未显示：

    MODEL_NAME=<redacted>
    BASE_URL=<redacted>
    API_KEY=<redacted>

## Interfaces and Dependencies

实现保持 Python 3.8 兼容并优先只用标准库。`extract_inputs.main()` 提供 CLI；核心函数 `extract_graph(graph_path) -> ExtractionResult` 便于测试。`build_style_rubric(...)` 和 `build_type_rubric(...)` 返回可 JSON 序列化字典。`sample_features(records, size, seed)` 返回稳定列表。

`run_judge.normalize_result(task, raw, record)` 返回经过门槛过滤的 `styles` 或 `types`。`run_judge.run_one(...)` 返回完整审计记录。输出任务键定义为 `(task, node_id, model, prompt_version)`。LLM 访问 OpenAI-compatible `/chat/completions`，凭据接口严格为 `API_KEY`、`BASE_URL`、`MODEL_NAME`。

边生成器输出现有 JSONL 图格式：关系 `start` 是原始特征节点快照，`end` 是对应 `汽车风格` 或 `汽车车型` 节点快照，`label` 分别为 `StyleAssociatedWith` 或 `TypeAssociatedWith`，`properties` 仅含 `confidence` 和 `reason`。关系 ID 必须由 task、node_id 和目标节点 ID 稳定生成，确保重复构建不会漂移。

Revision note (2026-08-17): 建立首版 ExecPlan，记录源图实测结构、独立新目录、双 Judge、公平 500 条模型对比、写图安全边界和可恢复运行约束。

Revision note (2026-08-17 19:12+08:00): 完成 Offline 实现、11 个测试和两模型各 500×2 smoke；补充代理/TLS、模型偏置、高并发稳定性、真实成本、质量结论与“不直接写图”的决策。

Revision note (2026-08-17 20:20+08:00): 增加经 dry-run 验证的一键 shell Judge 入口，以及防误触全量费用的显式确认门禁。

Revision note (2026-08-17 20:51+08:00): 完成 Prompt v1.1 固定缓存前缀、稳定 cache key 和单请求预热；补充真实 Style 命中、Type 短前缀未命中以及 Style Rubric 字段体积拆解。

Revision note (2026-08-17 21:02+08:00): 按用户最终选择升级 Prompt v1.2，将 Style Rubric 固定为 `parameter + description`，重新生成产物并记录 15,118-token Rubric 结果；取消多版本 LLM 对比。

Revision note (2026-08-18): 复核 `v1.2-style-rubric-lean/gpt-5-mini` 全量审计产物，记录未补齐的 1,246 个失败任务键、全量质量代理指标、磁盘写满事故，并明确“补齐 + 人工金标”和可独立开展的 P0 向量召回为下一步；继续禁止直接正式写图。

Revision note (2026-08-18 12:18+08:00): 按用户决策停止补跑失败键，以 `--allow-partial` 生成完整汇总及 Style/Type 候选边，记录覆盖、校验结果和“失败为未知而非空关系”的消费约束；未执行合图或 Neo4j 写入。

Revision note (2026-08-18): 按严格 `confidence > 0.8` 原地筛选 Style/Type 候选边，保留筛选前备份和独立筛选清单，并记录过滤后的覆盖、校验与汇总口径；未执行合图或 Neo4j 写入。

Revision note (2026-08-18): 将 v1.2/gpt-5-mini Offline 全流程整理为独立分步骤校验包，加入代码与数据快照、无凭据复现文档、49 文件哈希和一键语义验证，并记录实测通过结果。

Revision note (2026-08-18 12:43+08:00): 按用户授权生成原图加 `confidence > 0.8` 边的独立完整图并幂等上传 Neo4j，记录数据库基线、导入报告、字段级逐边比对、原图重复关系 ID 语义和直接脚本入口修复；校验包扩展至 Step 08 并再次验证通过。
