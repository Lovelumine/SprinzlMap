from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping

from ..alignment_types import AlignmentBlock


def fix_cross_block_insertion(
    prev_blk: AlignmentBlock,
    cur_blk: AlignmentBlock,
) -> Dict[str, Any] | None:
    prev_nums, prev_templ, prev_targ = prev_blk["lines"]
    cur_nums, cur_templ, cur_targ = cur_blk["lines"]
    if not prev_nums or not cur_nums:
        return

    prev_base = None
    for n in reversed(prev_nums):
        if not n or n.startswith("-") or n.startswith("V") or "i" in n:
            continue
        prev_base = n
        break
    if not prev_base or not prev_base.isdigit():
        return

    tail_start = len(prev_nums)
    while tail_start > 0 and prev_nums[tail_start - 1].startswith(prev_base + "i"):
        tail_start -= 1
    if tail_start >= len(prev_nums):
        return

    ins_bases = list(prev_targ[tail_start:])
    if not ins_bases:
        return

    cur_base = None
    cur_base_idx = None
    for i, n in enumerate(cur_nums):
        if not n or n.startswith("-") or n.startswith("V") or "i" in n:
            continue
        cur_base = n
        cur_base_idx = i
        break
    if not cur_base or not cur_base.isdigit() or cur_base_idx is None:
        return

    if int(cur_base) != int(prev_base) + 1:
        return
    if cur_targ[cur_base_idx] != "-":
        return

    ins1 = ins_bases[0]
    if not ins1 or ins1 == "-":
        return
    templ_base = str(cur_templ[cur_base_idx] or "").upper()
    if not templ_base or templ_base == "-":
        return

    # Regional DP can express a single substitution across a partition boundary
    # as an insertion after N plus a deletion at N+1. Sprinzl homology does not
    # require the target residue to match the template base, so prefer the
    # occupied canonical coordinate over that unsupported two-event explanation.
    del prev_nums[tail_start:]
    del prev_templ[tail_start:]
    del prev_targ[tail_start:]

    cur_targ[cur_base_idx] = ins1
    remain = ins_bases[1:]
    if remain:
        insert_pos = cur_base_idx + 1
        for base in remain:
            cur_nums.insert(insert_pos, f"{cur_base}i1")
            cur_templ.insert(insert_pos, "-")
            cur_targ.insert(insert_pos, base)
            insert_pos += 1

    k = cur_base_idx + 1
    ins_order = 1
    while k < len(cur_nums) and str(cur_nums[k]).startswith(cur_base + "i"):
        cur_nums[k] = f"{cur_base}i{ins_order}"
        ins_order += 1
        k += 1

    return {
        "rule": "cross_block_canonical_successor_fill_v1",
        "from_label": f"{prev_base}i1",
        "to_label": cur_base,
        "remaining_insertion_count": len(remain),
        "template_substitution": templ_base != str(ins1).upper(),
    }


def normalize_insertions_in_block(block: AlignmentBlock) -> None:
    nums, templ, targ = block["lines"]
    i = 0
    while i < len(nums):
        n = nums[i]
        if not n or n.startswith("-") or n.startswith("V") or "i" in n:
            i += 1
            continue
        base = n
        j = i + 1
        while j < len(nums) and nums[j].startswith(base + "i"):
            j += 1
        if j == i + 1:
            i += 1
            continue
        ins_idx = i + 1
        ins_base = targ[ins_idx]
        if ins_base == "-":
            i += 1
            continue
        if targ[i] == "-":
            targ[i] = ins_base
            del nums[ins_idx]
            del templ[ins_idx]
            del targ[ins_idx]
            k = i + 1
            while k < len(nums) and nums[k].startswith(base + "i"):
                suffix = nums[k][len(base) + 1 :]
                if suffix.isdigit():
                    new_i = int(suffix) - 1
                    nums[k] = f"{base}i{new_i}"
                k += 1
            continue

        next_idx = None
        for k in range(j, len(nums)):
            nn = nums[k]
            if not nn or nn.startswith("-") or nn.startswith("V") or "i" in nn:
                continue
            next_idx = k
            break
        if next_idx is not None and targ[next_idx] == "-" and templ[next_idx].upper() == ins_base.upper():
            targ[next_idx] = ins_base
            del nums[ins_idx]
            del templ[ins_idx]
            del targ[ins_idx]
            if ins_idx < next_idx:
                next_idx -= 1
            new_base = nums[next_idx]
            k = i + 1
            new_i = 1
            while k < len(nums) and nums[k].startswith(base + "i"):
                nums[k] = f"{new_base}i{new_i}"
                new_i += 1
                k += 1
        i += 1


