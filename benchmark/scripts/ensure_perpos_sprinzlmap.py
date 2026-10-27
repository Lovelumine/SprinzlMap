#!/usr/bin/env python3
"""Recompute ENSURE's Per-Position Alignment Variation in Sprinzl coordinates.

ENSURE plots, for every engineered suppressor tRNA, how the suppressor differs
from its parent at each numbered position.  The published counts compare the two
stored strings position by position, so any change in length upstream of a site
shifts every later column and books the difference at the wrong position.  Here
both members of each pair are numbered by SprinzlMap and compared in that
coordinate frame, which is what the axis labels already claim.

Three views come out of one run:

  current   the table ENSURE plots today, read back unchanged
  ensure_js ENSURE's own per-record coordinate map, same classifier
  map       SprinzlMap numbering, same classifier

Insertion columns (``70i4`` and the like) are not comparable across sequences by
name: which residue becomes ``70i4`` is a labelling choice.  Inside the gap after
an anchor column the two inserted strings are aligned with each other, and the
merged layout is re-indexed, so a shifted label cannot masquerade as a
substitution.

Nothing here is graded against the gold standard: the cohort has no answers, and
every case in the run manifest carries ``gold_evaluation_allowed=false``.

Usage
    python3 benchmark/scripts/ensure_perpos_sprinzlmap.py \
        --run-id 20261026-2028-ensure-suptna-external \
        --cohort-dir .runtime/ensure-perpos-20261026 \
        --out-dir research/paper/results/ensure_perpos
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

import pymysql  # noqa: E402

from backend.domain.benchmarking.case_index import pred_residue_map  # noqa: E402
from backend.infra.config.mysql_settings import load_shared_mysql_config  # noqa: E402
from backend.infra.storage.benchmark_run_store import (  # noqa: E402
    BenchmarkStoreError,
    connect_store,
)

VIEWS = ("current", "ensure_js", "map", "ensure_edits", "map_edits")
CODES = ("match", "mismatch", "insertion", "deletion")

NUMBER_RE = re.compile(r"^(-?\d+)([a-z]?)(?:i(\d+))?$")
V_RE = re.compile(r"^(V\d+)(?:i(\d+))?$")
COL_RE = re.compile(r"^-?\d+[a-z]?(i\d+)?$|^V\d+(i\d+)?$")
DOCUMENTED_AC_RE = re.compile(r"\(([ACGTU]{3})\)")

CODON_TABLE = {
    "UUU": "Phe", "UUC": "Phe", "UUA": "Leu", "UUG": "Leu",
    "CUU": "Leu", "CUC": "Leu", "CUA": "Leu", "CUG": "Leu",
    "AUU": "Ile", "AUC": "Ile", "AUA": "Ile", "AUG": "Met",
    "GUU": "Val", "GUC": "Val", "GUA": "Val", "GUG": "Val",
    "UCU": "Ser", "UCC": "Ser", "UCA": "Ser", "UCG": "Ser",
    "CCU": "Pro", "CCC": "Pro", "CCA": "Pro", "CCG": "Pro",
    "ACU": "Thr", "ACC": "Thr", "ACA": "Thr", "ACG": "Thr",
    "GCU": "Ala", "GCC": "Ala", "GCA": "Ala", "GCG": "Ala",
    "UAU": "Tyr", "UAC": "Tyr", "UAA": "Stop(Ochre)", "UAG": "Stop(Amber)",
    "CAA": "Gln", "CAG": "Gln", "AAU": "Asn", "AAC": "Asn",
    "AAA": "Lys", "AAG": "Lys", "GAU": "Asp", "GAC": "Asp",
    "GAA": "Glu", "GAG": "Glu", "CGU": "Arg", "CGC": "Arg",
    "CGA": "Arg", "CGG": "Arg", "AGU": "Ser", "AGC": "Ser",
    "AGA": "Arg", "AGG": "Arg", "GGU": "Gly", "GGC": "Gly",
    "GGA": "Gly", "GGG": "Gly", "UGU": "Cys", "UGC": "Cys",
    "UGG": "Trp", "UGA": "Stop(Opal)",
}


def norm(seq) -> str:
    return "".join(str(seq or "").upper().replace("T", "U").split())


def sort_key(position: str):
    """Frame order: numbered columns first, then V columns, then unknown names."""
    if position.startswith("V"):
        digits = re.findall(r"\d+", position)
        return (1, int(digits[0]), "", int(digits[1]) if len(digits) > 1 else 0)
    m = NUMBER_RE.fullmatch(position)
    if not m:
        return (2, 0, position, 0)
    return (0, int(m.group(1)), m.group(2) or "", int(m.group(3) or 0))


def anchor_of(position: str) -> str:
    """The column an insertion column hangs off: ``70i4`` -> ``70``, ``17ai2`` -> ``17a``."""
    m = COL_RE.fullmatch(position)
    if not m:
        return position
    if position.startswith("V"):
        mv = V_RE.fullmatch(position)
        return mv.group(1) if mv else position
    body = position[: position.rfind("i")] if re.search(r"i\d+$", position) else position
    return body


def is_insertion_column(position: str) -> bool:
    return bool(re.search(r"i\d+$", position))


REGION_ORDER = ("Acceptor stem", "D arm", "Anticodon arm", "Variable arm",
              "T arm", "Discriminator", "CCA end", "Other")


def region_of(position: str) -> str:
    """Coarse structural bucket for a Sprinzl column."""
    anchor = anchor_of(position)
    if anchor.startswith("V"):
        return "Variable arm"
    m = NUMBER_RE.fullmatch(anchor)
    if not m:
        return "Other"
    num = int(m.group(1))
    letter = m.group(2)
    if num in (74, 75, 76):
        return "CCA end"
    if num == 73:
        return "Discriminator"
    if num < 0:
        return "Acceptor stem"
    if letter in ("a", "b"):
        return "D arm"
    if num <= 9:
        return "Acceptor stem"
    if num <= 26:
        return "D arm"
    if num <= 43:
        return "Anticodon arm"
    if num <= 48:
        return "Variable arm"
    if num <= 65:
        return "T arm"
    return "Acceptor stem"


def anticodon(coords: dict) -> str:
    return "".join(coords.get(p, "") for p in ("34", "35", "36"))


def codon_of(antic: str) -> str:
    if len(antic) != 3 or any(ch not in "ACGU" for ch in antic):
        return ""
    comp = {"A": "U", "U": "A", "G": "C", "C": "G"}
    return "".join(comp[ch] for ch in reversed(antic))


def align_runs(left: str, right: str):
    """Pair two inserted strings inside the same gap.

    Gap and mismatch cost the same, so the alignment prefers to pair residues
    whenever the letters agree, and a pure shift never turns an unchanged
    residue into a substitution.
    """
    n, m = len(left), len(right)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = -i
    for j in range(1, m + 1):
        score[0][j] = -j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            pair = 2 if left[i - 1] == right[j - 1] else -1
            score[i][j] = max(score[i - 1][j - 1] + pair, score[i - 1][j], score[i][j - 1])
    ops = []
    i, j = n, m
    while i or j:
        if i and j and score[i][j] == score[i - 1][j - 1] + (2 if left[i - 1] == right[j - 1] else -1):
            ops.append((left[i - 1], right[j - 1]))
            i, j = i - 1, j - 1
        elif i and score[i][j] == score[i - 1][j]:
            ops.append((left[i - 1], "-"))
            i -= 1
        elif j:
            ops.append(("-", right[j - 1]))
            j -= 1
        else:
            ops.append((left[i - 1], "-"))
            i -= 1
    return list(reversed(ops))


def compare_coords(parent: dict, variant: dict):
    """Classify every column of the merged coordinate frame for one pair.

    Yields ``(position, kind, parent_base, sup_base)`` with kind in
    ``match / mismatch / insertion / deletion``.
    """
    anchors = sorted(
        {anchor_of(c) for c in parent} | {anchor_of(c) for c in variant},
        key=sort_key,
    )
    for anchor in anchors:
        pb, vb = parent.get(anchor), variant.get(anchor)
        if pb and vb:
            yield anchor, ("match" if pb == vb else "mismatch"), pb, vb
        elif vb:
            yield anchor, "insertion", "-", vb
        else:
            yield anchor, "deletion", pb, "-"
        left = [parent[c] for c in sorted((k for k in parent if anchor_of(k) == anchor and is_insertion_column(k)), key=sort_key)]
        right = [variant[c] for c in sorted((k for k in variant if anchor_of(k) == anchor and is_insertion_column(k)), key=sort_key)]
        if not left and not right:
            continue
        slot = 0
        for lp, rv in align_runs("".join(left), "".join(right)):
            slot += 1
            column = f"{anchor}i{slot}"
            if lp != "-" and rv != "-":
                yield column, ("match" if lp == rv else "mismatch"), lp, rv
            elif rv != "-":
                yield column, "insertion", "-", rv
            else:
                yield column, "deletion", lp, "-"


def align_strings(left: str, right: str):
    """Sequence-level edit script between parent and suppressor.

    Coordinates name an edit; they must not create one.  Pairing the two strings
    first, then looking up the column each edited residue carries, keeps an
    insertion in a numbering from being reported as a substitution at two
    positions.
    """
    n, m = len(left), len(right)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = -2 * i
    for j in range(1, m + 1):
        score[0][j] = -2 * j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            pair = 2 if left[i - 1] == right[j - 1] else -1
            score[i][j] = max(score[i - 1][j - 1] + pair, score[i - 1][j] - 2, score[i][j - 1] - 2)
    ops = []
    i, j = n, m
    while i or j:
        if i and j and score[i][j] == score[i - 1][j - 1] + (2 if left[i - 1] == right[j - 1] else -1):
            ops.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and score[i][j] == score[i - 1][j] - 2:
            ops.append((i - 1, None))
            i -= 1
        elif j:
            ops.append((None, j - 1))
            j -= 1
        else:
            ops.append((i - 1, None))
            i -= 1
    return list(reversed(ops))


def js_dict_for(blob, key):
    return {column: base for column, base in js_order(blob, key=key)}


def js_order(blob, *, key):
    """ENSURE's per-record column map, in the order the record stores it.

    That order is the chain order, and it is not the numeric order of the
    column names: a variable loop can be written ``45 V11 V12 V1 V2 46``.  The
    sequence index has to be read off the stored order, or every residue after
    the loop is named one column off.
    """
    try:
        entries = json.loads(blob or "[]")
    except Exception:
        return []
    out = []
    for e in entries:
        cid = str(e.get("id") or "").strip()
        base = str(e.get(key) or e.get("base") or "").strip().upper().replace("T", "U")
        if not COL_RE.fullmatch(cid) or base in {"", "-", "N"}:
            continue
        out.append((cid, base))
    return out


def edits_view(ori_seq, sup_seq, parent_frame, variant_frame, counts):
    """Count the edits found by aligning the two strings, named by coordinates.

    Returns ``(substitutions, substitutions_on_the_same_column, usable)``.
    A substitution whose two members carry different columns is still booked
    once, at the parent's column; how often that happens measures how much of a
    per-position count comes from the numbering rather than from the construct.
    """
    if len(parent_frame) != len(ori_seq) or len(variant_frame) != len(sup_seq):
        return 0, 0, False
    if "".join(b for _, b in parent_frame) != ori_seq or "".join(b for _, b in variant_frame) != sup_seq:
        return 0, 0, False
    subs = agreed = 0
    for ia, ib in align_strings(ori_seq, sup_seq):
        if ia is not None and ib is not None:
            a, b = ori_seq[ia], sup_seq[ib]
            ca, cb = parent_frame[ia][0], variant_frame[ib][0]
            if a == b:
                counts[ca]["match"] += 1
                continue
            subs += 1
            if ca == cb:
                agreed += 1
            counts[ca]["mismatch"] += 1
        elif ib is not None:
            counts[variant_frame[ib][0]]["insertion"] += 1
        else:
            counts[parent_frame[ia][0]]["deletion"] += 1
    return subs, agreed, True


def load_ensure(cfg):
    conn = pymysql.connect(**cfg)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `Index` idx, ENSURE_ID eid, `Species_source_of_origin_tRNA` species,"
            " `Sequence_of_origin_tRNA` ori_seq, `Sequence_of_sup-tRNA` sup_seq,"
            " `aa_and_anticodon_of_origin_tRNA` ori_anticodon,"
            " `aa_and_anticodon_of_sup-tRNA` sup_anticodon,"
            " js_origin_tRNA, js_sup_tRNA, PMID pmid"
            " FROM Engineered_sup_tRNA ORDER BY `Index`"
        )
        rows = list(cur.fetchall())
        cur.execute("SELECT * FROM engineered_sup_trna_perpos_counts")
        perpos = list(cur.fetchall())
    conn.close()
    return rows, perpos


def read_perpos_snapshot(path: Path) -> list:
    """Published per-position counts read back from a TSV snapshot.

    Once the live ENSURE table has been replaced by this analysis, the table can
    no longer serve as the "what ENSURE published before" reference.  A snapshot
    written before the swap keeps that comparison reproducible.
    """
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def js_columns(blob, *, key):
    """ENSURE's own per-record column map, as ``{column: base}``."""
    try:
        entries = json.loads(blob or "[]")
    except Exception:
        return {}
    out = {}
    for e in entries:
        cid = str(e.get("id") or "").strip()
        base = str(e.get(key) or e.get("base") or "").strip().upper().replace("T", "U")
        if not COL_RE.fullmatch(cid) or base in {"", "-", "N"}:
            continue
        out[cid] = base
    return out


