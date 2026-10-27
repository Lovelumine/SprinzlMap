#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: tRNAs_flat_exporter
功能: 根据给定的模板名称，从 data/tRNAs_flat.tsv 中导出两行 CSV：
  1. 第一行：除了 seqname 之外的所有列名（即位置编号）
  2. 第二行：对应模板的所有列值
可直接被其它脚本 import 调用。
"""

import csv
import os
from typing import Optional

def export_template_csv(
    template_name: str,
    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv')
) -> Optional[str]:
    """
    根据模板名称，从展平的 tRNA TSV 文件中提取对应记录并生成两行 CSV 文本。

    参数:
      template_name: 要查找的 seqname 字段值
      tsv_path:       tRNAs_flat.tsv 文件路径（制表符分隔，第一列必须是 seqname）

    返回:
      如果找到了对应记录，返回一个字符串，内容为：
        列名1,列名2,...,列名N\r\n
        值1,值2,...,值N\r\n
      如果没有找到，返回 None。

    示例:
      >>> csv_raw = export_template_csv("acidHosp1_chr.trna1-LeuTAG")
      >>> print(csv_raw)
      -1,1,2,3,...,73\r\n
      -,G,C,G,...,A\r\n
    """
    # 打开 TSV 文件
    try:
        with open(tsv_path, newline='', encoding='utf-8') as fin:
            reader = csv.DictReader(fin, delimiter='\t')
            # 确保 'seqname' 在字段名中
            if 'seqname' not in reader.fieldnames:
                raise ValueError(f"TSV 文件缺少 'seqname' 列: {tsv_path}")

            # 过滤出所有不含 seqname 的列，顺序即为 tRNA 的编号顺序
            number_columns = [c for c in reader.fieldnames if c != 'seqname']

            # 寻找匹配的行
            for row in reader:
                if row.get('seqname') == template_name:
                    # 构造 CSV 文本到内存
                    # 第一行：编号列表
                    header_line = ",".join(number_columns)
                    # 第二行：对应的每个列的值
                    values = [row[col] for col in number_columns]
                    value_line = ",".join(values)
                    # CRLF 结尾
                    return f"{header_line}\r\n{value_line}\r\n"
    except FileNotFoundError:
        raise RuntimeError(f"找不到 TSV 文件: {tsv_path}")
    except Exception as e:
        raise RuntimeError(f"导出 CSV 过程中出错: {e}")

    # 未找到该模板
    return None


if __name__ == "__main__":
    # 简单测试
    tpl = "acidHosp1_chr.trna1-LeuTAG"
    result = export_template_csv(tpl)
    if result is None:
        print(f"未找到模板: {tpl}")
    else:
        print("=== 导出结果 ===")
        print(result)