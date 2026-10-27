#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: compare_input_template
功能: 给定一条 RNA 序列，查找最匹配的 tRNA 模板，并提取输入序列和模板在六个结构区的对应片段。
可被其他脚本 import 调用。
"""
import os
import re
import logging
import hashlib
from typing import Dict, Any
from .annotation import annotate_trna_sequences
from ..group_by_region import group_by_region
from ..input_evidence import assess_input_evidence


logger = logging.getLogger(__name__)

# 六个区域名称列表，顺序重要
REGIONS = [
    "Aminoacyl arm 5' end",
    "D loop + D stem",
    "Anticodon loop + Anticodon stem",
    "Variable loop",
    "T loop + T stem",
    "Aminoacyl arm 3' end"
]

# 注释结果中各区段结构字段名
STRUCT_KEYS = {
    "Aminoacyl arm 5' end": "AA_Arm_5prime_Struct",
    "D loop + D stem": "D_Loop_Struct",
    "Anticodon loop + Anticodon stem": "Anticodon_Arm_Struct",
    "Variable loop": "Variable_Loop_Struct",
    "T loop + T stem": "T_Arm_Struct",
    "Aminoacyl arm 3' end": "AA_Arm_3prime_Struct",
}

def _norm_u(seq: str) -> str:
    return (seq or "").replace("T", "U").replace("t", "U").upper()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _patch_tail_cca(
    full_input_seq: str,
    last_region_seq: str,
    *,
    allow_terminal_cca: bool = True,
) -> str:
    """
    若原始输入序列在最后一段之后有完整 CCA 尾端，则把这三个碱基并入
    最后一段，以便后续统一投影到 74–76。部分或非 CCA 尾端不在这里补入。
    """
    if not allow_terminal_cca:
        return last_region_seq

    s_full = _norm_u(full_input_seq)
    s_last = _norm_u(last_region_seq)
    if not s_full or not s_last:
        return last_region_seq

    # 尝试在 full 中寻找最后一段的最后一次出现位置
    pos = s_full.rfind(s_last)
    if pos < 0:
        return last_region_seq

    extra = s_full[pos + len(s_last):]  # 最后一段之后的“尾巴”
    # 只看末尾最多 3 个碱基，并且仅保留 A/C/G/U
    extra = re.sub(r"[^ACGU]", "", extra)[:3]

    if extra == "CCA":
        return last_region_seq + extra
    return last_region_seq

def compare_input_template(
    input_seq: str,
    tsv_path: str = os.path.join('data', 'tRNAs_flat.tsv'),
    template_name: str | None = None,
    template_top_k: int = 5,
    input_type: str = "mature",
    cca_status: str = "unknown",
    domain_hint: str = "unknown",
    mitochondrial_subtype: str = "unknown",
) -> Dict[str, Any]:
    """
    输入: 一条 RNA 序列。
    步骤：
      1) find_best_match -> 模板
      2) group_by_region -> 模板各区编号/序列
      3) annotate_trna_sequences -> 输入序列分区
      4) 修补完整 3′ 端 CCA：把三个尾端碱基并入最后一段
    返回:
    {
      'template_name': str,
      'template_flat_seq': str,
      'template_full_numbering': List[str],
      'template_full_seq': str,
      'raw_input_seq': str,
      'annotation_meta': {...},
      'regions': {
         region_name: {
           'input_seq': str,
           'input_struct': str,
           'template_numbering': List[str],
           'template_seq': str
         }, ...
      }
    }
    """
    raw_input = _norm_u(input_seq)
    input_evidence = assess_input_evidence(
        target_seq=input_seq,
        input_type=input_type,
        cca_status=cca_status,
    )
    cca_assignment_allowed = bool(
        input_evidence["cca_evidence"]["coordinate_assignment_allowed"]
    )
    structural_input = raw_input[:-3] if cca_assignment_allowed else raw_input

    # 1) 输入序列注释（先拿到 tRNAscan 的 isotype/anticodon 提示，用于模板重排）
    input_id = f"input_{hashlib.sha1(structural_input.encode('utf-8')).hexdigest()[:12]}"
    annotated = annotate_trna_sequences(
        {input_id: structural_input},
        domain_hint=domain_hint,
        mitochondrial_subtype=mitochondrial_subtype,
    )
    if not annotated:
        return {}
    input_annot = annotated[0]  # 单条
    trnascan_hit = bool(input_annot.get("tRNAscanHit", False))
    preferred_isotype = str(input_annot.get("tRNAscanIsotype", "") or "") if trnascan_hit else ""
    preferred_anticodon = _norm_u(str(input_annot.get("tRNAscanAnticodon", "") or "")) if trnascan_hit else ""
    
    # Deferred local import to break circular dependency:
    # template_retrieval -> scoring -> pipeline -> segmentation -> template_retrieval
    from .template_retrieval import find_top_matches, get_template_by_name

    # 1) 找模板（支持指定模板名）
    template_candidates = []
    if template_name:
        best = get_template_by_name(
            template_name,
            tsv_path,
            preferred_isotype=preferred_isotype,
            preferred_anticodon=preferred_anticodon,
        )
        if best:
            template_candidates = [
                {
                    "seqname": best.get("seqname"),
                    "score": best.get("score"),
                    "rerank_score": best.get("rerank_score", best.get("score")),
                    "rank": 1,
                    "metadata_bonus": best.get("metadata_bonus", 0.0),
                    "anticodon_match": best.get("anticodon_match", 0),
                    "isotype_match": best.get("isotype_match", 0),
                    "template_isotype": best.get("template_isotype", ""),
                    "template_anticodon": best.get("template_anticodon", ""),
                    "family_match_rank": best.get("family_match_rank", 0),
                    "family_match_label": best.get("family_match_label", "none"),
                    "family_rescue_selected": bool(best.get("family_rescue_selected", False)),
                    "family_rescue_candidate": bool(best.get("family_rescue_candidate", False)),
                }
            ]
    else:
        raw_candidates = find_top_matches(
            structural_input,
            tsv_path,
            preferred_isotype=preferred_isotype,
            preferred_anticodon=preferred_anticodon,
            limit=max(1, int(template_top_k or 1)),
        )
        template_candidates = [
            {
                "seqname": item.get("seqname"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score", item.get("score")),
                "rank": item.get("rank"),
                "metadata_bonus": item.get("metadata_bonus", 0.0),
                "anticodon_match": item.get("anticodon_match", 0),
                "isotype_match": item.get("isotype_match", 0),
                "template_isotype": item.get("template_isotype", ""),
                "template_anticodon": item.get("template_anticodon", ""),
                "family_match_rank": item.get("family_match_rank", 0),
                "family_match_label": item.get("family_match_label", "none"),
                "family_rescue_selected": bool(item.get("family_rescue_selected", False)),
                "family_rescue_candidate": bool(item.get("family_rescue_candidate", False)),
            }
            for item in raw_candidates
        ]
        best = raw_candidates[0] if raw_candidates else None
    if not best:
        return {}
    template_name = best['seqname']
    template_flat_seq = best['flat_seq']
    template_score = best.get("score")
    template_margin = None
    if len(template_candidates) >= 2:
        top0 = template_candidates[0].get("rerank_score", template_candidates[0].get("score") or 0.0) or 0.0
        top1 = template_candidates[1].get("rerank_score", template_candidates[1].get("score") or 0.0) or 0.0
        try:
            template_margin = float(top0) - float(top1)
        except Exception:
            template_margin = None
    exclude_keys = {
        'seqname',
        'flat_seq',
        'score',
        'alignment',
        'rerank_score',
        'metadata_bonus',
        'anticodon_match',
        'isotype_match',
        'template_isotype',
        'template_anticodon',
        'family_match_rank',
        'family_match_label',
        'family_rescue_selected',
        'family_rescue_candidate',
        'rank',
    }
    template_full_numbering = [k for k in best.keys() if k not in exclude_keys]
    template_full_seq = ''.join((best.get(k, '') or '') for k in template_full_numbering)

    # 2) 模板分区
    template_regions = group_by_region(best)

    # 3) 组装 & 修补尾巴
    result: Dict[str, Any] = {
        'template_name': template_name,
        'template_flat_seq': template_flat_seq,
        'template_score': template_score,
        'template_margin': template_margin,
        'template_isotype': str(best.get("template_isotype", "") or ""),
        'template_anticodon': str(best.get("template_anticodon", "") or ""),
        'template_family_match_rank': int(best.get("family_match_rank", 0) or 0),
        'template_family_match_label': str(best.get("family_match_label", "none") or "none"),
        'template_family_rescue_selected': bool(best.get("family_rescue_selected", False)),
        'template_candidates': template_candidates,
        'template_full_numbering': template_full_numbering,
        'template_full_seq': template_full_seq,
        'raw_input_seq': raw_input,
        'annotation_meta': {},
        'regions': {}
    }

    empty_regions = []
    warnings: list[str] = []
    if not trnascan_hit:
        warnings.append(
            "tRNAscan-SE did not detect a tRNA hit for this sequence; "
            "structure may be non-canonical or the tool is unavailable."
        )
    for region in REGIONS:
        tpl = template_regions.get(region, {})
        inp_seq = _norm_u(input_annot.get(region, ""))
        struct_key = STRUCT_KEYS.get(region, "")
        inp_struct = (input_annot.get(struct_key, "") or "") if struct_key else ""

        if region == "Aminoacyl arm 3' end":
            # 只有完整 CCA 才并入 3′ 端区域。
            inp_seq_patched = _patch_tail_cca(
                raw_input,
                inp_seq,
                allow_terminal_cca=bool(
                    cca_assignment_allowed
                ),
            )

            # 可选：若模板编号包含 74/75/76，给个提示方便排查
            tnums = tpl.get('numbering', []) or []
            if any(n in tnums for n in ("74", "75", "76")) and inp_seq_patched != inp_seq:
                logger.info("Patched 3' tail for region %s: %r -> %r", region, inp_seq, inp_seq_patched)

            inp_seq = inp_seq_patched

        if not inp_seq:
            empty_regions.append(region)

        result['regions'][region] = {
            'input_seq': inp_seq,
            'input_struct': inp_struct,
            'template_numbering': tpl.get('numbering', []),
            'template_seq': tpl.get('sequence', '')
        }

    result['annotation_meta'] = {
        'empty_regions': empty_regions,
        'empty_count': len(empty_regions),
        'warnings': warnings,
        'trnascan_hit': trnascan_hit,
        'trnascan_isotype': str(input_annot.get("tRNAscanIsotype", "") or ""),
        'trnascan_anticodon': _norm_u(str(input_annot.get("tRNAscanAnticodon", "") or "")),
        'trnascan_anticodon_start': _safe_int(input_annot.get("tRNAscanAnticodonStart", 0), 0),
        'trnascan_anticodon_end': _safe_int(input_annot.get("tRNAscanAnticodonEnd", 0), 0),
        'trnascan_search_mode': str(input_annot.get("tRNAscanSearchMode", "G") or "G"),
        'trnascan_domain_hint': str(input_annot.get("tRNAscanDomainHint", domain_hint) or domain_hint),
        'trnascan_mitochondrial_subtype': str(
            input_annot.get("tRNAscanMitochondrialSubtype", mitochondrial_subtype)
            or mitochondrial_subtype
        ),
        'trnascan_command_options': list(input_annot.get("tRNAscanCommandOptions", []) or []),
        'trnascan_executable': str(input_annot.get("tRNAscanExecutable", "") or ""),
        'trnascan_version': str(input_annot.get("tRNAscanVersion", "") or ""),
        'annotation_source': str(input_annot.get("UsedModel", "") or ""),
        'segmentation_status': str(input_annot.get("SegmentationStatus", "") or ""),
        'segmentation_errors': str(input_annot.get("SegmentationErrors", "") or ""),
        # Keep full-length structure/sequence for optional cross-region boundary refinement.
        'secondary_structure': str(input_annot.get("SecondaryStructure", "") or ""),
        'struct_file_sequence': _norm_u(str(input_annot.get("StructFileSequence", "") or "")),
        'input_evidence': input_evidence,
    }
    return result

if __name__ == '__main__':
    # 示例测试
    test_seq = "AAAAAAUUAGUUUAAUCAAAAACCUUAGUAUGUCAAACUAAAAAAAUUAGAUCAUCUAAUAUUUUUUACCA"
    info = compare_input_template(test_seq)
    if not info:
        print("No match or annotation failed.")
    else:
        print(f"Template: {info['template_name']}")
        print(f"Template flat seq: {info['template_flat_seq']}\n")
        for region, data in info['regions'].items():
            print(f"{region}:")
            print(f"  Input seq           : {data['input_seq']}")
            print(f"  Template numbering  : {data['template_numbering']}")
            print(f"  Template seq        : {data['template_seq']}\n")
