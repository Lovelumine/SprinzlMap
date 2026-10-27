# Benchmark Report Specification

`benchmark/` 下的正式 benchmark 报告必须遵守下面这套目录、脚本和内容规范。

## 1. 目标

- benchmark 报告用于对单序列 Sprinzl 编号 benchmark run 进行正式书面分析；
- 报告必须可复现，不能依赖手工截图、手工改图或临时 notebook；
- 报告文档、图表和中间统计必须能从指定 run 目录重新生成。

## 2. 目录契约

正式报告统一写到：

- `benchmark/reports/<run_id>/`

该目录至少包含：

- `REPORT.md`
  正式报告文档；
- `report_data.json`
  供复核和二次消费的结构化统计结果；
- `figures/`
  报告引用的全部 PNG 图片目录。

## 3. 脚本位置

报告生成脚本统一放在：

- `benchmark/scripts/generate_benchmark_report.py`

要求：

- 脚本必须接受 `--run-dir` 参数；
- 脚本必须能够独立生成 `REPORT.md`、`report_data.json` 和全部图片；
- 图片必须由脚本直接产出，不允许手工拖拽或外部编辑器另存；
- 后续如果要新增图表，也必须仍由该脚本或其明确调用的子脚本生成。

## 4. 图片位置

图片统一写到：

- `benchmark/reports/<run_id>/figures/`

命名要求：

- 使用稳定前缀和两位编号；
- 使用 `.png`；
- 文件名必须与报告中的引用一一对应。

当前固定图表清单如下：

- `fig01_overall_views.png`
- `fig02_label_accuracy_distribution.png`
- `fig03_region_breakdown.png`
- `fig04_source_group_breakdown.png`
- `fig05_template_similarity_bins.png`
- `fig06_error_modes.png`

## 5. 报告内容要求

`REPORT.md` 至少包含下面这些章节：

- 标题与元信息；
- Executive Summary；
- Evaluation View and Metric Definitions；
- Overall Results；
- Per-Case Accuracy Distribution；
- Region Analysis；
- Biological Source Analysis；
- Template Similarity Analysis；
- Error Analysis；
- Root-Cause Interpretation；
- Priority Fixes。

必须写清楚：

- 使用的 run 标识；
- 统计口径；
- error-inclusive 与 ok-only 的区别；
- tail-trimmed 口径；
- biological source 分组口径；
- template similarity 的定义方式。

## 6. 图片与文案绑定规则

报告必须至少包含下面六张图：

1. `fig01_overall_views.png`
   用途：展示整体指标视图，至少覆盖 error-inclusive、ok-only、tail-trimmed 三种口径。
2. `fig02_label_accuracy_distribution.png`
   用途：展示单样本 `label_accuracy` 分布，并标出关键分位点。
3. `fig03_region_breakdown.png`
   用途：展示各结构区段的 residue-level accuracy 与 region exact-match。
4. `fig04_source_group_breakdown.png`
   用途：展示生物来源分组的样本量、error rate 与关键准确率。
5. `fig05_template_similarity_bins.png`
   用途：展示与命中模板相似度分箱后的性能变化。
6. `fig06_error_modes.png`
   用途：展示错误模式组成。

## 7. 图注格式

每张图在 `REPORT.md` 中都必须有独立图注，格式固定为：

- `Figure N. <图中展示的对象和比较维度>. Run: <run_id>. Metric scope: <统计口径>. Higher is better unless noted otherwise.`

附加要求：

- 如果图里同时混用了 case-level 和 residue-level 指标，图注必须写明；
- 如果图里使用了重建指标或推导指标，图注必须写明定义；
- 图注不能只写“结果如图所示”这类空话。

## 8. 表格要求

报告至少包含下面这些表：

- overall 指标表；
- region 指标表；
- biological source 指标表；
- template similarity 分箱表；
- error mode 表。

表格中的百分比统一写成百分数，保留两位小数。

## 9. 风格要求

- 报告正文默认使用中文；
- 图标题和坐标轴可使用英文，避免本地字体依赖；
- 结论必须围绕 benchmark 行为和失败原因，不写泛泛而谈的“模型还需提升”；
- 必须给出可操作的修复优先级。
