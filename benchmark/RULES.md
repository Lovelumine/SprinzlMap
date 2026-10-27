# Numbering Benchmark Rules

`benchmark/` 下的正式编号 benchmark 服从下面这套规则。

## 1. 任务规则

- 正式 benchmark 评估的是单序列到 Sprinzl 编号恢复。
- canonical benchmark 的输入只是一条 mature tRNA 序列。
- canonical benchmark 不给 anticodon hint，不给模板名，不给额外人工提示。
- 原始 gold 数据集为 [mature_benchmark.tsv](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/gold/mature_benchmark.tsv)。
- 当前论文结果使用人工校对后的 [mature_benchmark.curated.tsv](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/gold/mature_benchmark.curated.tsv)。
- 人工 decision 为 `excluded` 的记录不得出现在人工校对派生 gold 或正式 case manifest 中。

## 2. 数据规则

- 原始导出文件保留在 [export.tsv](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/gold/export.tsv)，不直接改写。
- 人工校对记录保留在 [manual_corrections.jsonl](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/gold/manual_corrections.jsonl)，不直接改写原始 mature 数据集。
- 正式 benchmark 只读取冻结后的 benchmark 数据集或其人工校对派生数据集，不在运行时临时改数据。
- benchmark 输入表必须是一行一条样本，`seqname` 必须稳定。
- benchmark 数据集一旦进入正式 run，不允许在 run 过程中替换文件内容。
- 正式 run manifest 必须保存 gold、case manifest 和额外 seqname filter 的 SHA-256。
- 重复序列、isotype 或 organism holdout 的分组键不得跨 split；检查结果保存在 split protocol 中。
- organism holdout 不能在没有版本化 taxonomy 的情况下写成 clade holdout。

## 3. 代码规则

- benchmark runner 必须支持断点续跑。
- benchmark runner 必须支持并行执行，worker 数由命令行参数控制，代码不应写死低并发上限。
- benchmark runner 必须把单样本结果原子落盘，不能只依赖内存里的总表。
- worker 不直接改写共享汇总文件；共享汇总只在主线程或收尾阶段生成。
- benchmark runner 的输入、输出和 manifest 字段必须稳定，不能随手改名。
- benchmark runner 发生单样本失败时，不能让整个 run 直接丢掉已完成结果。
- Agent 和 CM baseline 必须经过同一个 case evaluator 和同一组 hard guards。
- 正式比较必须在同一 clean git commit 上运行；dirty-worktree run 只能用于开发诊断。
- Agent+CM 实验必须在 run contract 中显式记录 planning 和 CM tool 开关；不能从运行名称推断组件是否启用。
- 未校准的 CM raw score margin 只能作为诊断证据，不能在结果中称为 calibrated confidence。

## 4. 存放规则

正式 run 不写入仓库。每条样本完成后立刻写两个后端，两个后端任一不可用就停止运行：

- MySQL `sprinzlmap.benchmark_executions`：唯一物理执行表，以 `record_type` 区分 run、case 和 attempt；case 行保存预测 CSV，评分读取当前 `trna_gold` 后动态计算。
- case 行不保存任何由答案推导的分数（`ANSWER_DERIVED_CASE_FIELDS`，2026-10-27 已从表中删除）。任何脚本要分数就用 `BenchmarkRunStore.case_rows()` 现算，不得写 SQL 去取列上的旧数。
- 旧的 `benchmark_runs`、`benchmark_case_results`、`benchmark_case_attempts` 已删除，任何脚本不得再引用；读一条 run 的索引就用 `benchmark_executions` 的列投影。
- MinIO `llm-trnaalign-artifacts`，前缀 `reviewer/LLM-tRNAAlign/benchmarks/<run_id>/`：完整 trace 与下列产物，目录契约不变。

产物契约：

- `manifest.json`
  记录命令、输入数据、method backend、provider/model 或 CM provenance、并发参数、git commit、worktree 状态、开始时间、结束时间。
- `cases/`
  单样本完整结果，按 seqname 摘要命名；索引行只保存可查询的扁平字段。
- `case_results.tsv`
  扁平化后的样本结果总表。
- `summary.json`
  汇总指标和计数。
- `summary.txt`
  便于直接查看的摘要。
- `logs/run.log`（对象 key 为 `run.log`）
  运行日志。
- `inputs/`
  该次运行使用的 gold 快照与 cohort 清单，连同 SHA-256 记入 run 元信息。

需要传统目录形态时用
`python benchmark/scripts/export_run_from_store.py --run-id <run_id>`，副本落在
被 git 忽略的 `.runtime/runs/<run_id>/`。

## 5. 断点续跑规则

- `benchmark_executions` 中已有该 run 的 case 行时，默认视为已有记录，不重复请求模型。
- 断点续跑以 store 中的索引行与 trace 为准，不以终端输出为准。
- 重跑失败样本时，必须显式指定 `--rerun-failed`。
- 全量重跑时，必须显式指定 `--rerun-all`。
- 不通过手工删除摘要文件来触发重跑；重跑策略只由 runner 参数控制。

## 6. 记录规则

单样本结果至少应记录：

- `seqname`
- `status`
- `elapsed_sec`
- `input_len`
- `template_name`
- `method_backend`
- `warnings`
- `route`
- `out_of_scope`
- `in_canonical_benchmark`
- `base_preserved`
- `legality_passed`
- `label_accuracy`
- `exact_match`
- `region_accuracy`
- `anticodon_accuracy`
- `cca_accuracy`
- `variable_accuracy`
- `llm_usage`
- `csv_content`
- `scientific_provenance`，CM backend 至少包含模型哈希、score 和逐位置来源。

## 7. 汇总规则

正式汇总至少应给出：

- 样本总数
- 完成数
- 失败数
- residue-level label accuracy
- full-sequence exact match rate
- region accuracy
- anticodon accuracy
- discriminator / CCA accuracy
- variable-loop accuracy
- legality pass rate
- base-preservation rate
- API call count
- prompt / completion / total tokens
- wall time 汇总

## 8. 并行规则

- `--max-workers` 是显式参数。
- `--max-workers` 控制样本级 worker，主要约束本地结构注释、模板检索和评测落盘。
- `--llm-global-max-workers` 控制 LLM dispatcher worker，用于控制实际 API 调用并发。
- benchmark 代码不假定 provider 端存在低并发限制，但不能把本地前处理无约束放大到同一并发。
- 并行只影响执行调度，不改变样本顺序定义、结果判定和汇总规则。
- 单样本结果必须与 worker 数无关；同一输入在同一配置下应得到同一评测口径。

## 9. 结果规则

- 正式 benchmark 的主结果只引用 store 中 `sealed` 状态的 run，以及它的
  `manifest.json`、`case_results.tsv` 和索引行。
- 内部 probe、smoke run、provider check 不写入正式 run 的 MySQL 表和 MinIO 前缀。
- 任何图表、正文数字和案例选择都必须能追回到对应 run 的 manifest 和结果文件。
