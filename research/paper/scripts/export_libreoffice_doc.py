#!/usr/bin/env python3
"""Export paper/main.tex to a cleaner LibreOffice-friendly HTML document.

The goal is not to be a general LaTeX converter. It handles the subset used by
paper/main.tex so LibreOffice can create an editable ODT/DOCX without exposing
raw TeX commands.
"""

from __future__ import annotations

import html
import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
TEX = PAPER / "main.tex"
BBL = PAPER / "main.bbl"
OUT = PAPER / "main_libreoffice.html"
FIGURE_MAX_WIDTH_CM = 15.7
FIGURE_MAX_HEIGHT_CM = 18.2


SUP = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
UMLAUTS = {
    "a": "ä",
    "o": "ö",
    "u": "ü",
    "A": "Ä",
    "O": "Ö",
    "U": "Ü",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def brace_content(text: str, command: str) -> str:
    marker = "\\" + command + "{"
    start = text.find(marker)
    if start < 0:
        return ""
    i = start + len(marker)
    depth = 1
    out = []
    while i < len(text) and depth:
        ch = text[i]
        if ch == "{":
            depth += 1
            out.append(ch)
        elif ch == "}":
            depth -= 1
            if depth:
                out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse_bibliography() -> tuple[dict[str, int], list[str]]:
    if not BBL.exists():
        return {}, []
    text = read_text(BBL)
    parts = re.split(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", text)
    keys: dict[str, int] = {}
    refs: list[str] = []
    for idx in range(1, len(parts), 2):
        key = parts[idx]
        entry = parts[idx + 1]
        n = len(refs) + 1
        keys[key] = n
        entry = entry.split(r"\bibitem", 1)[0]
        entry = re.sub(r"\\(?:begin|end)\{thebibliography\}(?:\{[^}]*\})?", "", entry)
        entry = re.sub(r"\\newblock\s*", " ", entry)
        entry = re.sub(r"\\penalty0\s*", "", entry)
        entry = re.sub(r"\\emph\{([^{}]*)\}", r"\1", entry)
        entry = re.sub(r"\\doi\{([^{}]*)\}", r"doi: \1", entry)
        entry = re.sub(r"\\url\{([^{}]*)\}", r"\1", entry)
        entry = re.sub(r"\\natexlab\{([^{}]*)\}", r"\1", entry)
        entry = entry.replace(r"{\aa}", "Å")
        entry = re.sub(r'\{\\\"([A-Za-z])\}', lambda m: UMLAUTS.get(m.group(1), m.group(1)), entry)
        entry = entry.replace("~", " ")
        entry = entry.replace("\\", "")
        entry = entry.replace("{", "").replace("}", "")
        entry = entry.replace("beginthebibliography", "").replace("endthebibliography", "")
        entry = re.sub(r"\s+", " ", entry).strip()
        if entry:
            refs.append(entry)
    return keys, refs


def collect_labels(body: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    fig_no = 0
    table_no = 0
    for match in re.finditer(r"\\begin\{(figure|table|center)\}|\{\\captionsetup\{hypcap=false\}", body):
        start = match.start()
        window = body[start : start + 2500]
        label = re.search(r"\\label\{([^}]+)\}", window)
        if not label:
            continue
        key = label.group(1)
        if key in mapping:
            continue
        if key.startswith("fig:"):
            fig_no += 1
            mapping[key] = str(fig_no)
        elif key.startswith("tab:"):
            table_no += 1
            mapping[key] = str(table_no)
    return mapping


def latex_inline(text: str, cite_map: dict[str, int], label_map: dict[str, str]) -> str:
    text = text.strip()
    text = text.replace(r"\\", " ")
    text = text.replace("~", " ")
    text = text.replace(r"\%", "%")
    text = text.replace(r"\_", "_")
    text = text.replace(r"\&", "&")
    text = text.replace(r"\allowbreak", "")
    text = text.replace(r"\quad", " ")
    text = text.replace(r"\small", "")
    text = text.replace(r"{\aa}", "Å")
    text = re.sub(r"\\\(\^([0-9+\-=()]+)\\\)", lambda m: m.group(1).translate(SUP), text)
    text = re.sub(r"\$\^\{([^{}]+)\}\$", lambda m: m.group(1).translate(SUP), text)
    text = re.sub(r"\\textit\{([^{}]*)\}", r"<em>\1</em>", text)
    text = re.sub(r"\\emph\{([^{}]*)\}", r"<em>\1</em>", text)
    text = re.sub(r"\\texttt\{([^{}]*)\}", r"<code>\1</code>", text)
    text = re.sub(r"\\nolinkurl\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\url\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\href\{([^{}]*)\}\{([^{}]*)\}", r"\2", text)

    def cite_repl(match: re.Match[str]) -> str:
        nums = []
        for key in match.group(1).split(","):
            key = key.strip()
            if key in cite_map:
                nums.append(str(cite_map[key]))
        return "[" + ",".join(nums) + "]" if nums else ""

    text = re.sub(r"\\citep\{([^}]+)\}", cite_repl, text)
    text = re.sub(r"\\cite\{([^}]+)\}", cite_repl, text)
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: label_map.get(m.group(1), "?"), text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def html_inline(text: str, cite_map: dict[str, int], label_map: dict[str, str]) -> str:
    converted = latex_inline(text, cite_map, label_map)
    converted = html.escape(converted, quote=False)
    converted = converted.replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")
    converted = converted.replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")
    return converted


def split_paragraphs(text: str) -> list[str]:
    text = re.sub(r"\\FloatBarrier", "\n\n", text)
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if p.strip()]


def image_src(path: str) -> str:
    p = path.strip()
    if p.endswith(".pdf"):
        p = p[:-4] + ".png"
    if not p.startswith("/"):
        p = str(PAPER / p)
    return Path(p).resolve().as_uri()


def image_path(path: str) -> Path:
    p = path.strip()
    if p.endswith(".pdf"):
        p = p[:-4] + ".png"
    if not p.startswith("/"):
        p = str(PAPER / p)
    return Path(p).resolve()


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def figure_size_attrs(path: Path) -> str:
    size = png_size(path)
    if not size:
        return f'style="width:{FIGURE_MAX_WIDTH_CM:.2f}cm; height:auto;"'
    width_px, height_px = size
    scale = min(FIGURE_MAX_WIDTH_CM / width_px, FIGURE_MAX_HEIGHT_CM / height_px)
    width_cm = width_px * scale
    height_cm = height_px * scale
    html_width = round(width_cm / 2.54 * 96)
    html_height = round(height_cm / 2.54 * 96)
    return (
        f'width="{html_width}" height="{html_height}" '
        f'style="width:{width_cm:.2f}cm; height:{height_cm:.2f}cm;"'
    )


def caption_text(block: str) -> str:
    cap = re.search(r"\\caption(?:of\{figure\})?\{", block)
    if not cap:
        return ""
    start = cap.end()
    depth = 1
    out = []
    i = start
    while i < len(block) and depth:
        ch = block[i]
        if ch == "{":
            depth += 1
            out.append(ch)
        elif ch == "}":
            depth -= 1
            if depth:
                out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def render_figure(block: str, cite_map: dict[str, int], label_map: dict[str, str], fig_no: int) -> str:
    inc = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", block)
    path = image_path(inc.group(1)) if inc else None
    src = path.as_uri() if path else ""
    size_attrs = figure_size_attrs(path) if path else ""
    cap = html_inline(caption_text(block), cite_map, label_map)
    out = ['<div class="figure">']
    if src:
        out.append(f'<img src="{src}" alt="Figure {fig_no}" {size_attrs}>')
    if cap:
        out.append(f'<p class="caption">图 {fig_no}. {cap}</p>')
    out.append("</div>")
    return "\n".join(out)


def render_table(block: str, cite_map: dict[str, int], label_map: dict[str, str], table_no: int) -> str:
    cap = html_inline(caption_text(block), cite_map, label_map)
    m = re.search(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", block, re.S)
    rows_html = []
    col_widths_cm = [4.1, 2.3, 2.3, 3.5, 3.5]
    col_widths_px = [round(cm / 2.54 * 96) for cm in col_widths_cm]
    if m:
        rows = re.split(r"\\\\", m.group(1))
        for row in rows:
            row = row.strip()
            row = re.sub(r"\\(toprule|midrule|bottomrule)", "", row).strip()
            if not row:
                continue
            cells = [html_inline(c, cite_map, label_map) for c in row.split("&")]
            tag = "th" if not rows_html else "td"
            rendered = []
            for cell_idx, cell in enumerate(cells):
                width_cm = col_widths_cm[min(cell_idx, len(col_widths_cm) - 1)]
                width_px = col_widths_px[min(cell_idx, len(col_widths_px) - 1)]
                align = "left" if cell_idx == 0 else "center"
                style = (
                    "border:1px solid #d1d5db; padding:4px 6px; "
                    f"text-align:{align}; vertical-align:middle; "
                    f"white-space:nowrap; width:{width_cm:.1f}cm;"
                )
                rendered.append(f'<{tag} width="{width_px}" style="{style}">{cell}</{tag}>')
            rows_html.append("<tr>" + "".join(rendered) + "</tr>")
    out = [
        '<p style="page-break-before: always; margin:0; text-indent:0;"></p>',
        '<div class="tablewrap" style="page-break-inside: avoid;">',
    ]
    if cap:
        out.append(f'<p class="caption">表 {table_no}. {cap}</p>')
    out.append('<table width="594" style="border-collapse:collapse; width:15.7cm; font-size:9pt;">')
    out.append(
        "<colgroup>"
        '<col width="155" style="width:4.1cm">'
        '<col width="87" style="width:2.3cm">'
        '<col width="87" style="width:2.3cm">'
        '<col width="132" style="width:3.5cm">'
        '<col width="132" style="width:3.5cm">'
        "</colgroup>"
    )
    out.extend(rows_html)
    out.append("</table></div>")
    return "\n".join(out)


def clean_block_text(block: str) -> str:
    block = re.sub(r"\\begin\{center\}.*?\\end\{center\}", "", block, flags=re.S)
    block = re.sub(r"\\begin\{figure\}.*?\\end\{figure\}", "", block, flags=re.S)
    block = re.sub(r"\\begin\{table\}.*?\\end\{table\}", "", block, flags=re.S)
    return block


def render_body(body: str, cite_map: dict[str, int], label_map: dict[str, str]) -> str:
    tokens = re.split(r"(\\section\{[^}]+\}|\\subsection\{[^}]+\}|\\begin\{figure\}.*?\\end\{figure\}|\\begin\{table\}.*?\\end\{table\}|\\begin\{center\}.*?\\end\{center\})", body, flags=re.S)
    out: list[str] = []
    fig_no = 0
    table_no = 0
    for token in tokens:
        if not token.strip():
            continue
        if token.startswith(r"\section"):
            title = re.search(r"\\section\{([^}]+)\}", token).group(1)
            out.append(f"<h1>{html_inline(title, cite_map, label_map)}</h1>")
        elif token.startswith(r"\subsection"):
            title = re.search(r"\\subsection\{([^}]+)\}", token).group(1)
            out.append(f"<h2>{html_inline(title, cite_map, label_map)}</h2>")
        elif token.startswith(r"\begin{figure}") or (token.startswith(r"\begin{center}") and r"\includegraphics" in token):
            fig_no += 1
            out.append(render_figure(token, cite_map, label_map, fig_no))
        elif token.startswith(r"\begin{table}"):
            table_no += 1
            out.append(render_table(token, cite_map, label_map, table_no))
        else:
            text = clean_block_text(token)
            for para in split_paragraphs(text):
                if para.startswith(r"\bibliographystyle") or para.startswith(r"\bibliography"):
                    continue
                out.append(f"<p>{html_inline(para, cite_map, label_map)}</p>")
    return "\n".join(out)


def main() -> None:
    tex = read_text(TEX)
    cite_map, refs = parse_bibliography()
    label_map = collect_labels(tex)
    title = latex_inline(brace_content(tex, "title").replace(r"\\", " "), cite_map, label_map)
    author = latex_inline(brace_content(tex, "author"), cite_map, label_map)

    body = tex.split(r"\begin{document}", 1)[1].split(r"\end{document}", 1)[0]
    body = re.sub(r"\\maketitle", "", body)
    body = re.sub(r"\\begin\{center\}\s*\\small.*?\\end\{center\}", "", body, flags=re.S)
    abstract = ""
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S)
    if m:
        abstract = m.group(1).strip()
        body = body[: m.start()] + body[m.end() :]

    refs_html = []
    for i, ref in enumerate(refs, 1):
        refs_html.append(f"<p class=\"ref\">[{i}] {html.escape(ref, quote=False)}</p>")

    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
	body {{
	  font-family: "Times New Roman", "SimSun", "Noto Serif CJK SC", serif;
	  font-size: 11pt;
	  line-height: 1.45;
	  color: #111827;
	  margin: 0;
	}}
	@page {{ size: A4; margin: 2.54cm; }}
	.title {{
	  font-family: "Times New Roman", "SimHei", "Noto Sans CJK SC", serif;
	  text-align: center;
	  font-size: 18pt;
	  font-weight: 700;
	  line-height: 1.25;
	  margin: 0 0 12pt;
	}}
	.authors {{ text-align: center; margin: 0 0 18pt; text-indent: 0; }}
	h1 {{
	  font-family: "Times New Roman", "SimHei", "Noto Sans CJK SC", serif;
	  font-size: 15pt;
	  font-weight: 700;
	  margin: 18pt 0 8pt;
	  text-indent: 0;
	  line-height: 1.25;
	}}
	h2 {{
	  font-family: "Times New Roman", "SimHei", "Noto Sans CJK SC", serif;
	  font-size: 12.5pt;
	  font-weight: 700;
	  margin: 12pt 0 6pt;
	  text-indent: 0;
	  line-height: 1.3;
	}}
	p {{ text-indent: 22pt; margin: 0 0 6pt; line-height: 1.45; }}
	.abstract {{ margin: 0 0 10pt; }}
	.abstract p {{ text-indent: 0; }}
	.figure {{ margin: 12pt 0 14pt; text-align: center; page-break-inside: avoid; }}
	.figure img {{ display: block; margin: 0 auto; }}
.caption {{ text-indent: 0; font-size: 9.5pt; color: #374151; text-align: left; margin: 6pt 0 8pt; line-height: 1.25; }}
.tablewrap {{ margin: 8pt 0 12pt; page-break-inside: avoid; }}
	table {{ border-collapse: collapse; width: 15.7cm; font-size: 9pt; }}
	th, td {{ border: 1px solid #d1d5db; padding: 4px 6px; vertical-align: middle; white-space: nowrap; }}
th {{ background: #f3f4f6; font-weight: 700; }}
code {{ font-family: "Courier New", monospace; }}
.ref {{ text-indent: 0; font-size: 9.5pt; margin: 0 0 4pt; line-height: 1.25; }}
</style>
</head>
<body>
<div class="title">{html.escape(title)}</div>
<div class="authors">{html.escape(author)}</div>
<h1>摘要</h1>
<div class="abstract"><p>{html_inline(abstract, cite_map, label_map)}</p></div>
{render_body(body, cite_map, label_map)}
<h1>参考文献</h1>
{chr(10).join(refs_html)}
</body>
</html>
"""
    OUT.write_text(html_doc, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
