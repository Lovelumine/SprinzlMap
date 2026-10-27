#!/usr/bin/env python3
"""Build a Nature Communications submission-ready editable Word manuscript.

The output follows the journal's manuscript conventions rather than its
typeset article layout: A4, single-column, double-spaced, left-aligned text,
standard fonts, continuous line numbers, Arabic page numbers, and display items
grouped after the statements. Title-page and declaration fields that are not
yet available are inserted as conspicuous editable placeholders.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_libreoffice_word as word  # noqa: E402
import export_libreoffice_doc as exporter  # noqa: E402


PAPER = SCRIPT_DIR.parent
REPO = PAPER.parents[1]
SOURCE = PAPER / "main.tex"
OUTDIR = PAPER / "submission"
DOCX = OUTDIR / "SprinzlMap_NatureCommunications_manuscript.docx"
WORK = REPO / "tmp" / "nature_submission_word"
HTML_PATH = WORK / "manuscript.html"
ODT_RAW = WORK / "manuscript.odt"
ODT_EMBEDDED = WORK / "manuscript-embedded.odt"
PDF_CHECK = WORK / "SprinzlMap_NatureCommunications_manuscript.pdf"

W_NS = word.W_NS
R_NS = word.R_NS
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

ET.register_namespace("w", W_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("cp", CP_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("dcterms", DCTERMS_NS)

FIGURE_PATTERN = re.compile(
    r"\\begin\{figure\}.*?\\end\{figure\}"
    r"|\\begin\{center\}.*?\\includegraphics.*?\\end\{center\}",
    re.S,
)
DISPLAY_MATH_PATTERN = re.compile(r"(?<!\\)\\\[(.*?)\\\]", re.S)
SUBSECTION_PATTERN = re.compile(r"\\subsection\{([^{}]+)\}")

AFFILIATION = (
    "MOE Key Laboratory of Gene Function and Regulation, State Key Laboratory "
    "for Biocontrol, Innovation Center for Evolutionary Synthetic Biology, "
    "Guangdong Provincial Key Laboratory of Plant Stress Biology, School of "
    "Agriculture and Biotechnology, School of Life Sciences, Sun Yat-sen "
    "University, Guangzhou 510275, China"
)
EMAIL = "zhengll33@mail.sysu.edu.cn"
ORCID = "https://orcid.org/0000-0002-7152-1095"
AUTHOR_LINE = (
    "Zhuo Ouyang, Qiuhui Wu, Mengdi Wang, Qingyu Tan, Ziman Chen, "
    "Lingling Zheng*"
)

JOURNAL_ABBREVIATIONS = {
    "Bioinformatics": "Bioinformatics",
    "Nature Communications": "Nat. Commun.",
    "Nucleic Acids Research": "Nucleic Acids Res.",
    "Nature Methods": "Nat. Methods",
    "Nature": "Nature",
    "RNA": "RNA",
    "IUBMB Life": "IUBMB Life",
    "Molecular Cell": "Mol. Cell",
    "Journal of Biological Chemistry": "J. Biol. Chem.",
    "The EMBO Journal": "EMBO J.",
    "Nature Chemical Biology": "Nat. Chem. Biol.",
    "Molecular Therapy Nucleic Acids": "Mol. Ther. Nucleic Acids",
    "Science": "Science",
}

ACCENT_MARKS = {
    '"': "\u0308",
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "c": "\u0327",
}


def run(args: list[str], *, cwd: Path = REPO) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def inline_html(text: str, cite_map: dict[str, int], label_map: dict[str, str]) -> str:
    """Render the LaTeX subset used by the manuscript as safe inline HTML."""

    text = text.strip()
    text = text.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&")
    text = text.replace(r"\,", " ")
    text = text.replace("--", "–")
    text = text.replace(r"\AA", "Å").replace(r"\alpha", "α").replace(r"\beta", "β")
    text = text.replace(r"\textdegree", "°")
    text = re.sub(r"\[[0-9.]+(?:em|pt|cm|mm)\]", " ", text)
    text = re.sub(r"\\emergencystretch\s*=\s*[^\s{}\n]+", " ", text)

    def keep_bold(match: re.Match[str]) -> str:
        return "\ue000" + match.group(1) + "\ue001"

    text = re.sub(r"\\textbf\{([^{}]*)\}", keep_bold, text)
    text = re.sub(r"\\textrm\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\text\{([^{}]*)\}", r"\1", text)
    text = re.sub(
        r"\$\^\{\\mathrm\{([^{}]+)\}\}\$",
        lambda m: "\ue002" + m.group(1) + "\ue003",
        text,
    )
    text = text.replace(r"$^\circ$", "°")
    text = text.replace(r"$10^{-3}$", "10⁻³")
    text = text.replace(r"$0.46\times10^{-3}$", "0.46 × 10⁻³")
    text = text.replace(r"$G$", "G").replace(r"$R$", "R")
    text = text.replace(r"$b_G$", "b_G").replace(r"$b_R$", "b_R")
    text = text.replace(r"$c$", "c").replace(r"$g=120$", "g = 120")
    text = text.replace(r"$G(c)=1$", "G(c) = 1")
    text = re.sub(r"\$([^$]+)\$", r"\1", text)

    rendered = exporter.html_inline(text, cite_map, label_map)
    rendered = rendered.replace("\ue000", "<strong>").replace("\ue001", "</strong>")
    rendered = rendered.replace("\ue002", "<sup>").replace("\ue003", "</sup>")

    def compress_citation(match: re.Match[str]) -> str:
        numbers = sorted({int(number) for number in match.group(1).split(",")})
        ranges: list[str] = []
        start = previous = numbers[0]
        for number in numbers[1:] + [numbers[-1] + 2]:
            if number != previous + 1:
                ranges.append(str(start) if start == previous else f"{start}–{previous}")
                start = number
            previous = number
        return "[" + ", ".join(ranges) + "]"

    rendered = re.sub(r"\[(\d+(?:,\d+)+)\]", compress_citation, rendered)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered


def clean_math_fallback(tex: str) -> str:
    text = tex
    text = re.sub(r"\\(?:mathrm|text|operatorname)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:left|right)", "", text)
    replacements = {
        r"\times": " × ",
        r"\qquad": "; ",
        r"\quad": "  ",
        r"\leq": " ≤ ",
        r"\geq": " ≥ ",
        r"\neq": " ≠ ",
        r"\cdot": " · ",
        r"\alpha": "α",
        r"\beta": "β",
        r"\Delta": "Δ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace(r"\\", "; ")
    text = text.replace("&", " ")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def equation_html(tex: str) -> str:
    """Convert the paper's displayed equations to editable Word text."""

    if r"\mathrm{accept}" in tex:
        return (
            "G(c) = 1 denotes that candidate c passes the hard guards."
            "<br>Accept the new candidate if G(c) = 1 and "
            "G(c<sub>current</sub>) = 0;"
            "<br>accept if G(c) = 1, G(c<sub>current</sub>) = 1, and "
            "Q(c) &gt; Q(c<sub>current</sub>) + 0.1;"
            "<br>otherwise retain the current state."
        )
    if "Similarity" in tex:
        return "Similarity = N<sub>match</sub> / N<sub>shared</sub>"
    if "S(A)" in tex:
        return (
            "S(A) = 2N<sub>match</sub> − N<sub>mismatch</sub> "
            "− 2N<sub>gap-open</sub> − 0.5N<sub>gap-extend</sub>"
        )
    if "Q(c)" in tex:
        return (
            "Q(c) = R<sub>structure</sub>(c) + R<sub>external</sub>(c) "
            "− P(c)"
        )
    if "Accuracy" in tex:
        return (
            "Per-nucleotide accuracy = N<sub>correct</sub> / N<sub>total</sub>;"
            "&nbsp;&nbsp;exact-sequence agreement = N<sub>perfect</sub> / "
            "N<sub>scorable</sub>"
        )
    if "Accepted" in tex:
        return (
            "Automatic-acceptance coverage = N<sub>accepted</sub> / "
            "N<sub>processed</sub>"
        )
    if tex.strip().startswith("r="):
        return (
            "r = [(G − b<sub>G</sub>) / (R − b<sub>R</sub>)] × "
            "(g / 179)<sup>7.82</sup>"
        )
    return html.escape(clean_math_fallback(tex), quote=False)


