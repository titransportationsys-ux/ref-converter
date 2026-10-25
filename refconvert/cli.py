"""Command line: python -m refconvert <command>. Exit codes: 0 ok, 1 error, 2 review needed, 3 unsupported."""
from __future__ import annotations

import argparse
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from . import emit, extractors, pipeline, verify
from .model import ExtractError, Unsupported
from .workspace import Work, slugify

GUIDE = {
    "NO_CONSENSUS": "Two or more engines read this differently. Look at the detail, compare with the original, then "
                    "'resolve --accept <engine>' if one is right, or '--override-text' with the correct text.",
    "SINGLE_SOURCE": "Only one engine produced text. Install/enable a second engine, ingest a second transcription, or confirm by hand.",
    "NEEDS_TRANSCRIPTION": "Scanned/image content. Transcribe it twice independently (converter agent = vision-A, verifier agent = vision-B), "
                           "'ingest' both, and the engines will be compared. Ask the user before opening the PDF/image.",
    "EMPTY_UNIT_GRAPHICS": "Drawing page with no text. Ask the user whether it carries anything needed; waive with a note if not.",
    "TABLE_MISMATCH": "Two engines built different tables. Check the original; accept the right engine or override with --rows-file.",
    "TABLE_UNCORROBORATED": "Table numbers are not on the page text: likely a misread cell. Check the original.",
    "TABLE_STRUCTURE": "Numbers are fine but row/column alignment is read by one engine only. Check the table against the page, then "
                       "resolve --accept <engine> --by <who checked>.",
    "SINGLE_SOURCE_TABLE": "Only one engine read this table. Add a second reading or confirm by hand.",
    "FORMULA_NO_CACHE": "Open the workbook in Excel, recalculate and save, then re-extract.",
    "JSON_NUMBER_MISMATCH": "Parsed numbers differ from the raw file; the file may be malformed. Inspect it.",
    "BIB_PARSE_FAILED": "The BibTeX parser rejected an entry. Fix the entry in the .bib and re-extract.",
    "EQ_BAD_EXPR": "Fix the expression in equations.yaml (see the detail), then 'build'.",
    "EQ_LITERAL_MISMATCH": "A number in the expression differs from the printed equation. Fix equations.yaml.",
    "EQ_EXAMPLE_FAIL": "The expression does not reproduce the source's worked example. Re-read the equation in the source.",
    "EQ_EXAMPLE_NOT_IN_SOURCE": "An example value is not printed in the source. Correct it or declare it under 'assumed'.",
    "EQ_SOURCE_NUMBER_NOT_FOUND": "source_text has numbers the page does not. Re-transcribe the equation.",
    "EQ_UNIT_MISSING": "The unit key (e.g. p112) does not exist. Use a key from 'status --units'.",
}


def _work(p) -> Work:
    w = Work(Path(p))
    w.manifest()
    return w


def _summary(w: Work) -> int:
    r = w.review()
    mg = w.merged()
    st = defaultdict(int)
    for u in mg["units"]:
        st[u["status"]] += 1
    print(f"engines: {', '.join(mg['engines'])}")
    print(f"units: {len(mg['units'])}  " + "  ".join(f"{k}={v}" for k, v in sorted(st.items())))
    print(f"flags: {r['open_blocking']} blocking open, {r['open_warnings']} warnings open, {r['resolved']} resolved, {r['info']} info")
    if r["stale_resolutions"]:
        print(f"WARNING: {len(r['stale_resolutions'])} resolution(s) no longer match their flag (evidence changed) and were ignored.")
    if r["open_blocking"]:
        print("NEXT: run 'status' to see what needs a decision. Nothing is guessed; output is blocked until each flag is resolved.")
        return 2
    print("NEXT: no blocking flags. Ask the user which outputs they want, then run 'emit'.")
    return 0


def cmd_inspect(a):
    p = Path(a.file)
    kind = extractors.detect(p)
    print(f"{p.name}: kind={kind}")
    for mod, why in (("pypdf", "pdf"), ("pdfplumber", "pdf"), ("docx", "docx"), ("pptx", "pptx"), ("openpyxl", "xlsx"), ("bs4", "html"),
                     ("ezdxf", "dxf"), ("bibtexparser", "bib"), ("rispy", "ris"), ("pandas", "csv"), ("yaml", "all")):
        try:
            __import__(mod)
            print(f"  [ok]      {mod} ({why})")
        except ImportError:
            print(f"  [missing] {mod} ({why})  -> pip install -r requirements.txt")
    import shutil
    print(f"  [{'ok' if shutil.which('pdftotext') else 'missing'}]  pdftotext (poppler, third PDF engine)")
    print(f"  [{'ok' if shutil.which('soffice') else 'missing'}]  LibreOffice (legacy .doc/.xls/.ppt conversion)")
    return 0


