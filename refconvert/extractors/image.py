"""Images cannot be read reliably by code alone: they are queued for blind double transcription."""
from __future__ import annotations

from pathlib import Path


def extract(path: Path, kind: str):
    from ..model import EngineResult
    er = EngineResult("image-probe")
    unit = f"img:{path.stem}"
    er.unit_meta[unit] = {"images": 1}
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        w = h = 0
    from ..model import Block
    er.blocks.append(Block(unit, "text", ""))
    issues = [{"code": "IMAGE_SOURCE", "severity": "INFO", "unit": unit,
               "detail": f"image {w}x{h}px. Needs two independent transcriptions (see SKILL.md: ingest vision-A / vision-B)."}]
    return [er], issues