def protect_equations(text: str) -> tuple[str, dict[str, str]]:
    equations: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        key = f"@@EQUATION{len(equations)}@@"
        equations[key] = equation_html(match.group(1))
        return key

    return DISPLAY_MATH_PATTERN.sub(repl, text), equations


def render_paragraph(
    paragraph: str,
    equations: dict[str, str],
    cite_map: dict[str, int],
    label_map: dict[str, str],
    *,
    css_class: str = "",
) -> str:
    paragraph = paragraph.strip()
    if not paragraph:
        return ""
    if paragraph.startswith("@@SUBSECTION"):
        match = re.fullmatch(r"@@SUBSECTION(\d+)@@", paragraph)
        if match:
            return ""

    eq_keys = [key for key in equations if key in paragraph]
    if not eq_keys:
        cls = f' class="{css_class}"' if css_class else ""
        return f"<p{cls}>{inline_html(paragraph, cite_map, label_map)}</p>"

    pieces = re.split("(" + "|".join(map(re.escape, eq_keys)) + ")", paragraph)
    out: list[str] = []
    for piece in pieces:
        if piece in equations:
            out.append(f'<p class="equation">{equations[piece]}</p>')
        elif piece.strip():
            out.append(f"<p>{inline_html(piece, cite_map, label_map)}</p>")
    return "\n".join(out)


