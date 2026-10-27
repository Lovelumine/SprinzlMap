"""Stacking geometry of AF3 3'-CCA/CAA tails against the 5UD5 chain-D A76 reference.

Replaces the single C3'-C3' distance with three quantities that actually describe
whether a terminal base occupies A76's stacking position:

  - ``ring_centre_distance`` : centre-to-centre distance between base rings.
  - ``face_gap``            : gap along the A76 ring normal (pi-stacking is
                              3.3-3.5 A; ~0 A means coplanar, ~10 A means the
                              base has left the stacking plane entirely).
  - ``lateral_offset``      : in-plane displacement after removing the normal
                              component (how far the ring slides out of overlap).

``angle`` is the inter-plane angle between the base normal and the A76 normal.

Reference is 5UD5 chain D (the crystal tRNA with a genuine mature CCA), not 1EHZ.
See ``docs/`` notes: the legacy ``d_3prime_to_A76`` in ``metrics.json`` was
measured against 1EHZ and is therefore not comparable with the 5UD5 panels.

    python3 research/paper/scripts/analyze_cca_stacking.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

RES = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
STRUCT = RES / "structures"
REF_PDB = STRUCT / "5ud5_chainD_superposed.pdb"
OUT_JSON = RES / "stacking_geometry.json"

# Base ring atoms. Purine first; fall back to pyrimidine.
PURINE = ("N1", "C2", "N3", "C4", "C5", "C6", "N7", "C8", "N9")
PYRIMIDINE = ("N1", "C2", "N3", "C4", "C5", "C6")

MODELS = {"DA1110": 76, "DA0940": 77, "DX1480": 74, "DL2150": 84}


def parse_pdb(path: Path) -> dict[int, dict[str, np.ndarray]]:
    residues: dict[int, dict[str, np.ndarray]] = {}
    for line in path.read_text().splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if line[16] not in (" ", "A"):
            continue
        try:
            resi = int(line[22:26])
        except ValueError:
            continue
        name = line[12:16].strip()
        element = line[76:78].strip() or name[0]
        if element == "H":
            continue
        residues.setdefault(resi, {})[name] = np.array(
            [float(line[30:38]), float(line[38:46]), float(line[46:54])]
        )
    return residues


def ring_plane(atoms: dict[str, np.ndarray]):
    """Least-squares plane through the base ring; returns (centre, unit normal)."""
    names = PURINE if all(n in atoms for n in PURINE) else PYRIMIDINE
    pts = np.array([atoms[n] for n in names if n in atoms])
    if len(pts) < 3:
        return None, None
    centre = pts.mean(0)
    _u, _s, vt = np.linalg.svd(pts - centre)
    normal = vt[2]
    return centre, normal / np.linalg.norm(normal)


def stacking_metrics(centre: np.ndarray, normal: np.ndarray,
                     ref_centre: np.ndarray, ref_normal: np.ndarray) -> dict:
    delta = centre - ref_centre
    face_gap = abs(float(np.dot(delta, ref_normal)))
    lateral = delta - np.dot(delta, ref_normal) * ref_normal
    cos = abs(float(np.dot(normal, ref_normal)))
    return {
        "ring_centre_distance": round(float(np.linalg.norm(delta)), 2),
        "face_gap": round(face_gap, 2),
        "lateral_offset": round(float(np.linalg.norm(lateral)), 2),
        "angle": round(float(np.degrees(np.arccos(min(1.0, cos)))), 1),
    }


def classify(m: dict) -> str:
    """Bucket the terminal base relative to A76's stacking site."""
    if m["face_gap"] <= 1.5 and m["angle"] <= 25.0:
        return "coplanar_displaced" if m["lateral_offset"] >= 3.0 else "stacked"
    if m["face_gap"] >= 5.0 or m["angle"] >= 35.0:
        return "displaced"
    return "partial"


def main() -> None:
    ref = parse_pdb(REF_PDB)
    ref_centre, ref_normal = ring_plane(ref[76])
    ref_tail = {r: ring_plane(ref[r]) for r in (74, 75, 76) if r in ref}

    report: dict[str, dict] = {
        "reference": {
            "pdb": str(REF_PDB),
            "id": "5UD5 chain D",
            "a76_centre": [round(float(v), 2) for v in ref_centre],
            "a76_normal": [round(float(v), 4) for v in ref_normal],
        },
        "conventions": {
            "ring_centre_distance": "base-ring centroid to A76 ring centroid, Angstrom",
            "face_gap": "displacement projected on the A76 ring normal, Angstrom; "
                        "canonical pi-stacking is 3.3-3.5",
            "lateral_offset": "in-plane displacement of the ring centroid, Angstrom; "
                              "ring overlap requires this to be small",
            "angle": "angle between base-ring normal and A76 ring normal, degrees",
        },
        "models": {},
    }

    for sid, last in MODELS.items():
        residues = parse_pdb(STRUCT / f"{sid}.pdb")
        tail = [last - 2, last - 1, last]
        per_residue = []
        for k, resi in enumerate(tail):
            centre, normal = ring_plane(residues[resi])
            ref_resi = 74 + k
            entry = {
                "resi": resi,
                "ref_resi": ref_resi if ref_resi in ref_tail else None,
                "terminal": resi == last,
            }
            entry.update(stacking_metrics(centre, normal, ref_centre, ref_normal))
            if entry["ref_resi"] is not None:
                rc, rn = ref_tail[ref_resi]
                entry["vs_equivalent_ref_residue"] = stacking_metrics(
                    centre, normal, rc, rn)
            per_residue.append(entry)

        terminal = per_residue[-1]
        report["models"][sid] = {
            "terminal_residue": last,
            "tail_residues": tail,
            "terminal": {k: terminal[k] for k in
                         ("ring_centre_distance", "face_gap",
                          "lateral_offset", "angle")},
            "classification": classify(terminal),
            "per_residue": per_residue,
        }

    OUT_JSON.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{OUT_JSON}  ({OUT_JSON.stat().st_size} bytes)\n")

    header = f"{'model':8} {'class':20} {'d_centre':>9} {'face_gap':>9} {'lateral':>8} {'angle':>7}"
    print(header)
    print("-" * len(header))
    for sid, m in report["models"].items():
        t = m["terminal"]
        print(f"{sid:8} {m['classification']:20} {t['ring_centre_distance']:9.2f} "
              f"{t['face_gap']:9.2f} {t['lateral_offset']:8.2f} {t['angle']:7.1f}")


if __name__ == "__main__":
    main()
