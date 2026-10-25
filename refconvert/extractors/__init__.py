"""Format detection and the extractor registry.

Every extractor returns ``(engines, issues)``: at least two independent
``EngineResult`` objects where the format allows it. Missing optional libraries
never crash a run; the engine is skipped and an ``ENGINE_UNAVAILABLE`` issue is
recorded, which later leaves the affected content with a single source and
therefore blocked until a second engine or a human confirms it.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from ..model import ExtractError, Unsupported

MAX_UNZIPPED = 500 * 1024 * 1024

EXT_KIND = {
    ".pdf": "pdf", ".docx": "docx", ".docm": "docx", ".pptx": "pptx", ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".csv": "csv", ".tsv": "csv", ".html": "html", ".htm": "html",
    ".txt": "text", ".md": "text", ".markdown": "text", ".rst": "text", ".tex": "text", ".log": "text",
    ".yaml": "text", ".yml": "text", ".xml": "text", ".svg": "text", ".ini": "text", ".toml": "text",
    ".bib": "bib", ".ris": "ris", ".json": "json", ".geojson": "json",
    ".dxf": "dxf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image", ".tiff": "image",
    ".bmp": "image", ".gif": "image", ".webp": "image",
}
LEGACY = {".doc": ".docx", ".rtf": ".docx", ".odt": ".docx", ".xls": ".xlsx", ".ods": ".xlsx",
          ".ppt": ".pptx", ".odp": ".pptx"}
HINTS = {
    ".dwg": "DWG is a closed binary format. In Civil 3D/AutoCAD run SAVEAS and choose an ASCII DXF, then re-run.",
    ".shp": "Shapefiles need a GIS tool. Export to GeoJSON or CSV (QGIS: Export > Save Features As) and re-run.",
    ".gdb": "File geodatabases need a GIS tool. Export the layers you need to GeoJSON or CSV and re-run.",
    ".dgn": "DGN is a closed binary format. Export to DXF from MicroStation and re-run.",
    ".rvt": "Revit files need Revit. Export schedules to CSV/XLSX and re-run.",
    ".epub": "EPUB: convert to HTML or PDF (for example with Calibre) and re-run.",
    ".mp3": "Audio is not supported. Provide a transcript as text.", ".mp4": "Video is not supported. Provide a transcript as text.",
    ".zip": "Use the 'batch' command for zip archives.",
}


def detect(path: Path) -> str:
    """Return the extractor kind for ``path`` or raise Unsupported with guidance."""
    ext = path.suffix.lower()
    if ext in EXT_KIND:
        kind = EXT_KIND[ext]
        head = path.read_bytes()[:8] if path.is_file() else b""
        if kind == "pdf" and not head.startswith(b"%PDF"):
            raise ExtractError(f"{path.name} has a .pdf extension but is not a PDF (bad header)")
        if kind in ("docx", "pptx", "xlsx") and not head.startswith(b"PK"):
            raise ExtractError(f"{path.name} is not an Office Open XML file (bad header); legacy binary files must be converted first")
        return kind
    if ext in LEGACY:
        return "legacy"
    if ext in HINTS:
        raise Unsupported(HINTS[ext])
    head = path.read_bytes()[:8192]
    if b"\x00" in head:
        raise Unsupported(f"Unknown binary format '{ext or 'no extension'}'. Export it to PDF, XLSX, CSV, DXF or text and re-run, "
                          "or transcribe it and add it with the 'ingest' command.")
    return "text"


def convert_legacy(path: Path, outdir: Path) -> Path:
    """Convert .doc/.xls/.ppt/.rtf/.odt... with LibreOffice if present."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise Unsupported(f"{path.suffix} is a legacy format and LibreOffice is not installed. Save it as "
                          f"{LEGACY[path.suffix.lower()]} (or PDF) from the original application and re-run.")
    target = LEGACY[path.suffix.lower()].lstrip(".")
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        proc = subprocess.run(
            [soffice, "--headless", f"-env:UserInstallation=file://{home}", "--convert-to", target,
             "--outdir", str(outdir), str(path)],
            capture_output=True, text=True, timeout=300)
    out = outdir / (path.stem + "." + target)
    if proc.returncode != 0 or not out.exists():
        raise ExtractError(f"LibreOffice could not convert {path.name}: {proc.stderr.strip()[:300]}")
    return out


def safe_zip_read(zf: zipfile.ZipFile, name: str) -> bytes:
    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_UNZIPPED:
        raise ExtractError(f"archive expands to {total} bytes, over the {MAX_UNZIPPED} safety limit")
    return zf.read(name)


def xml_parser():
    """lxml parser that never resolves entities or touches the network (untrusted files)."""
    from lxml import etree
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def parse_xml(data: bytes):
    from lxml import etree
    return etree.fromstring(data, xml_parser())


def read_text_file(path: Path) -> tuple[str, list]:
    """Decode a text file; falls back to cp1252 and says so."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), [{
            "code": "ENCODING_FALLBACK", "severity": "WARN", "unit": "",
            "detail": f"{path.name} is not valid UTF-8; decoded as cp1252. Check accented/Arabic characters."}]


def unavailable(name: str, why: str) -> dict:
    return {"code": "ENGINE_UNAVAILABLE", "severity": "INFO", "unit": "", "detail": f"engine '{name}' skipped: {why}"}


def extract(path: Path, workdir: Path, pages=None):
    """Dispatch to the right extractor. Returns (kind, engines, issues, derived_from)."""
    path = path.resolve()
    kind = detect(path)
    derived = None
    if kind == "legacy":
        derived = path
        path = convert_legacy(path, workdir / "_converted")
        kind = detect(path)
    from . import pdf, office, web, tabular, text, citations, cad, data, image
    mod = {"pdf": pdf, "docx": office, "pptx": office, "xlsx": office, "html": web, "csv": tabular,
           "text": text, "bib": citations, "ris": citations, "dxf": cad, "json": data, "image": image}[kind]
    if pages and kind != "pdf":
        raise ExtractError("--pages only applies to PDF files")
    engines, issues = mod.extract(path, kind, pages) if kind == "pdf" else mod.extract(path, kind)
    return kind, engines, issues, derived, path
