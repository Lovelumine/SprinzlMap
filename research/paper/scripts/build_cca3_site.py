#!/usr/bin/env python3
"""Build scene.json for the 3'-CCA/CAA terminal-base website.

Reads the Kabsch-superposed AF3 models and a complete 5UD5 tRNA reference (same frame as
the A-D publication figures) and emits per-residue atoms, bonds, backbone
polyline and base-ring plane normals, so the browser does no parsing at all.
"""
import json
import math
import os

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
REF = ("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/"
       "af3_cca4_20261019/structures/5ud5_chainD_superposed.pdb")
MODELS = os.path.join(ST, "structures")
OUT = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/site_cca3/data/scene.json"

PANELS = [
    ("DA1110", "CCA", True, 76, None),
    ("DA0940", "CCA", True, 77, None),
    ("DX1480", "CAA", False, 74, None),
    ("DL2150", "CCA", False, 84, None),
]
GREEN, ORANGE = "#127a42", "#e28118"

PURINE = ["N9", "C8", "N7", "C5", "C6", "N1", "C2", "N3", "C4"]
PYRIM = ["N1", "C2", "N3", "C4", "C5", "C6"]
SUGAR = {"C1'", "C2'", "C3'", "C4'", "C5'", "O2'", "O3'", "O4'", "O5'"}
PHOS = {"P", "OP1", "OP2", "OP3"}
RCOV = {"C": 0.76, "N": 0.71, "O": 0.66, "P": 1.07}


def element(name):
    n = name.strip()
    for ch in n:
        if ch.isalpha():
            return ch.upper() if ch.upper() != "C" or len(n) <= 2 else "C"
    return "C"


def atom_class(name, resn):
    if name in SUGAR:
        return "sugar"
    if name in PHOS:
        return "phosphate"
    ring = PURINE if resn in ("A", "G") else PYRIM
    if name in ring:
        return "ring"
    return "exocyclic"


def parse(path):
    """-> list of residues, each {resi, name, atoms:[(name,el,x,y,z)]}"""
    res, last = [], None
    for line in open(path):
        if not line.startswith(("ATOM", "HETATM")):
            continue
        name = line[12:16].strip()
        resn = line[17:20].strip().replace("DA", "A").replace("DC", "C")
        resn = resn.replace("DG", "G").replace("DT", "U").replace("DU", "U")
        if resn not in ("A", "C", "G", "U"):
            continue
        resi = int(line[22:26])
        x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
        if last is None or last["resi"] != resi or last["name"] != resn:
            last = {"resi": resi, "name": resn, "atoms": []}
            res.append(last)
        last["atoms"].append((name, element(name), x, y, z))
    return res


def newell(pts):
    n = [0.0, 0.0, 0.0]
    k = len(pts)
    for i in range(k):
        x1, y1, z1 = pts[i]
        x2, y2, z2 = pts[(i + 1) % k]
        n[0] += (y1 - y2) * (z1 + z2)
        n[1] += (z1 - z2) * (x1 + x2)
        n[2] += (x1 - x2) * (y1 + y2)
    return n


def ring_order(names, resn):
    """Ring atoms in cyclic order (for Newell sign reference)."""
    seq = PURINE if resn in ("A", "G") else PYRIM
    return [n for n in seq if n in names]


def build_chain(res, detail_from=1):
    out = []
    for r in res:
        names = [a[0] for a in r["atoms"]]
        xyz = [a[2:] for a in r["atoms"]]
        ring = [i for i, n in enumerate(names) if atom_class(n, r["name"]) == "ring"]
        info = {
            "resi": r["resi"],
            "name": r["name"],
            "atoms": [
                {"n": nm, "el": el, "p": [round(v, 3) for v in (x, y, z)],
                 "cls": atom_class(nm, r["name"])}
                for nm, el, x, y, z in r["atoms"]
            ],
            "bonds": [],
            "ring": ring,
        }
        if r["resi"] >= detail_from:
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    ei, ej = r["atoms"][i][1], r["atoms"][j][1]
                    d = math.dist(xyz[i], xyz[j])
                    if d < 1.25 * (RCOV[ei] + RCOV[ej]):
                        info["bonds"].append([i, j])
        if ring:
            cyc = ring_order(names, r["name"])
            idx = [names.index(c) for c in cyc if c in names]
            cen = [sum(xyz[i][k] for i in ring) / len(ring) for k in range(3)]
            nrm = newell([xyz[i] for i in idx])          # exact for a planar ring
            m = math.sqrt(sum(t * t for t in nrm)) or 1.0
            nrm = [t / m for t in nrm]
            info["plane"] = {"normal": [round(t, 4) for t in nrm],
                             "centroid": [round(v, 3) for v in cen],
                             "ring": idx}
        bb = next((i for i, n in enumerate(names) if n == "C3'"), None)
        info["c3"] = [round(v, 3) for v in xyz[bb]] if bb is not None else None
        out.append(info)
    # phosphate ester bonds to the next residue
    for a, b in zip(out, out[1:]):
        ia = next((i for i, at in enumerate(a["atoms"]) if at["n"] == "O3'"), None)
        ib = next((i for i, at in enumerate(b["atoms"]) if at["n"] == "P"), None)
        if ia is not None and ib is not None:
            d = math.dist(a["atoms"][ia]["p"], b["atoms"][ib]["p"])
            if d < 1.9:
                b["inter"] = [ia, d]
    return out


