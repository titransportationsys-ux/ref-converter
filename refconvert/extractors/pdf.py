"""PDF: three independent engines (pypdf, pdfplumber, poppler pdftotext)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, clean
from . import unavailable


def _pypdf(path: Path) -> EngineResult:
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


def _plumber(path: Path) -> EngineResult:
    import pdfplumber
    er = EngineResult("pdfplumber", version=getattr(pdfplumber, "__version__", ""))
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            u = f"p{i}"
            try:
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


def _pdftotext(path: Path) -> EngineResult:
    exe = shutil.which("pdftotext")
    if not exe:
        raise FileNotFoundError("pdftotext (poppler-utils) not installed")
    proc = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
    pages = proc.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    er = EngineResult("pdftotext")
    for i, t in enumerate(pages, 1):
        er.blocks.append(Block(f"p{i}", "text", clean(t)))
    return er


def extract(path: Path, kind: str):
    engines, issues = [], []
    for name, fn in (("pdfplumber", _plumber), ("pypdf", _pypdf), ("pdftotext", _pdftotext)):
        try:
            engines.append(fn(path))
        except ExtractError:
            raise
        except ImportError as e:
            issues.append(unavailable(name, f"library missing ({e})"))
        except Exception as e:  # noqa: BLE001
            issues.append(unavailable(name, str(e)[:200]))
    if not engines:
        raise ExtractError("no PDF engine could read this file (corrupt or not a real PDF)")
    return engines, issues
