"""DOCX, PPTX, XLSX: each has a library engine and an independent raw-XML engine."""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, clean
from . import parse_xml, safe_zip_read, unavailable

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def q(ns, tag):
    return f"{{{ns}}}{tag}"


# ------------------------------------------------------------------ DOCX
def _docx_lib(path: Path) -> EngineResult:
    import docx
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(path))
    er = EngineResult("python-docx")
    ti = 0
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            er.blocks.append(Block("doc", "text", clean(p.text), meta={"style": p.style.name if p.style is not None else ""}))
        elif tag == "tbl":
            ti += 1
            tb = Table(child, d)
            rows = [[x for tc in tr.tc_lst for x in [clean(_Cell(tc, tb).text)] + [""] * (tc.grid_span - 1)] for tr in tb._tbl.tr_lst]
            er.blocks.append(Block("doc", "table", rows=rows, tid=f"t{ti}"))
    return er


def _docx_raw(path: Path) -> EngineResult:
    er = EngineResult("docx-xml")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        root = parse_xml(safe_zip_read(zf, "word/document.xml"))
        styles = {}
        if "word/styles.xml" in names:
            for st in parse_xml(safe_zip_read(zf, "word/styles.xml")).iter(q(W, "style")):
                nm = st.find(q(W, "name"))
                styles[st.get(q(W, "styleId"))] = nm.get(q(W, "val")) if nm is not None else ""
        extra = []
        for n in sorted(names):
            if re.fullmatch(r"word/(footnotes|endnotes|comments|header\d*|footer\d*)\.xml", n):
                x = parse_xml(safe_zip_read(zf, n))
                txt = "".join(t.text or "" for t in x.iter(q(W, "t"))).strip()
                if txt:
                    extra.append(n.split("/")[-1])
        if extra:
            er.notes.append({"code": "PART_NOT_EXTRACTED", "severity": "WARN", "unit": "doc",
                             "detail": "Text present in " + ", ".join(extra) + " (footnotes/headers/comments) was NOT extracted. "
                                       "Check them in the original if they carry rules or numbers."})
    if root.find(f".//{q(W, 'ins')}") is not None or root.find(f".//{q(W, 'del')}") is not None:
        er.notes.append({"code": "TRACKED_CHANGES", "severity": "WARN", "unit": "doc",
                         "detail": "Document contains tracked changes; extracted text depends on accept/reject state. Ask which version is authoritative."})
    n_img = len(list(root.iter(q(W, "drawing")))) + len(list(root.iter(q(W, "pict"))))
    if n_img:
        er.unit_meta["doc"] = {"images": n_img}

    def run_text(el):
        out = []
        for node in el.iter():
            t = node.tag
            if t == q(W, "t"):
                out.append(node.text or "")
            elif t == q(W, "tab"):
                out.append("\t")
            elif t in (q(W, "br"), q(W, "cr")):
                out.append("\n")
            elif t == q(W, "noBreakHyphen"):
                out.append("-")
        return "".join(out)

    def own_paragraph_text(p):
        # same scope as python-docx: runs directly under the paragraph (+ hyperlinks)
        out = []
        for ch in p:
            if ch.tag == q(W, "r"):
                out.append(run_text(ch))
            elif ch.tag in (q(W, "hyperlink"), q(W, "ins"), q(W, "smartTag")):
                out.append(run_text(ch))
        return "".join(out)

    ti = [0]

    def walk(parent):
        for ch in parent:
            if ch.tag == q(W, "p"):
                ps = ch.find(f"{q(W, 'pPr')}/{q(W, 'pStyle')}")
                sid = ps.get(q(W, "val")) if ps is not None else None
                name = styles.get(sid, "Normal" if sid is None else sid)
                er.blocks.append(Block("doc", "text", clean(own_paragraph_text(ch)), meta={"style": name}))
            elif ch.tag == q(W, "tbl"):
                ti[0] += 1
                rows = []
                for tr in ch.findall(q(W, "tr")):
                    row = []
                    for tc in tr.findall(q(W, "tc")):
                        gs = tc.find(f"{q(W, 'tcPr')}/{q(W, 'gridSpan')}")
                        span = int(gs.get(q(W, "val"))) if gs is not None else 1
                        row += [clean("\n".join(own_paragraph_text(p) for p in tc.findall(q(W, "p"))))] + [""] * (span - 1)
                    rows.append(row)
                er.blocks.append(Block("doc", "table", rows=rows, tid=f"t{ti[0]}"))
            elif ch.tag == q(W, "sdt"):
                c = ch.find(q(W, "sdtContent"))
                if c is not None:
                    walk(c)

    walk(root.find(q(W, "body")))
    return er


