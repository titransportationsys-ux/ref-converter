"""CSV/TSV: csv module vs pandas."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..model import Block, EngineResult, ExtractError, clean
from . import read_text_file, unavailable


def _delim(path: Path, text: str) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def extract(path: Path, kind: str):
    text, issues = read_text_file(path)
    d = _delim(path, text)
    csv.field_size_limit(1 << 26)
    rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=d) if r]
    if not rows:
        raise ExtractError(f"{path.name} contains no rows")
    width = max(len(r) for r in rows)
    a = EngineResult("csv", canon_numbers=False)
    a.blocks.append(Block("table", "table", rows=[[clean(c) for c in r] + [""] * (width - len(r)) for r in rows], tid="t1"))
    engines = [a]
    try:
        import pandas as pd
        df = pd.read_csv(io.StringIO(text), sep=d, header=None, dtype=str, keep_default_na=False,
                         names=list(range(width)), engine="python", skip_blank_lines=True)
        b = EngineResult("pandas")
        b.blocks.append(Block("table", "table", rows=[[clean(str(c)) for c in r] for r in df.fillna("").values.tolist()], tid="t1"))
        engines.append(b)
    except ImportError as e:
        issues.append(unavailable("pandas", f"library missing ({e})"))
    except Exception as e:  # noqa: BLE001
        issues.append(unavailable("pandas", f"{type(e).__name__}: {str(e)[:200]}"))
    issues.append({"code": "DELIMITER", "severity": "INFO", "unit": "table", "detail": f"delimiter detected: {d!r}"})
    return engines, issues
