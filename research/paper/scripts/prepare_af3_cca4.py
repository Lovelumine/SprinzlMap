"""Place the 2026-10-19 AF3 rerun into the 1EHZ frame and measure 3' ends.

Models are superposed into the 1EHZ frame, but 3'-end distances are measured
against 5UD5 chain D so they agree with the paper panels and with
``analyze_cca_stacking.py``. The legacy 1EHZ-relative value is kept as
``d_3prime_to_1EHZ_A76`` for traceability.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

NEW = Path("/mnt/raid/alphafold3/outputs/cca4_20261019/final")
OLD = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca13/webview/structures")
OUT = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
IDS = ("DA1110", "DA0940", "DL2150", "DX1480")
GROUPS = {"DA1110": "ref_CCA", "DA0940": "ref_CCA",
          "DL2150": "CCA_like_no_ref", "DX1480": "CCA_like_no_ref"}


def read_metrics() -> dict[str, dict]:
    rows = {}
    for line in (NEW / "metrics.tsv").read_text().splitlines()[1:]:
        f = line.split("\t")
        rows[f[0]] = {"length": int(f[1]), "end3": f[2], "mean_plddt": float(f[3]),
                      "ptm": float(f[4]), "ranking": float(f[5]), "top_sample": f[6]}
    return rows


def parse_pdb(path: Path):
    lines = path.read_text().splitlines()
    atoms = {}
    for line in lines:
        if line.startswith(("ATOM", "HETATM")):
            key = (line[21], line[22:27].strip(), line[12:16].strip())
            atoms[key] = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
    return lines, atoms


def kabsch(mobile: np.ndarray, target: np.ndarray):
    mc, tc = mobile.mean(0), target.mean(0)
    u, _s, vt = np.linalg.svd((mobile - mc).T @ (target - tc))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, tc - mc @ r.T


def transform_lines(lines, r, t):
    out = []
    for line in lines:
        if line.startswith(("ATOM", "HETATM")):
            p = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]) @ r.T + t
            line = f"{line[:30]}{p[0]:8.3f}{p[1]:8.3f}{p[2]:8.3f}{line[54:]}"
        out.append(line)
    return "\n".join(out) + "\n"


def orientation(ref_atoms):
    def at(resi, name="C3'"):
        return ref_atoms[("A", str(resi), name)]

    elbow = np.mean([at(i) for i in range(44, 49)], axis=0)
    long_tip = at(76)
    short_tip = np.mean([at(i) for i in range(32, 39)], axis=0)
    y = long_tip - elbow
    y /= np.linalg.norm(y)
    x = short_tip - elbow
    x = x - np.dot(x, y) * y
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    tail = np.mean([at(i) for i in range(74, 77)], axis=0)
    if np.dot(tail - elbow, z) < 0:
        x, z = -x, -z
    r = np.stack([x, y, z])
    centre = np.mean([v for k, v in ref_atoms.items() if k[2] == "C3'"], axis=0)
    return r, centre


def transform_lines_3x3(lines, r, t):
    out = []
    for line in lines:
        if line.startswith(("ATOM", "HETATM")):
            p = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]) @ r.T + t
            line = f"{line[:30]}{p[0]:8.3f}{p[1]:8.3f}{p[2]:8.3f}{line[54:]}"
        out.append(line)
    return "\n".join(out) + "\n"


def main():
    meta = read_metrics()
    ref_lines, ref_atoms = parse_pdb(OLD / "1ehz.pdb")
    # The paper compares against 5UD5 chain D (a crystal tRNA carrying a genuine
    # mature CCA), not against 1EHZ. 1EHZ and 5UD5 put A76 C3' ~12 A apart, so
    # measuring against the wrong reference silently shifts every reported value.
    a76_1ehz = ref_atoms[("A", "76", "C3'")]
    _5ud5_lines, ref5 = parse_pdb(OUT / "structures" / "5ud5_chainD_superposed.pdb")
    a76 = ref5[("A", "76", "C3'")]
    r_orient, c_orient = orientation(ref_atoms)
    oriented = OUT / "structures_oriented"
    oriented.mkdir(exist_ok=True)
    (oriented / "1ehz.pdb").write_text(
        transform_lines_3x3(ref_lines, r_orient, -r_orient @ c_orient))
    (OUT / "orientation.json").write_text(
        json.dumps({"rotation": r_orient.tolist(), "centre": c_orient.tolist()}, indent=2) + "\n")
    summary = {}
    for sid in IDS:
        old_lines, old_sup = parse_pdb(OLD / f"{sid}_superposed.pdb")
        new_lines, new_atoms = parse_pdb(NEW / sid / "pdb" / f"{sid}_model.pdb")
        keys = sorted(k for k in new_atoms if k[2] == "C3'" and k in old_sup)
        mobile = np.array([new_atoms[k] for k in keys])
        target = np.array([old_sup[k] for k in keys])
        r, t = kabsch(mobile, target)
        placed = OUT / "structures" / f"{sid}.pdb"
        placed.write_text(transform_lines(new_lines, r, t))
        r_comb = r_orient @ r
        t_comb = r_orient @ (t - c_orient)
        placed_oriented = oriented / f"{sid}.pdb"
        placed_oriented.write_text(transform_lines(new_lines, r_comb, t_comb))

        moved = {k: v @ r.T + t for k, v in new_atoms.items()}
        last = meta[sid]["length"]
        tail = tuple(str(i) for i in range(last - 2, last + 1))
        resname = {}
        for line in new_lines:
            if line.startswith("ATOM"):
                resname[line[22:27].strip()] = line[17:20].strip()
        triplet = "".join(resname[i] for i in tail)
        d376 = float(np.linalg.norm(moved[("A", str(last), "C3'")] - a76))
        d376_1ehz = float(np.linalg.norm(moved[("A", str(last), "C3'")] - a76_1ehz))
        bfac = []
        for line in new_lines:
            if line.startswith("ATOM") and line[22:27].strip() in tail:
                bfac.append(float(line[60:66]))
        status = ("bona fide 3′-CCA" if GROUPS[sid] == "ref_CCA"
                  else ("3′-CCA, displaced" if triplet == "CCA" else f"3′-{triplet}, no CCA"))
        summary[sid] = {
            "length": last, "end3": triplet, "group": GROUPS[sid], "status": status,
            "mean_plddt": meta[sid]["mean_plddt"], "tail_plddt": round(float(np.mean(bfac)), 1),
            "ptm": meta[sid]["ptm"], "top_sample": meta[sid]["top_sample"],
            "d_3prime_to_A76": round(d376, 2),
            "d_3prime_to_A76_reference": "5UD5_chainD",
            "d_3prime_to_1EHZ_A76": round(d376_1ehz, 2),
            "canonical": GROUPS[sid] == "ref_CCA",
            "tail_residues": tail,
            "pdb": str(placed),
            "pdb_oriented": str(placed_oriented),
        }
        print(f"{sid}  len {last}  end3 {triplet}  d(5UD5 A76 C3') {d376:5.2f} A  "
              f"pLDDT {meta[sid]['mean_plddt']:.1f}  canonical={GROUPS[sid] == 'ref_CCA'}")
    (OUT / "metrics.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
