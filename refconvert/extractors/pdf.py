"""PDF: three independent engines (pypdf, pdfplumber, poppler pdftotext)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, clean
from . import unavailable

# Symbol fonts: the character CODE is a glyph index, not a letter. Only codes verified against a rendered page are mapped.
SYMBOL_FONTS = ("wingdings", "webdings", "zapfdingbats")
SYMBOL_MAP = {("wingdings", "y"): "\u2022"}      # AASHTO Green Book list bullet; checked against the page image


def _mark_chars(page, unit, er):
    """Rewrite pdfplumber chars in place BEFORE text/table extraction:
    * symbol-font glyphs become the real symbol (bullet) or a labelled marker + warning;
    * superscripts (smaller AND raised vs the previous char) get a '^' so '20a' is written '20^a'.
    Digit-after-digit superscripts (10^3 vs 103) are blocked for a human, because other engines read them as one number."""
    n_sym, n_sup, unknown = 0, 0, set()
    prev, prev_sup = None, False
    for c in page.chars:
        t = c.get("text", "")
        low = (c.get("fontname") or "").lower()
        fam = next((f for f in SYMBOL_FONTS if f in low), None)
        if fam:
            rep = SYMBOL_MAP.get((fam, t))
            if rep:
                n_sym += 1
                c["text"] = rep
            else:
                unknown.add((fam, t))
                c["text"] = f"[symbol:{fam}:U+{ord(t[0]):04X}]" if t else t
            prev, prev_sup = c, False
            continue
        is_sup = False
        if prev is not None and t.strip():
            adjacent = abs(c["x0"] - prev["x1"]) < 2.0
            raised = c["bottom"] < prev["bottom"] - 0.08 * prev["size"]
            if adjacent and not prev_sup and prev["size"] > c["size"] * 1.2 and raised:
                is_sup = True
                c["text"] = "^" + t
                n_sup += 1
                if t.isdigit() and prev.get("text", "")[-1:].isdigit():
                    er.notes.append({"code": "SUPERSCRIPT_DIGIT", "severity": "BLOCK", "unit": unit,
                                     "detail": f"superscript digit '{t}' directly after digit '{prev['text'][-1]}' (read as exponent here, but other "
                                               "engines see one number, e.g. 10^3 versus 103). Check against the page."})
            elif adjacent and prev_sup and abs(prev["size"] - c["size"]) < 0.5 and abs(c["bottom"] - prev["bottom"]) < 1.0:
                is_sup = True                      # continuation of a multi-character superscript
        prev, prev_sup = c, is_sup
    if n_sym:
        er.notes.append({"code": "SYMBOL_FONT_MAPPED", "severity": "INFO", "unit": unit,
                         "detail": f"{n_sym} symbol-font glyph(s) (Wingdings 'y') converted to the bullet character."})
    for fam, t in sorted(unknown):
        er.notes.append({"code": "SYMBOL_UNKNOWN", "severity": "WARN", "unit": unit,
                         "detail": f"symbol-font glyph {fam} code {t!r} is not in the verified map; kept as a labelled marker. Look at the page."})
    if n_sup:
        er.notes.append({"code": "SUPERSCRIPT_MARKED", "severity": "INFO", "unit": unit,
                         "detail": f"{n_sup} superscript character(s) written with a leading '^'."})


def _pypdf(path: Path, first: int, last: int | None) -> EngineResult:
    from pypdf import PdfReader
    r = PdfReader(str(path))
    if r.is_encrypted:
        try:
            ok = r.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise ExtractError("PDF is password-protected: ask the user for the password or an unlocked copy")
    er = EngineResult("pypdf")
    import pypdf
    er.version = pypdf.__version__
    for i, pg in enumerate(r.pages, 1):
        if i < first or (last is not None and i > last):
            continue
        u = f"p{i}"
        try:
            t = pg.extract_text() or ""
        except Exception as e:  # noqa: BLE001 - report, never hide
            t = ""
            er.notes.append({"code": "PAGE_READ_ERROR", "severity": "WARN", "unit": u, "detail": f"pypdf failed on page: {e}"})
        er.blocks.append(Block(u, "text", clean(t)))
        try:
            er.unit_meta[u] = {"images": len(pg.images)}
        except Exception:  # noqa: BLE001
            er.unit_meta[u] = {}
    return er


def _plumber(path: Path, first: int, last: int | None) -> EngineResult:
    import pdfplumber
    er = EngineResult("pdfplumber", version=getattr(pdfplumber, "__version__", ""))
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            if i < first or (last is not None and i > last):
                continue
            u = f"p{i}"
            try:
                _mark_chars(page, u, er)
                t = page.extract_text() or ""
                tables = page.extract_tables() or []
            except Exception as e:  # noqa: BLE001
                t, tables = "", []
                er.notes.append({"code": "PAGE_READ_ERROR", "severity": "WARN", "unit": u, "detail": f"pdfplumber failed on page: {e}"})
            er.blocks.append(Block(u, "text", clean(t)))
            for k, tb in enumerate(tables, 1):
                rows = [[clean(c or "").replace("\n", " ") for c in row] for row in tb]
                if any(any(c for c in row) for row in rows):
                    er.blocks.append(Block(u, "table", rows=rows, tid=f"t{k}"))
            er.unit_meta[u] = {"images": len(page.images),
                               "graphics": len(page.rects) + len(page.lines) + len(page.curves)}
    return er


def _pdftotext(path: Path, first: int, last: int | None) -> EngineResult:
    exe = shutil.which("pdftotext")
    if not exe:
        raise FileNotFoundError("pdftotext (poppler-utils) not installed")
    cmd = [exe, "-layout", "-enc", "UTF-8", "-f", str(first)] + (["-l", str(last)] if last else []) + [str(path), "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
    pages = proc.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    er = EngineResult("pdftotext")
    for i, t in enumerate(pages, 1):
        er.blocks.append(Block(f"p{i + first - 1}", "text", clean(t)))     # keep ORIGINAL physical page numbers
    return er


def extract(path: Path, kind: str, pages=None):
    first, last = pages if pages else (1, None)
    engines, issues = [], []
    for name, fn in (("pdfplumber", _plumber), ("pypdf", _pypdf), ("pdftotext", _pdftotext)):
        try:
            engines.append(fn(path, first, last))
        except ExtractError:
            raise
        except ImportError as e:
            issues.append(unavailable(name, f"library missing ({e})"))
        except Exception as e:  # noqa: BLE001
            issues.append(unavailable(name, str(e)[:200]))
    if not engines:
        raise ExtractError("no PDF engine could read this file (corrupt or not a real PDF)")
    return engines, issues
