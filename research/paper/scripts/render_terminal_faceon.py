"""PyMOL views of the 3'-CCA end, with the camera straight down its base planes.

The terminal trinucleotide is the subject: the camera is centred on it and zoomed
to it, so the CCA fills the frame and the rest of the fold only runs through the
picture as context. Only the last three residues are drawn as rings, i.e. sticks;
the rest of the chain is the nucleic-acid cartoon, the acceptor arm one wash of
the colour and the distant arms a deeper one. The camera direction is the shared
normal of the terminal three ring planes, which keeps all three open and readable
in the same frame; a companion view tilts 35 deg so the stacked rings fan apart
laterally. A third view adds the 5UD5 chain-D CCA, the crystal tRNA that carries
a genuine mature 3'-CCA.

PyMOL's headless `zoom` fits to a phantom window, so the framing is computed
here instead: with `orthoscopic` on, the visible half-height is 0.176 x the
camera distance, which turns a projected extent into an exact camera distance.
The window is square because a terminal end seen down its own base planes is
about as wide as it is tall, and the ends that differ - splayed versus stacked -
differ in both directions at once. Every render is checked for the one thing that
would spoil it - a context atom nearer the camera than a terminal ring and
landing on it in projection - and the count lands in occlusion.txt.
"""
import os

import numpy as np
from pymol import cmd

ST = "/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019"
MD = os.path.join(ST, "structures")
OUT = os.path.join(ST, "figures_terminal_pymol")
WIDTH, HEIGHT = 1800, 1800
FILL = float(os.environ.get("CCA_FILL", 0.85))
ORTHO_K = float(os.environ.get("CCA_KY", 0.1760))   # visible half-height = K x distance
ORTHO_KX = ORTHO_K * WIDTH / HEIGHT                 # same model-per-pixel on the other axis
# the end is framed close, so a cartoon tube drawn at its usual radius would be a
# cable: these are sized for roughly 60 px per angstrom
TUBE_R, RUNG_R, LOOP_R = 0.075, 0.035, 0.09

PANELS = [("DA1110", "green", 76), ("DA0940", "green", 77),
          ("DX1480", "orange", 74), ("DL2150", "orange", 84)]
RGB = {"green": (0.071, 0.478, 0.259), "orange": (0.886, 0.506, 0.094),
       "slate": (0.322, 0.427, 0.525)}
# how far each context layer is washed toward white: the arm the CCA sits on stays
# legible, the rest of the fold recedes behind it
FADE = {"_mid": 0.46, "_wash": 0.80}
for name, rgb in list(RGB.items()):
    for suffix, amount in FADE.items():
        RGB[name + suffix] = tuple(c + (1 - c) * amount for c in rgb)
RING = "name N1+C2+N3+C4+C5+C6+N7+C8+N9"
RING_NAMES = {"N1", "C2", "N3", "C4", "C5", "C6", "N7", "C8", "N9"}


def plane_normal(sel):
    """Least-squares normal of a base ring, from its ring atoms."""
    pts = cmd.get_coords(sel + f" and {RING}")
    return np.linalg.svd(pts - pts.mean(axis=0))[2][2]


def face_on(obj, resi):
    """Camera basis: forward down the shared plane normal, up along the tail axis.

    The shared normal is the principal direction of the three ring normals, not
    their least-squares residual - the bases of a stack are close to parallel, so
    the direction of least variance between them is across the stack and would
    point the camera exactly edge-on.
    """
    normals = np.array([plane_normal(f"{obj} and resi {r} and chain A") for r in resi])
    normals *= np.sign([np.dot(n, normals[0]) or 1.0 for n in normals])[:, None]
    forward = np.linalg.svd(normals)[2][0]
    forward /= np.linalg.norm(forward)

    first = np.array(cmd.get_atom_coords(f"{obj} and resi {resi[0]} and name C1'"))
    last = np.array(cmd.get_atom_coords(f"{obj} and resi {resi[-1]} and name C1'"))
    hint = last - first
    up = hint - forward * np.dot(hint, forward)
    up /= np.linalg.norm(up)
    return np.stack([np.cross(up, forward), up, forward], axis=1)


