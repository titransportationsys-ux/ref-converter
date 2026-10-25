"""Cross-engine comparison, consensus and flags.

Rules (the heart of the 'no mistakes' guarantee):

* A unit's text is ACCEPTED only if two independent engines agree on every
  number token AND on the text (char-4-gram Dice >= SIM). A lossless engine
  (verbatim text, JSON) needs no partner.
* Tables are accepted only if two engines produce identical cell grids. A table
  seen by one engine is BLOCKED (numbers not on the page / structure unverified).
* Anything else becomes a BLOCK flag. Nothing is silently guessed or fixed.
* A flag is cleared only by an explicit resolution (accept an engine, override
  with human-supplied text, or waive with a note). Resolutions are bound to the
  flag fingerprint, so they stop applying if the evidence changes.
"""
from __future__ import annotations

import re
from collections import Counter

from .model import Block, Flag, canon_number, cmp_text, dice, num_diff, numbers, NUM_RE, short_hash, squeeze, unit_label

SIM = 0.90
GRAPHICS_THRESHOLD = 20


class Book:
    """Collects flags and applies stored resolutions."""

    def __init__(self, resolutions: list[dict]):
        self.flags: list[Flag] = []
        self.res = {r["flag"]: r for r in resolutions}
        self.stale: list[dict] = []
        self._n: Counter = Counter()

    def add(self, code, severity, unit, detail, tid=""):
        key = f"{code}|{unit}|{tid}"
        self._n[key] += 1
        f = Flag(short_hash(f"{key}|{self._n[key]}"), code, severity, unit, detail, short_hash(detail, 12), tid)
        r = self.res.get(f.id)
        if severity == "INFO":
            f.status = "info"
            r = None
        elif r is not None:
            if r.get("fp") == f.fp:
                f.status, f.resolution = "resolved", r
            else:
                self.stale.append(r)
                r = None
        self.flags.append(f)
        return f, r

    def open_blocking(self):
        return [f for f in self.flags if f.severity == "BLOCK" and f.status == "open"]


# ------------------------------------------------------------------ helpers
def _index(e):
    idx = {}
    for b in e.blocks:
        d = idx.setdefault(b.unit, {"blocks": [], "tables": {}, "has_text": False})
        d["blocks"].append(b)
        if b.kind == "text":
            d["has_text"] = True
        else:
            d["tables"][b.tid] = b
    return idx


def _ordered_units(engines, idx):
    seen = []
    for e in engines:
        for u in idx[e.engine]:
            if u not in seen:
                seen.append(u)
    if seen and all(re.fullmatch(r"[ps]\d+", u) for u in seen) and len({u[0] for u in seen}) == 1:
        seen.sort(key=lambda u: int(u[1:]))
    return seen


