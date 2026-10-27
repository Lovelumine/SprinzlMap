#!/usr/bin/env python3
"""Put a recomputed Per-Position Alignment Variation table onto ENSURE.

ENSURE's chart reads ``engineered_sup_trna_perpos_counts`` through a generic
table endpoint, so the figure follows whatever the table holds.  The live table
is never edited in place: the new numbers go into a staging table and one
``RENAME TABLE`` swaps them, leaving the previous table under a dated name so
the change is reversible in a single statement.

Default is a preview.  ``--apply`` writes.
The previous table is kept under a dated name so the swap is reversible; pass
``--drop-old`` when the old numbers are already archived somewhere else, in which
case the swap drops it.

    python3 benchmark/scripts/push_perpos_to_ensure.py \
        --tsv research/paper/results/ensure_perpos/perpos_sprinzlmap_edits.tsv \
        --run-id 20261026-2028-ensure-suptna-external
    ... --apply
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "APP"))

import pymysql  # noqa: E402

from backend.infra.config.mysql_settings import load_shared_mysql_config  # noqa: E402

LIVE = "engineered_sup_trna_perpos_counts"
CODES = ("match", "mismatch", "insertion", "deletion")


def read_tsv(path: Path):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for rec in csv.DictReader(handle, delimiter="\t"):
            rows.append({
                "position": str(rec["position"]),
                **{code: int(rec.get(code) or 0) for code in CODES},
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tsv", type=Path, required=True, help="Recomputed table to publish")
    ap.add_argument("--run-id", required=True, help="SprinzlMap run the numbers came from")
    ap.add_argument("--apply", action="store_true", help="Perform the swap (default: preview only)")
    ap.add_argument("--drop-old", action="store_true",
                    help="After the swap, drop the archived table instead of keeping it")
    ap.add_argument("--suffix", default=dt.datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--keep-empty", action="store_true",
                    help="Keep positions whose four counts are all zero (dropped by default)")
    args = ap.parse_args()

    cfg = load_shared_mysql_config()
    if not cfg:
        raise SystemExit("SHARED_MYSQL_* is not configured")
    conn = pymysql.connect(**cfg)
    cur = conn.cursor(pymysql.cursors.DictCursor)

    new_rows = read_tsv(args.tsv)
    if not args.keep_empty:
        new_rows = [r for r in new_rows if any(r[code] for code in CODES)]
    cur.execute(f"SELECT * FROM {LIVE}")
    old_rows = {str(r["position"]): {code: int(r[code] or 0) for code in CODES} for r in cur.fetchall()}
    new_by_pos = {r["position"]: r for r in new_rows}

    changed = [
        (pos, old_rows[pos], new_by_pos[pos])
        for pos in sorted(new_by_pos, key=lambda p: (-abs(new_by_pos[p]["mismatch"] - old_rows.get(p, {}).get("mismatch", 0)), p))
        if pos in old_rows and new_by_pos[pos] != old_rows[pos]
    ]
    added = [p for p in new_by_pos if p not in old_rows]
    removed = [p for p in old_rows if p not in new_by_pos]

    def totals(rows):
        return {code: sum((r.get(code) or 0) for r in rows) for code in CODES}

    print(f"live table : {len(old_rows)} rows {totals(list(old_rows.values()))}")
    print(f"new table  : {len(new_by_pos)} rows {totals(new_rows)}")
    print(f"changed    : {len(changed)} rows | added: {len(added)} | removed: {len(removed)}")
    print("\nlargest changes at mismatch:")
    for pos, old, new in changed[:25]:
        print(f"  {pos:>7s}  mismatch {old['mismatch']:5d} -> {new['mismatch']:5d}"
              f"   ins {old['insertion']:4d} -> {new['insertion']:4d}"
              f"   del {old['deletion']:4d} -> {new['deletion']:4d}"
              f"   match {old['match']:5d} -> {new['match']:5d}")

    if not args.apply:
        print("\npreview only; pass --apply to write")
        conn.close()
        return

    staged = f"{LIVE}_staged_{args.suffix}"
    archived = f"{LIVE}_ensurejs_{args.suffix}"
    provenance = "engineered_sup_trna_perpos_provenance"
    cur.execute(f"DROP TABLE IF EXISTS {staged}")
    # Column set stays identical to the live table: ENSURE's chart endpoint reads
    # this table generically, so provenance lives only in the provenance table.
    cur.execute(
        f"CREATE TABLE {staged} ("
        "position VARCHAR(16) NOT NULL PRIMARY KEY,"
        "`match` INT NOT NULL DEFAULT 0, mismatch INT NOT NULL DEFAULT 0,"
        "insertion INT NOT NULL DEFAULT 0, deletion INT NOT NULL DEFAULT 0)"
        " CHARACTER SET utf8mb4"
    )
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.executemany(
        f"INSERT INTO {staged} (position, `match`, mismatch, insertion, deletion)"
        " VALUES (%s, %s, %s, %s, %s)",
        [(r["position"], r["match"], r["mismatch"], r["insertion"], r["deletion"])
         for r in new_rows],
    )
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {provenance} ("
        "id INT AUTO_INCREMENT PRIMARY KEY, table_name VARCHAR(64) NOT NULL,"
        "run_id VARCHAR(128) NOT NULL, source VARCHAR(64) NOT NULL, generated_at DATETIME NOT NULL,"
        "rows_replaced INT NOT NULL, archived_as VARCHAR(64) NOT NULL, note VARCHAR(255) NULL)"
    )
    cur.execute(
        f"INSERT INTO {provenance} (table_name, run_id, source, generated_at, rows_replaced, archived_as)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (LIVE, args.run_id, f"sprinzlmap:{args.run_id}", now, len(new_rows), archived),
    )
    cur.execute(f"RENAME TABLE {LIVE} TO {archived}, {staged} TO {LIVE}")
    if args.drop_old:
        cur.execute(f"DROP TABLE IF EXISTS {archived}")
    conn.commit()
    kept = "dropped" if args.drop_old else f"kept as {archived}"
    print(f"\nswapped: live table now SprinzlMap ({len(new_rows)} rows); previous table {kept}")
    conn.close()


if __name__ == "__main__":
    main()
