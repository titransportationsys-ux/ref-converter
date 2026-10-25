"""Independent re-check of emitted files.

This module deliberately does NOT reuse the consensus logic from compare.py. It asks three plain questions:

  (a) Does every emitted file contain exactly the numbers/cells of merged.json, and nothing else?
  (b) Is every merged unit identical (numbers, table cells) to what at least one raw engine actually read,
      unless a named human override replaced it?
  (c) Do the generated equation tests pass?
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from .emit import machine_cell, mod_name
from .model import cmp_text, num_diff, numbers, sha256_file
from .workspace import Work, now


def _expected_numbers(unit: dict) -> Counter:
    c = Counter()
    for b in unit["blocks"]:
        if b["kind"] == "text":
            c += numbers(b["text"])
        else:
            for r in b["rows"]:
                for cell in r:
                    c += numbers(cell)
            for fn in (b.get("meta") or {}).get("footnotes") or []:
                c += numbers(fn["text"])
    return c


def _md_bodies(text: str) -> dict:
    lines = text.split("\n")
    if lines and lines[0] == "---":
        end = lines.index("---", 1)
        lines = lines[end + 1:]
    bodies, cur, skip = {}, None, 0
    for line in lines:
        m = re.match(r"<!-- unit: (.+?) \| engine: ", line)
        if m:
            cur, skip = m.group(1), 1
            bodies[cur] = []
            continue
        if line.startswith("<!-- table: "):
            skip = 1
            continue
        if line in ("<!-- equations -->", "<!-- review-log -->"):
            cur = None
            continue
        if skip:
            skip -= 1
            continue
        if line.startswith("> **UNVERIFIED") or line.startswith("> **WAIVED") or line.startswith("> Raw page reading:") or re.fullmatch(r"\[src: .*\]", line):
            continue
        if cur is not None:
            bodies[cur].append(line)
    return bodies


def _find(out: Path, *cands):
    for c in cands:
        if (out / c).exists():
            return out / c
    return None


def run(work: Work, out: Path | None) -> dict:
    m, mg, review = work.manifest(), work.merged(), work.review()
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    src = Path(m["source"])
    if src.exists():
        add("source unchanged", sha256_file(src) == m["sha256"], "sha256 of the original file")
    else:
        checks.append({"name": "source unchanged", "status": "SKIP", "detail": "original file not found at recorded path"})
    add("no blocking flags open", review["open_blocking"] == 0, f"{review['open_blocking']} open")

    # (b) merged vs raw engines
    engines = {e.engine: e for e in work.engines()}
    bad = []
    for un in mg["units"]:
        texts = [b for b in un["blocks"] if b["kind"] == "text" and (b.get("meta") or {}).get("engine") != "human"
                 and not (b.get("meta") or {}).get("patched")]
        if texts:
            mine = numbers("\n".join(b["text"] for b in texts))
            ok = False
            for e in engines.values():
                et = "\n".join(x.text for x in e.blocks if x.unit == un["unit"] and x.kind == "text")
                if et and numbers(et) == mine and cmp_text("\n".join(b["text"] for b in texts)) == cmp_text(et):
                    ok = True
                    break
            if not ok:
                bad.append(f"{un['unit']} text")
        for b in un["blocks"]:
            if b["kind"] != "table" or (b.get("meta") or {}).get("engine") == "human" or (b.get("meta") or {}).get("patched"):
                continue
            want = [[cmp_text(c) for c in r] for r in b["rows"]]
            if not any([[cmp_text(c) for c in r] for r in x.rows] == want
                       for e in engines.values() for x in e.blocks if x.unit == un["unit"] and x.kind == "table" and x.tid == b["tid"]):
                bad.append(f"{un['unit']}.{b['tid']}")
    add("merged content equals a raw engine reading", not bad, ", ".join(bad[:15]))
    humans = [f"{un['unit']}" for un in mg["units"] for b in un["blocks"]
              if (b.get("meta") or {}).get("engine") == "human" or (b.get("meta") or {}).get("patched")]
    if humans:
        checks.append({"name": "human overrides present", "status": "INFO", "detail": "hand-supplied content in: " + ", ".join(sorted(set(humans)))})

    # (a) emitted files vs merged
    if out:
        slug = m["slug"]
        md = _find(out, f"{slug}.md", f"reference/{slug}.md")
        if md:
            bodies = _md_bodies(md.read_text(encoding="utf-8"))
            problems = []
            if [u["unit"] for u in mg["units"]] != list(bodies):
                problems.append("unit list/order differs")
            for un in mg["units"]:
                if un["unit"] in bodies:
                    got, want = numbers("\n".join(bodies[un["unit"]])), _expected_numbers(un)
                    if got != want:
                        problems.append(f"{un['unit']}: {num_diff(want, got, 6)}")
            add("markdown matches merged (numbers per unit)", not problems, " | ".join(problems[:8]))
        js = _find(out, f"{slug}.json", f"reference/{slug}.json")
        if js:
            d = json.loads(js.read_text(encoding="utf-8"))
            problems = []
            if [u["unit"] for u in d["units"]] != [u["unit"] for u in mg["units"]]:
                problems.append("unit list differs")
            else:
                for du, mu in zip(d["units"], mg["units"]):
                    for db, mb in zip(du["blocks"], mu["blocks"]):
                        if db["kind"] != mb["kind"] or (db["kind"] == "text" and db["text"] != mb["text"]) or \
                           (db["kind"] == "table" and db["rows"] != [[machine_cell(c) for c in r] for r in mb["rows"]]):
                            problems.append(mu["unit"])
                    if len(du["blocks"]) != len(mu["blocks"]):
                        problems.append(mu["unit"] + " block count")
            add("json matches merged", not problems, ", ".join(sorted(set(problems))[:10]))
        tdir = _find(out, "tables", "data/tables")
        n_tables = sum(1 for u in mg["units"] for b in u["blocks"] if b["kind"] == "table")
        if tdir or n_tables:
            problems = []
            for un in mg["units"]:
                for b in un["blocks"]:
                    if b["kind"] != "table":
                        continue
                    fn = (tdir or out) / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', un['unit']).strip('._') or 'x'}__{b['tid']}.csv"
                    if not fn.exists():
                        problems.append(f"{fn.name} missing")
                        continue
                    with open(fn, encoding="utf-8", newline="") as f:
                        rows = list(csv.reader(f))
                    if rows != [[machine_cell(c) for c in r] for r in b["rows"]]:
                        problems.append(f"{fn.name} differs")
                    fns = (b.get("meta") or {}).get("footnotes") or []
                    if fns:
                        fp = fn.with_name(fn.stem + ".footnotes.csv")
                        if not fp.exists() or list(csv.reader(open(fp, encoding="utf-8", newline="")))[1:] != [[x["mark"], x["text"]] for x in fns]:
                            problems.append(f"{fp.name} missing or differs")
            if tdir or problems:
                add("csv tables match merged", not problems, ", ".join(problems[:10]))
        code = _find(out, f"test_{mod_name(slug)}.py", f"scripts/test_{mod_name(slug)}.py")
        if code:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}       # never leave __pycache__ in a deliverable
            p = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(code.name)],
                               cwd=code.parent, capture_output=True, text=True, timeout=300, env=env)
            add("generated equation tests pass", p.returncode == 0, (p.stdout + p.stderr).strip().splitlines()[-1][:200] if (p.stdout + p.stderr).strip() else "")
    status = "PASS" if all(c["status"] != "FAIL" for c in checks) else "FAIL"
    res = {"status": status, "at": now(), "checks": checks,
           "note": "PASS means the files faithfully contain what the engines agreed on. It does not prove the engines read the page "
                   "correctly: the ref-verifier agent audits that against the source."}
    work.save_verification(res)
    return res
