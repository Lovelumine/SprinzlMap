"""Render two calibration balls whose pixel positions give PyMOL's ortho scale.

Framing has to be computed by hand because headless `zoom` fits to a phantom
window, and that computation needs the real pixels-per-angstrom on each axis for
a non-square canvas. Each ball sits at a known model offset from the view centre
at a known camera distance, so measuring its ink centroid in the PNG gives the
half-extent constant used by view_for():

    python3 -c "see below"   # centroid px of each ball, then
    k = offset * n_px / ((n_px/2 - centroid) * 2 * distance)
"""
import os

from pymol import cmd

OUT = "/tmp/ortho_calib"
os.makedirs(OUT, exist_ok=True)
DIST, OFFSET = 100.0, 8.0
W, H = 1500, 2200

cmd.reinitialize()
cmd.bg_color("white")
for key, val in {"orthoscopic": 1, "ray_trace_fog": 0, "depth_cue": 0,
                 "antialias": 0, "ray_shadow": 0}.items():
    cmd.set(key, val)
cmd.set_view([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -DIST, 0, 0, 0, 1.0, 2 * DIST + 50, 0])

for axis in (0, 1):
    cmd.delete("all")
    pos = [0.0, 0.0, 0.0]
    pos[axis] = OFFSET
    cmd.pseudoatom("ball", pos=pos)
    cmd.show("spheres", "ball")
    cmd.set("sphere_scale", 1.2, "ball")
    cmd.color("black", "ball")
    cmd.ray(W, H, 1, 1)
    cmd.png(os.path.join(OUT, f"axis{axis}.png"))
    print("wrote", axis, flush=True)
cmd.quit()
