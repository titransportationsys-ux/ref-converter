"""Orchestration: extract -> compare -> checks -> equations -> review files."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from . import checks, compare, equations, extractors
from .model import VERSION, Block, EngineResult, ExtractError, sha256_file
from .workspace import Work, now, slugify


def extract(src: Path, work_root: Path, pages=None, strip=None) -> Work:
    src = Path(src).resolve()
    if not src.is_file():
        raise ExtractError(f"{src} is not a file")
    work = Work(work_root)
    if work.exists():
        raise ExtractError(f"{work_root} already holds a conversion. Use a new --work folder or delete it first "
                           "(resolutions and equations there are your audit trail; nothing is overwritten).")
    kind, engines, issues, derived, used = extractors.extract(src, work.root, pages, strip)
    work.root.mkdir(parents=True, exist_ok=True)
    for er in engines:
        work.save_engine(er)
    work.save_manifest({
        "tool": f"refconvert {VERSION}", "created": now(), "source": str(src), "source_name": src.name,
        "sha256": sha256_file(src), "size": src.stat().st_size, "kind": kind, "slug": slugify(src.name),
        "pages": list(pages) if pages else None, "strip_patterns": list(strip) if strip else None,
        "derived_from": str(derived) if derived else None,
        "converted_file": str(used) if derived else None,
        "converted_sha256": sha256_file(used) if derived else None,
        "engine_order": [e.engine for e in engines], "issues": issues,
    })
    rebuild(work)
    return work


def ingest(work: Work, engine: str, path: Path) -> list[str]:
    """Add a transcription engine (e.g. vision-A) from a JSON/YAML file.

    File format:  {"units": {"p3": {"text": "...", "tables": [[["h1","h2"],["1","2"]]]}, ...}}
    """
    if not engine or not engine.replace("-", "").replace("_", "").isalnum():
        raise ExtractError("engine name must be letters/digits/-/_ (e.g. vision-A)")
    if engine in {"verbatim", "pdfplumber", "pypdf", "pdftotext"}:
        raise ExtractError(f"'{engine}' is a machine engine name; pick a transcription name such as vision-A")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict):
        raise ExtractError("ingest file must be a mapping with a 'units' mapping")
    known = {u["unit"] for u in work.merged().get("units", [])}
    er = EngineResult(engine)
    unknown = []
    for u, v in data["units"].items():
        u = str(u)
        if u not in known:
            unknown.append(u)
        if isinstance(v, str):
            v = {"text": v}
        er.blocks.append(Block(u, "text", str(v.get("text", ""))))
        for k, rows in enumerate(v.get("tables") or [], 1):
            if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
                raise ExtractError(f"unit {u}: tables must be lists of rows (lists of cells)")
            er.blocks.append(Block(u, "table", rows=[[str(c) for c in r] for r in rows], tid=f"t{k}"))
    work.save_engine(er)
    m = work.manifest()
    if engine not in m["engine_order"]:
        m["engine_order"].append(engine)
    m.setdefault("ingested", {})[engine] = {"file": str(path), "at": now()}
    work.save_manifest(m)
    rebuild(work)
    return unknown


def rebuild(work: Work) -> dict:
    m = work.manifest()
    engines = work.engines()
    merged, book = compare.build(engines, m.get("issues", []), work.resolutions(), work.patches())
    unit_text = checks.unit_text_map(merged)
    checks.run(merged, book, work.sanity_path)
    eqs = []
    if work.equations_path.exists():
        try:
            spec = equations.load_spec(work.equations_path)
        except Exception as e:  # noqa: BLE001
            book.add("EQ_SPEC_INVALID", "BLOCK", "", f"equations.yaml cannot be read: {e}")
            spec = {}
        eqs = equations.check(spec, unit_text, lambda c, s, u, d, t="": book.add(c, s, u, d, t)[0])
        spec_consts = spec.get("consts") or {}
    else:
        spec_consts = {}
    compare.finalize(merged, book)
    blocking = {f.tid for f in book.flags if f.tid.startswith("eq:") and f.severity == "BLOCK" and f.status == "open"}
    for e in eqs:
        e["status"] = "unverified" if f"eq:{e.get('name')}" in blocking or e.get("status") == "unverified" else "verified"
    merged["equations"] = eqs
    merged["consts"] = spec_consts
    merged["source"] = {k: m.get(k) for k in ("source_name", "source", "sha256", "kind", "slug", "derived_from", "converted_sha256")}
    merged["built"] = now()
    counts = Counter((f.severity, f.status) for f in book.flags)
    review = {
        "built": merged["built"],
        "open_blocking": len([f for f in book.flags if f.severity == "BLOCK" and f.status == "open"]),
        "open_warnings": len([f for f in book.flags if f.severity == "WARN" and f.status == "open"]),
        "resolved": len([f for f in book.flags if f.status == "resolved"]),
        "info": len([f for f in book.flags if f.severity == "INFO"]),
        "stale_resolutions": book.stale,
        "flags": [f.to_dict() for f in book.flags],
        "counts": {f"{k[0]}/{k[1]}": v for k, v in sorted(counts.items())},
    }
    work.save_merged(merged)
    work.save_review(review)
    return review


def _make_resolution(work: Work, flags: dict, fid: str, action: str, engine, text, rows, note: str, by: str, patches=None) -> dict:
    if action not in ("accept", "override", "waive", "drop", "patch"):
        raise ExtractError("action must be accept, override, waive, drop or patch")
    if action in ("override", "waive", "drop", "patch") and not (note or "").strip():
        raise ExtractError(f"a --note explaining the {action} is required (it goes into the audit trail)")
    if not (by or "").strip():
        raise ExtractError("--by is required (who decided: a person's name, or ref-verifier)")
    f = flags.get(fid)
    if f is None:
        raise ExtractError(f"no flag {fid}. Run 'status' to list flag ids.")
    if f["severity"] == "INFO":
        raise ExtractError(f"{fid} is informational and needs no resolution")
    is_table = bool(f["tid"]) and not f["tid"].startswith("eq:")
    r = {"flag": fid, "fp": f["fp"], "code": f["code"], "unit": f["unit"], "action": action, "note": note, "by": by, "at": now()}
    if action == "drop" and not is_table:
        raise ExtractError("drop only applies to table flags (an extracted 'table' that is not a table)")
    if action == "accept":
        if not engine:
            raise ExtractError("--accept needs an engine name")
        eng = next((e for e in work.engines() if e.engine == engine), None)
        if eng is None:
            raise ExtractError(f"engine '{engine}' does not exist in this work folder (have: {', '.join(work.manifest()['engine_order'])})")
        want = "table" if is_table else "text"
        if not any(b.unit == f["unit"] and b.kind == want and (want == "text" or b.tid == f["tid"]) for b in eng.blocks):
            raise ExtractError(f"engine '{engine}' has no {want} for unit {f['unit']}" + (f" table {f['tid']}" if want == "table" else "") + ": cannot accept it")
        if want == "text" and not any(b.text.strip() for b in eng.blocks if b.unit == f["unit"] and b.kind == "text"):
            raise ExtractError(f"engine '{engine}' has no text for {f['unit']}: cannot accept an empty reading")
        r["engine"] = engine
    elif action == "patch":
        if not patches or not all(isinstance(x, dict) and x.get("find") and "replace" in x for x in patches):
            raise ExtractError("patch needs a list of {find, replace}; 'find' must be the exact text now in the output")
        if not f["unit"]:
            raise ExtractError("patch needs a flag that belongs to a page/unit")
        r["patches"] = patches
    elif action == "override":
        if is_table:
            if rows is None:
                raise ExtractError("table flags need rows (JSON list of rows) for an override")
            r["rows"] = rows
        else:
            if text is None:
                raise ExtractError("text flags need --text for an override")
            r["text"] = text
    return r


def resolve_batch(work: Work, items: list[dict]) -> list[str]:
    """items: [{flag, action, engine?, text?, rows?, note, by}]. All validated first, written together, ONE rebuild."""
    flags = {f["id"]: f for f in work.review()["flags"]}
    made = [_make_resolution(work, flags, it["flag"], it["action"], it.get("engine"), it.get("text"), it.get("rows"), it.get("note", ""), it.get("by", ""), it.get("patches"))
            for it in items]
    rs = {r["flag"]: r for r in work.resolutions()}
    rs.update({r["flag"]: r for r in made})
    work._write(work.resolutions_path, list(rs.values()))
    rebuild(work)
    return [r["flag"] for r in made]


def resolve(work: Work, flag_ids: list[str], action: str, engine: str | None, text: str | None,
            rows_file: Path | None, note: str, by: str) -> list[str]:
    rows = json.loads(Path(rows_file).read_text(encoding="utf-8")) if rows_file else None
    return resolve_batch(work, [{"flag": fid, "action": action, "engine": engine, "text": text, "rows": rows, "note": note, "by": by}
                                for fid in flag_ids])


def patch_batch(work: Work, items: list[dict]) -> list[str]:
    """Page-level exact corrections: [{unit, find, replace, note, by}]. Validated together, ONE rebuild.
    'find' must occur in the CURRENT output of that unit, so a mistake in find is caught immediately."""
    from .model import short_hash
    units = {u["unit"]: u for u in work.merged().get("units", [])}
    cur = work.patches()
    made = []
    for it in items:
        u = str(it.get("unit", ""))
        if u not in units:
            raise ExtractError(f"unknown unit {u!r}")
        if not it.get("find") or "replace" not in it:
            raise ExtractError(f"{u}: patch needs 'find' (exact current text) and 'replace'")
        if not (it.get("note") or "").strip() or not (it.get("by") or "").strip():
            raise ExtractError(f"{u}: patch needs a --note (why) and --by (who)")
        text = "\n".join(b["text"] for b in units[u]["blocks"] if b["kind"] == "text") + "\n" + \
               "\n".join(c for b in units[u]["blocks"] if b["kind"] == "table" for r in b["rows"] for c in r)
        if it["find"] not in text:
            raise ExtractError(f"{u}: the text to replace is not in the current output: {it['find'][:80]!r}")
        pid = short_hash(f"{u}|{it['find']}|{it['replace']}")
        made.append({"id": pid, "unit": u, "find": it["find"], "replace": it["replace"], "note": it["note"], "by": it["by"], "at": now()})
    keep = {p["id"]: p for p in cur}
    keep.update({p["id"]: p for p in made})
    work.save_patches(list(keep.values()))
    rebuild(work)
    return [p["id"] for p in made]


def patch_remove(work: Work, ids: list[str]) -> int:
    cur = work.patches()
    left = [p for p in cur if p["id"] not in set(ids)]
    work.save_patches(left)
    rebuild(work)
    return len(cur) - len(left)