def cmd_extract(a):
    src = Path(a.file)
    root = Path(a.work) if a.work else Path("refwork") / slugify(src.name)
    pages = None
    if a.pages:
        try:
            lo, _, hi = a.pages.partition("-")
            pages = (int(lo), int(hi or lo))
            assert 1 <= pages[0] <= pages[1]
        except (ValueError, AssertionError):
            raise ExtractError("--pages must look like 536-562 (first-last physical PDF pages)")
    w = pipeline.extract(src, root, pages)
    print(f"work folder: {w.root}" + (f"  (PDF pages {pages[0]}-{pages[1]} only; citations use physical PDF page numbers)" if pages else ""))
    return _summary(w)


def cmd_batch(a):
    src, wroot = Path(a.path), Path(a.work_root)
    files = []
    if src.is_file() and src.suffix.lower() == ".zip":
        dest = wroot / "_unzipped" / slugify(src.name)
        dest.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
            if len(infos) > 5000 or sum(i.file_size for i in infos) > extractors.MAX_UNZIPPED:
                raise ExtractError("archive too large (over 5000 members or 500 MB expanded)")
            for i in infos:
                target = (dest / i.filename).resolve()
                if not str(target).startswith(str(dest.resolve())) or (i.external_attr >> 28) == 0xA:
                    print(f"skipped unsafe member: {i.filename}")
                    continue
                z.extract(i, dest)
        src = dest
    if src.is_dir():
        # EVERY non-hidden file is attempted or reported; nothing is silently ignored
        files = sorted(p for p in src.rglob("*") if p.is_file() and not any(part.startswith((".", "~$")) for part in p.relative_to(src).parts))
    else:
        files = [src]
    codes, used = {}, set()
    for f in files:
        slug = slugify(f.name)
        n = 1
        while slug in used:
            n += 1
            slug = f"{slugify(f.name)}_{n}"
        used.add(slug)
        print(f"\n== {f}")
        try:
            w = pipeline.extract(f, wroot / slug)
            codes[f.name] = _summary(w)
        except (Unsupported, ExtractError) as e:
            print(f"SKIPPED: {e}")
            codes[f.name] = 3 if isinstance(e, Unsupported) else 1
    print("\n== batch result")
    for k, v in codes.items():
        print(f"  {'ok' if v == 0 else 'REVIEW' if v == 2 else 'SKIPPED'}  {k}")
    return 0 if all(v == 0 for v in codes.values()) else 2


def cmd_ingest(a):
    w = _work(a.work)
    unknown = pipeline.ingest(w, a.engine, Path(a.file))
    if unknown:
        print("NOTE: these unit keys do not exist in the extraction (kept, but check spelling): " + ", ".join(unknown))
    return _summary(w)


def cmd_build(a):
    w = _work(a.work)
    pipeline.rebuild(w)
    return _summary(w)


def cmd_status(a):
    w = _work(a.work)
    r = w.review()
    if a.units:
        for u in w.merged()["units"]:
            print(f"{u['unit']:<20} {u['status']:<11} engine={u['engine']}")
        return 0
    rc = _summary(w)
    groups = defaultdict(list)
    for f in r["flags"]:
        if f["status"] == "open" and (f["severity"] != "INFO" or a.all) and (not a.code or f["code"] == a.code):
            groups[(f["severity"], f["code"])].append(f)
    for (sev, code), fl in sorted(groups.items(), key=lambda kv: (kv[0][0] != "BLOCK", kv[0][0] != "WARN", kv[0][1])):
        print(f"\n[{sev}] {code}  x{len(fl)}")
        if code in GUIDE:
            print(f"   how to fix: {GUIDE[code]}")
        for f in fl[: (999 if a.code else 3)]:
            print(f"   {f['id']}  {f['unit']}{'.' + f['tid'] if f['tid'] else ''}: {f['detail'][:300]}")
        if len(fl) > 3 and not a.code:
            print(f"   ... {len(fl) - 3} more (use --code {code} to list all)")
    return rc


