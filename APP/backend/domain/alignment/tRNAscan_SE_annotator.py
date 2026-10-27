import argparse
import os
import subprocess
import sys
import tempfile
import shutil
import re
import threading
from glob import glob
from pathlib import Path
from backend.domain.common.trnascan_runtime import (
    TRNASCAN_SENSITIVE_OPTIONS,
    resolve_trnascan_search_mode,
    should_use_wsl as _should_use_wsl,
    trnascan_runtime_provenance,
    windows_to_wsl_path,
)
from .debug_config import (
    DEBUG_ANNOTATE as _DEBUG_ANNOTATE,
    DEBUG_ANNOTATE_FULL as _DEBUG_ANNOTATE_FULL,
    DEBUG_ANNOTATE_SUMMARY as _DEBUG_ANNOTATE_SUMMARY,
)
from .runtime_helpers import runtime_bool, runtime_float, runtime_str

# ==================== Utils: logging ====================

def _dprint(msg: str):
    if _DEBUG_ANNOTATE:
        print(msg)

def _log(seq_id: str, model: str, step: str, status: str, detail: str = ""):
    """
    统一、单行、易 grep 的日志格式
    step: predict | segment | choose
    status: START | OK | FAIL | SKIP | FIX
    """
    # 默认只输出失败信息；详细过程需显式打开 TRNAALIGN_DEBUG_ANNOTATE
    if not (_DEBUG_ANNOTATE or status in ("FAIL", "ERROR")):
        return
    if detail:
        print(f"[SEQ:{seq_id}][MODEL:{model}][STEP:{step}] {status} | {detail}")
    else:
        print(f"[SEQ:{seq_id}][MODEL:{model}][STEP:{step}] {status}")

# ==================== Import extracters ====================

from .extract import (
    extract_amino_acid_arm,
    extract_d_loop,
    extract_anticodon_arm,
    extract_variable_loop,
    extract_t_arm
)

# ==================== Paths & OS helpers ====================
def _build_trnascan_cmd(
    fasta_path: str,
    results_path: str,
    struct_path: str,
    *,
    domain_hint: str = "unknown",
    mitochondrial_subtype: str = "unknown",
) -> list:
    use_wsl = _should_use_wsl()
    if use_wsl:
        fasta_exec = windows_to_wsl_path(fasta_path)
        results_exec = windows_to_wsl_path(results_path)
        struct_exec = windows_to_wsl_path(struct_path)
    else:
        fasta_exec = fasta_path
        results_exec = results_path
        struct_exec = struct_path

    # Keep CLI compatible with tRNAscan-SE v2.x:
    # -z <N> and -X <score> are the stable forms.
    search_mode = resolve_trnascan_search_mode(domain_hint, mitochondrial_subtype)
    cmd = [
        "tRNAscan-SE",
        *TRNASCAN_SENSITIVE_OPTIONS,
        *search_mode.args,
        "-Q",
        fasta_exec, "-o", results_exec, "-f", struct_exec
    ]
    return (["wsl"] + cmd) if use_wsl else cmd

# ==================== (NEW) Length check helpers ====================

def _len_norm(s: str) -> int:
    """
    仅用于长度比较：去掉空白字符，其余字符（含大小写 A/C/G/T/U）都计入长度。
    不做 T<->U 替换，因为长度与碱基类别无关。
    """
    if s is None:
        return 0
    if not isinstance(s, str):
        s = str(s)
    return len("".join(ch for ch in s if not ch.isspace()))

def _norm_TU(s: str) -> str:
    """比较用途：统一大小写，并把 U 视为 T。"""
    return (s or "").upper().replace("U", "T")

def _try_pad_trnascan_struct(seq_id: str, struct_seq: str, struct_str: str, original_seq: str):
    """
    如果 tRNAscan-SE 的 StructSeq 是 OriginalSequence 的“子串”，
    则在左右用原序列的碱基补齐，结构用 '.' 填充到与原序列等长。
    返回: (fixed_seq, fixed_str, fixed: bool, detail_msg: str)
    """
    orig_norm = _norm_TU(original_seq)
    sseq_norm = _norm_TU(struct_seq)

    # 仅在“明确是子串”时修复；否则不动
    idx = orig_norm.find(sseq_norm)
    if idx == -1:
        return struct_seq, struct_str, False, "substring not found, skip pad"

    left_pad  = idx
    right_pad = len(original_seq) - (idx + len(struct_seq))

    if left_pad < 0 or right_pad < 0:
        return struct_seq, struct_str, False, "computed pad < 0, skip pad"

    fixed_seq = original_seq  # 直接用原序列作为补齐后的 StructFileSequence
    fixed_str = "." * left_pad + struct_str + "." * right_pad

    # 打印修复细节（包含左右补齐的碱基）
    left_bases  = original_seq[:left_pad]
    right_bases = original_seq[-right_pad:] if right_pad > 0 else ""
    detail = (f"pad tRNAscan-SE output to original: left={left_pad} ('{left_bases}'), "
              f"right={right_pad} ('{right_bases}'); struct_len {len(struct_seq)} -> {len(fixed_seq)}")
    _log(seq_id, "tRNAscan-SE", "segment", "FIX", detail)

    return fixed_seq, fixed_str, True, detail

# ==================== tRNAscan-SE parse helpers ====================