def stacking_geometry(model_tail, ref_a76):
    """Terminal-base stacking relation against the 5UD5 A76 ring.

    Returns ring-centre distance, face gap (along the A76 normal), lateral
    in-plane offset and inter-plane angle. ``meanNormal`` is deliberately not
    used here: averaging the normals of three flexibly linked nucleotides does
    not describe a stacking plane.
    """
    term = model_tail[-1]
    if "plane" not in term:
        return None
    c = term["plane"]["centroid"]
    n = term["plane"]["normal"]
    rc = ref_a76["plane"]["centroid"]
    rn = ref_a76["plane"]["normal"]
    delta = [c[k] - rc[k] for k in range(3)]
    gap = sum(delta[k] * rn[k] for k in range(3))
    lateral = [delta[k] - gap * rn[k] for k in range(3)]
    lat = math.sqrt(sum(t * t for t in lateral))
    cos = abs(sum(n[k] * rn[k] for k in range(3)))
    dist = math.sqrt(sum(t * t for t in delta))
    angle = math.degrees(math.acos(min(1.0, cos)))
    if gap <= 1.5 and angle <= 25.0:
        cls = "coplanar_displaced" if lat >= 3.0 else "stacked"
    elif gap >= 5.0 or angle >= 35.0:
        cls = "displaced"
    else:
        cls = "partial"
    return {"ringCentreDistance": round(dist, 2),
            "faceGap": round(abs(gap), 2),
            "lateralOffset": round(lat, 2),
            "angle": round(angle, 1),
            "classification": cls}


def main():
    metrics = json.load(open(os.path.join(ST, "metrics.json")))
    ref = build_chain(parse(REF), detail_from=62)
    c3_76 = next(r["c3"] for r in ref if r["resi"] == 76)
    ref_a76 = next(r for r in ref if r["resi"] == 76)
    models = []
    for sid, end3, is_cca, n, dist in PANELS:
        res = parse(os.path.join(MODELS, sid + ".pdb"))
        ch = build_chain(res, detail_from=max(1, n - 12))
        tail = [r for r in ch if r["resi"] > n - 3]
        normals = [r["plane"]["normal"] for r in tail if "plane" in r]
        oriented = []
        for nn in normals:
            seed = oriented[0] if oriented else nn
            dot = sum(a * b for a, b in zip(seed, nn))
            oriented.append(nn if dot >= 0 else [-t for t in nn])
        mean = [sum(v) / len(v) for v in zip(*oriented)] if oriented else [0, 0, 1]
        m = math.sqrt(sum(t * t for t in mean)) or 1.0
        mean = [t / m for t in mean]
        c3s = [r["c3"] for r in tail if r["c3"]]
        mt = metrics.get(sid, {})
        distance = math.dist(c3s[-1], c3_76) if c3s else None
        models.append({
            "id": sid, "end3": end3, "bonafide": is_cca,
            "color": GREEN if is_cca else ORANGE,
            "length": n, "distance": round(distance, 2) if distance is not None else None,
            "stacking": stacking_geometry(tail, ref_a76),
            "status": mt.get("status", ""), "group": mt.get("group", ""),
            "meanPlddt": mt.get("mean_plddt"), "tailPlddt": mt.get("tail_plddt"),
            "ptm": mt.get("ptm"), "sample": mt.get("top_sample"),
            "tailResi": [r["resi"] for r in tail],
            "detailFrom": max(1, n - 12),
            "meanNormal": [round(t, 4) for t in mean],
            "tailC3": c3s[-1] if c3s else None,
            "chain": ch,
        })
    scene = {
        "palette": {"green": GREEN, "orange": ORANGE,
                    "grey": "#9ba3ad", "dark": "#2c2f36"},
        "reference": {"id": "5UD5 (链D)", "length": max(r["resi"] for r in ref),
                      "a76C3": c3_76,
                      "a76Ring": ref_a76["plane"]["centroid"] if "plane" in ref_a76 else c3_76,
                      "chain": ref},
        "models": models,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(scene, fh, separators=(",", ":"))
    print(OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