# ------------------------------------------------------------------ PPTX
def _pptx_lib(path: Path) -> EngineResult:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(str(path))
    er = EngineResult("python-pptx")

    def walk(shapes, u, state):
        for sh in shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(sh.shapes, u, state)
            elif getattr(sh, "has_table", False) and sh.has_table:
                state["t"] += 1
                rows = [[clean(c.text) for c in r.cells] for r in sh.table.rows]
                er.blocks.append(Block(u, "table", rows=rows, tid=f"t{state['t']}"))
            elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                for p in sh.text_frame.paragraphs:
                    er.blocks.append(Block(u, "text", clean(p.text)))
            elif sh.shape_type == MSO_SHAPE_TYPE.PICTURE or getattr(sh, "has_chart", False):
                er.unit_meta.setdefault(u, {"images": 0})["images"] += 1

    for i, slide in enumerate(prs.slides, 1):
        u = f"s{i}"
        er.unit_meta.setdefault(u, {"images": 0})
        walk(slide.shapes, u, {"t": 0})
        if not any(b.unit == u and b.kind == "text" for b in er.blocks):
            er.blocks.append(Block(u, "text", ""))
    return er


def _pptx_raw(path: Path) -> EngineResult:
    er = EngineResult("pptx-xml")
    with zipfile.ZipFile(path) as zf:
        pres = parse_xml(safe_zip_read(zf, "ppt/presentation.xml"))
        rels = parse_xml(safe_zip_read(zf, "ppt/_rels/presentation.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels.iter(q(PR, "Relationship"))}
        order = []
        for sid in pres.iter(q(P, "sldId")):
            t = target[sid.get(q(R, "id"))]
            order.append(posixpath.normpath(posixpath.join("ppt", t)) if not t.startswith("/") else t.lstrip("/"))
        has_notes = any(n.startswith("ppt/notesSlides/") for n in zf.namelist())
        if has_notes:
            er.notes.append({"code": "PART_NOT_EXTRACTED", "severity": "INFO", "unit": "",
                             "detail": "Speaker notes exist and were NOT extracted."})
        for i, part in enumerate(order, 1):
            u = f"s{i}"
            root = parse_xml(safe_zip_read(zf, part))
            er.unit_meta[u] = {"images": 0}
            state = {"t": 0}

            def para(p):
                out = []
                for n in p.iter():
                    if n.tag == q(A, "t"):
                        out.append(n.text or "")
                    elif n.tag == q(A, "br"):
                        out.append("\n")
                return "".join(out)

            def walk(parent):
                for ch in parent:
                    tag = ch.tag
                    if tag == q(P, "grpSp"):
                        walk(ch)
                    elif tag == q(P, "sp"):
                        tb = ch.find(q(P, "txBody"))
                        if tb is not None:
                            for p in tb.findall(q(A, "p")):
                                er.blocks.append(Block(u, "text", clean(para(p))))
                    elif tag == q(P, "pic"):
                        er.unit_meta[u]["images"] += 1
                    elif tag == q(P, "graphicFrame"):
                        tbl = ch.find(f".//{q(A, 'tbl')}")
                        if tbl is not None:
                            state["t"] += 1
                            rows = [[clean("\n".join(para(p) for p in tc.iter(q(A, "p")) if p.getparent() is tc.find(q(A, "txBody"))))
                                     for tc in tr.findall(q(A, "tc"))] for tr in tbl.findall(q(A, "tr"))]
                            er.blocks.append(Block(u, "table", rows=rows, tid=f"t{state['t']}"))
                        else:
                            er.unit_meta[u]["images"] += 1

            walk(root.find(q(P, "cSld")).find(q(P, "spTree")))
            if not any(b.unit == u and b.kind == "text" for b in er.blocks):
                er.blocks.append(Block(u, "text", ""))
    return er


# ------------------------------------------------------------------ XLSX
def _trim(rows):
    rows = [list(r) for r in rows]
    while rows and not any(c != "" for c in rows[-1]):
        rows.pop()
    width = 0
    for r in rows:
        k = len(r)
        while k and r[k - 1] == "":
            k -= 1
        width = max(width, k)
    return [r[:width] + [""] * (width - len(r[:width])) for r in rows]


def _cellstr(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return repr(v)
    return clean(str(v))


def _xlsx_lib(path: Path) -> EngineResult:
    import datetime
    import openpyxl
    from openpyxl.utils.datetime import to_excel
    er = EngineResult("openpyxl", canon_numbers=True)
    wb = openpyxl.load_workbook(str(path), data_only=True)
    wf = openpyxl.load_workbook(str(path), data_only=False)
    for ws in wb.worksheets:
        u = f"sheet:{ws.title}"
        rows, dates = [], []
        for r_i, row in enumerate(ws.iter_rows(), 1):
            out = []
            for c in row:
                v = c.value
                if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
                    dates.append(c.coordinate)
                    v = to_excel(v)
                out.append(_cellstr(v))
            rows.append(out)
        er.blocks.append(Block(u, "table", rows=_trim(rows), tid="t1"))
        if ws.sheet_state != "visible":
            er.notes.append({"code": "HIDDEN_SHEET", "severity": "WARN", "unit": u,
                             "detail": f"Sheet is {ws.sheet_state}. Confirm it should be part of the reference."})
        if dates:
            er.notes.append({"code": "DATE_CELLS", "severity": "WARN", "unit": u,
                             "detail": "Date/time cells are exported as Excel serial numbers: " + ", ".join(dates[:10]) +
                                       (" ..." if len(dates) > 10 else "") + ". Convert explicitly if dates matter."})
        nocache = []
        for row in wf[ws.title].iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("=") and ws[c.coordinate].value is None:
                    nocache.append(c.coordinate)
        if nocache:
            er.notes.append({"code": "FORMULA_NO_CACHE", "severity": "BLOCK", "unit": u,
                             "detail": "Formula cells have no stored value (file never recalculated): " + ", ".join(nocache[:10]) +
                                       ". Open and save the file in Excel, then re-run."})
    return er


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xlsx_raw(path: Path) -> EngineResult:
    er = EngineResult("xlsx-xml", canon_numbers=True)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in parse_xml(safe_zip_read(zf, "xl/sharedStrings.xml")).iter(q(S, "si")):
                shared.append("".join(t.text or "" for t in si.iter(q(S, "t"))))
        wb = parse_xml(safe_zip_read(zf, "xl/workbook.xml"))
        rels = parse_xml(safe_zip_read(zf, "xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels.iter(q(PR, "Relationship"))}
        for sh in wb.iter(q(S, "sheet")):
            part = target[sh.get(q(R, "id"))]
            part = part.lstrip("/") if part.startswith("/") else posixpath.normpath(posixpath.join("xl", part))
            root = parse_xml(safe_zip_read(zf, part))
            grid = {}
            for c in root.iter(q(S, "c")):
                ref, t = c.get("r"), c.get("t")
                v = c.find(q(S, "v"))
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(q(S, "t")))
                elif v is None or v.text is None:
                    continue
                elif t == "s":
                    val = shared[int(v.text)]
                elif t == "b":
                    val = "TRUE" if v.text.strip() == "1" else "FALSE"
                else:
                    val = v.text
                m = re.match(r"([A-Z]+)(\d+)", ref)
                grid[(int(m.group(2)) - 1, _col_index(ref))] = clean(val)
            nr = max((r for r, _ in grid), default=-1) + 1
            nc = max((c for _, c in grid), default=-1) + 1
            rows = [[grid.get((r, c), "") for c in range(nc)] for r in range(nr)]
            er.blocks.append(Block(f"sheet:{sh.get('name')}", "table", rows=_trim(rows), tid="t1"))
    return er


def extract(path: Path, kind: str):
    fns = {"docx": (("python-docx", _docx_lib), ("docx-xml", _docx_raw)),
           "pptx": (("python-pptx", _pptx_lib), ("pptx-xml", _pptx_raw)),
           "xlsx": (("openpyxl", _xlsx_lib), ("xlsx-xml", _xlsx_raw))}[kind]
    engines, issues = [], []
    for name, fn in fns:
        try:
            engines.append(fn(path))
        except ImportError as e:
            issues.append(unavailable(name, f"library missing ({e})"))
        except (zipfile.BadZipFile, KeyError) as e:
            raise ExtractError(f"{path.name} is not a valid {kind} file: {e}") from e
        except ExtractError:
            raise
        except Exception as e:  # noqa: BLE001
            issues.append(unavailable(name, f"{type(e).__name__}: {str(e)[:200]}"))
    if not engines:
        raise ExtractError(f"no engine could read {path.name}")
    return engines, issues