def parse_results_file(results_file: str) -> dict:
    results = {}
    if not os.path.exists(results_file):
        return results
    with open(results_file, "r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            # Typical columns:
            # Name tRNA# Begin End Type Codon IntronBegin IntronEnd Score [Note]
            if len(parts) < 6:
                continue
            seq_id = parts[0]
            isotype = parts[4]
            results[seq_id] = isotype
    return results


def _parse_results_details_file(results_file: str) -> dict:
    details = {}
    if not os.path.exists(results_file):
        return details
    with open(results_file, "r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("Sequence") or line.startswith("--------"):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            seq_id = parts[0]
            isotype = parts[4]
            anticodon = parts[5]
            try:
                anti_begin = int(parts[6])
            except Exception:
                anti_begin = 0
            try:
                anti_end = int(parts[7])
            except Exception:
                anti_end = 0
            details[seq_id] = {
                "Isotype": isotype,
                "Anticodon": anticodon,
                "AnticodonStart": anti_begin,
                "AnticodonEnd": anti_end,
            }
    return details

def parse_struct_file(struct_file: str) -> dict:
    def clean_id(raw_id: str) -> str:
        if "(" in raw_id:
            raw_id = raw_id.split("(")[0].strip()
        if ".trna" in raw_id:
            raw_id = raw_id.split(".trna")[0].strip()
        return raw_id

    structures = {}
    if not os.path.exists(struct_file):
        return structures

    current_id = None
    current_iso = ""
    current_anticodon = ""
    current_anticodon_start = 0
    current_anticodon_end = 0
    current_str = ""
    current_seq = ""

    with open(struct_file, "r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                if current_id is not None:
                    structures[current_id] = {
                        "Isotype": current_iso,
                        "Anticodon": current_anticodon,
                        "AnticodonStart": current_anticodon_start,
                        "AnticodonEnd": current_anticodon_end,
                        "Structure": current_str,
                        "StructSeq": current_seq
                    }
                    current_id = None
                    current_iso = ""
                    current_anticodon = ""
                    current_anticodon_start = 0
                    current_anticodon_end = 0
                    current_str = ""
                    current_seq = ""
                continue

            if (
                line.startswith(">")
                or ".trna" in line
                or line.startswith("URS")
                or line.startswith("Listeria")
                or line.startswith("PY")
            ):
                if current_id is not None:
                    structures[current_id] = {
                        "Isotype": current_iso,
                        "Anticodon": current_anticodon,
                        "AnticodonStart": current_anticodon_start,
                        "AnticodonEnd": current_anticodon_end,
                        "Structure": current_str,
                        "StructSeq": current_seq
                    }
                raw_id = line[1:] if line.startswith(">") else line
                truncated = clean_id(raw_id.split()[0])
                current_id = truncated
                current_iso = ""
                current_anticodon = ""
                current_anticodon_start = 0
                current_anticodon_end = 0
                current_str = ""
                current_seq = ""
                continue

            if line.startswith("Type:"):
                m_iso = re.search(r"Type:\s*([A-Za-z0-9_+\-]+)", line)
                if m_iso:
                    current_iso = m_iso.group(1)
                m_anti = re.search(r"Anticodon:\s*([A-Za-z]+)\s+at\s+(-?\d+)-(-?\d+)", line)
                if m_anti:
                    current_anticodon = m_anti.group(1)
                    try:
                        current_anticodon_start = int(m_anti.group(2))
                    except Exception:
                        current_anticodon_start = 0
                    try:
                        current_anticodon_end = int(m_anti.group(3))
                    except Exception:
                        current_anticodon_end = 0
            elif line.startswith("Str:"):
                current_str = line[4:].strip()
            elif line.startswith("Seq:"):
                current_seq = line[4:].strip()

        if current_id is not None:
            structures[current_id] = {
                "Isotype": current_iso,
                "Anticodon": current_anticodon,
                "AnticodonStart": current_anticodon_start,
                "AnticodonEnd": current_anticodon_end,
                "Structure": current_str,
                "StructSeq": current_seq
            }

    return structures

# ==================== RNAfold helpers ====================

def _dotbracket_to_angles(dot: str) -> str:
    return dot.replace("(", ">").replace(")", "<")


def _run_rnafold_python(seq_id: str, seq_str: str) -> tuple[str, str]:
    try:
        import RNA  # type: ignore[import-not-found]
    except Exception as exc:
        _log(seq_id, "RNAfold", "predict", "FAIL", f"RNAfold CLI missing and Python RNA unavailable: {exc}")
        return "", ""
    try:
        dotbracket, _energy = RNA.fold(seq_str)
    except Exception as exc:
        _log(seq_id, "RNAfold", "predict", "FAIL", f"Python RNA.fold failed: {exc}")
        return "", ""
    if not dotbracket:
        _log(seq_id, "RNAfold", "predict", "FAIL", "Python RNA.fold returned empty structure")
        return "", ""
    _log(seq_id, "RNAfold", "predict", "OK", "Python ViennaRNA fallback")
    return seq_str, _dotbracket_to_angles(dotbracket)


def _run_rnafold(seq_id: str, seq_str: str) -> tuple[str, str]:
    model = "RNAfold"
    _log(seq_id, model, "predict", "START")
    try:
        proc = subprocess.run(
            ["RNAfold", "--noPS"],
            input=(seq_str + "\n"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
    except FileNotFoundError:
        return _run_rnafold_python(seq_id, seq_str)
    except subprocess.TimeoutExpired:
        _log(seq_id, model, "predict", "FAIL", "RNAfold 超时")
        return "", ""

    if proc.returncode != 0:
        _log(seq_id, model, "predict", "FAIL", proc.stderr.strip()[:200])
        return "", ""

    out = proc.stdout.strip().splitlines()
    if len(out) < 2:
        _log(seq_id, model, "predict", "FAIL", f"输出异常: {out}")
        return "", ""

    struct_seq = out[0].strip()
    dot_line = out[1].strip()
    dotbracket = dot_line.split()[0]
    angle_struct = _dotbracket_to_angles(dotbracket)
    _log(seq_id, model, "predict", "OK")
    return struct_seq, angle_struct

# ==================== BPfold helpers ====================

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_BPFOLD_CHECKPOINT_DIR = runtime_str("BPFOLD_CHECKPOINT_DIR", str(_PROJECT_ROOT / "research" / "model_predict"))
_BPFOLD_TIMEOUT = runtime_float("BPFOLD_TIMEOUT", 20.0, 0.1, 3600.0)
_TRNASCAN_TIMEOUT = runtime_float("TRNASCAN_TIMEOUT", 900.0, 5.0, 3600.0)
_BPFOLD_BIN = shutil.which("BPfold")
_AYLM_2D_ENABLE = runtime_bool("AYLM_2D_ENABLE", False)
_AYLM_2D_ROOT = Path(runtime_str("AYLM_2D_ROOT", str(_PROJECT_ROOT.parent / "AYLMv1.2")))
_AYLM_2D_CHECKPOINT = Path(
    runtime_str(
        "AYLM_2D_CHECKPOINT",
        str(
            _AYLM_2D_ROOT
            / "downstream"
            / "secondstructure"
            / "reports"
            / "full_aylm_nonmito_quality_priority_v1"
            / "best_pairwise_head.pth"
        ),
    )
)
_AYLM_2D_DEVICE = runtime_str("AYLM_2D_DEVICE", "cpu")
_AYLM_2D_LOCK = threading.Lock()
_AYLM_2D_RUNTIME = None


def _load_aylm_2d_runtime():
    global _AYLM_2D_RUNTIME
    if _AYLM_2D_RUNTIME is not None:
        return _AYLM_2D_RUNTIME
    if not _AYLM_2D_ENABLE:
        _AYLM_2D_RUNTIME = {"enabled": False, "error": "disabled"}
        return _AYLM_2D_RUNTIME
    with _AYLM_2D_LOCK:
        if _AYLM_2D_RUNTIME is not None:
            return _AYLM_2D_RUNTIME
        try:
            if not _AYLM_2D_ROOT.is_dir():
                raise FileNotFoundError(f"AYLM root missing: {_AYLM_2D_ROOT}")
            if not _AYLM_2D_CHECKPOINT.is_file():
                raise FileNotFoundError(f"AYLM checkpoint missing: {_AYLM_2D_CHECKPOINT}")
            if str(_AYLM_2D_ROOT) not in sys.path:
                sys.path.insert(0, str(_AYLM_2D_ROOT))
            import torch  # type: ignore
            from downstream.secondstructure.scripts.train.train_pairwise_head import build_models  # type: ignore
            from downstream.secondstructure.src.dataset import encode_sequence_tokens  # type: ignore
            from downstream.secondstructure.src.structure_utils import (  # type: ignore
                decode_nussinov_style,
                normalize_sequence,
                pairs_to_dbn,
            )

            checkpoint = torch.load(_AYLM_2D_CHECKPOINT, map_location="cpu", weights_only=False)
            train_args = argparse.Namespace(**checkpoint["args"])
            train_args.device = _AYLM_2D_DEVICE
            device = torch.device(_AYLM_2D_DEVICE)

            encoder, head = build_models(train_args, device)
            encoder.load_state_dict(checkpoint["encoder"])
            head.load_state_dict(checkpoint["head"])
            encoder.eval()
            head.eval()

            _AYLM_2D_RUNTIME = {
                "enabled": True,
                "torch": torch,
                "device": device,
                "encoder": encoder,
                "head": head,
                "encode_sequence_tokens": encode_sequence_tokens,
                "normalize_sequence": normalize_sequence,
                "decode_nussinov_style": decode_nussinov_style,
                "pairs_to_dbn": pairs_to_dbn,
            }
        except Exception as exc:
            _AYLM_2D_RUNTIME = {"enabled": False, "error": str(exc)}
        return _AYLM_2D_RUNTIME


def _run_aylm_secondstructure(seq_id: str, seq_str: str) -> tuple[str, str]:
    model = "AYLM-2D"
    _log(seq_id, model, "predict", "START")
    runtime = _load_aylm_2d_runtime()
    if not runtime or not runtime.get("enabled"):
        detail = str((runtime or {}).get("error", "runtime unavailable"))
        if detail == "disabled":
            return "", ""
        _log(seq_id, model, "predict", "FAIL", detail[:200])
        return "", ""
    try:
        torch = runtime["torch"]
        normalize_sequence = runtime["normalize_sequence"]
        encode_sequence_tokens = runtime["encode_sequence_tokens"]
        decode_nussinov_style = runtime["decode_nussinov_style"]
        pairs_to_dbn = runtime["pairs_to_dbn"]
        device = runtime["device"]
        encoder = runtime["encoder"]
        head = runtime["head"]

        seq_norm = normalize_sequence(seq_str)
        token_ids = torch.tensor([encode_sequence_tokens(seq_norm)], dtype=torch.long, device=device)
        with torch.no_grad():
            encoded = encoder(token_ids)
            logits = head(encoded)
        score_matrix = logits[0, : len(seq_norm), : len(seq_norm)].detach().cpu().numpy()
        pred_pairs = decode_nussinov_style(score_matrix, seq_norm, min_loop_length=3, canonical_only=True)
        dotbracket = pairs_to_dbn(len(seq_norm), pred_pairs)
        angle_struct = _dotbracket_to_angles(dotbracket)
        _log(seq_id, model, "predict", "OK")
        return seq_norm, angle_struct
    except Exception as exc:
        _log(seq_id, model, "predict", "FAIL", str(exc)[:200])
        return "", ""

def _pick_first_dbn_under(path_like: str) -> str:
    if os.path.isdir(path_like):
        candidates = sorted(glob(os.path.join(path_like, "*.dbn")))
        return candidates[0] if candidates else ""
    if os.path.isfile(path_like):
        if path_like.lower().endswith(".dbn"):
            return path_like
        candidates = sorted(glob(os.path.join(os.path.dirname(path_like), "*.dbn")))
        return candidates[0] if candidates else ""
    return ""

def _parse_bpfold_dbn(dbn_path: str) -> tuple[str, str]:
    real_file = _pick_first_dbn_under(dbn_path)
    if not real_file:
        return "", ""
    try:
        with open(real_file, "r", encoding="utf-8") as fin:
            lines = [ln.strip() for ln in fin if ln.strip()]
        if not lines:
            return "", ""
        if lines[0].startswith(">") and len(lines) >= 3:
            seq = lines[1]
            dot = lines[2].split()[0]
        else:
            if len(lines) >= 2 and not lines[0].startswith(">"):
                seq = lines[0]
                dot = lines[1].split()[0]
            else:
                return "", ""
        return seq, _dotbracket_to_angles(dot)
    except Exception:
        return "", ""

def _run_bpfold_single(seq_id: str, seq_str: str, out_root: str) -> tuple[str, str]:
    model = "BPfold"
    _log(seq_id, model, "predict", "START")
    if not _BPFOLD_BIN:
        return "", ""
    if not os.path.isdir(_BPFOLD_CHECKPOINT_DIR):
        _log(seq_id, model, "predict", "FAIL", f"checkpoint dir missing: {_BPFOLD_CHECKPOINT_DIR}")
        return "", ""
    os.makedirs(out_root, exist_ok=True)
    tmp_fa = os.path.join(out_root, f"{seq_id}.fa")
    with open(tmp_fa, "w", encoding="utf-8") as f:
        f.write(f">{seq_id}\n{seq_str}\n")

    cmd = [
        _BPFOLD_BIN,
        "-c", _BPFOLD_CHECKPOINT_DIR,
        "-i", tmp_fa,
        "-o", out_root,
        "--out_type", "dbn"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_BPFOLD_TIMEOUT)
    except FileNotFoundError:
        _log(seq_id, model, "predict", "FAIL", "未找到 BPfold 可执行文件")
        return "", ""
    except subprocess.TimeoutExpired:
        _log(seq_id, model, "predict", "FAIL", f"BPfold timeout after {_BPFOLD_TIMEOUT}s")
        return "", ""
    except Exception as e:
        _log(seq_id, model, "predict", "FAIL", f"异常: {e}")
        return "", ""

    if proc.returncode != 0:
        msg = (proc.stderr or "").strip()
        _log(seq_id, model, "predict", "FAIL", msg[:200])
        return "", ""

    candidate = os.path.join(out_root, f"{seq_id}.dbn")
    if os.path.exists(candidate):
        seq, ang = _parse_bpfold_dbn(candidate)
        _log(seq_id, model, "predict", "OK" if (seq and ang) else "FAIL", "" if (seq and ang) else "解析 .dbn 失败")
        return seq, ang

    for p in sorted(glob(os.path.join(out_root, f"{seq_id}*.dbn"))):
        seq, ang = _parse_bpfold_dbn(p)
        if seq and ang:
            _log(seq_id, model, "predict", "OK")
            return seq, ang

    for p in sorted(glob(os.path.join(out_root, "**"), recursive=True)):
        if (os.path.isdir(p) and p.lower().endswith(".dbn")) or (os.path.isfile(p) and p.lower().endswith(".dbn")):
            seq, ang = _parse_bpfold_dbn(p)
            if seq and ang:
                _log(seq_id, model, "predict", "OK")
                return seq, ang

    _log(seq_id, model, "predict", "FAIL", "未找到可解析的 .dbn")
    return "", ""

# ==================== Segmentation ====================

def _segment_with_structure(seq_id: str, used_model: str, iso_hint: str, struct_seq: str, struct_str: str, original_seq: str):
    """
    尝试分段；成功返回 (entry, True, "")；失败返回 (entry, False, "原因;原因...")
    失败也会产出 entry（分段字段为空，SegmentationStatus=failed）
    """
    _log(seq_id, used_model, "segment", "START")

    # ---------- (MODIFIED) 长度一致性：若 tRNAscan-SE 输出为子串，先尝试补齐 ----------
    len_struct = _len_norm(struct_seq)
    len_orig   = _len_norm(original_seq)
    if len_struct != len_orig:
        if used_model == "tRNAscan-SE":
            struct_seq_fixed, struct_str_fixed, fixed, _detail = _try_pad_trnascan_struct(
                seq_id, struct_seq, struct_str, original_seq
            )
            if fixed:
                struct_seq = struct_seq_fixed
                struct_str = struct_str_fixed
                len_struct = _len_norm(struct_seq)
                len_orig   = _len_norm(original_seq)
        # 再次检查；若仍不等，则判失败并顺延
        if len_struct != len_orig:
            entry = {
                "SequenceID": seq_id,
                "Isotype": iso_hint,
                "SecondaryStructure": struct_str,
                "StructFileSequence": struct_seq,
                "OriginalSequence": original_seq,
                "UsedModel": used_model,

                "Aminoacyl arm 5' end": "",
                "AA_Arm_5prime_Struct": "",
                "Aminoacyl arm 3' end": "",
                "AA_Arm_3prime_Struct": "",
                "D loop + D stem": "",
                "D_Loop_Struct": "",
                "Anticodon loop + Anticodon stem": "",
                "Anticodon_Arm_Struct": "",
                "Variable loop": "",
                "Variable_Loop_Struct": "",
                "T loop + T stem": "",
                "T_Arm_Struct": "",

                "SegmentationStatus": "failed",
                "SegmentationErrors": f"length mismatch (orig={len_orig}, struct={len_struct})",
            }
            _log(seq_id, used_model, "segment", "FAIL", entry["SegmentationErrors"])
            return entry, False, entry["SegmentationErrors"]
    # ---------- (MODIFIED) 结束 ----------

    seg_ok = True
    seg_errs = []

    # 默认空值
    aa_arm_a_seq = aa_arm_b_seq = aa_arm_a_struct = aa_arm_b_struct = ""
    d_loop_seq = d_loop_struct = ""
    anticodon_arm_seq = anticodon_arm_struct = ""
    var_loop_seq = var_loop_struct = ""
    t_arm_seq = t_arm_struct = ""

    aa_arms = extract_amino_acid_arm(struct_seq, struct_str)
    if aa_arms is None:
        seg_ok = False
        seg_errs.append("aminoacyl arms not found (need '<' or '>')")
    else:
        aa_arm_a_seq, aa_arm_b_seq, aa_arm_a_struct, aa_arm_b_struct = aa_arms

    d_loop = extract_d_loop(struct_seq, struct_str)
    if d_loop is None:
        seg_ok = False
        seg_errs.append("D loop failed")
    else:
        d_loop_seq, d_loop_struct = d_loop

    anticodon_arm = extract_anticodon_arm(struct_seq, struct_str)
    if anticodon_arm is None:
        seg_ok = False
        seg_errs.append("anticodon arm failed")
    else:
        anticodon_arm_seq, anticodon_arm_struct = anticodon_arm

    variable_loop = extract_variable_loop(struct_seq, struct_str)
    if variable_loop is None:
        seg_ok = False
        seg_errs.append("variable loop failed")
    else:
        var_loop_seq, var_loop_struct = variable_loop

    t_arm = extract_t_arm(struct_seq, struct_str)
    if t_arm is None:
        seg_ok = False
        seg_errs.append("T arm failed")
    else:
        t_arm_seq, t_arm_struct = t_arm

    if seg_ok:
        total_len = (
            len(aa_arm_a_seq) +
            len(aa_arm_b_seq) +
            len(d_loop_seq) +
            len(anticodon_arm_seq) +
            len(var_loop_seq) +
            len(t_arm_seq)
        )
        if total_len != len(struct_seq):
            seg_ok = False
            seg_errs.append(f"length mismatch {total_len}!={len(struct_seq)}")

    entry = {
        "SequenceID": seq_id,
        "Isotype": iso_hint,
        "SecondaryStructure": struct_str,
        "StructFileSequence": struct_seq,
        "OriginalSequence": original_seq,
        "UsedModel": used_model,

        "Aminoacyl arm 5' end": aa_arm_a_seq,
        "AA_Arm_5prime_Struct": aa_arm_a_struct,
        "Aminoacyl arm 3' end": aa_arm_b_seq,
        "AA_Arm_3prime_Struct": aa_arm_b_struct,
        "D loop + D stem": d_loop_seq,
        "D_Loop_Struct": d_loop_struct,
        "Anticodon loop + Anticodon stem": anticodon_arm_seq,
        "Anticodon_Arm_Struct": anticodon_arm_struct,
        "Variable loop": var_loop_seq,
        "Variable_Loop_Struct": var_loop_struct,
        "T loop + T stem": t_arm_seq,
        "T_Arm_Struct": t_arm_struct,

        "SegmentationStatus": "ok" if seg_ok else "failed",
        "SegmentationErrors": "; ".join(seg_errs) if not seg_ok else "",
    }

    _log(seq_id, used_model, "segment", "OK" if seg_ok else "FAIL", entry["SegmentationErrors"])
    if seg_ok:
        struct = entry["SecondaryStructure"]
        if _DEBUG_ANNOTATE_FULL:
            print(
                f"[ANNOTATE][{seq_id}] Secondary structure ({used_model}) length {len(struct)}:"
                f"\n{struct}"
            )
        else:
            _dprint(f"[ANNOTATE][{seq_id}] Secondary structure ({used_model}) length {len(struct)}")
    return entry, seg_ok, entry["SegmentationErrors"]

# ==================== Master annotate (cascade per-seq) ====================

def annotate_trna_sequences(
    seq_dict: dict,
    *,
    domain_hint: str = "unknown",
    mitochondrial_subtype: str = "unknown",
) -> list:
    """
    对每条序列按模型级联（tRNAscan-SE → BPfold → RNAfold）尝试；
    一旦某模型分段成功则停止；同时缓存“被尝试过的模型”的原始预测，供打印比对。
    """
    temp_dir = tempfile.mkdtemp(prefix="trna_annotate_")
    results = []
    try:
        # 写批量 FASTA 给 tRNAscan-SE
        fasta_path  = os.path.join(temp_dir, "input.fa")
        results_path = os.path.join(temp_dir, "results.txt")
        struct_path  = os.path.join(temp_dir, "struct.txt")
        with open(fasta_path, "w", encoding="utf-8") as f:
            for sid, s in seq_dict.items():
                f.write(f">{sid}\n")
                for i in range(0, len(s), 80):
                    f.write(s[i:i+80] + "\n")

        # 运行 tRNAscan-SE（一次）
        search_mode = resolve_trnascan_search_mode(domain_hint, mitochondrial_subtype)
        trnascan_provenance = trnascan_runtime_provenance()
        trnascan_command_options = [
            *TRNASCAN_SENSITIVE_OPTIONS,
            *search_mode.args,
            "-Q",
        ]
        cmd = _build_trnascan_cmd(
            fasta_path,
            results_path,
            struct_path,
            domain_hint=domain_hint,
            mitochondrial_subtype=mitochondrial_subtype,
        )
        _dprint(f"[INFO] Running tRNAscan-SE: {' '.join(cmd)}")
        try:
            trna_proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TRNASCAN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            _dprint(f"[WARN] tRNAscan-SE timed out after {_TRNASCAN_TIMEOUT:g}s")
            trna_proc = None
        trna_ok = trna_proc is not None and trna_proc.returncode == 0
        trna_isos = parse_results_file(results_path) if trna_ok else {}
        trna_details = _parse_results_details_file(results_path) if trna_ok else {}
        trna_structs = parse_struct_file(struct_path) if trna_ok else {}

        # 逐条级联
        summary_rows = []

        for sid, seq in seq_dict.items():
            model_preds = {  # 只要被“尝试过”的模型就会填入
                "AYLM-2D": None,
                "tRNAscan-SE": None,
                "BPfold": None,
                "RNAfold": None,
            }

            used = None
            entry = None
            trnascan_hit = False
            if trna_ok and sid in trna_structs:
                cand_meta = trna_structs.get(sid, {}) or {}
                if cand_meta.get("StructSeq") and cand_meta.get("Structure"):
                    trnascan_hit = True

            # 0) AYLM-2D（优先尝试结构预测，但仍保留 tRNAscan 元信息做 family/anticodon 提示）
            ay_meta = trna_details.get(sid, {}) if trna_ok else {}
            if sid in trna_structs:
                st_meta = trna_structs.get(sid, {}) or {}
                if st_meta.get("Isotype"):
                    ay_meta["Isotype"] = st_meta.get("Isotype")
                if st_meta.get("Anticodon"):
                    ay_meta["Anticodon"] = st_meta.get("Anticodon")
                if st_meta.get("AnticodonStart"):
                    ay_meta["AnticodonStart"] = st_meta.get("AnticodonStart")
                if st_meta.get("AnticodonEnd"):
                    ay_meta["AnticodonEnd"] = st_meta.get("AnticodonEnd")
            aseq, astr = _run_aylm_secondstructure(sid, seq)
            if aseq and astr:
                iso = str(ay_meta.get("Isotype", "") or "") or "AYLM_2D_pred"
                anti = str(ay_meta.get("Anticodon", "") or "")
                anti_begin = ay_meta.get("AnticodonStart", 0) or 0
                anti_end = ay_meta.get("AnticodonEnd", 0) or 0
                model_preds["AYLM-2D"] = {
                    "Isotype": iso,
                    "Anticodon": anti,
                    "AnticodonStart": anti_begin,
                    "AnticodonEnd": anti_end,
                    "StructSeq": aseq,
                    "SecondaryStructure": astr,
                    "Tried": True,
                }
                entry_try, ok, reason = _segment_with_structure(
                    sid, "AYLM-2D", iso, aseq, astr, seq
                )
                if ok:
                    used = "AYLM-2D"
                    entry = entry_try
                    _log(sid, "AYLM-2D", "choose", "OK", "cascade stop")
                else:
                    _log(sid, "AYLM-2D", "choose", "FAIL", reason)

            # 1) tRNAscan-SE
            if used is None and trna_ok and sid in trna_structs:
                cand = trna_structs[sid]
                detail = trna_details.get(sid, {}) or {}
                iso  = cand.get("Isotype", "") or detail.get("Isotype", "") or trna_isos.get(sid, "")
                anti = cand.get("Anticodon", "") or detail.get("Anticodon", "")
                anti_begin = cand.get("AnticodonStart", 0) or detail.get("AnticodonStart", 0)
                anti_end = cand.get("AnticodonEnd", 0) or detail.get("AnticodonEnd", 0)
                sstr = cand.get("Structure", "")
                sseq = cand.get("StructSeq", "")
                if sseq and sstr:
                    model_preds["tRNAscan-SE"] = {
                        "Isotype": iso,
                        "Anticodon": anti,
                        "AnticodonStart": anti_begin,
                        "AnticodonEnd": anti_end,
                        "StructSeq": sseq,
                        "SecondaryStructure": sstr,
                        "Tried": True
                    }
                    _log(sid, "tRNAscan-SE", "choose", "START")
                    entry_try, ok, reason = _segment_with_structure(
                        sid, "tRNAscan-SE", iso, sseq, sstr, seq
                    )
                    if ok:
                        used = "tRNAscan-SE"
                        entry = entry_try
                        _log(sid, "tRNAscan-SE", "choose", "OK", "cascade stop")
                    else:
                        _log(sid, "tRNAscan-SE", "choose", "FAIL", reason)

            # 2) BPfold（仅未成功时）
            if used is None:
                bpf_out = os.path.join(temp_dir, f"bpf_{sid}")
                bseq, bstr = _run_bpfold_single(sid, seq, bpf_out)
                if bseq and bstr:
                    model_preds["BPfold"] = {
                        "Isotype": "BPfold_pred",
                        "StructSeq": bseq,
                        "SecondaryStructure": bstr,
                        "Tried": True
                    }
                    entry_try, ok, reason = _segment_with_structure(
                        sid, "BPfold", "BPfold_pred", bseq, bstr, seq
                    )
                    if ok:
                        used = "BPfold"
                        entry = entry_try
                        _log(sid, "BPfold", "choose", "OK", "cascade stop")
                    else:
                        _log(sid, "BPfold", "choose", "FAIL", reason)

            # 3) RNAfold（仅未成功时）
            if used is None:
                rseq, rstr = _run_rnafold(sid, seq)
                if rseq and rstr:
                    model_preds["RNAfold"] = {
                        "Isotype": "RNAfold_pred",
                        "StructSeq": rseq,
                        "SecondaryStructure": rstr,
                        "Tried": True
                    }
                    entry_try, ok, reason = _segment_with_structure(
                        sid, "RNAfold", "RNAfold_pred", rseq, rstr, seq
                    )
                    if ok:
                        used = "RNAfold"
                        entry = entry_try
                        _log(sid, "RNAfold", "choose", "OK", "cascade stop")
                    else:
                        _log(sid, "RNAfold", "choose", "FAIL", reason)

            # 都失败：给失败记录
            if entry is None:
                entry = {
                    "SequenceID": sid,
                    "Isotype": "",
                    "SecondaryStructure": "",
                    "StructFileSequence": "",
                    "OriginalSequence": seq,
                    "UsedModel": "",
                    "Aminoacyl arm 5' end": "",
                    "AA_Arm_5prime_Struct": "",
                    "Aminoacyl arm 3' end": "",
                    "AA_Arm_3prime_Struct": "",
                    "D loop + D stem": "",
                    "D_Loop_Struct": "",
                    "Anticodon loop + Anticodon stem": "",
                    "Anticodon_Arm_Struct": "",
                    "Variable loop": "",
                    "Variable_Loop_Struct": "",
                    "T loop + T stem": "",
                    "T_Arm_Struct": "",
                    "SegmentationStatus": "failed",
                    "SegmentationErrors": "AYLM-2D/tRNAscan-SE/BPfold/RNAfold 均未得到可用结构或分段失败",
                    "ModelPredictions": {k: (v if v is not None else {"Tried": False, "Skipped": True}) for k,v in model_preds.items()},
                    "tRNAscanHit": trnascan_hit,
                    "tRNAscanIsotype": "",
                    "tRNAscanAnticodon": "",
                    "tRNAscanAnticodonStart": 0,
                    "tRNAscanAnticodonEnd": 0,
                    "tRNAscanSearchMode": search_mode.flag,
                    "tRNAscanDomainHint": search_mode.domain_hint,
                    "tRNAscanMitochondrialSubtype": search_mode.mitochondrial_subtype,
                    "tRNAscanCommandOptions": trnascan_command_options,
                    "tRNAscanExecutable": trnascan_provenance.get("executable", ""),
                    "tRNAscanVersion": trnascan_provenance.get("version", ""),
                }
                summary_rows.append((sid, "-", "failed", "no usable structure"))
            else:
                # 填入 ModelPredictions，并将未尝试的标注为 skipped
                entry["ModelPredictions"] = {k: (v if v is not None else {"Tried": False, "Skipped": True}) for k,v in model_preds.items()}
                entry["tRNAscanHit"] = trnascan_hit
                summary_rows.append((sid, entry["UsedModel"], entry["SegmentationStatus"], entry.get("SegmentationErrors","")))

            # Attach tRNAscan anticodon/isotype metadata to every output row.
            trna_meta = trna_details.get(sid, {}) if trna_ok else {}
            if sid in trna_structs:
                st_meta = trna_structs.get(sid, {}) or {}
                if st_meta.get("Isotype"):
                    trna_meta["Isotype"] = st_meta.get("Isotype")
                if st_meta.get("Anticodon"):
                    trna_meta["Anticodon"] = st_meta.get("Anticodon")
                if st_meta.get("AnticodonStart"):
                    trna_meta["AnticodonStart"] = st_meta.get("AnticodonStart")
                if st_meta.get("AnticodonEnd"):
                    trna_meta["AnticodonEnd"] = st_meta.get("AnticodonEnd")

            entry["tRNAscanIsotype"] = str(trna_meta.get("Isotype", "") or "")
            entry["tRNAscanAnticodon"] = str(trna_meta.get("Anticodon", "") or "")
            entry["tRNAscanSearchMode"] = search_mode.flag
            entry["tRNAscanDomainHint"] = search_mode.domain_hint
            entry["tRNAscanMitochondrialSubtype"] = search_mode.mitochondrial_subtype
            entry["tRNAscanCommandOptions"] = trnascan_command_options
            entry["tRNAscanExecutable"] = trnascan_provenance.get("executable", "")
            entry["tRNAscanVersion"] = trnascan_provenance.get("version", "")
            try:
                entry["tRNAscanAnticodonStart"] = int(trna_meta.get("AnticodonStart", 0) or 0)
            except Exception:
                entry["tRNAscanAnticodonStart"] = 0
            try:
                entry["tRNAscanAnticodonEnd"] = int(trna_meta.get("AnticodonEnd", 0) or 0)
            except Exception:
                entry["tRNAscanAnticodonEnd"] = 0

            results.append(entry)

        # 汇总
        if _DEBUG_ANNOTATE or _DEBUG_ANNOTATE_SUMMARY:
            print("\n=== Summary (per sequence) ===")
            print("SeqID\tUsedModel\tSegStatus\tNote")
            for sid, um, st, note in summary_rows:
                print(f"{sid}\t{um}\t{st}\t{note}")

        return results

    finally:
        shutil.rmtree(temp_dir)

# ==================== CLI test ====================

if __name__ == "__main__":
    test_sequences = {
        "tRNA1": "AAAAAAUUAGUUUAAUCAAAAACCUUAGUAUGUCAAACUAAAAAAAUUAGAUCAUCUAAUAUUUUUUACCA",
        "tRNA2": "AGUUAAUGUAGCUUAUAAUAAAGCAAAGCACUGAAAAUGCUUAGAUGGAUUCAAAAAUCCCAUAAACACCA",
        "tRNA3": "GGAAAAUUAACUAUAGGUAAAGUGGAUUAUUUGCUAAGUAAUUGAAUUGUAAAUUCUUAUGAGUUCGAAUCUCAUAUUUUCCGCCA",
        "tRNA4": "AUCCUCGUUAGUAUAGUGGUGAGUAUCCCCGCUCGUCACGCGGGAGACCGGGGUUCGAUUCCCCGACGGGGAGCCA",
        "tRNA5": "AAGAAACUAGUUAAACUAAUAACACUGGAUUGUCAGACCGGAGUAACUGGUAAACAAUCAGUGUUUCUUGCCA",
    }

    print("正在注释测试序列，请稍候…")
    res = annotate_trna_sequences(test_sequences)

    print("\n=== 注释结果 ===")
    for e in res:
        print(f"SequenceID           : {e['SequenceID']}")
        print(f"  UsedModel          : {e['UsedModel']}")
        print(f"  Isotype            : {e['Isotype']}")
        print(f"  SecondaryStructure : {e['SecondaryStructure']}")
        print(f"  StructFileSequence : {e['StructFileSequence']}")
        print(f"  OriginalSequence   : {e['OriginalSequence']}")
        print(f"  Aminoacyl arm 5' end      : {e['Aminoacyl arm 5\' end']}")
        print(f"  AA_Arm_5prime_Struct      : {e['AA_Arm_5prime_Struct']}")
        print(f"  Aminoacyl arm 3' end      : {e['Aminoacyl arm 3\' end']}")
        print(f"  AA_Arm_3prime_Struct      : {e['AA_Arm_3prime_Struct']}")
        print(f"  D loop + D stem           : {e['D loop + D stem']}")
        print(f"  D_Loop_Struct             : {e['D_Loop_Struct']}")
        print(f"  Anticodon loop + stem     : {e['Anticodon loop + Anticodon stem']}")
        print(f"  Anticodon_Arm_Struct      : {e['Anticodon_Arm_Struct']}")
        print(f"  Variable loop             : {e['Variable loop']}")
        print(f"  Variable_Loop_Struct      : {e['Variable_Loop_Struct']}")
        print(f"  T loop + T stem           : {e['T loop + T stem']}")
        print(f"  T_Arm_Struct              : {e['T_Arm_Struct']}")
        print(f"  SegmentationStatus        : {e['SegmentationStatus']}")
        if e['SegmentationStatus'] == "failed":
            print(f"  SegmentationErrors        : {e['SegmentationErrors']}")
        # 打印被尝试过的模型的原始预测
        print("  -- 各模型原始二级结构（用于比对） --")
        for mdl in ["AYLM-2D", "tRNAscan-SE", "BPfold", "RNAfold"]:
            mp = e["ModelPredictions"].get(mdl)
            if mp and mp.get("Tried"):
                print(f"    [{mdl}] StructSeq          : {mp.get('StructSeq','')}")
                print(f"    [{mdl}] SecondaryStructure : {mp.get('SecondaryStructure','')}")
            else:
                print(f"    [{mdl}] skipped / 无预测")
        print()
