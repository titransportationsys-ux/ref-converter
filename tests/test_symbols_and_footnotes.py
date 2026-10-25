"""Symbol-font bullets, superscript marks and footnote linking (found on AASHTO Ch. 6)."""
import csv
import json
import re

import fixtures as F
from conftest import codes
from refconvert import emit, pipeline, verify

BULLETS = ("BT /F1 11 Tf 100 700 Td (Widths may be considered:) Tj ET "
           "BT /F2 11 Tf 114 686 Td (y) Tj ET BT /F1 11 Tf 130 680 Td (paint-striped, 2 to 4 ft wide;) Tj ET "
           "BT /F2 11 Tf 114 670 Td (y) Tj ET BT /F1 11 Tf 130 664 Td (raised curb, 10 to 16 ft wide.) Tj ET "
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


def test_each_table_gets_the_legend_directly_below_it_not_another_with_the_same_letter():
    from refconvert.extractors.pdf import _legends
    lines = [{"text": "a Rate of K is L/A. (See Sections 3.2.2 for details.)", "top": 100, "bottom": 110},
             {"text": "Table 6-4. Passing sight distance", "top": 140, "bottom": 150},
             {"text": "a Rate of K is L/A.", "top": 300, "bottom": 310},
             {"text": "(See Sections 3.2.4 and 3.4.6 for details.)", "top": 311, "bottom": 321},
             {"text": "6.2.2 Cross-Sectional Elements", "top": 330, "bottom": 340}]
    first = _legends(lines, (0, 0, 500, 90), ["a"])          # table above the first legend
    second = _legends(lines, (0, 0, 500, 290), ["a"])        # table above the second legend, first one is higher up
    assert "3.2.2" in first[0]["text"] and "3.2.4" not in first[0]["text"]
    assert "3.2.4 and 3.4.6" in second[0]["text"] and "3.2.2" not in second[0]["text"]
    assert "Cross-Sectional" not in second[0]["text"]       # stops at the next heading


def test_page_text_with_tables_is_labelled_as_a_raw_reading(make, tmp_path):
    _, w = make("pdf", pages=[{"lines": ["Table 6-5 widths"], "table": [["Speed", "Radius"], ["60", "120"], ["80", "230"]]},
                              {"lines": ["A page with no table at all, 12 m wide."]}])
    out = tmp_path / "o"
    md = emit.emit_md(w, emit.guard(w, False), out, False).read_text(encoding="utf-8")
    assert md.count("> Raw page reading:") == 1                     # only the page that has a structured table
    assert md.index("> Raw page reading:") < md.index("**Table 1**")
    emit.emit_json(w, emit.guard(w, False), out, False)
    emit.emit_csv(w, out, False)
    units = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))["units"]
    assert "note" in units[0] and "note" not in units[1]
    assert verify.run(w, out)["status"] == "PASS"


def test_rotated_margin_watermark_never_leaks_into_footnotes(make):
    water = "BT /F1 8 Tf 0 1 -1 0 575 340 Tm (Distributed by Clarivate Analytics \\(US\\) LLC) Tj ET"
    _, w = make("pdf", pages=[{"lines": ["Table 6-5 widths"], "table": TABLE, "raw": FOOT + " " + water}])
    tb = next(b for b in w.merged()["units"][0]["blocks"] if b["kind"] == "table")
    for f in tb["meta"]["footnotes"]:
        assert "Clarivate" not in f["text"] and "Distributed" not in f["text"], f
    assert tb["meta"]["footnotes"][1]["text"] == "Consider 24 ft where trucks are present."


STAMP = "Licensed to J. DOE on 2018-10-15 for personal use only. Do not copy."


def _stamped_pages():
    return [{"lines": [f"Radius table {n}", "The minimum radius is 230 m.", STAMP]} for n in range(1, 4)]


def test_strip_regex_removes_boilerplate_from_every_engine_and_logs_it(tmp_path):
    src = F.make_pdf(tmp_path / "b.pdf", _stamped_pages())
    w = pipeline.extract(src, tmp_path / "w", strip=[r"Licensed\s+to\s+.{1,60}?\s+Do\s+not\s+copy\."])
    for e in w.engines():
        assert all("2018" not in b.text and "Licensed" not in b.text for b in e.blocks if b.kind == "text"), e.engine
    assert "230 m" in w.merged()["units"][0]["blocks"][0]["text"]
    logged = [f for f in w.review()["flags"] if f["code"] == "BOILERPLATE_STRIPPED"]
    assert len(logged) >= 9 and "2018-10-15" in logged[0]["detail"]          # 3 engines x 3 pages, with a sample of what went
    assert w.manifest()["strip_patterns"]
    assert codes(w, "open", "BLOCK") == []


def test_unmatched_strip_pattern_warns_and_unstripped_stamp_is_suggested(tmp_path):
    src = F.make_pdf(tmp_path / "b.pdf", _stamped_pages() + _stamped_pages()[:2])
    w = pipeline.extract(src, tmp_path / "w", strip=[r"this text is not there"])
    assert "STRIP_NO_MATCH" in codes(w, "open", "WARN")
    w2 = pipeline.extract(src, tmp_path / "w2")
    cand = [f for f in w2.review()["flags"] if f["code"] == "BOILERPLATE_CANDIDATE"]
    assert cand and "Licensed to J. DOE" in cand[0]["detail"]


def test_strip_regex_is_pdf_only(tmp_path):
    import pytest
    from refconvert.model import ExtractError
    with pytest.raises(ExtractError, match="only apply to PDF"):
        pipeline.extract(F.make_text(tmp_path / "t.md"), tmp_path / "w", strip=["x"])