def render_fragment(
    body: str,
    cite_map: dict[str, int],
    label_map: dict[str, str],
) -> str:
    body, equations = protect_equations(body)
    subsection_map: dict[str, str] = {}

    def subsection_repl(match: re.Match[str]) -> str:
        key = f"@@SUBSECTION{len(subsection_map)}@@"
        subsection_map[key] = match.group(1)
        return f"\n\n{key}\n\n"

    body = SUBSECTION_PATTERN.sub(subsection_repl, body)
    for command in (
        r"\begin{sloppypar}",
        r"\end{sloppypar}",
        r"\begingroup",
        r"\endgroup",
        r"\FloatBarrier",
        r"\medskip",
        r"\noindent",
    ):
        body = body.replace(command, "")
    body = re.sub(r"\\emergencystretch\s*=\s*[^\s{}\n]+", "", body)

    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if paragraph in subsection_map:
            out.append(f"<h2>{inline_html(subsection_map[paragraph], cite_map, label_map)}</h2>")
        else:
            rendered = render_paragraph(paragraph, equations, cite_map, label_map)
            if rendered:
                out.append(rendered)
    return "\n".join(out)


def split_sections(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"\\section\{([^{}]+)\}", body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1)] = body[start:end].strip()
    return sections


def extract_figures(body: str) -> list[dict[str, str]]:
    figures: list[dict[str, str]] = []
    for number, match in enumerate(FIGURE_PATTERN.finditer(body), 1):
        block = match.group(0)
        image = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", block)
        if not image:
            continue
        figures.append(
            {
                "number": str(number),
                "caption": exporter.caption_text(block),
                "image": image.group(1),
            }
        )
    return figures


def figure_html(
    figure: dict[str, str],
    cite_map: dict[str, int],
    label_map: dict[str, str],
) -> str:
    path = exporter.image_path(figure["image"])
    attrs = exporter.figure_size_attrs(path)
    src = path.as_uri()
    number = html.escape(figure["number"])
    return (
        '<p class="figure-page">'
        f'<img src="{html.escape(src, quote=True)}" alt="Fig. {number}" {attrs}>'
        "</p>"
        f'<p class="figure-label"><strong>Fig. {number}</strong></p>'
    )


def clean_bib_text(value: str) -> str:
    """Convert the small BibTeX subset used in refs.bib to plain text."""

    text = value.strip()
    text = text.replace(r"\AA", "Å").replace(r"\aa", "å")
    text = text.replace(r"\alpha", "α").replace(r"\beta", "β")
    text = text.replace(r"\&", "&").replace(r"\%", "%")
    text = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\text(?:it|bf|rm)?\{([^{}]*)\}", r"\1", text)

    def accent(match: re.Match[str]) -> str:
        command = match.group(1)
        letter = match.group(2)
        mark = ACCENT_MARKS.get(command, "")
        return unicodedata.normalize("NFC", letter + mark)

    text = re.sub(r"\{\\([\"'`^~=.])\s*([A-Za-z])\}", accent, text)
    text = re.sub(r"\{\\c\s*([A-Za-z])\}", lambda m: accent_text(m.group(1), "c"), text)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    text = text.replace(r"\ ", " ")
    text = text.replace("$", "")
    text = text.replace("{", "").replace("}", "")
    text = text.replace("--", "–")
    return re.sub(r"\s+", " ", text).strip()


def accent_text(letter: str, command: str) -> str:
    return unicodedata.normalize("NFC", letter + ACCENT_MARKS.get(command, ""))


def split_bib_author(author: str) -> tuple[str, str]:
    if "," in author:
        family, given = author.split(",", 1)
        return family.strip(), given.strip()
    parts = author.split()
    return parts[-1] if parts else author.strip(), " ".join(parts[:-1])


def format_initials(given: str) -> str:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", given)
    initials: list[str] = []
    for token in tokens:
        pieces = token.split("-")
        initials.append("-".join(piece[0].upper() + "." for piece in pieces if piece))
    return " ".join(initials)


def format_bib_authors(author_field: str) -> str:
    raw_authors = [author.strip() for author in author_field.split(" and ") if author.strip()]
    has_more = any(author.lower() == "others" for author in raw_authors)
    actual = [author for author in raw_authors if author.lower() != "others"]
    use_et_al = has_more or len(actual) >= 6
    selected = actual[:1] if use_et_al else actual
    names: list[str] = []
    for raw in selected:
        raw = clean_bib_text(raw)
        family, given = split_bib_author(raw)
        cleaned_family = clean_bib_text(family)
        initials = format_initials(clean_bib_text(given))
        names.append(f"{cleaned_family}, {initials}".rstrip(", "))
    if use_et_al:
        return f"{names[0]} et al."
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{', '.join(names[:-1])} & {names[-1]}"


