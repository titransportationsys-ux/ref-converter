"""DXF (ASCII): raw group-code parser vs ezdxf. Model-space entities only."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, Unsupported, clean
from . import read_text_file, unavailable

HEADER = ["type", "layer", "text", "coords"]
HANDLED = {"LINE", "POINT", "CIRCLE", "ARC", "TEXT", "MTEXT", "INSERT", "LWPOLYLINE", "POLYLINE", "VERTEX", "SEQEND"}


def _fmt(x) -> str:
    s = f"{round(float(x), 6):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _mtext_plain(s: str) -> str:
    s = s.replace("\\P", "\n").replace("\\~", " ")
    s = re.sub(r"\\[fFcCHhTtQqWwAaLlOoKk][^;\\]*;?", "", s)
    s = re.sub(r"\\[Ss]([^;]*);", lambda m: m.group(1).replace("^", "/").replace("#", "/"), s)
    return clean(s.replace("{", "").replace("}", "").replace("\\\\", "\\"))


def _raw(path: Path):
    text, issues = read_text_file(path)
    if text.startswith("AutoCAD Binary DXF"):
        raise Unsupported("Binary DXF is not supported by the raw parser. Save as ASCII DXF (or install ezdxf and rely on it alone, which leaves the content single-sourced).")
    lines = text.splitlines()
    pairs = []
    for i in range(0, len(lines) - 1, 2):
        try:
            pairs.append((int(lines[i].strip()), lines[i + 1].strip()))
        except ValueError as e:
            raise ExtractError(f"{path.name} is not a valid ASCII DXF (bad group code near line {i + 1})") from e
    ents, cur, in_ents, i = [], None, False, 0
    while i < len(pairs):
        code, val = pairs[i]
        if code == 0 and val == "SECTION" and i + 1 < len(pairs) and pairs[i + 1] == (2, "ENTITIES"):
            in_ents, i = True, i + 2
            continue
        if code == 0 and val == "ENDSEC":
            if cur:
                ents.append(cur)
                cur = None
            in_ents = False
        elif in_ents:
            if code == 0:
                if cur:
                    ents.append(cur)
                cur = {"type": val, "g": []}
            elif cur is not None:
                cur["g"].append((code, val))
        i += 1
    er = EngineResult("dxf-raw")
    rows = [list(HEADER)]
    for e in ents:
        g, typ = e["g"], e["type"]
        layer = next((v for c, v in g if c == 8), "0")
        first = lambda c, d="0": float(next((v for cc, v in g if cc == c), d))  # noqa: E731
        txt, nums = "", []
        if typ == "LINE":
            nums = [first(10), first(20), first(30), first(11), first(21), first(31)]
        elif typ in ("POINT", "VERTEX"):
            nums = [first(10), first(20), first(30)]
        elif typ == "CIRCLE":
            nums = [first(10), first(20), first(30), first(40)]
        elif typ == "ARC":
            nums = [first(10), first(20), first(30), first(40), first(50), first(51)]
        elif typ == "TEXT":
            txt = clean(next((v for c, v in g if c == 1), ""))
            nums = [first(10), first(20), first(30)]
        elif typ == "MTEXT":
            body = "".join(v for c, v in g if c == 3) + next((v for c, v in g if c == 1), "")
            txt = _mtext_plain(body)
            nums = [first(10), first(20), first(30)]
        elif typ == "INSERT":
            txt = next((v for c, v in g if c == 2), "")
            nums = [first(10), first(20), first(30)]
        elif typ == "LWPOLYLINE":
            xs = [float(v) for c, v in g if c == 10]
            ys = [float(v) for c, v in g if c == 20]
            nums = [x for xy in zip(xs, ys) for x in xy]
            txt = "closed" if int(first(70, "0")) & 1 else "open"
        rows.append([typ, layer, txt, " ".join(_fmt(n) for n in nums)])
    er.blocks.append(Block("modelspace", "table", rows=rows, tid="t1"))
    other = Counter(e["type"] for e in ents if e["type"] not in HANDLED)
    if other:
        issues.append({"code": "UNHANDLED_ENTITY", "severity": "WARN", "unit": "modelspace",
                       "detail": "Entities listed by type/layer only, geometry NOT extracted: " + ", ".join(f"{k} x{v}" for k, v in sorted(other.items()))})
    if "BLOCKS" in text and any(e["type"] == "INSERT" for e in ents):
        issues.append({"code": "BLOCK_REFERENCES", "severity": "INFO", "unit": "modelspace",
                       "detail": "INSERT (block) references are listed but not exploded; text inside block definitions is not extracted."})
    return er, issues


def _ezdxf(path: Path) -> EngineResult:
    import ezdxf
    doc = ezdxf.readfile(str(path))
    er = EngineResult("ezdxf", version=ezdxf.__version__)
    rows = [list(HEADER)]

    def loc(p):
        return [p[0], p[1], p[2] if len(p) > 2 else 0.0]

    for e in doc.modelspace():
        typ, layer = e.dxftype(), e.dxf.layer
        txt, nums = "", []
        if typ == "LINE":
            nums = loc(e.dxf.start) + loc(e.dxf.end)
        elif typ == "POINT":
            nums = loc(e.dxf.location)
        elif typ == "CIRCLE":
            nums = loc(e.dxf.center) + [e.dxf.radius]
        elif typ == "ARC":
            nums = loc(e.dxf.center) + [e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle]
        elif typ == "TEXT":
            txt, nums = clean(e.dxf.text), loc(e.dxf.insert)
        elif typ == "MTEXT":
            txt, nums = clean(e.plain_text()), loc(e.dxf.insert)
        elif typ == "INSERT":
            txt, nums = e.dxf.name, loc(e.dxf.insert)
        elif typ == "LWPOLYLINE":
            nums = [c for p in e.get_points("xy") for c in p]
            txt = "closed" if e.closed else "open"
        rows.append([typ, layer, txt, " ".join(_fmt(n) for n in nums)])
        if typ == "POLYLINE":
            for v in e.vertices:
                rows.append(["VERTEX", v.dxf.layer, "", " ".join(_fmt(n) for n in loc(v.dxf.location))])
            rows.append(["SEQEND", layer, "", ""])
    er.blocks.append(Block("modelspace", "table", rows=rows, tid="t1"))
    return er


def extract(path: Path, kind: str):
    engines, issues = [], []
    try:
        er, iss = _raw(path)
        engines.append(er)
        issues += iss
    except Unsupported:
        pass
    try:
        engines.append(_ezdxf(path))
    except ImportError as e:
        issues.append(unavailable("ezdxf", f"library missing ({e})"))
    except Exception as e:  # noqa: BLE001
        issues.append(unavailable("ezdxf", f"{type(e).__name__}: {str(e)[:200]}"))
    if not engines:
        raise ExtractError(f"{path.name} could not be read as DXF by any engine")
    return engines, issues