def toward_camera(coords, resi, basis, tail):
    """Flip the sight line so the terminal residues are the nearest thing on screen.

    Third column = toward viewer, so a positive tail-to-centroid projection means
    the far side of the molecule sits behind the CCA and no line can be drawn
    over a base plane. This is what makes the no-occlusion claim structural
    rather than a matter of how the ring normals happened to be signed.
    """
    rows = [i for i, r in enumerate(resi) if r in tail]
    if np.dot(coords[rows].mean(0) - coords.mean(0), basis[:, 2]) < 0:
        basis[:, 0] *= -1
        basis[:, 2] *= -1
    return basis


def roll_to_long_axis(coords, resi, basis, tail):
    """Spin the camera about the sight line so the acceptor arm stands up in the frame.

    Looking down the base planes fixes the sight direction but leaves one free
    angle, and the tRNA uses it badly: the arm falls on a diagonal and wastes the
    corners of the frame. That angle is set from the in-plane principal axis
    instead, with the 3' end at the top.
    """
    screen = coords @ basis
    inplane = screen[:, :2] - screen[:, :2].mean(axis=0)
    cov = np.cov(inplane.T)
    angle = 0.5 * np.arctan2(2 * cov[0, 1], cov[0, 0] - cov[1, 1])
    up = basis[:, 0] * np.cos(angle) + basis[:, 1] * np.sin(angle)
    height = coords @ up
    rows = [i for i, r in enumerate(resi) if r in tail]
    if height[rows].mean() < height.mean():
        up = -up
    return np.stack([np.cross(up, basis[:, 2]), up, basis[:, 2]], axis=1)


def tilt(basis, deg):
    """Rotate the camera about its own right axis so a stack of planes fans out.

    Seen exactly down the shared normal, three stacked bases sit on top of one
    another - their ring clearances are 0.3-1.5 A, so no camera can pull them
    apart without tilting. A modest tilt separates them along the screen while
    the rings stay plainly open.
    """
    a = np.radians(deg)
    up = basis[:, 1] * np.cos(a) + basis[:, 2] * np.sin(a)
    forward = basis[:, 2] * np.cos(a) - basis[:, 1] * np.sin(a)
    return np.stack([basis[:, 0], up, forward], axis=1)


def distance_for(basis, points, fill, anchor):
    """The camera distance that fits `points` about `anchor` at `fill` of the frame."""
    screen = (np.asarray(points) - anchor) @ basis
    reach = np.abs(screen).max(axis=0)
    return max(reach[1] / (ORTHO_K * fill), reach[0] / (ORTHO_KX * fill))


def view_for(basis, points, fill, anchor, floor=0.0):
    """The 18-float view that frames `points` about `anchor`, no closer than `floor`.

    PyMOL's view holds the camera axes every third float - right, up,
    toward-viewer - which is exactly a row-major flatten of a matrix whose
    columns are those axes, so `basis` goes in as built.

    The anchor is the 3' end, not the middle of the drawn atoms: bounding-box
    centring buries the subject against the top of the frame under a long stretch
    of acceptor stem, which is the opposite of the picture being asked for. Given
    an anchor, the frame is symmetric about it and its radius is the furthest
    framed atom, so the end sits in the middle and nothing framed is clipped.
    """
    distance = max(distance_for(basis, points, fill, anchor), floor)
    return [*basis.flatten(),
            0.0, 0.0, -distance,
            float(anchor[0]), float(anchor[1]), float(anchor[2]),
            1.0, 2.0 * distance + 50.0, 0.0]


def faceon_score(obj, tail):
    """cos(angle) between each terminal ring plane normal and the sight line.

    1.0 means the camera looks straight down the plane, which is the whole point
    of the view, so it is worth measuring rather than trusting the construction.
    """
    v = cmd.get_view(0)
    sight = np.array([v[2], v[5], v[8]])
    sight = sight / np.linalg.norm(sight)
    return [round(float(abs(np.dot(plane_normal(f"{obj} and resi {r} and chain A"), sight))), 3)
            for r in tail]


