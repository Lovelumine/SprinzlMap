# CCA 末端图源脚本归档

这里保存 Figure 4 B--E 的渲染脚本与元数据。原位置是工作区的
`af3_cca4_20261019_all/figure_cca/`（交付稿，已移入回收站，不再保留）。
归档的目的是让 `scripts/render_individual_row.py` 在没有交付夹的情况下仍能重跑横版四格。

同目录的 `README.md` 是原作者对图本身的说明（投影、配色、末端残基编号），保持原样未改。

## 文件

| 文件 | 作用 |
|---|---|
| `render_cca_v2.py` | 主渲染脚本：读 `reference_aligned/structures/*.pdb`，投影、上色、画整体与末端视图 |
| `render_individual.py` | 单条序列的竖版 tile，接近交付稿 `individual_compact` 的构图 |
| `render_cca.py` | 第一版渲染，保留作对照 |
| `check_af3_integrity.py` | 校验 AF3 模型与参考结构，输出 `af3_integrity_check.json` |
| `projection.json` | 投影矩阵与视图参数 |
| `residue_labels.csv` | 残基标签 |
| `individual_compact_notes.txt` | 交付稿 `individual_compact` 的生成参数说明 |
| `v2_notes.md` | v2 渲染的改动记录 |

## 重跑横版四格

`scripts/render_individual_row.py` 需要一个 `--source` 目录，目录里放 `render_cca_v2.py`，
参考结构则位于该目录的**同级** `reference_aligned/structures/`：

```
<ws>/figure_cca/render_cca_v2.py
<ws>/reference_aligned/structures/<SID>.pdb
```

参考结构可以直接从仓库取（`results/af3_cca4_20261019/structures/` 已含
`5ud5_chainD_superposed.pdb`、`DA1110`、`DA0940`、`DX1480`、`DL2150`）：

```bash
WS=/tmp/cca-row
mkdir -p $WS/figure_cca $WS/reference_aligned/structures
cp research/paper/scripts/cca_artwork/render_cca_v2.py $WS/figure_cca/
cp research/paper/results/af3_cca4_20261019/structures/*.pdb $WS/reference_aligned/structures/
python research/paper/scripts/render_individual_row.py \
  --source $WS/figure_cca \
  --out research/paper/results/af3_cca4_20261019/figures_individual_row
```

2026-10-27 用这套布局重跑过一次，四张输出与仓库现有文件逐像素一致（maxdiff 0）。

输出为 1059x815 的四张 PNG，按公共包围盒裁剪，四格同一比例尺。
仓库内 `results/af3_cca4_20261019/figures_individual_row/` 的四张图由该流程生成。
