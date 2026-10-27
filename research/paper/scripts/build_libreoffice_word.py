#!/usr/bin/env python3
"""Build the editable LibreOffice/Word manuscript and a rendered PDF check."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
EXPORT_SCRIPT = PAPER / "scripts" / "export_libreoffice_doc.py"
HTML = PAPER / "main_libreoffice.html"
RAW_ODT = PAPER / "main_libreoffice.odt"
ODT = PAPER / "main_google_docs.odt"
DOCX = PAPER / "main_google_docs.docx"
PDF = PAPER / "main_google_docs.pdf"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


for prefix, uri in (
    ("w", W_NS),
    ("r", R_NS),
    ("wp", WP_NS),
    ("a", A_NS),
    ("pic", PIC_NS),
    ("mc", MC_NS),
    ("w14", W14_NS),
):
    ET.register_namespace(prefix, uri)


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def w_tag(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def w_attr(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def child(parent: ET.Element, name: str, index: int | None = None) -> ET.Element:
    tag = w_tag(name)
    found = parent.find(tag)
    if found is not None:
        return found
    found = ET.Element(tag)
    if index is None:
        parent.append(found)
    else:
        parent.insert(index, found)
    return found


def paragraph_style(p: ET.Element) -> str:
    p_pr = p.find(w_tag("pPr"))
    if p_pr is None:
        return ""
    p_style = p_pr.find(w_tag("pStyle"))
    if p_style is None:
        return ""
    return p_style.get(w_attr("val"), "")


def paragraph_text(p: ET.Element) -> str:
    return "".join(t.text or "" for t in p.iter(w_tag("t"))).strip()


def set_spacing(p_pr: ET.Element, before: int, after: int, line: int | None = None) -> None:
    spacing = child(p_pr, "spacing")
    attrs = {
        w_attr("before"): str(before),
        w_attr("after"): str(after),
    }
    if line is not None:
        attrs[w_attr("line")] = str(line)
        attrs[w_attr("lineRule")] = "auto"
    spacing.attrib.clear()
    spacing.attrib.update(attrs)


def set_indent(p_pr: ET.Element, first_line: int | None = None) -> None:
    ind = child(p_pr, "ind")
    ind.attrib.clear()
    ind.set(w_attr("left"), "0")
    ind.set(w_attr("right"), "0")
    if first_line is not None:
        ind.set(w_attr("firstLine"), str(first_line))


def set_justification(p_pr: ET.Element, value: str | None) -> None:
    jc = p_pr.find(w_tag("jc"))
    if value is None:
        if jc is not None:
            p_pr.remove(jc)
        return
    jc = child(p_pr, "jc")
    jc.attrib.clear()
    jc.set(w_attr("val"), value)


def set_run_fonts(r_pr: ET.Element, east_asia: str = "SimSun", ascii_font: str = "Times New Roman") -> None:
    fonts = child(r_pr, "rFonts", 0)
    fonts.set(w_attr("ascii"), ascii_font)
    fonts.set(w_attr("hAnsi"), ascii_font)
    fonts.set(w_attr("cs"), ascii_font)
    fonts.set(w_attr("eastAsia"), east_asia)


def set_run_size(r_pr: ET.Element, half_points: int) -> None:
    sz = child(r_pr, "sz")
    sz.set(w_attr("val"), str(half_points))
    sz_cs = child(r_pr, "szCs")
    sz_cs.set(w_attr("val"), str(half_points))


def ensure_bold(r_pr: ET.Element) -> None:
    child(r_pr, "b")
    child(r_pr, "bCs")


def set_paragraph_format(p: ET.Element, *, first_line: int | None, before: int, after: int, line: int | None, jc: str | None) -> None:
    p_pr = p.find(w_tag("pPr"))
    if p_pr is None:
        p_pr = ET.Element(w_tag("pPr"))
        p.insert(0, p_pr)
    set_indent(p_pr, first_line)
    set_spacing(p_pr, before, after, line)
    set_justification(p_pr, jc)


def set_paragraph_runs(p: ET.Element, *, east_asia: str, size: int, bold: bool = False, ascii_font: str = "Times New Roman") -> None:
    for run in p.iter(w_tag("r")):
        r_pr = run.find(w_tag("rPr"))
        if r_pr is None:
            r_pr = ET.Element(w_tag("rPr"))
            run.insert(0, r_pr)
        set_run_fonts(r_pr, east_asia=east_asia, ascii_font=ascii_font)
        set_run_size(r_pr, size)
        if bold:
            ensure_bold(r_pr)


def apply_style(styles_root: ET.Element, style_id: str, *, east_asia: str, size: int, first_line: int | None, before: int, after: int, line: int | None, bold: bool = False, jc: str | None = None) -> None:
    style = None
    for candidate in styles_root.findall(w_tag("style")):
        if candidate.get(w_attr("styleId")) == style_id:
            style = candidate
            break
    if style is None:
        return
    p_pr = child(style, "pPr")
    set_indent(p_pr, first_line)
    set_spacing(p_pr, before, after, line)
    set_justification(p_pr, jc)
    for tag in ("pBdr", "numPr"):
        existing = p_pr.find(w_tag(tag))
        if existing is not None:
            p_pr.remove(existing)
    r_pr = child(style, "rPr")
    set_run_fonts(r_pr, east_asia=east_asia)
    set_run_size(r_pr, size)
    if bold:
        ensure_bold(r_pr)


def normalize_styles(styles_path: Path) -> None:
    root = ET.parse(styles_path).getroot()
    doc_defaults = child(root, "docDefaults", 0)
    r_pr_default = child(child(doc_defaults, "rPrDefault"), "rPr")
    set_run_fonts(r_pr_default, east_asia="SimSun")
    set_run_size(r_pr_default, 22)
    p_pr_default = child(child(doc_defaults, "pPrDefault"), "pPr")
    set_indent(p_pr_default, 440)
    set_spacing(p_pr_default, 0, 120, 348)
    set_justification(p_pr_default, "both")

    apply_style(root, "Normal", east_asia="SimSun", size=22, first_line=440, before=0, after=120, line=348, jc="both")
    apply_style(root, "BodyText", east_asia="SimSun", size=22, first_line=440, before=0, after=120, line=348, jc="both")
    apply_style(root, "Heading1", east_asia="SimHei", size=30, first_line=None, before=360, after=160, line=300, bold=True)
    apply_style(root, "Heading2", east_asia="SimHei", size=25, first_line=None, before=240, after=120, line=312, bold=True)
    apply_style(root, "BodyTextcaption", east_asia="SimSun", size=19, first_line=None, before=120, after=160, line=300)
    apply_style(root, "Caption", east_asia="SimSun", size=19, first_line=None, before=120, after=160, line=300)
    apply_style(root, "BodyTextref", east_asia="SimSun", size=19, first_line=None, before=0, after=80, line=300)
    apply_style(root, "TableContents", east_asia="SimSun", size=18, first_line=None, before=0, after=0, line=276)
    apply_style(root, "TableHeading", east_asia="SimHei", size=18, first_line=None, before=0, after=0, line=276, bold=True, jc="center")
    ET.ElementTree(root).write(styles_path, encoding="utf-8", xml_declaration=True)


def normalize_document(document_path: Path) -> None:
    root = ET.parse(document_path).getroot()
    body = root.find(w_tag("body"))
    if body is None:
        return

    text_seen = 0
    previous_text = ""
    for p in body.iter(w_tag("p")):
        text = paragraph_text(p)
        style = paragraph_style(p)
        has_drawing = p.find(f".//{w_tag('drawing')}") is not None

        if has_drawing and not text:
            set_paragraph_format(p, first_line=None, before=240, after=120, line=None, jc="center")
            continue
        if not text:
            set_paragraph_format(p, first_line=None, before=0, after=0, line=None, jc=None)
            continue

        if text_seen == 0:
            set_paragraph_format(p, first_line=None, before=0, after=240, line=300, jc="center")
            set_paragraph_runs(p, east_asia="SimHei", size=36, bold=True)
        elif text_seen == 1:
            set_paragraph_format(p, first_line=None, before=0, after=360, line=276, jc="center")
            set_paragraph_runs(p, east_asia="SimSun", size=22)
        elif style == "Heading1":
            set_paragraph_format(p, first_line=None, before=360, after=160, line=300, jc=None)
            set_paragraph_runs(p, east_asia="SimHei", size=30, bold=True)
        elif style == "Heading2":
            set_paragraph_format(p, first_line=None, before=240, after=120, line=312, jc=None)
            set_paragraph_runs(p, east_asia="SimHei", size=25, bold=True)
        elif style in {"BodyTextcaption", "Caption"}:
            set_paragraph_format(p, first_line=None, before=120, after=160, line=300, jc="center")
            set_paragraph_runs(p, east_asia="SimSun", size=19)
        elif style == "BodyTextref":
            set_paragraph_format(p, first_line=None, before=0, after=80, line=300, jc=None)
            set_paragraph_runs(p, east_asia="SimSun", size=19)
        elif style in {"TableContents", "TableHeading"}:
            set_paragraph_format(p, first_line=None, before=0, after=0, line=276, jc="center")
            set_paragraph_runs(p, east_asia="SimSun" if style == "TableContents" else "SimHei", size=18, bold=style == "TableHeading")
        elif previous_text == "摘要":
            set_paragraph_format(p, first_line=None, before=0, after=200, line=348, jc="both")
            set_paragraph_runs(p, east_asia="SimSun", size=22)
        else:
            set_paragraph_format(p, first_line=440, before=0, after=120, line=348, jc="both")
            set_paragraph_runs(p, east_asia="SimSun", size=22)

        previous_text = text
        text_seen += 1

    for sect_pr in body.iter(w_tag("sectPr")):
        pg_sz = child(sect_pr, "pgSz")
        pg_sz.set(w_attr("w"), "11906")
        pg_sz.set(w_attr("h"), "16838")
        pg_mar = child(sect_pr, "pgMar")
        pg_mar.attrib.update(
            {
                w_attr("top"): "1440",
                w_attr("right"): "1440",
                w_attr("bottom"): "1440",
                w_attr("left"): "1440",
                w_attr("header"): "720",
                w_attr("footer"): "720",
                w_attr("gutter"): "0",
            }
        )
    ET.ElementTree(root).write(document_path, encoding="utf-8", xml_declaration=True)


def normalize_docx(path: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with ZipFile(path) as zin:
            zin.extractall(tmp)
        normalize_styles(tmp / "word" / "styles.xml")
        normalize_document(tmp / "word" / "document.xml")
        with ZipFile(path, "w", ZIP_DEFLATED) as zout:
            for item in tmp.rglob("*"):
                if item.is_file():
                    zout.write(item, item.relative_to(tmp).as_posix())


def resolve_image(href: str) -> Path:
    candidates = [
        PAPER / href,
        PAPER / href.replace("../", ""),
        ROOT / href,
    ]
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.exists():
            return candidate
    raise FileNotFoundError(href)


def embed_odt_images(src: Path, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with ZipFile(src) as zin:
            zin.extractall(tmp)

        content_path = tmp / "content.xml"
        manifest_path = tmp / "META-INF" / "manifest.xml"
        content = content_path.read_text(encoding="utf-8")
        manifest = manifest_path.read_text(encoding="utf-8")
        (tmp / "Pictures").mkdir(exist_ok=True)
        used: list[str] = []

        def repl(match: re.Match[str]) -> str:
            href = unquote(match.group(1))
            if "figures/" not in href:
                return match.group(0)
            image_path = resolve_image(href)
            name = f"Pictures/{image_path.name}"
            shutil.copyfile(image_path, tmp / name)
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

        if dest.exists():
            dest.unlink()
        with ZipFile(dest, "w") as zout:
            mimetype = tmp / "mimetype"
            if mimetype.exists():
                zout.write(mimetype, "mimetype", compress_type=ZIP_STORED)
            for path in tmp.rglob("*"):
                if path.is_file() and path.name != "mimetype":
                    zout.write(path, path.relative_to(tmp).as_posix(), compress_type=ZIP_DEFLATED)


def main() -> None:
    libreoffice = shutil.which("libreoffice")
    if not libreoffice:
        raise SystemExit("libreoffice is required")

    for path in (RAW_ODT, ODT, DOCX, PDF):
        path.unlink(missing_ok=True)

    run([sys.executable, str(EXPORT_SCRIPT)])
    run([libreoffice, "--headless", "--convert-to", "odt", "--outdir", str(PAPER), str(HTML)])
    embed_odt_images(RAW_ODT, ODT)
    run([libreoffice, "--headless", "--convert-to", "docx", "--outdir", str(PAPER), str(ODT)])
    normalize_docx(DOCX)
    run([libreoffice, "--headless", "--convert-to", "pdf", "--outdir", str(PAPER), str(DOCX)])
    print(DOCX)
    print(PDF)


if __name__ == "__main__":
    main()