def trace_and_sticks(name, n, colour, radius, rungs=True):
    """Terminal sticks, the acceptor arm as cartoon, and the distant fold pale.

    The context is the nucleic-acid cartoon used in the A-D panels: one smooth
    backbone tube with the bases reduced to ladder rungs, so no base in the
    chain reads as a ring. Only the last three residues are drawn as sticks. The
    context is split where the acceptor arm ends, because the arm belongs in the
    story and the D- and anticodon arms do not; each layer is then a paler wash of
    the same hue, which is how it recedes. An all-atom line trace was tried first
    and is not what "线状" means here - it jagged through every sugar and left the
    bases as wireframes. The overlaid reference drops the rungs, as it did in
    those panels, so the two chains stay separable where they cross.

    A washed-out colour rather than `transparency`: this build renders a
    transparency-80 cartoon exactly as opaque, so fading by colour is the only
    fade that reaches the image.
    """
    cmd.hide("everything", name)
    near = f"resi 1-10 or resi {n - 16}-{n}"
    cmd.create(name + "_far", f"{name} and not ({near})")
    cmd.create(name + "_ctx", f"{name} and ({near}) and resi 1-{n - 3}")
    cmd.create(name + "_tail", f"{name} and resi {n - 2}-{n}")
    cmd.delete(name)
    modes = (3, 2) if rungs else (0, 0)
    for obj, tint in ((name + "_ctx", colour + "_mid"), (name + "_far", colour + "_wash")):
        for key, val in {"cartoon_nucleic_acid_mode": modes[0],
                         "cartoon_ladder_mode": modes[1],
                         "cartoon_loop_radius": LOOP_R,
                         "cartoon_tube_radius": TUBE_R,
                         "cartoon_ladder_radius": RUNG_R}.items():
            cmd.set(key, val, obj)
        cmd.cartoon("automatic", obj)
        cmd.show("cartoon", obj)
        cmd.color("c_" + tint, obj)
    cmd.show("sticks", name + "_tail")
    cmd.color("c_" + colour, name + "_tail")
    cmd.set("stick_radius", radius, name + "_tail")


def parts(name):
    """The three objects as one selection, parenthesised because `and` binds tighter than `or`."""
    return f"({name}_ctx or {name}_far or {name}_tail)"


def atoms_of(sel):
    """The coordinates and residue numbers of a selection, as arrays."""
    atoms = cmd.get_model(sel).atom
    return np.array([a.coord for a in atoms]), np.array([int(a.resi) for a in atoms])


def arm_atoms(sel, n, back=1):
    """The 3' end and the `back` residues below it on its own strand - what to zoom on.

    Framing is deliberately not symmetric about the acceptor stem: the 5' strand
    is where the mature numbering restarts, and on models whose 5' end does not
    sit opposite the 3' one it drags the camera back and leaves the CCA a speck
    in one quadrant. The stem the end grows off is enough context to frame by.
    """
    keep = set(range(n - 2 - back, n + 1))
    coords, resi = atoms_of(sel)
    rows = [i for i, r in enumerate(resi) if r in keep]
    return coords[rows], resi[rows]


def centre_on(coords, basis):
    """The middle of `coords` as seen by this camera, back in model coordinates."""
    screen = np.asarray(coords) @ basis
    return ((screen.min(axis=0) + screen.max(axis=0)) / 2) @ basis.T


def end_basis(sel, n):
    """The camera that looks straight down the terminal base planes, end upward."""
    tail = range(n - 2, n + 1)
    basis = toward_camera(*atoms_of(sel), face_on(sel, tail), tail)
    return roll_to_long_axis(*arm_atoms(sel, n, back=12), basis, tail)


def inside(point, polygon):
    """Ray-cast point-in-polygon test in the screen plane."""
    x, y = point
    hit = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def occlusion(obj, tail, basis):
    """Context atoms that sit inside a terminal ring's footprint, in front of its plane.

    A line can only hide a base if it is nearer the camera than that base and
    lands on it in projection, so this is the exact condition to check.
    """
    atoms = cmd.get_model(obj).atom
    tail = [str(int(r)) for r in tail]
    planes = {}
    for resi in tail:
        screen = np.array([np.array(a.coord) @ basis
                           for a in atoms if a.resi == resi and a.name in RING_NAMES])
        planes[resi] = (hull(screen[:, :2]), screen[:, 2].max())
    report = []
    for atom in atoms:
        if atom.resi in tail:
            continue
        x, y, w = np.array(atom.coord) @ basis
        for resi, (footprint, front) in planes.items():
            if w > front + 0.4 and inside((x, y), footprint):
                report.append((f"{atom.name}{atom.resi}", f"ring{resi}",
                               round(float(w - front), 2)))
    return report


