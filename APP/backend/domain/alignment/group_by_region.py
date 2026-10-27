#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: extract_regions
功能: 从 find_best_match 返回的字典中，依据 number_to_region 将各位置分组，
输出每个 tRNA 区域的编号列表和对应序列。
可被其他脚本 import 调用。
"""
from typing import Dict, Any, List
from .number_to_region import number_to_region

# 需要排除的字段
EXCLUDE_KEYS = {'seqname', 'flat_seq', 'score', 'alignment'}


def group_by_region(match_info: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    输入: find_best_match 返回的字典 match_info
    返回: 一个字典，键为 region 名称，值为子字典，包含:
      - 'numbering': List[str] 该区域的编号列表
      - 'sequence' : str       对应编号的碱基序列拼接
    """
    # 临时字典： region -> list of (tid, base)
    region_map: Dict[str, List[str]] = {}

    for key, val in match_info.items():
        if key in EXCLUDE_KEYS:
            continue
        # key 可能是位置编号，例如 '1', '17a', 'V1', etc.
        try:
            region = number_to_region(key)
        except ValueError:
            # 忽略无法识别的字段
            continue
        region_map.setdefault(region, []).append((key, val))

    # 构造最终输出
    result: Dict[str, Dict[str, Any]] = {}
    for region, items in region_map.items():
        # 按编号排序：数字先按 int 排，带字母和 V 也可以按字符串自然顺序
        numbering = [tid for tid, _ in items]
        sequence = ''.join(base for _, base in items)
        result[region] = {
            'numbering': numbering,
            'sequence': sequence
        }
    return result


if __name__ == '__main__':
    # 示例：直接调用 find_best_match，然后分组
    from .find_best_match import find_best_match

    seq = "GUCCCGCUGGUGUAAU#GADAGCAUACGAUCCUNCUAAGPUUGCGGUCCUGGTPCGAUCCCAGGGCGGGAUACCA"
    info = find_best_match(seq, tsv_path='data/tRNAs_flat.tsv')
    if not info:
        print('No match found')
    else:
        regions = group_by_region(info)
        import json
        print(json.dumps(regions, ensure_ascii=False, indent=2))
