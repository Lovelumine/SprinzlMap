"""Build the AF3 tRNA 3D figures."""
from __future__ import annotations

import csv
import json
import math
import sys

import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from af3_render_lib import (DARK, GREEN, GREY, ORANGE, View, backbone_xyz, draw_model,
                         draw_text, kabsch, plddt_color, principal_axes, raster_disc,
                         read_residues, write_png)

REF = "/mnt/raid/alphafold3/databases/mmcif_files/1ehz.cif"
MODELS = "/mnt/raid/alphafold3/outputs/cca13"
MANIFEST = "/mnt/raid/alphafold3/inputs/cca13_manifest.json"
OUT = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13/figures"


def sprinzl_map(seqname):
    with open("/tmp/gold.tsv") as f:
        r = csv.DictReader(f, delimiter="\t")
        cols = r.fieldnames[1:]
        for row in r:
            if row["seqname"].split("|")[-1] == seqname:
                i, mp = 0, {}
                for c in cols:
                    b = (row.get(c) or "-").strip().upper()
                    if b not in ("-", ""):
                        i += 1
                        if c.isdigit():
                            mp.setdefault(int(c), i)
                return mp
    return {}


def superpose(model, mp, ref):
    common = [p for p in range(10, 71) if p in mp and p in ref and mp[p] in model]
    P = [model[mp[p]]["C3'"] for p in common]
    Q = [ref[p]["C3'"] for p in common]
    R, t = kabsch(P, Q)
    dev = np.sum(((np.array(P) @ R.T + t) - np.array(Q)) ** 2, axis=1)
    out = {}
    for i in sorted(model):
        d = {k: (tuple(np.asarray(v, float) @ R.T + t) if k not in ("_c", "_b") else v)
             for k, v in model[i].items()}
        out[i] = d
    return out, float(np.sqrt(dev.mean()))


def panel_lshape(res, title, sub, W=560, H=600, fs=2, rad=2.9):
    img = np.full((H, W, 3), 255, np.uint8)
    xyz = backbone_xyz(res)
    axes = principal_axes(xyz)
    v = View(xyz, axes, W, H - 44, margin=0.14)
    ids = [i for i in sorted(res) if "C3'" in res[i]]
    draw_model(img, res, v, lambda i: plddt_color(res[i].get("_b", 80)), rad)
    for idx, col, r in [(ids[0], DARK, 2.2 * fs), (ids[-1], (200, 30, 35), 2.8 * fs)]:
        x, y, _ = v.to2d(res[idx]["C3'"])
        raster_disc(img, x, y, r, col)
    draw_text(img, 8, 8, title, DARK, fs)
    draw_text(img, 8, 8 + 17 * fs, sub, (90, 90, 100), fs)
    return img


def panel_superpose(ref, mt, mf, nt, nf, dt, df):
    W, H = 880, 720
    AH = 560
    img = np.full((H, W, 3), 255, np.uint8)
    xyz = backbone_xyz(ref)
    axes = principal_axes(xyz)
    v = View(xyz, axes, W, AH, margin=0.13)

    draw_model(img, ref, v, lambda i: GREY, 2.6)
    draw_model(img, mt, v, lambda i: GREEN, 3.3)
    draw_model(img, mf, v, lambda i: ORANGE, 3.3)

    p76 = ref[76]["C3'"]
    x76, y76, _ = v.to2d(p76)
    pts = [("1", p76, DARK, x76, y76)]
    for tag, model, col in [("2", mt, GREEN), ("3", mf, ORANGE)]:
        p = model[max(model)]["C3'"]
        x, y, _ = v.to2d(p)
        pts.append((tag, p, col, x, y))
    # leader lines from A76 to each predicted 3' terminus
    for tag, _, col, x, y in pts[1:]:
        n = 40
        for i in range(n):
            if i % 3 == 2:
                continue
            u0, u1 = i / n, (i + 1) / n
            raster_disc(img, x76 + (x - x76) * u0, y76 + (y - y76) * u0, 1.5, col)
    for tag, _, col, x, y in pts:
        raster_disc(img, x, y, 9.0, col)
        draw_text(img, x + 12, y - 8, tag, col, 3)

    draw_text(img, 14, 12, "AF3 3' TERMINUS PLACEMENT vs 1EHZ CRYSTAL", DARK, 3)
    draw_text(img, 14, 38, "ALL CHAINS SUPERPOSED ON SPRINZL CORE 10-70", (95, 95, 105), 2)

    yb = AH + 20
    rows = [
        (GREY, "1EHZ CRYSTAL  (REFERENCE FRAME)"),
        (GREEN, f"2  {nt}  TRUE CCA TAIL      3' END -> A76 = {dt:.1f} A"),
        (ORANGE, f"3  {nf}  CCA-LIKE END       3' END -> A76 = {df:.1f} A"),
        (DARK, "1  A76  ACCEPTOR POSITION IN 1EHZ"),
    ]
    for i, (col, txt) in enumerate(rows):
        raster_disc(img, 26, yb + i * 26 + 7, 6.0, col)
        draw_text(img, 44, yb + i * 26, txt, col, 2)
    draw_text(img, 14, H - 24, "1EHZ SPANS 15.0 A FROM A73 TO C76, SO THE TWO CLASSES ARE UNAMBIGUOUSLY SEPARATED",
              (95, 95, 105), 2)
    return img


