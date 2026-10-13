"""JSON / GeoJSON: lossless flatten with literal number text, cross-checked against the raw file."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, NUM_RE, canon_number, cmp_text
from . import read_text_file


def _flatten(o, path="$", keys=None):
    if isinstance(o, dict):
        for k, v in o.items():
            if keys is not None:
                keys.append(str(k))
            yield from _flatten(v, f"{path}.{k}", keys)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _flatten(v, f"{path}[{i}]", keys)
    else:
        yield path, o


def extract(path: Path, kind: str):
    text, issues = read_text_file(path)
    try:
        obj = json.loads(text, parse_float=str, parse_int=str)   # keep numbers as written
    except json.JSONDecodeError as e:
        raise ExtractError(f"{path.name} is not valid JSON: {e}") from e
    rows = [["path", "value"]]
    keys: list = []
    for p, v in _flatten(obj, keys=keys):
        rows.append([p, "" if v is None else ("true" if v is True else "false" if v is False else str(v))])
    er = EngineResult("json", lossless=True)
    er.blocks.append(Block("json", "table", rows=rows, tid="t1"))
    # cross-check: every number token in the raw file must survive in the flattened table (paths + values)
    raw = Counter(canon_number(m) for m in NUM_RE.findall(cmp_text(text)))
    # array indices in the synthetic paths are not in the file, so count values and the original keys only
    flat = Counter(canon_number(m) for r in rows[1:] for m in NUM_RE.findall(cmp_text(r[1])))
    flat += Counter(canon_number(m) for k in keys for m in NUM_RE.findall(cmp_text(k)))
    if raw != flat:
        er.notes.append({"code": "JSON_NUMBER_MISMATCH", "severity": "BLOCK", "unit": "json",
                         "detail": "numbers in the raw file differ from the parsed data: " +
                                   "; ".join(f"{k}: raw {raw[k]} vs parsed {flat[k]}" for k in sorted(set(raw) | set(flat)) if raw[k] != flat[k])[:400]})
    return [er], issues