def write_perpos(path: Path, counts: dict, frame: list) -> None:
    lines = ["position\tmatch\tmismatch\tinsertion\tdeletion"]
    for column in frame:
        row = counts.get(column, {})
        lines.append("\t".join([column] + [str(row.get(code, 0)) for code in CODES]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True,
                    help="SprinzlMap run(s) that numbered the cohort; comma separated, "
                         "an ok case always wins over a case that recorded a technical "
                         "failure, otherwise the first run listed wins")
    ap.add_argument("--cohort-dir", type=Path, required=True,
                    help="Directory written by ensure_cohort_inputs.py (row_map.json, reference.tsv)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--ensure-snapshot", type=Path, default=None,
                    help="TSV of the counts ENSURE published before this analysis "
                         "replaced the live table; used for the 'current' view")
    args = ap.parse_args()

    shared = load_shared_mysql_config()
    if not shared:
        raise SystemExit("SHARED_MYSQL_* is not configured")
    ensure_rows, ensure_perpos = load_ensure(shared)
    if args.ensure_snapshot:
        ensure_perpos = read_perpos_snapshot(args.ensure_snapshot)

    row_map = json.loads((args.cohort_dir / "row_map.json").read_text(encoding="utf-8"))
    rows_to_cases: dict = row_map["rows"]

    # The sequence each case was numbered from, so a numbering can be checked
    # against the record it came from.
    seqnames: dict[str, str] = {}
    with (args.cohort_dir / "reference.tsv").open(encoding="utf-8") as handle:
        handle.readline()
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            seqnames[fields[0]] = "".join(b for b in fields[1:] if b != "-")

    # A cohort can be split over more than one stored run: the main run plus any
    # run that retried a technical failure.  A side keeps the cleanest numbering
    # available: an ``ok`` case beats a case that recorded a technical failure
    # regardless of the order the runs are listed in, because a failed case can
    # still carry a fallback numbering that would otherwise win on arrival order.
    numbered: dict[str, dict] = {}
    numbered_frame: dict[str, list] = {}
    numbered_status: dict[str, str] = {}
    numbered_run: dict[str, str] = {}
    numbered_reproduced: dict[str, bool] = {}
    status_counts: collections.Counter = collections.Counter()
    numbering_source: collections.Counter = collections.Counter()
    run_ids = [r.strip() for r in args.run_id.split(",") if r.strip()]
    for run_id in run_ids:
        try:
            store = connect_store(run_id)
        except BenchmarkStoreError as exc:
            raise SystemExit(f"store unavailable for {run_id}: {exc}")
        case_rows = store.case_rows(
            run_id=run_id, active_answer=False,
            columns=("status", "prediction_csv", "input_len"),
        )
        for row in case_rows:
            seqname = str(row.get("seqname") or "")
            status_counts[f"{run_id}:{row.get('status')}"] += 1
            if numbered_status.get(seqname) == "ok":
                continue
            labels, bases = pred_residue_map(row.get("prediction_csv"))
            coords = {}
            for label, base in zip(labels, bases):
                if COL_RE.fullmatch(str(label)):
                    coords.setdefault(str(label), base)
            if not coords:
                continue
            previous_run = numbered_run.get(seqname)
            if previous_run is not None:
                numbering_source[previous_run] -= 1
            numbered[seqname] = coords
            numbered_status[seqname] = str(row.get("status") or "")
            numbered_run[seqname] = run_id
            numbered_frame[seqname] = [(str(label), base) for label, base in zip(labels, bases)]
            numbered_reproduced[seqname] = "".join(bases) == seqnames.get(seqname, "\x00")
            numbering_source[run_id] += 1
    reproduced = sum(1 for flag in numbered_reproduced.values() if flag)

    totals = {view: collections.defaultdict(collections.Counter) for view in VIEWS}
    used = collections.Counter()
    pair_records = []
    skip: collections.Counter = collections.Counter()
    reassignments: collections.Counter = collections.Counter()
    stability = collections.Counter()
    consistency = collections.defaultdict(lambda: [0, 0, 0])  # substitutions, agreed, pairs
    anticodon_check = collections.defaultdict(collections.Counter)
    anticodon_flags = []
    profiles = {v: collections.Counter() for v in VIEWS}
    change_histogram = {v: collections.Counter() for v in VIEWS}

    for row in ensure_perpos:
        column = str(row["position"])
        for code in CODES:
            totals["current"][column][code] += int(row.get(code) or 0)
    # ENSURE stores the published aggregate, not the pairs behind it, so its
    # web table gives a position count rather than a pair count.
    ensure_table_positions = len(ensure_perpos)

    for r in ensure_rows:
        eid = str(r["eid"] or f"ensure-{r['idx']}")
        cases = rows_to_cases.get(eid) or {}
        ori_key, sup_key = cases.get("ori"), cases.get("sup")
        if not ori_key or not sup_key:
            skip["pair missing a side in the cohort"] += 1
            continue
        ori_seq, sup_seq = norm(r["ori_seq"]), norm(r["sup_seq"])

        pj_all = js_order(r["js_origin_tRNA"], key="base")
        vj_all = js_order(r["js_sup_tRNA"], key="sup_base")
        # ENSURE writes the anticodon of both members as text, and in the
        # Sprinzl frame that triplet is 34-36 by definition.  It is a check on
        # the numbering that never touched the gold standard.
        for label, blob_key, coords, field in (
            ("map", "ori", numbered.get(ori_key), r["ori_anticodon"]),
            ("map", "sup", numbered.get(sup_key), r["sup_anticodon"]),
            ("ensure_js", "ori", js_dict_for(r["js_origin_tRNA"], "base"), r["ori_anticodon"]),
            ("ensure_js", "sup", js_dict_for(r["js_sup_tRNA"], "sup_base"), r["sup_anticodon"]),
        ):
            m = DOCUMENTED_AC_RE.search(str(field or ""))
            if not m or not coords:
                continue
            want = m.group(1).upper().replace("T", "U")
            got = "".join(coords.get(c, "") for c in ("34", "35", "36"))
            if len(got) != 3:
                anticodon_check[(label, blob_key)]["absent"] += 1
            elif got == want:
                anticodon_check[(label, blob_key)]["agrees"] += 1
            else:
                anticodon_check[(label, blob_key)]["differs"] += 1
                if label == "map":
                    anticodon_flags.append({
                        "ensure_id": eid, "role": blob_key, "annotated": want,
                        "at_34_36": got, "in_sequence": want in (ori_seq if blob_key == "ori" else sup_seq),
                        "species": r["species"] or "", "pmid": r["pmid"] or "",
                    })

        for view, parent, variant in (
            ("map", numbered.get(ori_key), numbered.get(sup_key)),
            ("ensure_js", js_columns(r["js_origin_tRNA"], key="base"),
             js_columns(r["js_sup_tRNA"], key="sup_base")),
        ):
            if not parent or not variant:
                if view == "map":
                    skip["pair without a numbering"] += 1
                continue
            if view == "ensure_js" and (len(parent) != len(ori_seq) or len(variant) != len(sup_seq)):
                skip["ENSURE map does not lay out its own sequence"] += 1
                continue
            changed = 0
            changed_columns = set()
            for column, kind, pb, vb in compare_coords(parent, variant):
                totals[view][column][kind] += 1
                if kind != "match":
                    changed += 1
                    changed_columns.add(anchor_of(column))
                    if view == "map":
                        pair_records.append({
                            "ensure_id": eid, "position": column, "kind": kind,
                            "parent_base": pb, "sup_base": vb,
                            "species": r["species"] or "", "pmid": r["pmid"] or "",
                        })
            used[view] += 1
            # Where did the engineering land?  The anticodon is the designed part
            # of a suppressor, so pairs that touch nothing else are the clean case.
            touched = changed_columns - {"34", "35", "36"}
            if not changed_columns:
                profiles[view]["no change"] += 1
            elif not touched:
                profiles[view]["anticodon only"] += 1
            elif changed_columns & {"34", "35", "36"}:
                profiles[view]["anticodon plus other positions"] += 1
            else:
                profiles[view]["other positions only"] += 1
            change_histogram[view][min(len(changed_columns), 12)] += 1
            if view == "map":
                # Coordinate stability: do the two numberings put the same
                # canonical columns on a pair whose backbone is otherwise the same?
                pa = {c for c in parent if not is_insertion_column(c)}
                va = {c for c in variant if not is_insertion_column(c)}
                stability["canonical columns only on one side"] += len(pa ^ va)
                stability["pairs with identical canonical column set"] += int(pa == va)
                antic_parent, antic_variant = anticodon(parent), anticodon(variant)
                if antic_parent and antic_variant and antic_parent != antic_variant:
                    reassignments[(antic_parent, antic_variant)] += 1

        for view, parent, variant in (
            ("map_edits", numbered_frame.get(ori_key), numbered_frame.get(sup_key)),
            ("ensure_edits", pj_all, vj_all),
        ):
            if not parent or not variant:
                continue
            subs, agreed, usable = edits_view(ori_seq, sup_seq, parent, variant, totals[view])
            if usable:
                consistency[view][0] += subs
                consistency[view][1] += agreed
                consistency[view][2] += 1
                used[view] += 1

    # How often SprinzlMap and ENSURE's own coordinates put a different residue
    # on the same column of the same sequence.  A pair comparison inherits every
    # disagreement here, so it says how much of the published variation is a
    # numbering difference rather than an engineering difference.
    disagreement = collections.Counter()
    compared_sides = collections.Counter()
    for r in ensure_rows:
        eid = str(r["eid"] or f"ensure-{r['idx']}")
        cases = rows_to_cases.get(eid) or {}
        for role, blob, base_key, seq in (("ori", r["js_origin_tRNA"], "base", norm(r["ori_seq"])),
                                          ("sup", r["js_sup_tRNA"], "sup_base", norm(r["sup_seq"]))):
            ours = numbered.get(cases.get(role) or "")
            theirs = js_columns(blob, key=base_key)
            if not ours or not theirs or len(theirs) != len(seq):
                continue
            compared_sides[role] += 1
            for column in {c for c in ours if not is_insertion_column(c)} | {c for c in theirs if not is_insertion_column(c)}:
                if ours.get(column, "") != theirs.get(column, ""):
                    disagreement[column] += 1

    frame = sorted(
        {str(row["position"]) for row in ensure_perpos}
        | set(totals["map"]) | set(totals["ensure_js"]),
        key=sort_key,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_perpos(args.out_dir / "perpos_ensure_current.tsv", totals["current"], frame)
    write_perpos(args.out_dir / "perpos_ensure_js.tsv", totals["ensure_js"], frame)
    write_perpos(args.out_dir / "perpos_sprinzlmap.tsv", totals["map"], frame)
    write_perpos(args.out_dir / "perpos_sprinzlmap_edits.tsv", totals["map_edits"], frame)
    write_perpos(args.out_dir / "perpos_ensure_edits.tsv", totals["ensure_edits"], frame)

    with (args.out_dir / "pair_diffs_sprinzlmap.tsv").open("w", encoding="utf-8") as fh:
        fh.write("ensure_id\tposition\tregion\tkind\tparent_base\tsup_base\tspecies\tpmid\n")
        for rec in sorted(pair_records, key=lambda x: (sort_key(x["position"]), x["ensure_id"])):
            fh.write("\t".join([rec["ensure_id"], rec["position"], region_of(rec["position"])]
                               + [str(rec[k]) for k in ("kind", "parent_base", "sup_base",
                                                        "species", "pmid")]) + "\n")

    with (args.out_dir / "anticodon_reassignments.tsv").open("w", encoding="utf-8") as fh:
        fh.write("parent_anticodon\tsup_anticodon\tparent_codon\tsup_codon\ttarget\tcount\n")
        for (pa, va), n in sorted(reassignments.items(), key=lambda kv: -kv[1]):
            pc, vc = codon_of(pa), codon_of(va)
            target = CODON_TABLE.get(vc, "")
            fh.write(f"{pa}\t{va}\t{pc}\t{vc}\t{target}\t{n}\n")

    with (args.out_dir / "anticodon_annotation_flags.tsv").open("w", encoding="utf-8") as fh:
        fh.write("ensure_id\trole\tannotated_anticodon\tcodon_at_34_36\tannotated_present_in_sequence\tspecies\tpmid\n")
        for f in anticodon_flags:
            fh.write("\t".join(str(f[k]) for k in
                               ("ensure_id", "role", "annotated", "at_34_36", "in_sequence", "species", "pmid")) + "\n")

    with (args.out_dir / "column_disagreement_vs_ensure.tsv").open("w", encoding="utf-8") as fh:
        fh.write("position	region	disagree	sides_compared	rate\n")
        for column, n in sorted(disagreement.items(), key=lambda kv: sort_key(kv[0])):
            total = sum(compared_sides.values())
            fh.write(f"{column}\t{region_of(column)}\t{n}\t{total}\t{n / total:.4f}\n")

    def top(view: str, code: str, n=15):
        items = [(col, counts[code]) for col, counts in totals[view].items() if counts.get(code)]
        return sorted(items, key=lambda kv: -kv[1])[:n]

    def rollup(view: str):
        agg = collections.defaultdict(collections.Counter)
        for col, counts in totals[view].items():
            for code, n in counts.items():
                agg[region_of(col)][code] += n
        return {k: dict(v) for k, v in sorted(agg.items(), key=lambda kv: (REGION_ORDER.index(kv[0]), kv[0])) if k in REGION_ORDER}

    summary = {
        "run_id": args.run_id,
        "ensure_rows": len(ensure_rows),
        "cases_numbered": len(numbered),
        "case_status": dict(status_counts),
        "numbering_source": dict(numbering_source),
        "numberings_reproducing_record_sequence": reproduced,
        "pairs_used_by_view": dict(used),
        "pairs_skipped": dict(skip),
        "coordinate_stability": dict(stability),
        "change_profiles": {v: dict(profiles[v]) for v in ("map", "ensure_js")},
        "anticodon_flagged_records": len(anticodon_flags),
        "anticodon_documented_vs_34_36": {
            f"{view}/{role}": {k: anticodon_check[(view, role)][k] for k in ("agrees", "differs", "absent")}
            for (view, role) in sorted(anticodon_check)},
        "pair_consistency": {
            v: {"pairs": consistency[v][2], "substitutions": consistency[v][0],
                "same_column": consistency[v][1],
                "rate": round(consistency[v][1] / consistency[v][0], 4) if consistency[v][0] else None}
            for v in ("map_edits", "ensure_edits")},
        "changed_columns_per_pair": {v: {str(k): change_histogram[v][k]
                                         for k in sorted(change_histogram[v])}
                                     for v in ("map", "ensure_js")},
        "disagreement_with_ENSURE": {
            "sides_compared": dict(compared_sides),
            "columns": len(disagreement),
            "worst": sorted(((col, n) for col, n in disagreement.items()), key=lambda kv: -kv[1])[:20],
            "mean_per_column": round(sum(disagreement.values()) / max(1, sum(compared_sides.values())), 4),
        },
        "ensure_table_positions": ensure_table_positions,
        "totals_by_view": {v: {c: sum(x.get(c, 0) for x in totals[v].values()) for c in CODES}
                           for v in VIEWS},
        "columns_with_signal": {v: sum(1 for x in totals[v].values()
                                       if any(x.get(c, 0) for c in CODES[1:])) for v in VIEWS},
        "top_mismatch": {v: top(v, "mismatch") for v in VIEWS},
        "top_insertion": {v: top(v, "insertion") for v in VIEWS},
        "top_deletion": {v: top(v, "deletion") for v in VIEWS},
        "region_rollup": {v: rollup(v) for v in VIEWS},
        "anticodon_reassignments": sorted(
            ({"parent_anticodon": pa, "sup_anticodon": va,
              "parent_codon": codon_of(pa), "target_codon": codon_of(va),
              "target": CODON_TABLE.get(codon_of(va), ""), "count": n}
             for (pa, va), n in reassignments.items()),
            key=lambda r: -r["count"]),
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    for key in ("ensure_rows", "cases_numbered", "numberings_reproducing_record_sequence",
                "pairs_used_by_view", "ensure_table_positions", "pairs_skipped", "coordinate_stability",
                "disagreement_with_ENSURE", "totals_by_view", "columns_with_signal"):
        print(f"{key}: {json.dumps(summary[key], ensure_ascii=False)}")
    for v in VIEWS:
        print(f"\n{v} top mismatch: {top(v, 'mismatch', 12)}")
    print(f"\nregion rollup (map): {json.dumps(summary['region_rollup']['map'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
