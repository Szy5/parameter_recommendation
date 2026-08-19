# Benchmark Fixed-Type Recall

这个模块只评估甲方 Benchmark keywords 对七类特征节点的语义召回，不做 AssociatedWith 投票或 style/type 分类。

运行和验收口径以 `.agent/BENCHMARK_FIXED_TYPE_RECALL_EXECPLAN.md` 为准。主命令：

    python3 -u -m feature.benchmark_fixed_type_recall.run_recall \
      --features feature/artifacts/benchmark_upstream_offline/validation/v1.2-style-rubric-lean_gpt-5-mini/01_inputs/features_all.jsonl \
      --benchmark benchmark/benchmark_100_inputs.jsonl \
      --output-dir feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f \
      --model BAAI/bge-m3 \
      --revision 5617a9f61b028005a4858fdac845db406aefb181

该命令会下载固定 revision 的 BGE-M3 并使用本地 GPU，不调用 LLM Judge API。输出的 Top 20 是向量近邻，不等同于节点级准确率。

主跑结束后，可在不重新调用模型的情况下重算分析和校验：

    python3 -m feature.benchmark_fixed_type_recall.analyze_recall \
      --run-dir feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f \
      --manual-review feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f/manual_review_b001_b010.jsonl

生成 portable HTML 报告的 canonical 输入：

    python3 -m feature.benchmark_fixed_type_recall.build_report \
      --run-dir feature/artifacts/benchmark_fixed_type_recall/bge-m3_5617a9f

最终结果优先查看 `report.html`；逐条召回证据在 `recall_top20.jsonl`，机器汇总在 `analysis_summary.json`，独立复算结果在 `validation_report.json`。
