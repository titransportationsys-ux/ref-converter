"""Data model and text normalisation shared by every module.

Two kinds of normalisation exist and must not be mixed up:

* ``clean``     - light, lossless-in-meaning clean-up for DISPLAY and OUTPUT
                  (keeps superscripts, minus signs, Arabic digits as written).
* ``cmp_text``  - aggressive folding used ONLY to COMPARE engines
                  (NFKC, Arabic-Indic digits -> ASCII, minus -> '-').
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

VERSION = "0.1.0"

# Number token: optional sign (only when not glued to a preceding word/sign),
# digits, optional .,-separated groups, optional exponent. Clause numbers such
# as 3.5.2 stay one token.
NUM_RE = re.compile(r"(?<![\d.,])(?:(?<![\w\-+])[-+])?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?")

_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
})
_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGIT_FOLD.update({0x066B: ".", 0x066C: ",", 0x2212: "-"})


def clean(s: str) -> str:
    """Display clean-up: NFC, ligatures, soft hyphens, control chars, spaces."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).translate(_LIGATURES)
    s = s.replace("­", "").replace("\x00", "").replace("​", "")
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n").replace("\f", "\n")
    s = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", s)  # de-hyphenate line breaks (letters only)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def cmp_text(s: str) -> str:
    """Aggressive folding for comparing two engines' output."""
    s = unicodedata.normalize("NFKC", clean(s)).translate(_DIGIT_FOLD)
    return re.sub(r"\s+", " ", s).strip()


def squeeze(s: str) -> str:
    """Letters and digits only, lower-case (Unicode aware, so Arabic survives)."""
    return re.sub(r"[\W_]+", "", cmp_text(s).lower())


def canon_number(tok: str) -> str:
    t = tok
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    elif re.fullmatch(r"[-+]?\d+,\d+", t):
        t = t.replace(",", ".")
    return t.lstrip("+")


def numbers(s: str) -> Counter:
    """Multiset of canonical number tokens in ``s`` (after comparison folding)."""
    return Counter(canon_number(m) for m in NUM_RE.findall(cmp_text(s)))


def num_diff(a: Counter, b: Counter, limit: int = 12) -> str:
    only_a = sorted((a - b).elements())
    only_b = sorted((b - a).elements())
    parts = []
    if only_a:
        parts.append("only in first: " + ", ".join(only_a[:limit]) + (" ..." if len(only_a) > limit else ""))
    if only_b:
        parts.append("only in second: " + ", ".join(only_b[:limit]) + (" ..." if len(only_b) > limit else ""))
    return "; ".join(parts) or "numbers equal"


def dice(a: str, b: str, n: int = 4) -> float:
    """Order-insensitive text similarity on squeezed character n-grams (0..1)."""
    sa, sb = squeeze(a), squeeze(b)
    if not sa and not sb:
        return 1.0
    if min(len(sa), len(sb)) < n:
        return 1.0 if sa == sb else 0.0
    ca = Counter(sa[i:i + n] for i in range(len(sa) - n + 1))
    cb = Counter(sb[i:i + n] for i in range(len(sb) - n + 1))
    return 2 * sum((ca & cb).values()) / (sum(ca.values()) + sum(cb.values()))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(s: str, n: int = 8) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def unit_label(unit: str) -> str:
    """Human-readable citation for a unit key (p3 -> 'p. 3')."""
    m = re.fullmatch(r"p(\d+)", unit)
    if m:
        return f"p. {m.group(1)}"
    m = re.fullmatch(r"s(\d+)", unit)
    if m:
        return f"slide {m.group(1)}"
    for pre, name in (("sheet:", "sheet "), ("bib:", "entry "), ("ris:", "entry "), ("img:", "image ")):
        if unit.startswith(pre):
            return name + unit[len(pre):]
    m = re.fullmatch(r"l(\d+)-(\d+)", unit)
    if m:
        return f"lines {m.group(1)}-{m.group(2)}"
    return {"doc": "document", "page": "page", "table": "table", "json": "data", "modelspace": "modelspace"}.get(unit, unit)


def unit_title(unit: str) -> str:
    """Heading text for a unit (p3 -> 'Page 3')."""
    m = re.fullmatch(r"([ps])(\d+)", unit)
    if m:
        return ("Page " if m.group(1) == "p" else "Slide ") + m.group(2)
    lab = unit_label(unit)
    return lab[0].upper() + lab[1:]


@dataclass
class Block:
    unit: str                      # citation unit: p3, s2, sheet:Name, doc, ...
    kind: str                      # "text" | "table"
    text: str = ""
    rows: list = field(default_factory=list)   # table rows (list of list of str)
    tid: str = ""                  # table id inside the unit (t1, t2, ...)
    meta: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Block":
        return Block(d["unit"], d["kind"], d.get("text", ""), d.get("rows", []), d.get("tid", ""), d.get("meta", {}))


@dataclass
class EngineResult:
    engine: str
    blocks: list = field(default_factory=list)
    lossless: bool = False          # verbatim read: one engine is enough
    canon_numbers: bool = False     # compare numeric cells by value, not text
    version: str = ""
    unit_meta: dict = field(default_factory=dict)   # unit -> {"images": n, "graphics": n}
    notes: list = field(default_factory=list)       # [{"code","severity","unit","detail"}]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "EngineResult":
        return EngineResult(
            d["engine"], [Block.from_dict(b) for b in d.get("blocks", [])], d.get("lossless", False),
            d.get("canon_numbers", False), d.get("version", ""), d.get("unit_meta", {}), d.get("notes", []))


@dataclass
class Flag:
    id: str
    code: str
    severity: str                  # BLOCK | WARN | INFO
    unit: str
    detail: str
    fp: str                        # fingerprint of the detail; a resolution only applies if it still matches
    tid: str = ""
    status: str = "open"           # open | resolved | info
    resolution: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ExtractError(Exception):
    """The file is supported but could not be read (corrupt, encrypted, ...)."""


class Unsupported(Exception):
    """The format is not supported; the message tells the user what to do."""