def top_level_parts(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == delimiter and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def parse_bibtex(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    entries: dict[str, dict[str, str]] = {}
    position = 0
    while True:
        match = re.search(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text[position:])
        if not match:
            break
        entry_start = position + match.start()
        key = match.group(2)
        body_start = position + match.end()
        depth = 1
        cursor = body_start
        while cursor < len(text) and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        body = text[body_start : cursor - 1]
        fields = {"ENTRYTYPE": match.group(1).lower()}
        for part in top_level_parts(body):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip().lower()
            value = value.strip().rstrip(",")
            if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
                value = value[1:-1]
            elif len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            fields[name] = value
        entries[key] = fields
        position = cursor
    return entries


def format_nature_reference(fields: dict[str, str]) -> str:
    authors = format_bib_authors(fields.get("author", ""))
    title = clean_bib_text(fields.get("title", "")).rstrip(".")
    year = clean_bib_text(fields.get("year", ""))
    if fields.get("ENTRYTYPE") == "inproceedings":
        venue = clean_bib_text(fields.get("booktitle", "Proceedings"))
        url = clean_bib_text(fields.get("url", ""))
        url_text = f" {html.escape(url)}" if url else ""
        return (
            f"{html.escape(authors)} {html.escape(title)}. "
            f"<em>{html.escape(venue)}</em> ({html.escape(year)}).{url_text}"
        )

    journal = clean_bib_text(fields.get("journal", ""))
    journal = JOURNAL_ABBREVIATIONS.get(journal, journal)
    volume = clean_bib_text(fields.get("volume", ""))
    pages = clean_bib_text(fields.get("pages", ""))
    doi = clean_bib_text(fields.get("doi", ""))
    pieces = [f"{html.escape(authors)}", f"{html.escape(title)}."]
    if journal:
        venue = f"<em>{html.escape(journal)}</em>"
        if volume:
            venue += f" <strong>{html.escape(volume)}</strong>"
        if pages:
            venue += f", {html.escape(pages)}"
        pieces.append(venue)
    if year:
        pieces.append(f"({html.escape(year)}).")
    if doi:
        pieces.append(
            f'<a href="https://doi.org/{html.escape(doi, quote=True)}">'
            f"https://doi.org/{html.escape(doi)}</a>"
        )
    return " ".join(pieces)


def build_html() -> Path:
    tex = SOURCE.read_text(encoding="utf-8")
    cite_map, references = exporter.parse_bibliography()
    label_map = exporter.collect_labels(tex)
    title = exporter.latex_inline(
        exporter.brace_content(tex, "title").replace(r"\\", " "),
        cite_map,
        label_map,
    )
    title = re.sub(r"\s*\[[0-9.]+(?:em|pt|cm|mm)\]\s*", " ", title).strip()
    author = AUTHOR_LINE

    body = tex.split(r"\begin{document}", 1)[1].split(r"\end{document}", 1)[0]
    body = body.replace(r"\maketitle", "")

    abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S)
    if not abstract_match:
        raise RuntimeError("Abstract not found in main.tex")
    abstract = abstract_match.group(1).strip()
    body = body[: abstract_match.start()] + body[abstract_match.end() :]

    keyword_match = re.search(
        r"\\noindent\\textbf\{Keywords:\}(.*?)(?=\\section\{)",
        body,
        re.S,
    )
    if not keyword_match:
        raise RuntimeError("Keywords not found in main.tex")
    keywords = keyword_match.group(1).strip()
    body = body[: keyword_match.start()] + body[keyword_match.end() :]

    figures = extract_figures(body)
    if len(figures) != 6:
        raise RuntimeError(f"Expected 6 figures, found {len(figures)}")
    body = FIGURE_PATTERN.sub("", body)
    body = re.sub(r"\\bibliographystyle\{[^}]+\}|\\bibliography\{[^}]+\}", "", body)
    sections = split_sections(body)

    ordered_sections = [
        ("Introduction", "Introduction"),
        ("Results", "Results"),
        ("Discussion", "Discussion"),
        ("Methods", "Methods"),
        ("Code availability", "Code availability"),
    ]
    missing = [source_name for source_name, _ in ordered_sections if source_name not in sections]
    if missing:
        raise RuntimeError(f"Missing manuscript sections: {', '.join(missing)}")

    section_html: list[str] = []
    for source_name, heading in ordered_sections:
        section_html.append(f"<h1>{heading}</h1>")
        section_html.append(render_fragment(sections[source_name], cite_map, label_map))
        if source_name == "Methods":
            section_html.extend(
                [
                    "<h1>Data availability</h1>",
                    (
                        '<p class="placeholder"><strong>[TO BE COMPLETED]</strong>'
                        " Provide the public repository, accession number, or DOI "
                        "for the benchmark inputs, gold-standard snapshot, run "
                        "outputs, source data, and any access conditions.</p>"
                    ),
                ]
            )

    bibliography = parse_bibtex(PAPER / "refs.bib")
    ordered_keys = [
        key for key, _ in sorted(cite_map.items(), key=lambda item: item[1])
    ]
    refs_html: list[str] = []
    for index, key in enumerate(ordered_keys, 1):
        if key in bibliography:
            rendered = format_nature_reference(bibliography[key])
        else:
            fallback = references[index - 1]
            fallback = re.sub(
                r"\$\^mathrm([A-Za-z0-9]+)\$",
                lambda match: "\ue002" + match.group(1) + "\ue003",
                fallback,
            )
            rendered = inline_html(fallback, {}, label_map)
        refs_html.append(f'<p class="reference">[{index}] {rendered}</p>')

    statement_html = """
<h1>Acknowledgements</h1>
<p class="placeholder"><strong>[TO BE COMPLETED]</strong> List funding sources, grant numbers, and individuals who contributed but do not meet authorship criteria.</p>
<h1>Author contributions</h1>
<p>Z.O. developed the SprinzlMap software and wrote the manuscript. Q.W. also wrote the manuscript. M.W. prepared the scientific figures. Q.T. and Z.C. performed the experimental validation. All authors read and approved the final manuscript.</p>
<h1>Funding</h1>
<p class="placeholder"><strong>[TO BE COMPLETED]</strong> List each author's relevant funding sources and grant numbers.</p>
<h1>Competing interests</h1>
<p class="placeholder"><strong>[TO BE COMPLETED]</strong> Provide the competing-interests statement confirmed by all authors. Nature Communications requires this statement for every manuscript.</p>
"""

    legends = "\n".join(
        (
            f'<p class="figure-legend"><strong>Fig. {fig["number"]} |</strong> '
            f'{inline_html(fig["caption"], cite_map, label_map)}</p>'
        )
        for fig in figures
    )
    grouped_figures = "\n".join(
        figure_html(figure, cite_map, label_map) for figure in figures
    )

    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
@page {{ size: A4; margin: 2.54cm; }}
body {{
  font-family: "Times New Roman", "SimSun", serif;
  font-size: 11pt;
  line-height: 2.0;
  color: #000000;
  margin: 0;
  text-align: left;
}}
p {{
  margin: 0;
  line-height: 2.0;
  text-align: left;
  text-indent: 0;
}}
h1, h2 {{
  font-family: "Times New Roman", "SimHei", sans-serif;
  text-align: left;
  text-indent: 0;
  line-height: 2.0;
  page-break-after: avoid;
}}
h1 {{ font-size: 14pt; margin: 12pt 0 0; }}
h2 {{ font-size: 12pt; margin: 6pt 0 0; }}
.journal {{ font-size: 11pt; font-weight: bold; }}
.article-type {{ font-size: 11pt; }}
.doc-title {{ font-size: 18pt; font-weight: bold; margin-top: 18pt; }}
.authors {{ font-size: 12pt; margin-top: 12pt; }}
.metadata {{ font-size: 10.5pt; margin-top: 6pt; }}
.page-break {{ page-break-before: always; }}
.keywords {{ margin-top: 12pt; }}
.abstract-text {{ margin-top: 6pt; }}
.equation {{ text-align: left; margin: 6pt 0; }}
.placeholder {{ color: #7a1f1f; }}
.reference {{ font-size: 10pt; line-height: 2.0; }}
.figure-legend {{ font-size: 10pt; line-height: 2.0; margin-bottom: 8pt; }}
.figure-page {{ page-break-before: always; text-align: center; }}
.figure-page img {{ display: block; margin: 0 auto; }}
.figure-label {{ text-align: left; font-size: 11pt; font-weight: bold; }}
code {{ font-family: "Courier New", monospace; }}
</style>
</head>
<body>
<p class="journal">NATURE COMMUNICATIONS</p>
<p class="article-type">Article</p>
<p class="doc-title">{html.escape(title)}</p>
<p class="authors">{html.escape(author)}</p>
<p class="metadata"><strong>Affiliations:</strong> {html.escape(AFFILIATION)}</p>
<p class="metadata"><strong>*Corresponding author:</strong> Lingling Zheng</p>
<p class="metadata"><strong>Email:</strong> {EMAIL}</p>
<p class="metadata"><strong>ORCID:</strong> {ORCID}</p>

<h1 class="page-break">Abstract</h1>
<p class="abstract-text">{inline_html(abstract, cite_map, label_map)}</p>
<p class="keywords"><strong>Keywords:</strong> {inline_html(keywords, cite_map, label_map)}</p>

{chr(10).join(section_html)}

<h1>References</h1>
{chr(10).join(refs_html)}

{statement_html}

<h1>Figure legends</h1>
{legends}

<h1>Figures</h1>
{grouped_figures}
</body>
</html>
"""
    HTML_PATH.write_text(html_doc, encoding="utf-8")
    return HTML_PATH


def add_footer(
    base: Path,
    document_root: ET.Element,
    relationships_root: ET.Element,
    content_types_root: ET.Element,
) -> None:
    body = document_root.find(word.w_tag("body"))
    if body is None:
        raise RuntimeError("DOCX body missing")
    sect_pr = body.find(word.w_tag("sectPr"))
    if sect_pr is None:
        sect_pr = ET.Element(word.w_tag("sectPr"))
        body.append(sect_pr)

    for existing in list(sect_pr.findall(word.w_tag("footerReference"))):
        sect_pr.remove(existing)

    ids = {rel.get("Id") for rel in relationships_root.findall(f"{{{REL_NS}}}Relationship")}
    rel_id = "rIdNaturePageFooter"
    while rel_id in ids:
        rel_id += "X"
    relationship = ET.SubElement(relationships_root, f"{{{REL_NS}}}Relationship")
    relationship.set("Id", rel_id)
    relationship.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
    )
    relationship.set("Target", "footer-nature.xml")

    footer_ref = ET.Element(word.w_tag("footerReference"))
    footer_ref.set(word.w_attr("type"), "default")
    footer_ref.set(f"{{{R_NS}}}id", rel_id)
    sect_pr.insert(0, footer_ref)

    override = ET.SubElement(content_types_root, f"{{{CT_NS}}}Override")
    override.set("PartName", "/word/footer-nature.xml")
    override.set(
        "ContentType",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    )

    footer_root = ET.Element(word.w_tag("ftr"))
    paragraph = ET.SubElement(footer_root, word.w_tag("p"))
    p_pr = ET.SubElement(paragraph, word.w_tag("pPr"))
    jc = ET.SubElement(p_pr, word.w_tag("jc"))
    jc.set(word.w_attr("val"), "center")

    begin_run = ET.SubElement(paragraph, word.w_tag("r"))
    begin = ET.SubElement(begin_run, word.w_tag("fldChar"))
    begin.set(word.w_attr("fldCharType"), "begin")
    instr_run = ET.SubElement(paragraph, word.w_tag("r"))
    instr = ET.SubElement(instr_run, word.w_tag("instrText"))
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = " PAGE "
    separate_run = ET.SubElement(paragraph, word.w_tag("r"))
    separate = ET.SubElement(separate_run, word.w_tag("fldChar"))
    separate.set(word.w_attr("fldCharType"), "separate")
    value_run = ET.SubElement(paragraph, word.w_tag("r"))
    value = ET.SubElement(value_run, word.w_tag("t"))
    value.text = "1"
    end_run = ET.SubElement(paragraph, word.w_tag("r"))
    end = ET.SubElement(end_run, word.w_tag("fldChar"))
    end.set(word.w_attr("fldCharType"), "end")

    (base / "word" / "footer-nature.xml").write_bytes(
        ET.tostring(footer_root, encoding="utf-8", xml_declaration=True)
    )


def normalize_styles(styles_path: Path) -> None:
    root = ET.parse(styles_path).getroot()
    doc_defaults = word.child(root, "docDefaults", 0)
    r_pr_default = word.child(word.child(doc_defaults, "rPrDefault"), "rPr")
    word.set_run_fonts(r_pr_default, east_asia="SimSun", ascii_font="Times New Roman")
    word.set_run_size(r_pr_default, 22)
    lang = word.child(r_pr_default, "lang")
    lang.set(word.w_attr("val"), "en-US")
    lang.set(word.w_attr("eastAsia"), "zh-CN")

    p_pr_default = word.child(word.child(doc_defaults, "pPrDefault"), "pPr")
    word.set_indent(p_pr_default, 0)
    word.set_spacing(p_pr_default, 0, 0, 480)
    word.set_justification(p_pr_default, "left")

    word.apply_style(
        root,
        "Normal",
        east_asia="SimSun",
        size=22,
        first_line=0,
        before=0,
        after=0,
        line=480,
        jc="left",
    )
    word.apply_style(
        root,
        "BodyText",
        east_asia="SimSun",
        size=22,
        first_line=0,
        before=0,
        after=0,
        line=480,
        jc="left",
    )
    word.apply_style(
        root,
        "Heading1",
        east_asia="SimHei",
        size=28,
        first_line=0,
        before=240,
        after=120,
        line=480,
        bold=True,
        jc="left",
    )
    word.apply_style(
        root,
        "Heading2",
        east_asia="SimHei",
        size=24,
        first_line=0,
        before=120,
        after=0,
        line=480,
        bold=True,
        jc="left",
    )
    word.apply_style(
        root,
        "Caption",
        east_asia="SimSun",
        size=20,
        first_line=0,
        before=0,
        after=80,
        line=480,
        jc="left",
    )
    word.apply_style(
        root,
        "BodyTextcaption",
        east_asia="SimSun",
        size=20,
        first_line=0,
        before=0,
        after=80,
        line=480,
        jc="left",
    )
    ET.ElementTree(root).write(styles_path, encoding="utf-8", xml_declaration=True)


def normalize_document(document_path: Path) -> None:
    root = ET.parse(document_path).getroot()
    body = root.find(word.w_tag("body"))
    if body is None:
        raise RuntimeError("DOCX body missing")

    for p in body.iter(word.w_tag("p")):
        text = word.paragraph_text(p)
        style = word.paragraph_style(p)
        has_drawing = p.find(f".//{word.w_tag('drawing')}") is not None

        if has_drawing and not text:
            word.set_paragraph_format(
                p,
                first_line=None,
                before=0,
                after=120,
                line=None,
                jc="center",
            )
            continue
        if not text:
            word.set_paragraph_format(
                p,
                first_line=None,
                before=0,
                after=0,
                line=None,
                jc=None,
            )
            continue

        if style == "Heading1":
            word.set_paragraph_format(
                p,
                first_line=None,
                before=240,
                after=120,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimHei", size=28, bold=True)
        elif style == "Heading2":
            word.set_paragraph_format(
                p,
                first_line=None,
                before=120,
                after=0,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimHei", size=24, bold=True)
        elif text.startswith("SprinzlMap:"):
            word.set_paragraph_format(
                p,
                first_line=None,
                before=240,
                after=120,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimHei", size=36, bold=True)
        elif text in {"NATURE COMMUNICATIONS", "Article"}:
            word.set_paragraph_format(
                p,
                first_line=None,
                before=0,
                after=0,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimSun", size=22, bold=True)
        elif text.startswith("Fig. "):
            word.set_paragraph_format(
                p,
                first_line=None,
                before=0,
                after=80,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimSun", size=20)
        elif text.startswith("["):
            word.set_paragraph_format(
                p,
                first_line=None,
                before=0,
                after=0,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimSun", size=20)
        else:
            word.set_paragraph_format(
                p,
                first_line=0,
                before=0,
                after=0,
                line=480,
                jc="left",
            )
            word.set_paragraph_runs(p, east_asia="SimSun", size=22)

    for sect_pr in body.iter(word.w_tag("sectPr")):
        pg_sz = word.child(sect_pr, "pgSz")
        pg_sz.set(word.w_attr("w"), "11906")
        pg_sz.set(word.w_attr("h"), "16838")
        pg_mar = word.child(sect_pr, "pgMar")
        pg_mar.attrib.update(
            {
                word.w_attr("top"): "1440",
                word.w_attr("right"): "1440",
                word.w_attr("bottom"): "1440",
                word.w_attr("left"): "1440",
                word.w_attr("header"): "720",
                word.w_attr("footer"): "720",
                word.w_attr("gutter"): "0",
            }
        )
        cols = word.child(sect_pr, "cols")
        cols.set(word.w_attr("num"), "1")
        pg_num = word.child(sect_pr, "pgNumType")
        pg_num.set(word.w_attr("fmt"), "decimal")
        pg_num.set(word.w_attr("start"), "1")
        line_numbers = word.child(sect_pr, "lnNumType")
        line_numbers.set(word.w_attr("countBy"), "1")
        line_numbers.set(word.w_attr("restart"), "continuous")
        line_numbers.set(word.w_attr("distance"), "360")
        sect_pr.remove(line_numbers)
        pg_num_index = list(sect_pr).index(pg_num)
        sect_pr.insert(pg_num_index, line_numbers)

    ET.ElementTree(root).write(document_path, encoding="utf-8", xml_declaration=True)


def update_core_properties(path: Path) -> None:
    root = ET.parse(path).getroot()
    title = root.find(f"{{{DC_NS}}}title")
    if title is not None:
        title.text = (
            "SprinzlMap: an auditable scientific agent for tRNA "
            "Sprinzl-coordinate mapping"
        )
    subject = root.find(f"{{{DC_NS}}}subject")
    if subject is None:
        subject = ET.SubElement(root, f"{{{DC_NS}}}subject")
    subject.text = "Nature Communications submission manuscript"
    creator = root.find(f"{{{DC_NS}}}creator")
    if creator is None:
        creator = ET.SubElement(root, f"{{{DC_NS}}}creator")
    creator.text = "Ouyang Zhuo et al."
    ET.register_namespace("", CP_NS)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def normalize_docx(path: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        with ZipFile(path) as zin:
            zin.extractall(base)

        normalize_styles(base / "word" / "styles.xml")
        normalize_document(base / "word" / "document.xml")

        document_root = ET.parse(base / "word" / "document.xml").getroot()
        rels_root = ET.parse(base / "word" / "_rels" / "document.xml.rels").getroot()
        content_root = ET.parse(base / "[Content_Types].xml").getroot()
        add_footer(base, document_root, rels_root, content_root)
        ET.ElementTree(document_root).write(
            base / "word" / "document.xml", encoding="utf-8", xml_declaration=True
        )
        ET.register_namespace("", REL_NS)
        ET.ElementTree(rels_root).write(
            base / "word" / "_rels" / "document.xml.rels",
            encoding="utf-8",
            xml_declaration=True,
        )
        ET.register_namespace("", CT_NS)
        ET.ElementTree(content_root).write(
            base / "[Content_Types].xml", encoding="utf-8", xml_declaration=True
        )
        update_core_properties(base / "docProps" / "core.xml")

        path.unlink()
        with ZipFile(path, "w", ZIP_DEFLATED) as zout:
            for item in base.rglob("*"):
                if item.is_file():
                    zout.write(item, item.relative_to(base).as_posix())


def embed_odt_images(src: Path, dest: Path) -> None:
    """Embed figures rewritten by LibreOffice relative to the tmp workspace."""

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        with ZipFile(src) as zin:
            zin.extractall(base)

        content_path = base / "content.xml"
        manifest_path = base / "META-INF" / "manifest.xml"
        content = content_path.read_text(encoding="utf-8")
        manifest = manifest_path.read_text(encoding="utf-8")
        pictures = base / "Pictures"
        pictures.mkdir(exist_ok=True)
        used: list[str] = []

        def repl(match: re.Match[str]) -> str:
            href = unquote(match.group(1))
            if "figures/" not in href:
                return match.group(0)
            candidates = [
                (WORK / href).resolve(),
                (PAPER / href).resolve(),
                (REPO / href).resolve(),
                PAPER / "figures" / Path(href).name,
            ]
            image_path = next((path for path in candidates if path.exists()), None)
            if image_path is None:
                raise FileNotFoundError(href)
            name = f"Pictures/{image_path.name}"
            shutil.copyfile(image_path, base / name)
            used.append(name)
            return f'xlink:href="{name}"'

        content = re.sub(r'xlink:href="([^"]+)"', repl, content)
        content_path.write_text(content, encoding="utf-8")
        for name in sorted(set(used)):
            if f'manifest:full-path="{name}"' not in manifest:
                manifest = manifest.replace(
                    "</manifest:manifest>",
                    f'<manifest:file-entry manifest:full-path="{name}" '
                    'manifest:media-type="image/png"/></manifest:manifest>',
                )
        manifest_path.write_text(manifest, encoding="utf-8")

        dest.unlink(missing_ok=True)
        with ZipFile(dest, "w") as zout:
            mimetype = base / "mimetype"
            if mimetype.exists():
                zout.write(mimetype, "mimetype", compress_type=ZIP_STORED)
            for item in base.rglob("*"):
                if item.is_file() and item.name != "mimetype":
                    zout.write(
                        item,
                        item.relative_to(base).as_posix(),
                        compress_type=ZIP_DEFLATED,
                    )


def validate_output() -> None:
    run(["unzip", "-t", str(DOCX)])
    run(
        [
            "libreoffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(WORK),
            str(DOCX),
        ]
    )

    with ZipFile(DOCX) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
        media = [name for name in zf.namelist() if name.startswith("word/media/")]
    if len(media) != 6:
        raise RuntimeError(f"Expected 6 embedded figures, found {len(media)}")
    if "word/footer-nature.xml" not in ZipFile(DOCX).namelist():
        raise RuntimeError("Nature footer with page field was not embedded")
    if "lnNumType" not in document_xml:
        raise RuntimeError("Continuous Word line numbering was not embedded")
    if not re.search(r"<w:instrText[^>]*> PAGE </w:instrText>", document_xml):
        footer_xml = ZipFile(DOCX).read("word/footer-nature.xml").decode("utf-8")
        if " PAGE " not in footer_xml:
            raise RuntimeError("Arabic page-number field missing")

    text = re.sub(r"<[^>]+>", "", document_xml)
    forbidden = re.findall(
        r"\\(?:section|subsection|ref|cite|begin|end|textbf|textit|mathrm|caption)\b",
        text,
    )
    if forbidden:
        raise RuntimeError(f"Unrendered LaTeX commands remain: {sorted(set(forbidden))}")

    pdf_info = subprocess.run(
        ["pdfinfo", str(PDF_CHECK)],
        check=True,
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    page_match = re.search(r"^Pages:\s+(\d+)", pdf_info, re.M)
    size_match = re.search(r"^File size:\s+([^\n]+)", pdf_info, re.M)
    if not page_match:
        raise RuntimeError("Could not determine rendered PDF page count")
    print(f"DOCX: {DOCX}")
    print(f"Pages: {page_match.group(1)}")
    print(f"File size: {size_match.group(1) if size_match else 'unknown'}")
    print(f"Embedded figures: {len(media)}")
    print(f"Validation PDF: {PDF_CHECK}")


def main() -> None:
    libreoffice = shutil.which("libreoffice")
    if not libreoffice:
        raise SystemExit("libreoffice is required")
    if not SOURCE.exists() or not (PAPER / "main.bbl").exists():
        raise SystemExit("Compile main.tex with BibTeX before building the DOCX")

    OUTDIR.mkdir(parents=True, exist_ok=True)
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for path in (DOCX, ODT_RAW, ODT_EMBEDDED, PDF_CHECK):
        path.unlink(missing_ok=True)

    html_path = build_html()
    run(
        [
            libreoffice,
            "--headless",
            "--convert-to",
            "odt",
            "--outdir",
            str(WORK),
            str(html_path),
        ]
    )
    embed_odt_images(ODT_RAW, ODT_EMBEDDED)
    run(
        [
            libreoffice,
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            str(WORK),
            str(ODT_EMBEDDED),
        ]
    )
    built_docx = WORK / "manuscript-embedded.docx"
    shutil.move(built_docx, DOCX)
    normalize_docx(DOCX)
    validate_output()


if __name__ == "__main__":
    main()
