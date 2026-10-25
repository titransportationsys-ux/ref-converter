"""Symbol-font bullets, superscript marks and footnote linking (found on AASHTO Ch. 6)."""
import csv
import json
import re

import fixtures as F
from conftest import codes
from refconvert import emit, pipeline, verify

BULLETS = ("BT /F1 11 Tf 100 700 Td (Widths may be considered:) Tj ET "
           "BT /F2 11 Tf 114 680 Td (y) Tj ET BT /F1 11 Tf 130 680 Td (paint-striped, 2 to 4 ft wide;) Tj ET "
           "BT /F2 11 Tf 114 664 Td (y) Tj ET BT /F1 11 Tf 130 664 Td (raised curb, 10 to 16 ft wide.) Tj ET "
           "BT /F1 11 Tf 100 640 Td (The variable y and a typo are plain letters.) Tj ET")


def test_symbol_font_bullet_becomes_a_list_item_and_real_y_is_untouched(make, tmp_path):
    _, w = make("pdf", pages=[{"raw": BULLETS}])
    text = w.merged()["units"][0]["blocks"][0]["text"]
    assert "• paint-striped, 2 to 4 ft wide;" in text and "• raised curb" in text
    assert "variable y and a typo" in text                                  # a real letter y stays a y
    assert "SYMBOL_FONT_MAPPED" in codes(w)
    assert codes(w, "open", "BLOCK") == [], w.review()["flags"]
    md = emit.emit_md(w, emit.guard(w, False), tmp_path / "o", False).read_text(encoding="utf-8")
    assert "- • paint-striped, 2 to 4 ft wide;" in md and "- • raised curb" in md
    assert not re.search(r"(?m)^y\s*$", md)
    assert verify.run(w, tmp_path / "o")["status"] == "PASS"


def test_unknown_symbol_is_labelled_and_warned_never_guessed(make):
    raw = "BT /F1 11 Tf 100 700 Td (Rule) Tj ET BT /F2 11 Tf 114 680 Td (q) Tj ET BT /F1 11 Tf 130 680 Td (see text) Tj ET"
    _, w = make("pdf", pages=[{"raw": raw}])
    plumber = next(e for e in w.engines() if e.engine == "pdfplumber")
    assert "[symbol:wingdings:U+0071]" in plumber.blocks[0].text           # labelled marker, never silently a letter
    assert "SYMBOL_UNKNOWN" in codes(w, "open", "WARN")                    # and a person is told


TABLE = [["Speed", "Under 400"], ["20", ("20", "a")], ["25", ("22", "b")]]
FOOT = ("BT /F1 6 Tf 56 400 Td (a) Tj ET BT /F1 9 Tf 68 397 Td (An 18-ft minimum width may be used for low volumes.) Tj ET "
        "BT /F1 6 Tf 56 386 Td (b) Tj ET BT /F1 9 Tf 68 383 Td (Consider 24 ft where trucks are present.) Tj ET "
        "BT /F1 9 Tf 56 360 Td (6.2.2.2 Number of Lanes) Tj ET")


def test_superscript_marks_are_explicit_and_footnotes_are_linked(make, tmp_path):
    _, w = make("pdf", pages=[{"lines": ["Table 6-5 widths"], "table": TABLE, "raw": FOOT}])
    tb = next(b for b in w.merged()["units"][0]["blocks"] if b["kind"] == "table")
    assert tb["rows"][1][1] == "20^a" and tb["rows"][2][1] == "22^b"      # never a bare 20a
    assert [f["mark"] for f in tb["meta"]["footnotes"]] == ["a", "b"]
    assert tb["meta"]["footnotes"][0]["text"].startswith("An 18-ft minimum width")
    assert "FOOTNOTE_NOT_FOUND" not in codes(w)
    if codes(w, "open", "BLOCK"):
        for f in [f for f in w.review()["flags"] if f["status"] == "open" and f["severity"] == "BLOCK"]:
            pipeline.resolve(w, [f["id"]], "accept", "pdfplumber", None, None, "checked against the page", "test")
    out = tmp_path / "o"
    review = emit.guard(w, False)
    md = emit.emit_md(w, review, out, False).read_text(encoding="utf-8")
    assert "| 20 | 20^a |" in md and "Footnotes (as printed under the table):" in md and "- ^a An 18-ft minimum width" in md
    emit.emit_csv(w, out, False)
    emit.emit_json(w, review, out, False)
    foot = list(csv.reader(open(next((out / "tables").glob("*.footnotes.csv")), encoding="utf-8")))
    assert foot[0] == ["mark", "text"] and foot[1][0] == "a"
    assert json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))["units"][0]["blocks"][-1]["footnotes"][1]["mark"] == "b"
    assert verify.run(w, out)["status"] == "PASS"


def test_missing_footnote_text_is_warned(make):
    _, w = make("pdf", pages=[{"lines": ["Table 6-5 widths"], "table": TABLE}])
    assert "FOOTNOTE_NOT_FOUND" in codes(w, "open", "WARN")


def test_superscript_digit_after_digit_blocks_because_engines_disagree_on_meaning(make):
    raw = ("BT /F1 11 Tf 100 700 Td (Flow of 10) Tj ET BT /F1 6 Tf 150.7 704 Td (3) Tj ET "
           "BT /F1 11 Tf 160 700 Td ( vehicles per day) Tj ET")
    _, w = make("pdf", pages=[{"raw": raw}])
    assert "SUPERSCRIPT_DIGIT" in codes(w, "open", "BLOCK")