def _canon_cell(c: str) -> str:
    t = cmp_text(c)
    if re.fullmatch(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", t):
        return format(float(t), ".12g")
    return t


def _grid(rows, canon):
    f = _canon_cell if canon else cmp_text
    return [[f(c) for c in r] for r in rows]


def _grid_diff(ga, gb) -> str:
    if len(ga) != len(gb):
        return f"row count {len(ga)} vs {len(gb)}"
    for i, (ra, rb) in enumerate(zip(ga, gb), 1):
        if len(ra) != len(rb):
            return f"row {i}: column count {len(ra)} vs {len(rb)}"
        for j, (a, b) in enumerate(zip(ra, rb), 1):
            if a != b:
                return f"row {i}, col {j}: '{a[:40]}' vs '{b[:40]}'"
    return "grids equal"


def _rows_in_lines(rows, text) -> bool:
    """Independent structure check: every non-empty row must equal, EXACTLY, one line of ``text``
    (cells joined by single spaces, nothing before, between or after), and rows must appear top to bottom.
    A dropped label column, a side-by-side neighbour, wrapped or merged cells all fail on purpose."""
    lines = [cmp_text(ln) for ln in text.split("\n")]
    ptr, seen = 0, 0
    for row in rows:
        cells = [cmp_text(c) for c in row if cmp_text(c)]
        if not cells:
            continue
        seen += 1
        target = " ".join(cells).replace("^", "")           # other engines print a superscript mark inline
        for i in range(ptr, len(lines)):
            if lines[i] == target:
                ptr = i + 1
                break
        else:
            return False
    return seen > 0


def _table_numbers(rows) -> Counter:
    c = Counter()
    for r in rows:
        for cell in r:
            c += numbers(cell)
    return c


def _text_of(blocks) -> str:
    return "\n".join(b.text for b in blocks if b.kind == "text")


def _dicts(blocks):
    return [{"kind": b.kind, "text": b.text, "rows": b.rows, "tid": b.tid, "meta": b.meta} for b in blocks]


# ------------------------------------------------------------------ build
def build(engines, issues, resolutions):
    book = Book(resolutions)
    for it in list(issues) + [n for e in engines for n in e.notes]:
        book.add(it["code"], it["severity"], it.get("unit", ""), it["detail"], it.get("tid", ""))

    merged = {"engines": [e.engine for e in engines], "units": []}
    if not engines:
        return merged, book
    idx = {e.engine: _index(e) for e in engines}
    by_name = {e.engine: e for e in engines}
    lossless = {e.engine for e in engines if e.lossless}

    for u in _ordered_units(engines, idx):
        present = [e for e in engines if u in idx[e.engine]]
        label = unit_label(u)
        text_engine, agreed, text_override = None, [], None
        unit_flags = []

        tx = {e.engine: _text_of(idx[e.engine][u]["blocks"]) for e in present if idx[e.engine][u]["has_text"]}
        consensus_text = ""
        if tx:
            nonempty = [n for n, t in tx.items() if squeeze(t)]
            empty = [n for n in tx if n not in nonempty]
            det = None
            if lossless & set(nonempty):
                text_engine = next(n for n in nonempty if n in lossless)
                agreed = [text_engine]
            elif len(nonempty) >= 2:
                pair = None
                for i in range(len(nonempty)):
                    for j in range(i + 1, len(nonempty)):
                        a, b = tx[nonempty[i]], tx[nonempty[j]]
                        if numbers(a) == numbers(b) and dice(a, b) >= SIM:
                            pair = (nonempty[i], nonempty[j])
                            break
                    if pair:
                        break
                if pair:
                    text_engine, agreed = pair[0], list(pair)
                    for n in nonempty:
                        if n in pair:
                            continue
                        ok_n = numbers(tx[n]) == numbers(tx[text_engine])
                        if ok_n and dice(tx[n], tx[text_engine]) >= SIM:
                            agreed.append(n)                      # a third engine agrees too
                            continue
                        book.add("ENGINE_OUTLIER", "WARN", u,
                                 f"engine '{n}' disagrees with the accepted text (numbers: "
                                 f"{num_diff(numbers(tx[text_engine]), numbers(tx[n]), 6)}; similarity {dice(tx[text_engine], tx[n]):.2f}); "
                                 f"accepted by {', '.join(pair)}" + (" [numbers identical, wording differs]" if ok_n else ""))
                else:
                    a, b = nonempty[0], nonempty[1]
                    det = (f"engines '{a}' and '{b}' disagree: {num_diff(numbers(tx[a]), numbers(tx[b]))}; "
                           f"text similarity {dice(tx[a], tx[b]):.2f} (need >= {SIM}); no two of {len(nonempty)} engines agree")
                    f, r = book.add("NO_CONSENSUS", "BLOCK", u, det)
                    unit_flags.append(f)
                    text_engine = nonempty[0]
                    text_override, text_engine, agreed = _apply_text_res(r, tx, nonempty, text_engine)
            elif len(nonempty) == 1:
                det = (f"only engine '{nonempty[0]}' produced text here" +
                       (f" ({', '.join(empty)} returned nothing)" if empty else "") +
                       ". Content is single-sourced; add a second engine/transcription or confirm manually.")
                f, r = book.add("SINGLE_SOURCE", "BLOCK", u, det)
                unit_flags.append(f)
                text_engine = nonempty[0]
                text_override, text_engine, agreed = _apply_text_res(r, tx, nonempty, text_engine)
            else:
                meta = [e.unit_meta.get(u, {}) for e in present]
                images = max((m.get("images", 0) for m in meta), default=0)
                graphics = max((m.get("graphics", 0) for m in meta), default=0)
                if images:
                    f, r = book.add("NEEDS_TRANSCRIPTION", "BLOCK", u,
                                    f"no text layer but {images} image(s) on {label}: probably scanned. Needs two blind transcriptions "
                                    "(ingest vision-A and vision-B) or a human override.")
                    unit_flags.append(f)
                    text_override, text_engine, agreed = _apply_text_res(r, tx, [], None)
                elif graphics > GRAPHICS_THRESHOLD:
                    f, r = book.add("EMPTY_UNIT_GRAPHICS", "BLOCK", u,
                                    f"no text but {graphics} drawing objects on {label}: a drawing or text converted to outlines. "
                                    "Confirm it carries nothing needed, or transcribe it.")
                    unit_flags.append(f)
                    text_override, text_engine, agreed = _apply_text_res(r, tx, [], None)
                else:
                    book.add("BLANK_UNIT", "INFO", u, f"{label} has no text and no images (blank).")
            if text_engine and tx.get(text_engine) is not None:
                consensus_text = text_override if text_override is not None else tx[text_engine]
            elif text_override is not None:
                consensus_text = text_override
            for n in empty:
                if nonempty:
                    book.add("ENGINE_EMPTY", "INFO", u, f"engine '{n}' returned no text for {label}")
            if nonempty:
                m = max((e.unit_meta.get(u, {}).get("images", 0) for e in present), default=0)
                if m:
                    book.add("FIGURE_PRESENT", "INFO", u, f"{m} image/figure object(s) on {label} were NOT converted. "
                             "Ask the user before analysing charts or diagrams.")

        # ---- tables
        tids = []
        for e in present:
            for t in idx[e.engine][u]["tables"]:
                if t not in tids:
                    tids.append(t)
        merged_tables = {}
        for tid in tids:
            cands = [(e, idx[e.engine][u]["tables"][tid]) for e in present if tid in idx[e.engine][u]["tables"]]
            chosen, extra_meta = None, {}
            if len(cands) >= 2:
                pair = None
                for i in range(len(cands)):
                    for j in range(i + 1, len(cands)):
                        cn = cands[i][0].canon_numbers or cands[j][0].canon_numbers
                        if _grid(cands[i][1].rows, cn) == _grid(cands[j][1].rows, cn):
                            pair = (i, j)
                            break
                    if pair:
                        break
                if pair:
                    chosen = cands[pair[0]]
                    extra_meta["agreed"] = [cands[pair[0]][0].engine, cands[pair[1]][0].engine]
                    for k, (e, b) in enumerate(cands):
                        if k not in pair:
                            cn = e.canon_numbers or chosen[0].canon_numbers
                            book.add("TABLE_OUTLIER", "WARN", u, f"engine '{e.engine}' table {tid} differs from accepted: "
                                     f"{_grid_diff(_grid(chosen[1].rows, cn), _grid(b.rows, cn))}", tid)
                else:
                    (ea, ba), (eb, bb) = cands[0], cands[1]
                    cn = ea.canon_numbers or eb.canon_numbers
                    f, r = book.add("TABLE_MISMATCH", "BLOCK", u,
                                    f"table {tid}: '{ea.engine}' vs '{eb.engine}': {_grid_diff(_grid(ba.rows, cn), _grid(bb.rows, cn))}", tid)
                    unit_flags.append(f)
                    chosen = _apply_table_res(r, cands, cands[0], merged_tables, tid, u)
            else:
                e, b = cands[0]
                missing = (_table_numbers(b.rows) - numbers(consensus_text)) if (tx and consensus_text) else None
                layout = None
                if tx and consensus_text and not missing:
                    layout = next((n for n in tx if n != e.engine and tx[n] and _rows_in_lines(b.rows, tx[n])), None)
                if e.lossless:
                    chosen = (e, b)
                    extra_meta["agreed"] = [e.engine]
                elif layout is not None:
                    chosen = (e, b)
                    extra_meta["agreed"] = [e.engine, f"{layout}:line-layout"]
                else:
                    if missing is not None:
                        if missing:
                            code = "TABLE_UNCORROBORATED"
                            det = (f"table {tid} ({e.engine} only): numbers not found in the agreed page text: "
                                   f"{', '.join(sorted(missing.elements())[:12])}")
                        else:
                            code = "TABLE_STRUCTURE"
                            det = (f"table {tid} ({e.engine} only): every number is on the page, but ROW/COLUMN ALIGNMENT could not be "
                                   "confirmed by a second engine (rows not found in order on single lines). Check it against the page.")
                    else:
                        code = "SINGLE_SOURCE_TABLE"
                        det = f"table {tid} read by '{e.engine}' only; no second engine or page text to compare."
                    f, r = book.add(code, "BLOCK", u, det, tid)
                    unit_flags.append(f)
                    chosen = _apply_table_res(r, cands, cands[0], merged_tables, tid, u)
            if chosen is not None and getattr(chosen[0], "engine", "") == "human" and tx and consensus_text:
                # a hand-typed table must still be made of numbers that are printed on the page
                missing = _table_numbers(chosen[1].rows) - numbers(consensus_text)
                if missing:
                    f2, _ = book.add("TABLE_OVERRIDE_NUMBERS", "BLOCK", u,
                                     f"table {tid} override contains numbers not found in the agreed page text: "
                                     f"{', '.join(sorted(missing.elements())[:12])}. Fix the override (typo?) or waive with a note.", tid)
                    unit_flags.append(f2)
            if chosen is not None and tid not in merged_tables:
                e, b = chosen
                merged_tables[tid] = Block(u, "table", rows=b.rows, tid=tid, meta={**(b.meta or {}), "engine": e.engine, **extra_meta})

        # ---- assemble blocks in reading order
        blocks, emitted = [], set()
        if text_override is not None:
            blocks.append(Block(u, "text", text_override, meta={"engine": "human"}))
        if text_engine and text_override is None:
            for b in idx[by_name[text_engine].engine][u]["blocks"]:
                if b.kind == "text":
                    blocks.append(Block(u, "text", b.text, meta={**b.meta, "engine": text_engine}))
                elif b.tid in merged_tables and b.tid not in emitted:
                    blocks.append(merged_tables[b.tid])
                    emitted.add(b.tid)
        for tid, tb in merged_tables.items():
            if tid not in emitted:
                blocks.append(tb)
        merged["units"].append({"unit": u, "label": label, "status": "verified", "engine": "human" if text_override is not None else text_engine,
                                "agreed": agreed, "blocks": _dicts(blocks), "flags": [f.id for f in unit_flags]})
    _apply_patches(merged, book)
    return merged, book


def _apply_patches(merged, book):
    """Exact find-and-replace corrections recorded as 'patch' resolutions (who, why, what). A patch that no longer finds
    its text blocks again: the output must never silently differ from what was decided."""
    per_unit = {}
    for f in list(book.flags):
        r = f.resolution
        if f.status == "resolved" and r and r.get("action") == "patch":
            per_unit.setdefault(f.unit, []).append((f, r))
    for un in merged["units"]:
        for f, r in per_unit.get(un["unit"], []):
            for pt in r["patches"]:
                hit = 0
                for b in un["blocks"]:
                    if b["kind"] == "text" and pt["find"] in b["text"]:
                        hit += b["text"].count(pt["find"])
                        b["text"] = b["text"].replace(pt["find"], pt["replace"])
                        b["meta"] = {**(b.get("meta") or {}), "patched": True}
                    elif b["kind"] == "table":
                        for row in b["rows"]:
                            for i, c in enumerate(row):
                                if pt["find"] in c:
                                    hit += c.count(pt["find"])
                                    row[i] = c.replace(pt["find"], pt["replace"])
                                    b["meta"] = {**(b.get("meta") or {}), "patched": True}
                if not hit:
                    book.add("PATCH_NOT_APPLIED", "BLOCK", un["unit"], f"patch text not found any more: {pt['find'][:80]!r} (flag {f.id}). The text changed; redo the decision.")


def _apply_text_res(r, tx, nonempty, text_engine):
    """Apply a resolution to a text-level flag. Returns (override_text, text_engine, agreed)."""
    if not r:
        return None, text_engine, []
    if r["action"] == "accept" and r.get("engine") in tx:
        return None, r["engine"], [r["engine"]]
    if r["action"] == "override":
        return r["text"], None, ["human"]
    return None, text_engine, []           # waive: keep provisional choice


def _apply_table_res(r, cands, default, merged_tables, tid, u):
    if not r:
        return default                       # unresolved: keep the provisional table (unit stays 'unverified')
    if r["action"] == "accept":
        for e, b in cands:
            if e.engine == r.get("engine"):
                return (e, b)
        return default
    if r["action"] == "drop":
        return None                              # the extractor saw a 'table' that is not one: leave it out (page text stays)
    if r["action"] == "override":
        class _H:                            # tiny stand-in engine for human-supplied rows
            engine = "human"
        keep = {k: v for k, v in (default[1].meta or {}).items() if k == "footnotes"}      # the legend text stays with the table
        return (_H, Block(u, "table", rows=r["rows"], tid=tid, meta=keep))
    return default                           # waive


def finalize(merged, book):
    """Compute each unit's status from its flags."""
    by_unit = {}
    for f in book.flags:
        by_unit.setdefault(f.unit, []).append(f)
    for un in merged["units"]:
        fl = [f for f in by_unit.get(un["unit"], []) if f.severity == "BLOCK"]
        if any(f.status == "open" for f in fl):
            un["status"] = "unverified"
        elif any(f.resolution and f.resolution["action"] == "waive" for f in fl):
            un["status"] = "waived"
        elif fl:
            un["status"] = "resolved"
        else:
            un["status"] = "verified"
        un["flags"] = [f.id for f in by_unit.get(un["unit"], []) if f.severity != "INFO"]
    return merged
