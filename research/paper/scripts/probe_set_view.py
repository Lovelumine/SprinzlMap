"""Check that a hand-built view survives set_view, or find what PyMOL does to it."""
import numpy as np
from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
cmd.reinitialize()
cmd.set("orthoscopic", 1)
cmd.load(ST + "/structures/DA1110.pdb", "m")

RING = "name N1+C2+N3+C4+C5+C6+N7+C8+N9"
normals = []
for r in (74, 75, 76):
    pts = cmd.get_coords(f"m and resi {r} and chain A and {RING}")
    normals.append(np.linalg.svd(pts - pts.mean(0))[2][2])
normals = np.array(normals)
forward = np.linalg.svd(normals)[2][2]
forward /= np.linalg.norm(forward)
print("normal set vs forward cos:", [round(float(abs(np.dot(n, forward))), 3) for n in normals], flush=True)

hint = np.array(cmd.get_atom_coords("m and resi 76 and name C1'")) - \
       np.array(cmd.get_atom_coords("m and resi 74 and name C1'"))
up = hint - forward * np.dot(hint, forward)
up /= np.linalg.norm(up)
basis = np.stack([np.cross(up, forward), up, forward], axis=1)
view = [*basis.flatten(), 0, 0, -100, 0, 0, 0, 1.0, 250.0, 0.0]
cmd.set_view(view)
back = cmd.get_view(0)
sight_back = np.array([back[2], back[5], back[8]])
print("set  forward:", np.round(forward, 3), flush=True)
print("read sight   :", np.round(sight_back / np.linalg.norm(sight_back), 3), flush=True)
print("read group0  :", np.round([back[0], back[3], back[6]], 3), flush=True)
print("read group1  :", np.round([back[1], back[4], back[7]], 3), flush=True)
print("cos(sight,forward):", round(float(abs(np.dot(sight_back, forward)) / np.linalg.norm(sight_back)), 4), flush=True)
cmd.quit()