def cmd_resolve(a):
    w = _work(a.work)
    r = w.review()
    ids = list(a.flag or [])
    if a.code:
        ids += [f["id"] for f in r["flags"] if f["code"] == a.code and f["status"] == "open" and (not a.unit or f["unit"] == a.unit)]
    if not ids:
        raise ExtractError("no flags selected: give --flag ID or --code CODE (see 'status')")
    action = "accept" if a.accept else "override" if (a.override_text is not None or a.rows_file) else "waive" if a.waive else None
    if not action:
        raise ExtractError("choose one of --accept ENGINE, --override-text TEXT / --rows-file FILE, --waive")
    ok, failed = [], []
    for fid in ids:
        try:
            pipeline.resolve(w, [fid], action, a.accept, a.override_text, Path(a.rows_file) if a.rows_file else None, a.note or "", a.by or "")
            ok.append(fid)
        except ExtractError as e:
            failed.append((fid, str(e)))
    print(f"resolved {len(ok)}; failed {len(failed)}")
    for fid, e in failed[:20]:
        print(f"  {fid}: {e}")
    _summary(w)
    return 1 if failed else 0


def cmd_emit(a):
    w = _work(a.work)
    review = emit.guard(w, a.allow_unverified)
    out = Path(a.out)
    fmts = [x.strip() for x in a.fmt.split(",") if x.strip()]
    done = []
    if "skill" in fmts:
        done.append(emit.emit_skill(w, review, out, a.skill_name, a.description, a.force))
    else:
        if "md" in fmts:
            done.append(emit.emit_md(w, review, out, a.force))
        if "json" in fmts:
            done.append(emit.emit_json(w, review, out, a.force))
        if "csv" in fmts:
            done += emit.emit_csv(w, out, a.force)
        if "code" in fmts:
            done += emit.emit_code(w, review, out, a.force)
    unknown = set(fmts) - {"md", "json", "csv", "code", "skill"}
    if unknown:
        raise ExtractError(f"unknown format(s): {', '.join(sorted(unknown))}")
    for p in done:
        print(f"wrote {p}")
    print("NEXT: run 'verify' (add --out to check the files), then have the ref-verifier agent audit against the source.")
    return 0


def cmd_verify(a):
    w = _work(a.work)
    res = verify.run(w, Path(a.out) if a.out else None)
    for c in res["checks"]:
        print(f"[{c['status']}] {c['name']}" + (f": {c['detail']}" if c["detail"] else ""))
    print(f"\nVERIFICATION {res['status']}\n{res['note']}")
    return 0 if res["status"] == "PASS" else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="refconvert", description="Convert reference documents into verified, project-ready data.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect"); p.add_argument("file"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("extract"); p.add_argument("file"); p.add_argument("--work"); p.add_argument("--pages", help="PDF only: first-last physical page, e.g. 536-562"); p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("batch"); p.add_argument("path"); p.add_argument("--work-root", default="refwork"); p.set_defaults(fn=cmd_batch)
    p = sub.add_parser("ingest"); p.add_argument("work"); p.add_argument("--engine", required=True); p.add_argument("file"); p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("build"); p.add_argument("work"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("status"); p.add_argument("work"); p.add_argument("--all", action="store_true"); p.add_argument("--code")
    p.add_argument("--units", action="store_true"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("resolve"); p.add_argument("work"); p.add_argument("--flag", action="append"); p.add_argument("--code"); p.add_argument("--unit")
    p.add_argument("--accept", metavar="ENGINE"); p.add_argument("--override-text"); p.add_argument("--rows-file"); p.add_argument("--waive", action="store_true")
    p.add_argument("--note"); p.add_argument("--by"); p.set_defaults(fn=cmd_resolve)
    p = sub.add_parser("emit"); p.add_argument("work"); p.add_argument("--out", required=True); p.add_argument("--as", dest="fmt", default="md")
    p.add_argument("--skill-name"); p.add_argument("--description"); p.add_argument("--allow-unverified", action="store_true")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_emit)
    p = sub.add_parser("verify"); p.add_argument("work"); p.add_argument("--out"); p.set_defaults(fn=cmd_verify)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except Unsupported as e:
        print(f"UNSUPPORTED: {e}", file=sys.stderr)
        return 3
    except (ExtractError, FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
