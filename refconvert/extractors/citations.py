"""BibTeX and RIS: own parser vs third-party parser (bibtexparser / rispy)."""
from __future__ import annotations

import re
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, clean
from . import read_text_file, unavailable


def _strip_braces(v: str) -> str:
    return clean(re.sub(r"[{}]", "", v)).replace("\n", " ")


# ---------------------------------------------------------------- BibTeX
def _scan_value(t: str, i: int):
    """Read one value starting at t[i]; returns (text, next_index). Handles {..}, "..", bare and '#' joins."""
    n, parts = len(t), []
    while True:
        while i < n and t[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if t[i] == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += (t[j] == "{") - (t[j] == "}")
                j += 1
            parts.append(t[i + 1:j - 1])
            i = j
        elif t[i] == '"':
            j, depth = i + 1, 0
            while j < n and not (t[j] == '"' and depth == 0):
                depth += (t[j] == "{") - (t[j] == "}")
                j += 1
            parts.append(t[i + 1:j])
            i = j + 1
        else:
            m = re.compile(r"[^,\s}#]+").match(t, i)
            if not m:
                break
            parts.append(m.group(0))
            i = m.end()
        k = i
        while k < n and t[k] in " \t\r\n":
            k += 1
        if k < n and t[k] == "#":
            i = k + 1
            continue
        break
    return "".join(parts), i


def _bib_own(text: str):
    entries, issues, pos = [], [], 0
    head = re.compile(r"@(\w+)\s*([{(])")
    while True:
        m = head.search(text, pos)
        if not m:
            break
        typ, op = m.group(1).lower(), m.group(2)
        if op == "(":
            issues.append({"code": "BIB_PAREN_ENTRY", "severity": "WARN", "unit": "",
                           "detail": f"@{typ}( ... ) with parentheses not supported by the own parser"})
            pos = m.end()
            continue
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += (text[j] == "{") - (text[j] == "}")
            j += 1
        body = text[m.end():j - 1]
        pos = j
        if typ in ("comment", "string", "preamble"):
            continue
        km = re.match(r"\s*([^,\s]+)\s*,", body)
        if not km:
            issues.append({"code": "BIB_NO_KEY", "severity": "WARN", "unit": "", "detail": f"@{typ} entry without a key"})
            continue
        key, fields, i = km.group(1), [], km.end()
        while i < len(body):
            fm = re.compile(r"[\s,]*([\w\-:.]+)\s*=\s*").match(body, i)
            if not fm:
                break
            val, i = _scan_value(body, fm.end())
            fields.append((fm.group(1).lower(), _strip_braces(val)))
        entries.append((key, typ, fields))
    return entries, issues


def _bib_third(text: str):
    import bibtexparser
    out = []
    if hasattr(bibtexparser, "parse_string"):                      # v2
        lib = bibtexparser.parse_string(text)
        for e in lib.entries:
            out.append((e.key, e.entry_type.lower(), [(f.key.lower(), _strip_braces(str(f.value))) for f in e.fields]))
        failed = [str(b.error)[:100] for b in lib.failed_blocks]
    else:                                                          # v1
        lib = bibtexparser.loads(text)
        for e in lib.entries:
            out.append((e["ID"], e["ENTRYTYPE"].lower(),
                        [(k.lower(), _strip_braces(v)) for k, v in e.items() if k not in ("ID", "ENTRYTYPE")]))
        failed = []
    return out, failed


def _entries_to_result(name, entries):
    er = EngineResult(name)
    seen = {}
    for key, typ, fields in entries:
        seen[key] = seen.get(key, 0) + 1
        k = key if seen[key] == 1 else f"{key}#{seen[key]}"
        rows = [["entrytype", typ]] + [[f, v] for f, v in sorted(fields, key=lambda x: x[0])]
        er.blocks.append(Block(f"bib:{k}", "table", rows=rows, tid="t1"))
    return er


# ---------------------------------------------------------------- RIS
def _ris_own(text: str):
    entries, cur, last = [], None, None
    for ln in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", ln)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "TY":
                cur = []
            if cur is None:
                continue
            if tag == "ER":
                entries.append(cur)
                cur, last = None, None
                continue
            cur.append([tag, val])
            last = cur[-1]
        elif cur is not None and ln.strip() and last is not None:
            last[1] += " " + ln.strip()
    if cur:
        entries.append(cur)
    return entries


def _ris_third(text: str):
    import rispy
    ident = {t: t for t in ("TY", "ID", "T1", "TI", "T2", "T3", "A1", "A2", "A3", "AU", "AB", "N1", "N2", "KW",
                            "PY", "Y1", "DA", "JO", "JF", "JA", "J2", "VL", "IS", "SP", "EP", "SN", "UR", "DO",
                            "L1", "L2", "PB", "CY", "LA", "M1", "M3", "ED", "DB", "DP", "AN", "AD", "C1", "C2",
                            "C3", "C4", "C5", "C6", "C7", "C8", "ET", "CN", "CA", "LB", "OP", "RN", "RP", "SE",
                            "ST", "T3", "TA", "TT", "U1", "U2", "U3", "U4", "U5", "VO", "Y2")}
    out = []
    for rec in rispy.loads(text, mapping=ident, skip_unknown_tags=False):
        row = []
        for tag, val in rec.items():
            if tag == "unknown_tag":
                for t2, vs in val.items():
                    for v in (vs if isinstance(vs, list) else [vs]):
                        row.append([t2, str(v).strip()])
            else:
                for v in (val if isinstance(val, list) else [val]):
                    row.append([tag, str(v).strip()])
        out.append(row)
    return out


def _ris_result(name, entries):
    er = EngineResult(name)
    for n, rows in enumerate(entries, 1):
        er.blocks.append(Block(f"ris:{n}", "table", rows=[[t, clean(v).replace("\n", " ")] for t, v in sorted(rows, key=lambda r: r[0])], tid="t1"))
    return er


def extract(path: Path, kind: str):
    text, issues = read_text_file(path)
    engines = []
    if kind == "bib":
        own, iss = _bib_own(text)
        issues += iss
        if not own:
            raise ExtractError(f"no BibTeX entries found in {path.name}")
        engines.append(_entries_to_result("bib-own", own))
        try:
            third, failed = _bib_third(text)
            engines.append(_entries_to_result("bibtexparser", third))
            for f in failed:
                issues.append({"code": "BIB_PARSE_FAILED", "severity": "BLOCK", "unit": "", "detail": f"bibtexparser could not parse a block: {f}"})
        except ImportError as e:
            issues.append(unavailable("bibtexparser", f"library missing ({e})"))
    else:
        own = _ris_own(text)
        if not own:
            raise ExtractError(f"no RIS records (TY ... ER) found in {path.name}")
        engines.append(_ris_result("ris-own", own))
        try:
            engines.append(_ris_result("rispy", _ris_third(text)))
        except ImportError as e:
            issues.append(unavailable("rispy", f"library missing ({e})"))
        except Exception as e:  # noqa: BLE001
            issues.append(unavailable("rispy", f"{type(e).__name__}: {str(e)[:200]}"))
    return engines, issues