def main():
    ref = read_residues(REF)
    man = json.load(open(MANIFEST))
    by = {info["id"].split("|")[-1]: (code, info) for code, info in man.items()}
    true_id, false_id = "DH0660", "DI1600"
    ct, it = by[true_id]
    cf, iff = by[false_id]

    mt_raw = read_residues(f"{MODELS}/{ct}/{ct}_model.cif")
    mf_raw = read_residues(f"{MODELS}/{cf}/{cf}_model.cif")
    mt, rt = superpose(mt_raw, sprinzl_map(true_id), ref)
    mf, rf = superpose(mf_raw, sprinzl_map(false_id), ref)

    p76 = np.array(ref[76]["C3'"])
    dt = float(np.linalg.norm(np.array(mt[max(mt)]["C3'"]) - p76))
    df = float(np.linalg.norm(np.array(mf[max(mf)]["C3'"]) - p76))
    print(f"{true_id} coreRMSD={rt:.2f} 3'->A76={dt:.1f} | {false_id} coreRMSD={rf:.2f} 3'->A76={df:.1f}")

    a = panel_lshape(mt_raw, "AF3 DH0660  TRUE CCA TAIL", "L SHAPE, VIEW ALONG PRINCIPAL AXES, 76 NT")
    b = panel_lshape(mf_raw, "AF3 DI1600  CCA-LIKE END", "L SHAPE, VIEW ALONG PRINCIPAL AXES, 74 NT")
    legend = np.full((46, a.shape[1] * 2, 3), 255, np.uint8)
    draw_text(legend, 14, 6, "PLDDT  >90 DARK GREEN   70-90 GREEN   50-70 YELLOW   <50 ORANGE", (110, 110, 120), 2)
    draw_text(legend, 14, 26, "BLACK DOT = 5' END      RED DOT = 3' END", (110, 110, 120), 2)
    pair = np.concatenate([np.concatenate([a, b], axis=1), legend], axis=0)
    write_png(f"{OUT}/fig_af3_lshape_pair.png", pair)

    c = panel_superpose(ref, mt, mf, true_id, false_id, dt, df)
    write_png(f"{OUT}/fig_af3_superpose.png", c)

    cols, rows = 5, 3
    cw, ch = 300, 320
    grid = np.full((ch * rows + 30, cw * cols, 3), 255, np.uint8)
    order = ["TAA", "TAB", "TAC", "TAD", "TAE", "TAF", "TAG", "TAH", "TAI", "TAJ", "TAK", "TAL", "TAM"]
    for n, code in enumerate(order):
        sid = man[code]["id"].split("|")[-1]
        res = read_residues(f"{MODELS}/{code}/{code}_model.cif")
        mres, _ = superpose(res, sprinzl_map(sid), ref)
        d = float(np.linalg.norm(np.array(mres[max(mres)]["C3'"]) - p76))
        tag = "TRUE CCA " if code in ("TAA", "TAB", "TAC", "TAD", "TAE") else "CCA-LIKE "
        img = panel_lshape(res, f"{sid} {tag}", f"{man[code]['terminal10']}  3'->76 {d:.1f} A",
                           W=cw, H=ch, fs=1, rad=1.7)
        r, c2 = divmod(n, cols)
        grid[r * ch:(r + 1) * ch, c2 * cw:(c2 + 1) * cw] = img
    draw_text(grid, 10, ch * rows + 6, "AF3 PREDICTIONS, 13 CCA-TAIL EXAMPLES FROM FIG 5A. "
              "DISTANCE MEASURED AFTER CORE SUPERPOSITION ON 1EHZ (73->76 SPANS 15.0 A)", DARK, 2)
    write_png(f"{OUT}/fig_af3_grid13.png", grid)
    print("wrote figures to", OUT)


if __name__ == "__main__":
    main()
