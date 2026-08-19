# Benchmark 上游 Offline 图处理

本目录实现 `BENCHMARK上游链路改造方案.md` 的 Offline 部分：从 `kgdata_0804.jsonl` 抽取七类特征节点，动态构造 Style/Type Rubric，分别运行两个 LLM Judge，并生成可审计的 `StyleAssociatedWith` / `TypeAssociatedWith` 候选边。

推荐使用统一 shell 入口：

    ./feature/benchmark_upstream_offline/run_offline_judge.sh --help

    ./feature/benchmark_upstream_offline/run_offline_judge.sh smoke gpt-4o-mini both

全量执行有显式费用与质量确认门禁：

    CONFIRM_FULL_RUN=1 \
      ./feature/benchmark_upstream_offline/run_offline_judge.sh full gpt-5-mini both

完整执行计划、验收标准和恢复方式见 `.agent/BENCHMARK_UPSTREAM_OFFLINE_EXECPLAN.md`。默认 smoke 样本是七种节点标签分层、固定 seed 的 500 条；两个模型必须复用同一个 `features_smoke_500.jsonl`。运行器输出是 append-only，重复原命令会跳过已经成功的 task + node_id。

## Prompt 缓存

当前 Prompt v1.2 按缓存前缀组织为：

    固定 System 指令 + 固定完整 Rubric
    动态 User feature

每个模型、Judge、Prompt 版本和 Rubric 内容使用一个稳定的
`prompt_cache_key`。默认情况下，运行器先为每个选中的 Judge 同步执行一个请求，
该请求结束后才启动剩余请求的并发池，避免冷缓存时多个大请求同时进入服务端。
诊断时可用 `--no-warmup` 关闭预热，但正式批次不建议关闭。

Style Rubric 中每条 `Guides(指导)` 只保留 `parameter` 和 `description`，不传入
`range`、`unit` 或关系上的 `guidance`。当前 284 条 Guide 对应的紧凑 Rubric 约
15,118 tokens，完整固定 Style System 约 15,677 tokens；相比 v1.1 的完整五字段 Rubric
减少约 56.2%。源图中 47 条 Guide 对应参数没有 description，这些条目保留
`"description":""`，不会补造说明。

此前使用 Prompt v1.1 在当前网关上的 `gpt-4o-mini` 实测结果：Style 的固定 System
约 35,075 tokens，第二个不同 feature 请求命中 35,072 cached tokens；Type 的固定 System
约 4,282 tokens，连续不同 feature 请求以及完全相同请求的 `cached_tokens` 都仍为 0。
也就是说，
代码侧缓存前缀和 key 已稳定，但当前网关没有缓存这段约 4k 的 Type 前缀。不要为了凑服务端
门槛而在 Type Rubric 中复制无意义文本；若 Type 缓存是成本前提，应先向网关提供方确认其
最小可缓存前缀、模型映射和缓存支持。

缓存审计字段保存在每条结果的 `prompt_cache_key` 与
`usage.prompt_tokens_details.cached_tokens` 中。Prompt 版本或 Rubric 内容变化会自然生成不同
key；也必须使用新的输出目录，避免旧版本结果被混入新批次。
统一 shell 入口会自动把输出写入 `runs/<scope>/<prompt-version>/<model>/`，因此升级
Prompt 后无需手工清理或覆盖旧结果。

快速开始：

    python3 -m feature.benchmark_upstream_offline.extract_inputs \
      --graph kgdata_0804.jsonl \
      --output-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --sample-size 500 --seed 20260817

    python3 -u -m feature.benchmark_upstream_offline.run_judge \
      --task both \
      --input feature/artifacts/benchmark_upstream_offline/inputs/features_smoke_500.jsonl \
      --rubric-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --output-dir feature/artifacts/benchmark_upstream_offline/smoke/gpt-4o-mini \
      --env feature/.env --model gpt-4o-mini --workers 20

`gpt-5-mini` 建议省略 temperature：

    python3 -u -m feature.benchmark_upstream_offline.run_judge \
      --task both \
      --input feature/artifacts/benchmark_upstream_offline/inputs/features_smoke_500.jsonl \
      --rubric-dir feature/artifacts/benchmark_upstream_offline/inputs \
      --output-dir feature/artifacts/benchmark_upstream_offline/smoke/gpt-5-mini \
      --env feature/.env --model gpt-5-mini --workers 20 --temperature none

构建 smoke 对比：

    python3 -m feature.benchmark_upstream_offline.analyze_smoke \
      --input-root feature/artifacts/benchmark_upstream_offline/smoke \
      --models gpt-4o-mini gpt-5-mini \
      --output feature/artifacts/benchmark_upstream_offline/smoke/comparison.json

Judge 输出保留原始响应和 token usage，可能包含输入节点数据，不应当作面向终端用户的接口。`build_edges.py` 只写新文件，不会修改源图或 Neo4j。

客户端默认直连 `.env` 中显式配置的 `BASE_URL`。当前工作环境的通用 HTTP(S) 代理会截断该网关的 TLS 连接；如果换到必须通过代理出网的环境，可显式增加 `--use-env-proxy`。

当前 500 条 Prompt v1 smoke 结果显示两种模型都有明显过度挂边问题，因此四个 `*_edges.jsonl` 只是格式验证和审计候选，不能直接写正式图。详细指标与改进建议见 `feature/artifacts/benchmark_upstream_offline/SMOKE_REPORT.md`。Prompt v1.1 的缓存验证只调用了极少量记录，Prompt v1.2 只完成离线结构与 token 验证；两者都不代表质量问题已经解决。
