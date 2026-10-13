"""HTML: BeautifulSoup/lxml vs the standard-library HTMLParser."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from ..model import Block, EngineResult, clean
from . import read_text_file, unavailable

SKIP = {"script", "style", "noscript", "template", "head"}
# Both engines apply the SAME text rule: a line break around these elements, none around inline ones.
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "caption", "thead", "tbody", "tfoot", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "aside", "header", "footer", "nav", "main", "figure", "figcaption", "address", "hr",
              "table", "ul", "ol", "dl", "dt", "dd", "pre", "blockquote", "form", "fieldset"}


def _span(v) -> int:
    try:
        return max(1, min(int(str(v).strip()), 200))
    except (TypeError, ValueError):
        return 1


def _bs4_text(node, out):
    from bs4.element import NavigableString, PreformattedString
    for ch in node.children:
        if isinstance(ch, PreformattedString):          # comments, CDATA, doctype, declarations
            continue
        if isinstance(ch, NavigableString):
            out.append(str(ch))
        elif ch.name in BLOCK_TAGS:
            out.append("\n")
            _bs4_text(ch, out)
            out.append("\n")
        else:
            _bs4_text(ch, out)


def _bs4(html: str) -> EngineResult:
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        soup = BeautifulSoup(html, "html.parser")
    er = EngineResult("bs4")
    tables = []
    for tb in soup.find_all("table"):
        rows = []
        for tr in tb.find_all("tr"):
            if tr.find_parent("table") is not tb:
                continue
            row = []
            for c in tr.find_all(["td", "th"]):
                if c.find_parent("tr") is tr:
                    row += [clean(c.get_text(" "))] + [""] * (_span(c.get("colspan")) - 1)
            rows.append(row)
        tables.append(rows)
    for t in soup(list(SKIP)):
        t.decompose()
    er.unit_meta["page"] = {"images": len(soup.find_all("img"))}
    parts: list = []
    _bs4_text(soup, parts)
    er.blocks.append(Block("page", "text", clean("".join(parts))))
    for k, rows in enumerate(tables, 1):
        er.blocks.append(Block("page", "table", rows=rows, tid=f"t{k}"))
    return er


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.out: list[str] = []
        self.tables: list[list] = []      # finished tables in document order of START
        self.stack: list[dict] = []
        self.imgs = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag == "img":
            self.imgs += 1
        if tag == "table":
            t = {"rows": [], "row": None, "cell": None, "idx": len(self.tables)}
            self.tables.append(t["rows"])
            self.stack.append(t)
        elif tag == "tr" and self.stack:
            self.stack[-1]["row"] = []
            self.stack[-1]["rows"].append(self.stack[-1]["row"])
        elif tag in ("td", "th") and self.stack and self.stack[-1]["row"] is not None:
            self.stack[-1]["cell"] = []
            self.stack[-1]["row"].append((self.stack[-1]["cell"], _span(dict(attrs).get("colspan"))))
        if tag in BLOCK_TAGS and not self.skip:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag in ("td", "th") and self.stack:
            self.stack[-1]["cell"] = None
        elif tag == "tr" and self.stack:
            self.stack[-1]["row"] = None
        elif tag == "table" and self.stack:
            self.stack.pop()
        if tag in BLOCK_TAGS and not self.skip:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(data)
        for t in self.stack:
            if t["cell"] is not None:
                t["cell"].append(data)


def _stdlib(html: str) -> EngineResult:
    p = _P()
    p.feed(html)
    p.close()
    er = EngineResult("htmlparser")
    er.unit_meta["page"] = {"images": p.imgs}
    er.blocks.append(Block("page", "text", clean("".join(p.out))))
    for k, rows in enumerate(p.tables, 1):
        er.blocks.append(Block("page", "table", rows=[[x for c, sp in r for x in [clean(" ".join(c))] + [""] * (sp - 1)] for r in rows], tid=f"t{k}"))
    return er


def extract(path: Path, kind: str):
    html, issues = read_text_file(path)
    engines = []
    for name, fn in (("bs4", _bs4), ("htmlparser", _stdlib)):
        try:
            engines.append(fn(html))
        except ImportError as e:
            issues.append(unavailable(name, f"library missing ({e})"))
    issues.append({"code": "WEB_SOURCE", "severity": "INFO", "unit": "page",
                   "detail": "HTML text is extracted as plain lines (no Markdown structure). Menus/footers may be included."})
    return engines, issues
