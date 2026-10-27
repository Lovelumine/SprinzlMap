# benchmark 目录规则

`benchmark/` 用于正式编号 benchmark 的设计、数据契约、脚本入口和结果规范。
目录中的资产应直接服务单序列到 Sprinzl 编号恢复。

## 一、目录用途

适合放在这里的内容包括：

- 金标准来源说明与可复现的导出/快照规则；
- 正式 benchmark 规则文档；
- benchmark 设计文档；
- gold 来源说明；
- split 定义与数据 manifest；
- 正式 benchmark 脚本；
- 输出 schema；
- run manifest 与正式结果汇总。

非正式评测、诊断输出和调试文件统一放在下游目录或开发目录。

## 二、任务定义

正式 benchmark 评估的是编号恢复，而不是泛泛的对齐质量。

- 输入：单条 mature tRNA，或论文明确纳入的其他输入类型；
- 输出：每个输入碱基的 Sprinzl 编号、结构区段、插入状态和关键锚点状态；
- gold：正式编号资源提供的逐碱基真值；
- 评价目标：每个输入碱基是否被放到了正确且合法的 Sprinzl 位置。

## 三、最低资产要求

任何结果在被称为 benchmark 结果之前，这个目录至少应具备：

- benchmark 设计文档，写清楚纳入规则、排除规则和 split 策略；
- gold 来源说明；
- 数据 manifest 或 split manifest；
- 稳定的 benchmark 脚本入口；
- 输出 schema；
- run manifest，至少记录命令、输入、输出、时间戳和 git commit。

## 四、金标准来源

金标准和人工校准记录的主存储是 MySQL，由 `APP/backend/infra/storage/sequence_repository.py`
和 `APP/backend/domain/gold_curation/` 访问。仓库不再把 TSV 当作金标准主副本，也不保留
`benchmark/gold/` 数据目录。需要离线评测时，应从 MySQL 按固定查询导出临时快照，并在 run
manifest 中记录查询条件、行数和内容哈希；评测结束后可删除临时快照。这样可以保证网站、人工复核
和 benchmark 使用同一份最新记录，同时仍能复现实验输入。

### 4.1 三层存储

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 原始参考 | MySQL `trna_sequence_aliases.baseline` | tRNAdb 提供的原始编号，导入后不改写，对应复核页 A |
| 决定历史 | MySQL `trna_annotations` + `trna_annotation_heads` | 每次人工校准一行、只追加；head 指向该序列最新决定；payload 里保留决定当时看到的 A/B 参照 |
| 现行答案 | MySQL `trna_gold` | 一条序列一行答案：`raw_*` 与 `gold_*` 并排，加来源（`raw` / `adopted_gold` / `adopted_model` / `manual` / `unsure` / `excluded`）、矫正日期与决定哈希；评分只读这一张表 |

维护答案表的脚本：

- 重建与校验：[rebuild_gold_table.py](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/scripts/rebuild_gold_table.py)（幂等，人工校准轮次结束后跑一次）
- 把答案表冻结成某次 run 的输入：[stage_gold_from_store.py](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/scripts/stage_gold_from_store.py)，runner 也可以直接用 `--gold-from-store`
- 把某个批次封存时写下的汇总分数按现行答案刷新：[refresh_run_summary_against_gold.py](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/scripts/refresh_run_summary_against_gold.py)（默认 dry-run，`--apply` 写回；页面上的逐样本分数本来就是实时算的，这个工具只处理 run 行上的 `summary_json`）

规则：模型输出冻结不动，答案可以随复核更新，评分不落第二份。读一条 case 就是拿它的
冻结预测对现行 `trna_gold` 重新打分；run 行上的 `gold_sha256` 记录这批封存时用的答案
快照，所以任何分数都能回答“是对着哪一版答案算的”。提交校准时 `append_decision()` 会
同步更新 `trna_gold` 对应行，不需要等重建。

当前正式 runner 是：

- [run_numbering_benchmark.py](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/scripts/run_numbering_benchmark.py)

当前详细规则文档是：

- [RULES.md](/home/ouyangzhuo/LLM-tRNAAlign/benchmark/RULES.md)

## 五、优先报告的指标

正式 benchmark 以编号中心指标为主：

- residue-level Sprinzl label accuracy；
- full-sequence exact numbering match rate；
- region accuracy；
- anticodon anchor accuracy；
- discriminator / CCA-tail accuracy；
- variable-loop numbering accuracy；
- insertion-position error rate 或 precision/recall；
- numbering legality pass rate。

全局 alignment 风格指标只能作为补充，不能替代这些编号指标。

## 六、split 与泄漏控制

正式 benchmark 需要明确写出泄漏控制规则：

- 同一条 gold 记录不能同时出现在开发汇报和正式测试汇报里；
- 模板检索泄漏规则必须文档化；
- 论文里使用的 family、species、similarity bin、structure type 分层，必须能从保存的 manifest 复现；
- probe、smoke run、provider check 不能混进正式 benchmark 测试集。

正式运行结果不保存在仓库里：索引写在 MySQL `sprinzlmap`，trace 与产物写在 MinIO
`llm-trnaalign-artifacts`，位置与取回方式见
[运行存储说明](../docs/architecture/benchmark-run-storage.md)。仓库中已经没有
`benchmark/runs/`，分析脚本只传 run id，缺目录时自动从 store 物化到 `.runtime/runs/`。

正式 Agent/CM 消融应通过 runner 的 `--case-manifest`、`--protocol` 和 `--split` 参数选择样本，不再临时手工拼接 seqname 列表。