def hull(points):
    """Andrew's monotone chain convex hull, counter-clockwise."""
    pts = sorted(map(tuple, points))
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and np.cross(np.subtract(out[-1], out[-2]), np.subtract(p, out[-2])) <= 0:
                out.pop()
            out.append(p)
        return out
    return half(pts) + half(pts[::-1])[:-1]


os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "occlusion.txt")
if os.path.exists(LOG):
    os.remove(LOG)
cmd.reinitialize()
cmd.bg_color("white")
for k, v in {"orthoscopic": 1, "ray_trace_mode": 0, "antialias": 3, "ambient": 0.55,
             "direct": 0.60, "specular": 0.05, "shininess": 60, "ray_shadow": 0,
             "ray_trace_fog": 0, "depth_cue": 0, "line_width": 0.7}.items():
    cmd.set(k, v)
for name, rgb in RGB.items():
    cmd.set_color("c_" + name, list(rgb))


def shoot(tag, obj, basis, tail, frame, centre):
    """Frame the end, check `obj` for occlusion, and save one view.

    `frame` is a list of point arrays whose extent sets the zoom - the 3' end and
    a residue below it, not the whole chain - and `centre` is the terminal
    triplet, whose projected box is what sits in the middle of the image. The rest
    of the fold still renders, and still gets the occlusion check.
    """
    anchor = centre_on(centre, basis)
    cmd.set_view(view_for(basis, np.vstack(frame), FILL, anchor, SCALE))
    blocked = occlusion(obj, tail, basis)
    cmd.ray(WIDTH, HEIGHT, 2, 2)
    cmd.png(os.path.join(OUT, tag))
    with open(LOG, "a") as log:
        log.write(f"{tag} face-on cos {faceon_score(obj, tail)} "
                  f"occluding context atoms: {blocked or 'none'}\n")
    print(tag, flush=True)


# one camera distance for the whole set: fitting each end to its own frame would
# print the stacked CCAs at twice the magnification of the splayed ones, and how
# far a model's end reaches is exactly what the four panels are there to compare
SCALE = 0.0
for sid, colour, n in PANELS:
    cmd.load(os.path.join(MD, f"{sid}.pdb"), sid)
    basis = end_basis(sid, n)
    anchor = centre_on(arm_atoms(sid, n, back=0)[0], basis)
    SCALE = max(SCALE, distance_for(basis, arm_atoms(sid, n)[0], FILL, anchor))
    cmd.delete(sid)

for sid, colour, n in PANELS:
    tail = range(n - 2, n + 1)
    cmd.load(os.path.join(MD, f"{sid}.pdb"), sid)
    trace_and_sticks(sid, n, colour, 0.15)
    whole = parts(sid)
    frame, end = arm_atoms(whole, n), arm_atoms(whole, n, back=0)
    basis = end_basis(whole, n)
    shoot(f"{sid}_cca_faceon.png", whole, basis, tail, [frame[0]], end[0])
    shoot(f"{sid}_cca_tilt35.png", whole, tilt(basis, 35), tail, [frame[0]], end[0])

    ref = f"{sid}_ref"
    cmd.load(os.path.join(MD, "5ud5_chainD_superposed.pdb"), ref)
    cmd.remove(f"{ref} and resname HOH")
    trace_and_sticks(ref, 76, "slate", 0.12, rungs=False)
    # the overlay brings its own end, so both define the frame
    refsel = parts(ref)
    shoot(f"{sid}_cca_faceon_ref.png", whole, basis, tail,
          [frame[0], arm_atoms(refsel, 76)[0]],
          np.vstack([end[0], arm_atoms(refsel, 76, back=0)[0]]))
    for obj in (f"{ref}_ctx", f"{ref}_far", f"{ref}_tail",
                f"{sid}_ctx", f"{sid}_far", f"{sid}_tail"):
        cmd.delete(obj)

cmd.quit()
