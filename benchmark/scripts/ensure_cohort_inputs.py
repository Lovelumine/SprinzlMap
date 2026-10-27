#!/usr/bin/env python3
"""Turn ENSURE's engineered sup-tRNA table into a SprinzlMap numbering cohort.

Each row carries a parent tRNA and the engineered suppressor built from it.
Both are numbered: the per-column comparison that ENSURE plots as "Per-Position
Alignment Variation" only means something in a coordinate frame, so the parent
has to be numbered by the same system as the variant.

ENSURE's own per-record column map (``js_*_tRNA``) is used to lay each sequence
out over extended Sprinzl columns.  A sequence is only accepted when dropping
the gap columns reproduces its stored string exactly, so the input the model
sees is provably the record's sequence.  Nothing here becomes an answer row:
every case is marked ``gold_evaluation_allowed=false``.
"""
"""Build the SprinzlMap numbering cohort from ENSURE's engineered sup-tRNA table.

The column order of ``reference.tsv`` is the molecule's chain order, not lexical
order: V11..V17, V1..V5 and V27..V21 sit between 45 and 46.  The benchmark runner
reads a sequence by joining the row left to right, so a lexical header would
quietly hand the model a sequence whose variable arm was moved to the 3' end.
This script checks that after writing, and refuses to produce a cohort that does
not reproduce the record's own sequence.

Usage::

    PYTHONPATH=APP python3 benchmark/scripts/ensure_cohort_inputs.py --out-dir .runtime/ensure-perpos-20261026 --write
"""
import argparse, collections, json, os, re, sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

from backend.domain.alignment.sprinzl_coordinates import order_key as sprinzl_order_key  # noqa: E402
COL_RE = re.compile(r"^-?\d+[a-z]?(i\d+)?$|^V\d+$")


def norm(seq):
    return "".join(str(seq or "").upper().replace("T", "U").split())


def valid(seq):
    return bool(seq) and set(seq) <= set("ACGU")


def load_rows(cfg):
    conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor)
    with conn.cursor() as cur:
        cur.execute("""SELECT `Index` idx, ENSURE_ID ensure_id,
            `Species_source_of_origin_tRNA` species,
            `Sequence_of_origin_tRNA` origin_seq, `Sequence_of_sup-tRNA` sup_seq,
            js_origin_tRNA, js_sup_tRNA
            FROM Engineered_sup_tRNA ORDER BY `Index`""")
        return list(cur.fetchall())


def column_entries(blob, *, key):
    """Read one record's column map.  ``key`` picks the side: the parent stores
    its base under ``base``, the engineered sequence under ``sup_base``."""
    try:
        arr = json.loads(blob or "[]")
    except Exception:
        return []
    out = []
    for e in arr:
        cid = str(e.get("id") or "").strip()
        base = str(e.get(key) or e.get("base") or "").strip().upper().replace("T", "U")
        if not cid or not COL_RE.fullmatch(cid):
            continue
        if base in {"", "-", "N"}:
            continue
        out.append((cid, base))
    return out


def frame_sort_key(col):
    """Chain order.  Neutral ``p<n>`` frames, which only ever appear on their own
    rows, go after the Sprinzl columns."""
    if re.fullmatch(r"p\d+", col):
        return (1, 0, int(col[1:]), "")
    try:
        index, kind, slot = sprinzl_order_key(col)
        return (0, index, kind, f"{slot:04d}")
    except ValueError:
        return (2, 0, 0, col)



def build(cfg, min_len=50, max_len=200):
    rows = load_rows(cfg)
    cases = {}          # column-frame key -> record
    frame_fallback = set()
    per_row = {}        # ensure_id -> {role: case_key}
    stats = collections.Counter()
    for r in rows:
        eid = str(r["ensure_id"] or f"ensure-{r['idx']}")
        for role, seq_blob, js_blob in (("sup", r["sup_seq"], r["js_sup_tRNA"]),
                                        ("ori", r["origin_seq"], r["js_origin_tRNA"])):
            seq = norm(seq_blob)
            if not seq:
                stats[f"{role}:no_sequence"] += 1
                continue
            if not valid(seq):
                stats[f"{role}:non_rna_symbol"] += 1
                continue
            if not (min_len <= len(seq) <= max_len):
                stats[f"{role}:length_out_of_range"] += 1
                continue
            entries = column_entries(js_blob, key="sup_base" if role=="sup" else "base")
            # ENSURE's column frame is usable only when it is a slice of the shared
            # Sprinzl frame: each column once, in chain order.  Rows that repeat a
            # column or put an insertion before its anchor keep their sequence, but
            # are given a neutral frame so the written order cannot reorder bases.
            ids = [cid for cid, _ in entries]
            def usable_layout():
                if "".join(b for _, b in entries) != seq:
                    stats[f"{role}:frame_not_reproducing_sequence"] += 1
                    return False
                if len(set(ids)) != len(ids):
                    stats[f"{role}:duplicate_column"] += 1
                    return False
                try:
                    keys = [frame_sort_key(c) for c in ids]
                except Exception:
                    stats[f"{role}:unparsable_column"] += 1
                    return False
                if not all(a < b for a, b in zip(keys, keys[1:])):
                    stats[f"{role}:column_out_of_chain_order"] += 1
                    return False
                return True
            if not usable_layout():
                # ENSURE's own column map does not reproduce its own sequence for
                # these records.  They are still numbered, but with a neutral
                # frame, so the coordinate comparison skips them and the count is
                # reported instead of hidden.
                stats[f"{role}:neutral_frame"] += 1
                entries = [(f"p{i}", b) for i, b in enumerate(seq, 1)]
                frame_fallback.add(f"{eid}:{role}")
            key = f"ensure:{eid}:{role}"
            record = cases.get(key)
            if record is None:
                cases[key] = {
                    "seqname": key, "sequence": seq, "columns": entries,
                    "ensure_id": eid, "role": role, "species": (r["species"] or "").strip(),
                }
            per_row.setdefault(eid, {})[role] = key
            stats[f"{role}:accepted"] += 1
    # Deduplicate: identical sequences are numbered once and shared by every row.
    by_seq = {}
    for key, rec in sorted(cases.items()):
        rep = by_seq.setdefault(rec["sequence"], key)
        rec["shared_by"] = rep
    distinct = {by_seq[rec["sequence"]]: rec for rec in cases.values()}
    # A row must be pointed at the sequence that was actually numbered.  Without
    # this indirection, every row sharing a sequence with an earlier row asks for
    # a case name the run never had.
    row_cases = {}
    for eid, roles in per_row.items():
        for role, key in roles.items():
            row_cases.setdefault(eid, {})[role] = cases[key]["shared_by"]
    return rows, cases, distinct, row_cases, stats, frame_fallback