以下命令用于新实验，需先从 MySQL 准备 `.runtime/benchmark/cases.jsonl`，并指定新的运行 ID；并不表示这些对照已经完成。

同一 runner 可在不改变评分器的情况下运行四组核心消融。LLM planner 和 fixed planner 使用相同的 Harness、toolbox、action allowlist 和预算；两组都允许被选中的局部 postcheck 工具调用 LLM，区别只在 action selector：

```bash
python benchmark/scripts/run_numbering_benchmark.py \
  --method-backend agent \
  --agent-planner-policy llm \
  --agent-canonical-root-strategy agent_toolbox \
  --provider deepseek \
  --model deepseekv4-flash-guangdong \
  --case-manifest .runtime/benchmark/cases.jsonl \
  --protocol unique_sequence \
  --split calibration \
  --run-id llm-planner-calibration

python benchmark/scripts/run_numbering_benchmark.py \
  --method-backend agent \
  --agent-planner-policy fixed \
  --agent-canonical-root-strategy agent_toolbox \
  --provider deepseek \
  --model deepseekv4-flash-guangdong \
  --case-manifest .runtime/benchmark/cases.jsonl \
  --protocol unique_sequence \
  --split calibration \
  --run-id fixed-planner-calibration
```

CM-only 和 legacy-only 都走相同的 biological scope gate 与 shared hard guards，但不调用 planner LLM 或 postcheck LLM：

```bash
python benchmark/scripts/run_numbering_benchmark.py \
  --method-backend agent \
  --no-llm \
  --agent-planner-policy fixed \
  --agent-canonical-root-strategy cm_only \
  --case-manifest .runtime/benchmark/cases.jsonl \
  --protocol unique_sequence \
  --split calibration \
  --run-id cm-only-calibration

python benchmark/scripts/run_numbering_benchmark.py \
  --method-backend agent \
  --no-llm \
  --agent-planner-policy fixed \
  --agent-canonical-root-strategy legacy_only \
  --case-manifest .runtime/benchmark/cases.jsonl \
  --protocol unique_sequence \
  --split calibration \
  --run-id legacy-only-calibration
```

`agent_toolbox` 是 Agent 的默认策略。`cm_first` 只保留为历史兼容基线；`cm_only` 和 `legacy_only` 是严格单工具消融。已知 broad source 只调用相应 domain CM；未知来源运行校准 portfolio。任何候选仍需通过共享 hard guards。

`--agent-canonical-root-strategy legacy_only` 只用于旧流程同提交消融。`--agent-enable-cm-root-recovery` 仅保留给该兼容路径，不是默认网页架构。runner 会从 case manifest 传递 mature input 和 broad source，但不会从 organism 名推断 mammal/vertebrate subtype。mitochondrial、virus、Sec 和其他专用路线的 abstention 必须保留在结果中，不能记成 canonical CM 成功。

`summary.json` 同时报告全体输入指标和 `selective_metrics`。正式结果必须同时给 accepted coverage、accepted-subset accuracy、`investigate`、`abstained` 和 error rate，不能只报接受样本准确率。

Each case is written as an atomic JSON checkpoint under `<out-dir>/cases/` as soon as
it finishes. A normal rerun skips only valid `ok` checkpoints and retries missing or
failed cases. When the original command used `--rerun-all`, resume it with
`--resume`; this flag takes precedence over `--rerun-all` and prevents completed
cases from being evaluated again. `--rerun-all` is reserved for an intentional new
full run. The runner submits at most `--max-workers` cases at a time and records
`status`, `attempt`, `completed_case_count`, and `last_checkpoint_at` in
`manifest.json`.

正式比较必须来自 clean worktree 和同一提交。`manifest.json` 会记录 worktree 状态；CM run 还会冻结 Infernal 版本、运行参数及四个 numbering model 的 SHA-256。`--limit` run 只能称为 smoke 或 development evaluation。

## 七、准入条件

一个资产只有在同时满足下面几条时，才进入 `benchmark/`：

- 服务正式编号任务；
- 有稳定文件契约或 schema；
- 预计会在论文写作中被正式引用；
- 可以从 manifest 或文档里追回 provenance。

## 八、命名纪律

在这个仓库里，`benchmark` 只指正式编号 benchmark。

- 非正式评测统一使用 `probe`、`diagnostic` 或 `development evaluation`；
- pair-task alignment 结果不写成 benchmark；
- 临时诊断输出不进入 `benchmark/`。

## 九、当前运行与清理规则

store 中登记五个批次，全部 `sealed`：`20261020-164533-deepseekv4-flash-guangdong`
（2,530 条，论文主结果）加四个消融对照
（`20261023-041911-control-cm-direct`、`20261023-043053-control-harness-cm-root`、
`20261023-043053-control-harness-retrieval-dp`、`20261023-043053-control-fixed-planner`），
四个对照使用同一份冻结答案（`gold_sha256` `fe94efd2…`）和同一组 2,530 条队列。

`20261019-173732-deepseekv4-flash-guangdong`（2,531 条合并 composite）与更早的报告、
中断批次已在 2026-10-23 从 MySQL 与 MinIO 删除，只在 Git 历史里可查；它的指标留在
[`docs/operations/gold-answer-table-2026-10-23.md`](../docs/operations/gold-answer-table-2026-10-23.md)。

复核 API 和网页默认选择 `20261020-164533-deepseekv4-flash-guangdong`；访问未登记的运行 ID 返回 404，不从外部归档回读。
图 2 的 cohort 固定为 2,530 条；详细指标以
[当前结果说明](../docs/architecture/sprinzlmap-benchmark-results.md) 和
[图 2 数据说明](../research/paper/results/figure2/README.md) 为准。
