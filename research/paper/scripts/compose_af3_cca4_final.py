"""Compose each AF3 model render into a separate, minimally annotated figure."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path("/home/ouyangzhuo/LLM-tRNAAlign/research/paper/results/af3_cca4_20261019")
RENDER = ROOT / "figures_pymol"
OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)

W, H = 2200, 1520
INK = (43, 46, 51)
MUTED = (112, 117, 122)
FAINT = (230, 230, 226)
LEADER = (170, 177, 180)
SAGE = (168, 181, 168)
TAUPE = (204, 191, 173)
ELBOW = (186, 184, 174)
GHOST = (143, 158, 163)
BASEC = (84, 128, 143)
BASEA = (199, 158, 71)
TEAL = (33, 107, 102)
RUST = (158, 74, 56)
FONT_DIR = Path("/home/ouyangzhuo/.local/share/fonts/aylm_fonts")

MODELS = {
    "DA1110": dict(n=76, plddt=90.93, ptm=0.63, dist=8.55, seq="CCA",
                   accent=TEAL, note="",
                   badge="Bona fide 3\u2032-CCA", status="canonical 3\u2032 site"),
    "DA0940": dict(n=77, plddt=86.46, ptm=0.51, dist=6.95, seq="CCA",
                   accent=TEAL, note="",
                   badge="Bona fide 3\u2032-CCA", status="canonical 3\u2032 site"),
    "DL2150": dict(n=84, plddt=86.52, ptm=0.58, dist=18.68, seq="CCA",
                   accent=RUST, note="8-nt 3\u2032 extension",
                   badge="3\u2032-CCA, displaced", status="displaced from site"),
    "DX1480": dict(n=74, plddt=86.54, ptm=0.53, dist=18.28, seq="CAA",
                   accent=RUST, note="",
                   badge="3\u2032-CAA, no CCA", status="displaced \u00b7 no CCA"),
}

# full view: crop and scale in the render, then paste
FULL_CROP = (160, 230, 1080, 1290)
FULL_POS = (180, 300)
FULL_SCALE = 1.0
FULL_MAP = lambda x, y: (FULL_POS[0] + (x - FULL_CROP[0]) * FULL_SCALE,
                         FULL_POS[1] + (y - FULL_CROP[1]) * FULL_SCALE)
FULL_LABELS = [
    ("acceptor arm", (641, 567), (560, 505), (570, 520), "right"),
    ("T arm", (907, 822), (1000, 750), (995, 762), "left"),
    ("D arm", (596, 878), (515, 900), (525, 894), "right"),
    ("anticodon arm", (354, 1150), (350, 1238), (350, 1224), "right"),
]

# tail view: common crop, displayed at native size
TAIL_CROP = (330, 120, 940, 1185)
TAIL_POS = (1290, 300)
TAIL_MAP = lambda x, y: (TAIL_POS[0] + (x - TAIL_CROP[0]),
                         TAIL_POS[1] + (y - TAIL_CROP[1]))


def font(size, bold=False):
    name = "Arialbd.TTF" if bold else "Arial.TTF"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def textlen(draw, text, fnt):
    return draw.textlength(text, font=fnt)


def leader(draw, start, end, color=LEADER, width=2, dot=False):
    draw.line([start, end], fill=color, width=width)
    if dot:
        r = 4
        draw.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=color)


def swatch(draw, x, y, color, size=15, radius=3):
    draw.rounded_rectangle([x, y, x + size, y + size], radius=radius, fill=color)


def accent_tip(path, accent):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    d = np.abs(a - np.array(accent)).sum(axis=2)
    ys, xs = np.where(d < 70)
    y0 = ys.min()
    xs = xs[ys <= y0 + 4]
    return float(np.median(xs)), float(y0)


def draw_full_labels(draw):
    for name, anchor, pos, start, align in FULL_LABELS:
        ax, ay = FULL_MAP(*anchor)
        tx, ty = FULL_MAP(*pos)
        sx, sy = FULL_MAP(*start)
        leader(draw, (sx, sy), (ax, ay))
        draw.text((tx, ty), name, font=font(25), fill=INK,
                  anchor="rm" if align == "right" else "lm")


def draw_sequence(draw, x, baseline, seq):
    f_prime, f_dash, f_base = font(20), font(22), font(45, bold=True)
    tokens = [("5\u2032", f_prime, MUTED, 14), ("\u2013", f_dash, MUTED, 9)]
    for i, base in enumerate(seq):
        if i:
            tokens.append(("\u2013", f_dash, MUTED, 9))
        tokens.append((base, f_base, BASEC if base == "C" else BASEA, 0))
    tokens += [("\u2013", f_dash, MUTED, 9), ("3\u2032", f_prime, MUTED, 14)]
    for text, fnt, color, dy in tokens:
        draw.text((x, baseline - dy), text, font=fnt, fill=color, anchor="ls")
        x += textlen(draw, text, fnt) + 3
    return x


def compose(sid, info):
    accent = info["accent"]
    fig = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(fig)

    f_overline = font(20)
    f_id = font(66, bold=True)
    f_sub = font(25)
    f_rule = font(20)
    f_note = font(21)

    draw.text((104, 52), "ALPHAFOLD3 \u00b7 tRNA 3\u2032-END", font=f_overline, fill=MUTED)
    draw.rounded_rectangle([84, 88, 90, 172], radius=3, fill=accent)
    draw.text((104, 82), sid, font=f_id, fill=INK)
    sub = f"{info['n']} nt"
    if info["note"]:
        sub += f" \u00b7 {info['note']}"
    sub += f" \u00b7 mean pLDDT {info['plddt']:.1f} \u00b7 pTM {info['ptm']:.2f}"
    draw.text((104, 176), sub, font=f_sub, fill=MUTED)

    f_badge = font(27, bold=True)
    tw = textlen(draw, info["badge"], f_badge)
    bx1, by0, bh = 2116, 102, 62
    bw = tw + 74
    bx0 = bx1 - bw
    draw.rounded_rectangle([bx0, by0, bx1, by0 + bh], radius=31, fill=accent)
    draw.ellipse([bx0 + 28, by0 + 27, bx0 + 36, by0 + 35], fill="white")
    draw.text((bx0 + 48, by0 + bh / 2 - 2), info["badge"], font=f_badge,
              fill="white", anchor="lm")

    draw.line([84, 228, 2116, 228], fill=FAINT, width=2)

    # left panel legend
    items = [(SAGE, "acceptor + T arm"), (TAUPE, "D + anticodon arm"),
             (ELBOW, "elbow"), (accent, "3\u2032 terminus")]
    x = 84
    for color, label in items:
        swatch(draw, x, 258, color)
        draw.text((x + 23, 266), label, font=f_rule, fill=MUTED, anchor="lm")
        x += 23 + textlen(draw, label, f_rule) + 34
    draw.text((1100, 266), "FULL-LENGTH MODEL", font=f_rule, fill=MUTED, anchor="rm")

    # right panel title + marker legend
    draw.text((1290, 266), "3\u2032 TERMINUS", font=f_rule, fill=MUTED, anchor="lm")
    f_leg = font(20)
    term_txt = "model 3\u2032 end"
    ghost_txt = "1EHZ A76 (ref.)"
    term_w = textlen(draw, term_txt, f_leg)
    ghost_w = textlen(draw, ghost_txt, f_leg)
    term_x = 2116 - term_w
    draw.ellipse([term_x - 18, 263, term_x - 8, 273], fill=accent)
    draw.text((term_x, 266), term_txt, font=f_leg, fill=MUTED, anchor="lm")
    ghost_x = term_x - 28 - ghost_w
    draw.ellipse([ghost_x - 18, 263, ghost_x - 8, 273], fill=GHOST)
    draw.text((ghost_x, 266), ghost_txt, font=f_leg, fill=MUTED, anchor="lm")

    # renders
    full = Image.open(RENDER / f"{sid}_full.png").convert("RGB").crop(FULL_CROP)
    if FULL_SCALE != 1.0:
        full = full.resize((int(full.width * FULL_SCALE), int(full.height * FULL_SCALE)),
                           Image.Resampling.LANCZOS)
    fig.paste(full, FULL_POS)

    tail = Image.open(RENDER / f"{sid}_tail.png").convert("RGB").crop(TAIL_CROP)
    fig.paste(tail, TAIL_POS)

    draw_full_labels(draw)

    # 3' end callout in the full view, anchored to the topmost accent pixel
    tipx, tipy = accent_tip(RENDER / f"{sid}_full.png", accent)
    tpx, tpy = FULL_MAP(tipx, tipy)
    lx, ly = tpx + 66, tpy - 30
    leader(draw, (lx - 6, ly + 13), (tpx + 5, tpy + 8), color=accent, width=2, dot=True)
    draw.text((lx, ly), "3\u2032 end", font=font(25, bold=True), fill=accent)

    # scale bars
    f_scale = font(19)
    sb10 = 10 * 14.680505487867586 * FULL_SCALE
    sx, sy = 850, 1340
    draw.line([sx, sy, sx + sb10, sy], fill=(74, 78, 82), width=3)
    draw.line([sx, sy - 6, sx, sy + 6], fill=(74, 78, 82), width=3)
    draw.line([sx + sb10, sy - 6, sx + sb10, sy + 6], fill=(74, 78, 82), width=3)
    draw.text((sx, sy - 12), "10 \u00c5", font=f_scale, fill=MUTED, anchor="ls")

    sb5 = 5 * 24.86012371395125
    tx, ty = 1300, 1300
    draw.line([tx, ty, tx + sb5, ty], fill=(74, 78, 82), width=3)
    draw.line([tx, ty - 6, tx, ty + 6], fill=(74, 78, 82), width=3)
    draw.line([tx + sb5, ty - 6, tx + sb5, ty + 6], fill=(74, 78, 82), width=3)
    draw.text((tx, ty - 12), "5 \u00c5", font=f_scale, fill=MUTED, anchor="ls")

    # measurement block over the empty lower-left of the tail view
    draw.rounded_rectangle([1300, 668, 1304, 778], radius=2, fill=accent)
    draw.text((1318, 676), "C3\u2032\u2013C3\u2032 TO 1EHZ A76", font=font(18, bold=True),
              fill=MUTED)
    draw.text((1318, 704), f"{info['dist']:.1f} \u00c5", font=font(48, bold=True), fill=accent)
    draw.ellipse([1322, 778, 1330, 786], fill=accent)
    draw.text((1336, 782), info["status"], font=font(21), fill=MUTED, anchor="lm")

    draw.text((1318, 858), "TERMINAL TRIPLET", font=font(18, bold=True), fill=MUTED)
    draw_sequence(draw, 1318, 922, info["seq"])

    draw.line([84, 1400, 2116, 1400], fill=FAINT, width=2)
    draw.text((84, 1420),
              "Ghost: 1EHZ A76 (external reference, not in the AF3 input); C3\u2032 after superposition. "
              "Dashed line: C3\u2032\u2013C3\u2032 distance (3D).",
              font=f_note, fill=MUTED)
    draw.text((84, 1452),
              "Bona fide 3\u2032-CCA requires the CCA triplet at the canonical 1EHZ A76 site; "
              "a terminal CCA alone is not sufficient.",
              font=f_note, fill=MUTED)

    out = OUT / f"fig_af3_cca_{sid}.png"
    fig.save(out, dpi=(200, 200))
    print(out, fig.size)


for sid, info in MODELS.items():
    compose(sid, info)