def frame_columns(cases):
    seen = set()
    for rec in cases.values():
        seen.update(cid for cid, _ in rec["columns"])
    return sorted(seen, key=frame_sort_key)


def verify_written(tsv_path: Path, expected: dict[str, str]) -> list[str]:
    """Re-read the cohort exactly as the runner does and compare with the record."""
    bad = []
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            fields = line.rstrip("\n").split("\t")
            seqname, letters = fields[0], "".join(b for b in fields[1:] if b != "-")
            if letters != expected.get(seqname):
                bad.append(seqname)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / ".runtime/ensure-cohort")
    ap.add_argument("--write", action="store_true", help="emit the runner inputs")
    ap.add_argument("--min-len", type=int, default=50)
    ap.add_argument("--max-len", type=int, default=200)
    args = ap.parse_args()
    globals()["HERE"] = args.out_dir
    cfg = dict(host=os.environ["SHARED_MYSQL_HOST"], port=int(os.environ["SHARED_MYSQL_PORT"]),
               user=os.environ["SHARED_MYSQL_USER"], password=os.environ["SHARED_MYSQL_PASSWORD"],
               database=os.environ["SHARED_MYSQL_DATABASE"], charset="utf8mb4")
    rows, cases, distinct, per_row, stats, frame_fallback = build(cfg, args.min_len, args.max_len)
    ids = {str(r["ensure_id"] or f"ensure-{r['idx']}") for r in rows}
    print(f"records whose own column map does not reproduce their sequence: {len(frame_fallback)}")
    print(f"ENSURE rows: {len(rows)}")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")
    print(f"case rows kept: {len(cases)} | distinct sequences to number: {len(distinct)}")
    pairs = [e for e, roles in per_row.items() if "sup" in roles and "ori" in roles]
    print(f"rows with a usable ENSURE id: {len(ids)}")
    print(f"rows with both parent and suppressor usable: {len(pairs)}")
    if not args.write:
        return
    HERE.mkdir(parents=True, exist_ok=True)
    cols = frame_columns(distinct)
    with (HERE / "reference.tsv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("seqname\t" + "\t".join(cols) + "\n")
        for key, rec in sorted(distinct.items()):
            place = dict(rec["columns"])
            fh.write(key + "\t" + "\t".join(place.get(c, "-") for c in cols) + "\n")
    with (HERE / "seqnames.txt").open("w", encoding="utf-8") as fh:
        fh.write("".join(f"{k}\n" for k in sorted(distinct)))
    with (HERE / "case_manifest.jsonl").open("w", encoding="utf-8") as fh:
        for key, rec in sorted(distinct.items()):
            fh.write(json.dumps({
                "schema_version": "3.1", "representative_seqname": key,
                "cohort_source": "engineered_sup_trna",
                "metadata": {"broad_source": "engineered", "ensure_id": rec["ensure_id"],
                             "role": rec["role"], "species": rec["species"]},
                "gold_evaluation_allowed": False,
                "curation_status": "external_no_answer", "exclusion_reason": None,
                "frame_fallback": (key.split(":")[1] + ":" + key.split(":")[2]) in frame_fallback,
            }, ensure_ascii=False) + "\n")
    (HERE / "cohort_membership.tsv").write_text(
        "seqname\trole\tensure_id\tspecies\tshared_by\n" +
        "".join(f"{k}\t{r['role']}\t{r['ensure_id']}\t{r['species']}\t{r['shared_by']}\n"
                for k, r in sorted(distinct.items())), encoding="utf-8")
    unplaced = [c for c in cols if frame_sort_key(c)[0] == 2]
    if unplaced:
        print(f"column names outside the Sprinzl frame: {unplaced[:10]}")
    expected = {k: r["sequence"] for k, r in distinct.items()}
    bad = verify_written(HERE / "reference.tsv", expected)
    print(f"rows whose written order reproduces the record: {len(expected) - len(bad)}/{len(expected)}")
    if bad:
        raise SystemExit(f"cohort rejected: {len(bad)} cases do not lay out their sequence, e.g. {bad[:5]}")
    (HERE / "row_map.json").write_text(json.dumps(
        {"rows": {str(r["ensure_id"] or f"ensure-{r['idx']}"): per_row.get(
                       str(r["ensure_id"] or f"ensure-{r['idx']}"), {}) for r in rows},
         "columns": cols}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote inputs to {HERE}")


if __name__ == "__main__":
    main()
