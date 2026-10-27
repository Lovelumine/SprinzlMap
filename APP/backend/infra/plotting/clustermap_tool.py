#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csv → clustermap → png bytes

核心调用:
    png_bytes = csv_bytes_to_clustermap_bytes(raw_csv_bytes)
"""

import io
from typing import Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
import seaborn as sns
from matplotlib.colors import ListedColormap


# ---------- 可调色表 ----------
NUC_COLORS = {
    "-": "#D3D3D3",  # 灰 (gap)
    "A": "#FF9999",  # 红
    "G": "#99FF99",  # 绿
    "C": "#9999FF",  # 蓝
    "U": "#FFCC99",  # 橙
    "N": "#B0E0E6",  # 浅青（未知/不确定）
}
# 注意：把 N 单独作为一个类别，避免映射出 NaN
NUC_ORDER = ["-", "A", "G", "C", "U", "N"]
NUC_TO_INT = {n: i for i, n in enumerate(NUC_ORDER)}


def _load_alignment_from_bytes(csv_bytes: bytes) -> Tuple[pd.DataFrame, list, list]:
    """
    把原始 CSV 字节解析成:
        - seq_df: 行=样本，列=位点，元素=字符
        - names: 行名
        - col_labels: 位点编号
    约定: 第一行第一列是 'name' 或 'ID'，其余列是位点编号;
         之后每行: 第 1 列=样本名, 其余=字符
    """
    df = pd.read_csv(io.BytesIO(csv_bytes), header=None)
    col_labels = df.iloc[0, 1:].tolist()
    data_df = df.iloc[1:, :].copy()
    names = data_df.iloc[:, 0].tolist()
    seq_df = data_df.iloc[:, 1:].astype(str)
    seq_df.columns = col_labels
    seq_df.index = names
    return seq_df, names, col_labels


def _seq_df_to_int_matrix(seq_df: pd.DataFrame) -> pd.DataFrame:
    """
    字符矩阵 -> 数值矩阵 (按 NUC_TO_INT)
    处理逻辑：
      - 统一去空格并转大写
      - 按列 map 到数值；未识别字符回退到 'N'，避免 NaN
      - 最后再 astype(int)
    """
    def _map_col(col: pd.Series) -> pd.Series:
        up = col.str.strip().str.upper()
        mapped = up.map(NUC_TO_INT)
        # 对未识别的字符（map 后为 NaN）统一回退到 'N'
        mapped = mapped.fillna(NUC_TO_INT["N"])
        return mapped.astype(int)

    return seq_df.apply(_map_col, axis=0)


def csv_bytes_to_clustermap_bytes(
    csv_bytes: bytes,
    figure_dpi: int = 300,
    col_cluster: bool = False,
) -> bytes:
    """
    输入: CSV 原始字节
    输出: PNG 原始字节 (clustermap)

    - 行做层次聚类, 列默认不聚 (可改 col_cluster)
    - 无 color bar, 下方自定义图例
    """
    # 1. 解析
    seq_df, names, col_labels = _load_alignment_from_bytes(csv_bytes)
    int_df = _seq_df_to_int_matrix(seq_df)
    num_data = int_df.values

    # 2. 颜色映射（顺序与 NUC_TO_INT 对应）
    cmap = ListedColormap([NUC_COLORS[n] for n in NUC_ORDER])

    # 3. 绘图
    sns.set(font_scale=0.7)
    g = sns.clustermap(
        num_data,
        cmap=cmap,
        row_cluster=True,
        col_cluster=col_cluster,
        linewidths=0.1,
        xticklabels=col_labels,
        yticklabels=names,
        figsize=(max(6, 0.3 * len(col_labels)), max(4, 0.5 * len(names))),
        dendrogram_ratio=0.08,
        cbar_pos=None,
    )
    plt.setp(g.ax_heatmap.get_xticklabels(), rotation=90)

    # 4. 图例（顺序与颜色一致）
    patches = [mpatches.Patch(color=NUC_COLORS[k], label=k) for k in NUC_ORDER]
    g.fig.legend(
        handles=patches,
        loc="lower center",
        ncol=len(NUC_ORDER),
        frameon=False,
        fontsize=10,
    )
    g.fig.subplots_adjust(bottom=0.15)

    # 5. 导出为字节流
    buf = io.BytesIO()
    g.fig.savefig(buf, format="png", dpi=figure_dpi, bbox_inches="tight")
    plt.close(g.fig)  # 释放内存
    buf.seek(0)
    return buf.getvalue()


def csv_bytes_to_clustermap_svg_bytes(
    csv_bytes: bytes,
    col_cluster: bool = False,
) -> bytes:
    """
    输入: CSV 原始字节
    输出: SVG 原始字节 (clustermap)
    """
    seq_df, names, col_labels = _load_alignment_from_bytes(csv_bytes)
    int_df = _seq_df_to_int_matrix(seq_df)
    num_data = int_df.values

    cmap = ListedColormap([NUC_COLORS[n] for n in NUC_ORDER])

    sns.set(font_scale=0.7)
    g = sns.clustermap(
        num_data,
        cmap=cmap,
        row_cluster=True,
        col_cluster=col_cluster,
        linewidths=0.1,
        xticklabels=col_labels,
        yticklabels=names,
        figsize=(max(6, 0.3 * len(col_labels)), max(4, 0.5 * len(names))),
        dendrogram_ratio=0.08,
        cbar_pos=None,
    )
    plt.setp(g.ax_heatmap.get_xticklabels(), rotation=90)

    patches = [mpatches.Patch(color=NUC_COLORS[k], label=k) for k in NUC_ORDER]
    g.fig.legend(
        handles=patches,
        loc="lower center",
        ncol=len(NUC_ORDER),
        frameon=False,
        fontsize=10,
    )
    g.fig.subplots_adjust(bottom=0.15)

    buf = io.BytesIO()
    g.fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(g.fig)
    buf.seek(0)
    return buf.getvalue()


# ----------------------------------------------------------------------
# 简单自测 / CLI 调用
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print(
            "Usage: python -m backend.infra.plotting.clustermap_tool <input.csv> [output.svg]",
            file=sys.stderr,
        )
        sys.exit(2)

    input_csv = Path(sys.argv[1])
    if not input_csv.exists():
        print(f"Input file not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    with open(input_csv, "rb") as f:
        svg_bytes = csv_bytes_to_clustermap_bytes(f.read())

    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out.svg")
    with open(out, "wb") as f:
        f.write(svg_bytes)
    print(f"Clustermap written to {out}")
