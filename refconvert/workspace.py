"""A work folder holds everything about ONE source reference.

    manifest.json     source path, sha256, kind, engines, issues
    engines/*.json    one file per engine (machine engines + ingested transcriptions)
    resolutions.json  every human/verifier decision on a flag (audit trail)
    equations.yaml    equations transcribed from the source (optional, authored by Claude)
    sanity.yaml       range rules for this reference (optional)
    merged.json       result of the last build (accepted content per unit)
    review.json       flags of the last build
    verification.json result of the last 'verify'
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .model import EngineResult


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._")
    return (s or "reference")[:60]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Work:
    def __init__(self, root: Path):
        self.root = Path(root)

    # --- paths
    @property
    def manifest_path(self): return self.root / "manifest.json"
    @property
    def engines_dir(self): return self.root / "engines"
    @property
    def resolutions_path(self): return self.root / "resolutions.json"
    @property
    def equations_path(self): return self.root / "equations.yaml"
    @property
    def sanity_path(self): return self.root / "sanity.yaml"
    @property
    def merged_path(self): return self.root / "merged.json"
    @property
    def review_path(self): return self.root / "review.json"
    @property
    def verification_path(self): return self.root / "verification.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    # --- json helpers
    @staticmethod
    def _read(p: Path, default):
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    @staticmethod
    def _write(p: Path, obj):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- manifest
    def manifest(self) -> dict:
        if not self.exists():
            raise FileNotFoundError(f"{self.root} is not a refconvert work folder (no manifest.json). Run 'extract' first.")
        return self._read(self.manifest_path, {})

    def save_manifest(self, m: dict):
        self._write(self.manifest_path, m)

    # --- engines
    def save_engine(self, er: EngineResult):
        self._write(self.engines_dir / f"{er.engine}.json", er.to_dict())

    def engines(self) -> list[EngineResult]:
        order = self.manifest().get("engine_order", [])
        out = []
        for name in order:
            p = self.engines_dir / f"{name}.json"
            if p.exists():
                out.append(EngineResult.from_dict(self._read(p, {})))
        return out

    # --- resolutions
    def resolutions(self) -> list[dict]:
        return self._read(self.resolutions_path, [])

    def add_resolution(self, r: dict):
        rs = [x for x in self.resolutions() if x["flag"] != r["flag"]]
        rs.append(r)
        self._write(self.resolutions_path, rs)

    # --- results
    def save_merged(self, m): self._write(self.merged_path, m)
    def merged(self): return self._read(self.merged_path, {})
    def save_review(self, r): self._write(self.review_path, r)
    def review(self): return self._read(self.review_path, {})
    def save_verification(self, v): self._write(self.verification_path, v)
    def verification(self): return self._read(self.verification_path, {})
