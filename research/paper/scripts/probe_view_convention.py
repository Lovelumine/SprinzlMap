"""Decide whether PyMOL's view matrix holds the camera axes as rows or as columns.

A cyclic permutation of the identity - rows (0,0,1), (1,0,0), (0,1,0), det +1 -
separates the two readings cleanly: if the rows are the camera axes then the
model +y axis is the sight line, so a ball on +y lands dead centre; if the
columns are, then +y is screen right and the ball lands right of centre.

Measured here: +y lands right, +z lands up, +x lands on the sight line, so the
axes are the columns - equivalently every third float of the flat view, which is
what render_terminal_faceon.py relies on. An identity view cannot show this,
because the identity is its own transpose.
"""
import os

from pymol import cmd

OUT = "/tmp/ortho_conv"
os.makedirs(OUT, exist_ok=True)
cmd.reinitialize()
cmd.bg_color("white")
for key, val in {"orthoscopic": 1, "antialias": 0, "ray_shadow": 0,
                 "ray_trace_fog": 0, "depth_cue": 0}.items():
    cmd.set(key, val)
cmd.set_view([0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, -60, 0, 0, 0, 1.0, 170.0, 0.0])
for axis, colour in ((1, "black"), (0, "red"), (2, "blue")):
    cmd.delete("all")
    pos = [0.0, 0.0, 0.0]
    pos[axis] = 8.0
    cmd.pseudoatom("ball", pos=pos)
    cmd.show("spheres", "ball")
    cmd.set("sphere_scale", 1.0, "ball")
    cmd.color(colour, "ball")
    cmd.ray(900, 900, 1, 1)
    cmd.png(os.path.join(OUT, f"axis{axis}.png"))
    print("wrote", axis, colour, flush=True)
cmd.quit()
