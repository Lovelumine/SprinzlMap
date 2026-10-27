# Benchmark 运行的存储位置

Status: ACTIVE
Type: Architecture / data storage
Last updated: 2026-10-23

benchmark 的运行输出不放在仓库里。仓库不再保留 `benchmark/runs/`，也不再保留
`benchmark/artifacts/`。一条样本算完就立刻写入两个后端，中途断开时脚本直接停止，
不会留下半个本地结果目录。

## 一、数据在哪

| 内容 | 位置 |
| --- | --- |
| run 级元信息（state、model、git commit、gold 的 SHA-256、run contract、开始与结束时间、归档 key） | MySQL `sprinzlmap.benchmark_executions`，`record_type='run'` 的行 |
| 每条样本一行索引（46 项运行期字段：状态、路由、disposition、guard 计数、token 用量，加 8 项预测与 trace 引用；不含任何评分列） | 同上，`record_type='case'` 的行；评分读取 `trna_gold` 后动态计算 |
| 每次重试记录（outcome、error、latency、usage） | 同上，`record_type='attempt'` 的行 |
| 每条样本的完整 trace JSON，以及 `manifest.json`、`summary.json`、`case_results.tsv`、日志与输入快照 | MinIO 桶 `llm-trnaalign-artifacts`，前缀 `reviewer/LLM-tRNAAlign/benchmarks/<run_id>/` |
| 整个 run 的打包归档（`.tar.zst`） | 同上，key 记在 run 行的 `archive_key` |

`benchmark_runs`、`benchmark_case_results`、`benchmark_case_attempts` 三张兼容视图已在
2026-10-23 删除：所有读取都走 `benchmark_executions` 的列投影（`run_column_pairs()`、
`case_column_pairs()`、`attempt_column_pairs()`），字段名保持不变，因此脚本与复核页
看到的仍然是 `state`、`attempt`、`outcome` 这类规范名。`ensure_schema()` 遇到同名视图
会直接删除；遇到同名物理表则停止运行，因为那意味着数据还没有并入执行表。

`case_*` 里的评分列已在 2026-10-27 删除（36 列，见 `ANSWER_DERIVED_CASE_FIELDS`）。
那些列是运行时按当时的答案写下的副本：复核页和论文图都实时重算，所以从不读它们，
但直接写 SQL 的人拿到的是旧数（例如主批次的完全匹配 1975，而按现行答案是 1977）。
现在一条样本只有预测是冻结的，分数一律由 `trna_gold` 现算；`ensure_schema()` 见到还
留着这些列的库就把它们删掉。保留的 `case_legality_passed` 不依赖答案，它是预测自己
是否通过结构规则。run 行上的 `summary_json` 仍是封存时的汇总，需要时用
`benchmark/scripts/refresh_run_summary_against_gold.py --apply` 刷新。

MinIO 上的目录结构与旧版本地目录一致（`cases/`、`inputs/`、`logs/` 等），因此旧分析
脚本只需要一个 run id 就能工作。

## 二、常用命令

```bash
# 列出 store 里登记过的 run（读 MySQL，不读磁盘）
python benchmark/scripts/export_run_from_store.py --list-runs

# 把某个 run 取回本地一份可分析的目录，落在被 git 忽略的 .runtime/runs/<run_id>/
python benchmark/scripts/export_run_from_store.py --run-id <run_id>

# 新的正式运行：默认只写 store，本地不留文件
python benchmark/scripts/run_numbering_benchmark.py --run-id <run_id> ...
```

需要本地副本时显式加 `--keep-local`；`--no-store` 只用于离线调试，且必须同时
`--keep-local`。分析脚本（报告、作图、对比、重跑）传入 run id 时会自动调用
`resolve_run_dir()`，缺目录就从 store 物化，不再要求 `benchmark/runs/` 存在。

## 三、后端连接

| 机器 | MySQL | MinIO |
| --- | --- | --- |
| 开发机 | `223.82.75.76:3306`（或内网 `192.168.236.1:3306`），库 `sprinzlmap` | `http://223.82.75.76:9000` |
| 服务器 | `127.0.0.1:3306` | `http://127.0.0.1:9000` |

MinIO 账号与策略为 `sprinzlmap-artifacts`，只允许访问 `llm-trnaalign-artifacts` 桶。
不要用 `https://minio.lumoxuan.cn`：该域名走 Cloudflare，`PutObject` 与
`ListObjectsV2` 会返回 520，必须直连源站 9000 端口。凭据全部在 `.env` 中
（`MYSQL_*`、`SHARED_MYSQL_*`、`MINIO_*`），`SHARED_MYSQL_*` 是 sup-tRNA 网站
（`ensure` 库）专用，与本项目分开。

## 四、当前登记的 run

| run_id | 样本数 | MySQL 行 | MinIO 对象 | 归档字节 | 归档 SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `20261020-164533-deepseekv4-flash-guangdong` | 2,530（主结果） | 2,530 case + 21 attempt | 2,569 | 1,833,345 | `75212b496044fd1a…d207ba8d` |
| `20261023-041911-control-cm-direct` | 2,530 | 2,530 case | 2,535 | 未打包 | — |
| `20261023-043053-control-harness-cm-root` | 2,530 | 2,530 case | 2,535 | 未打包 | — |
| `20261023-043053-control-harness-retrieval-dp` | 2,530 | 2,530 case | 2,535 | 未打包 | — |
| `20261023-043053-control-fixed-planner` | 2,530 | 2,530 case | 2,539 | 未打包 | — |

五个批次都是 `sealed`，`n_pending = 0`；主批次含 21 条技术失败重试记录。对照批次没有
打包归档，逐样本对象本身就是完整证据。

2026-10-23 清掉两类历史残留：`20261019-173732-deepseekv4-flash-guangdong`
（2,531 条合并 composite，2,531 个 case 行加 2,574 个对象）被删除；MinIO 里九个早期
run 的 `.run.tar.zst`（`deepseek-v4-pro-*`、`deepseek-chat-full-v1`、
`scientific-explicit-*-ec24320`）也删了，它们的索引行早已不在，对象成了孤儿。
1019 的指标留在[金标准答案表运维记录](../operations/gold-answer-table-2026-10-23.md)，
主批次的 `retry_lineage.json` 仍写着它的前驱 id。

论文正文与图 2 只使用 20261020 这一组 2,530 条结果，其中 `tdbD00000397|DD1150`
已按人工校准判为非典型 tRNA，不计入主评分，评分分母为 2,529。