def normalize_blocks_with_rollback(
    *,
    blocks: List[AlignmentBlock],
    target_seq: str,
    validate_alignment_fn: Callable[[str, List[str]], bool],
) -> bool:
    snapshot = [
        [list(blk["lines"][0]), list(blk["lines"][1]), list(blk["lines"][2])]
        for blk in blocks
    ]

    pending_repairs: List[tuple[AlignmentBlock, Dict[str, Any]]] = []
    if len(blocks) > 1:
        for i in range(1, len(blocks)):
            repair = fix_cross_block_insertion(blocks[i - 1], blocks[i])
            if repair is not None:
                pending_repairs.append((blocks[i], repair))

    for blk in blocks:
        normalize_insertions_in_block(blk)

    post_target: List[str] = []
    for blk in blocks:
        post_target.extend(blk["lines"][2])
    if validate_alignment_fn(target_seq, post_target):
        for block, repair in pending_repairs:
            meta = block.setdefault("meta", {})
            audit = list(meta.get("deterministic_boundary_repairs") or [])
            audit.append(repair)
            meta["deterministic_boundary_repairs"] = audit
        return True

    for blk, snap in zip(blocks, snapshot):
        blk["lines"] = [list(snap[0]), list(snap[1]), list(snap[2])]
    return False


def maybe_reanchor_anticodon_slots(
    *,
    num_row: List[str],
    seq_row: List[str],
    ann_meta: Mapping[str, Any],
    use_llm: bool,
    target_seq: str,
    validate_alignment_fn: Callable[[str, List[str]], bool],
    norm_rna_token_fn: Callable[[str], str],
) -> bool:
    if not use_llm:
        return False

    def _compact_insertions_for_base(base_label: str, base_idx: int) -> None:
        ins_positions: List[int] = []
        k = base_idx + 1
        while k < len(num_row):
            label = str(num_row[k] or "")
            if not label.startswith(base_label + "i"):
                break
            suffix = label[len(base_label) + 1 :]
            if not suffix.isdigit():
                break
            ins_positions.append(k)
            k += 1
        if not ins_positions:
            return

        residues = [seq_row[pos] for pos in ins_positions if seq_row[pos] != "-"]
        for i, pos in enumerate(ins_positions, start=1):
            num_row[pos] = f"{base_label}i{i}"
            seq_row[pos] = residues[i - 1] if i <= len(residues) else "-"

    expected = norm_rna_token_fn(str(ann_meta.get("trnascan_anticodon", "") or ""))
    if len(expected) != 3:
        return False

    idx34 = next((i for i, n in enumerate(num_row) if n == "34"), None)
    idx35 = next((i for i, n in enumerate(num_row) if n == "35"), None)
    idx36 = next((i for i, n in enumerate(num_row) if n == "36"), None)
    if idx34 is None or idx35 is None or idx36 is None:
        return False
    if not (idx34 < idx35 < idx36):
        return False
    if seq_row[idx34] != "-":
        return False

    idx36i1 = None
    k = idx36 + 1
    while k < len(num_row):
        label = str(num_row[k] or "")
        if not label.startswith("36i"):
            break
        suffix = label[len("36i") :]
        if suffix.isdigit() and int(suffix) == 1:
            idx36i1 = k
            break
        k += 1
    if idx36i1 is None:
        return False

    c35 = norm_rna_token_fn(seq_row[idx35])
    c36 = norm_rna_token_fn(seq_row[idx36])
    c36i1 = norm_rna_token_fn(seq_row[idx36i1])
    if len(c35) != 1 or len(c36) != 1 or len(c36i1) != 1:
        return False
    if c35 + c36 + c36i1 != expected:
        return False

    backup = list(seq_row)
    seq_row[idx34] = seq_row[idx35]
    seq_row[idx35] = seq_row[idx36]
    seq_row[idx36] = seq_row[idx36i1]
    seq_row[idx36i1] = "-"
    _compact_insertions_for_base("36", idx36)

    if not validate_alignment_fn(target_seq, seq_row):
        seq_row[:] = backup
        return False
    return True


def compact_and_prune_insertion_columns(
    num_row: List[str],
    seq_row: List[str],
    template_row: Optional[List[str]] = None,
) -> bool:
    changed = False
    drop_idx: List[int] = []

    i = 0
    while i < len(num_row):
        match = re.match(r"^(.+?)i(\d+)$", str(num_row[i] or ""))
        if not match:
            i += 1
            continue

        parent = match.group(1)
        j = i
        seg_positions: List[int] = []
        while j < len(num_row):
            child_match = re.match(rf"^{re.escape(parent)}i(\d+)$", str(num_row[j] or ""))
            if not child_match:
                break
            seg_positions.append(j)
            j += 1

        residues = [seq_row[pos] for pos in seg_positions if seq_row[pos] != "-"]
        for rank, pos in enumerate(seg_positions, start=1):
            desired_label = f"{parent}i{rank}"
            desired_base = residues[rank - 1] if rank <= len(residues) else "-"
            if num_row[pos] != desired_label:
                num_row[pos] = desired_label
                changed = True
            if seq_row[pos] != desired_base:
                seq_row[pos] = desired_base
                changed = True
            if rank > len(residues):
                drop_idx.append(pos)

        i = j

    if drop_idx:
        for pos in reversed(drop_idx):
            del num_row[pos]
            del seq_row[pos]
            if template_row is not None:
                del template_row[pos]
        changed = True
    return changed
