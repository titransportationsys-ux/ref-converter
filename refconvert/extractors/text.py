"""Plain text / Markdown: verbatim and lossless, chunked by line range for citations."""
from __future__ import annotations

from pathlib import Path

from ..model import Block, EngineResult, ExtractError
from . import read_text_file

CHUNK = 80


def extract(path: Path, kind: str):
    text, issues = read_text_file(path)
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = (norm[:-1] if norm.endswith("\n") else norm).split("\n")     # editor-style line numbers
    if not any(ln.strip() for ln in lines):
        raise ExtractError(f"{path.name} is empty")
    er = EngineResult("verbatim", lossless=True)
    for start in range(0, len(lines), CHUNK):
        chunk = lines[start:start + CHUNK]
        u = f"l{start + 1}-{start + len(chunk)}"
        er.blocks.append(Block(u, "text", "\n".join(chunk)))
    return [er], issues
