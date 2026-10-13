import csv
import json
import subprocess
import sys

import pytest

from conftest import codes
from refconvert import emit, equations, pipeline, verify
from refconvert.model import ExtractError

SRC = ("Stopping sight distance\n"
       "SSD = 0.278Vt + V2/(254((a/9.81) + G))   (3-1)\n"
       "Example: V = 100 km/h, t = 2.5 s, a = 3.4 m/s2: SSD = 183.1 m\n")

SPEC = """
equations:
  - name: stopping_sight_distance
    unit: l1-3
    expr: "0.278*V*t + V**2/(254*(a/9.81 + G))"
    vars:
      V: {unit: km/h, min: 30, max: 140}
      t: {unit: s}
      a: {unit: m/s2}
      G: {unit: ""}
    result: {unit: m}
    source_text: "SSD = 0.278Vt + V2/(254((a/9.81) + G))"
    ignore_source_numbers: [3, 1]
    examples:
      - {inputs: {V: 100, t: 2.5, a: 3.4, G: 0}, expected: 183.1, decimals: 1, assumed: [G]}
"""


@pytest.fixture
def eqwork(tmp_path):
    src = tmp_path / "geo.md"
    src.write_text(SRC, encoding="utf-8")
    w = pipeline.extract(src, tmp_path / "w_geo")
    return w


def set_spec(w, text):
    w.equations_path.write_text(text, encoding="utf-8")
    pipeline.rebuild(w)


def test_correct_equation_passes_all_cross_checks(eqwork):
    set_spec(eqwork, SPEC)
    assert codes(eqwork, "open", "BLOCK") == []
    assert eqwork.merged()["equations"][0]["status"] == "verified"


def test_transposed_constant_is_caught_two_ways(eqwork):
    set_spec(eqwork, SPEC.replace("0.278*V*t", "0.287*V*t"))
    found = codes(eqwork, "open", "BLOCK")
    assert "EQ_LITERAL_MISMATCH" in found            # expr literal != printed equation
    assert "EQ_EXAMPLE_FAIL" in found                # and the printed worked example no longer reproduces


def test_wrong_printed_example_is_caught(eqwork):
    set_spec(eqwork, SPEC.replace("expected: 183.1", "expected: 190.0"))
    assert {"EQ_EXAMPLE_FAIL", "EQ_EXAMPLE_NOT_IN_SOURCE"} <= set(codes(eqwork, "open", "BLOCK"))


def test_input_not_in_source_must_be_declared_assumed(eqwork):
    set_spec(eqwork, SPEC.replace("V: 100", "V: 90").replace("assumed: [G]", "assumed: []"))
    assert "EQ_EXAMPLE_NOT_IN_SOURCE" in codes(eqwork, "open", "BLOCK")


def test_source_text_must_exist_in_the_page(eqwork):
    set_spec(eqwork, SPEC.replace("0.278Vt + V2/(254((a/9.81) + G))", "0.278Vt + V2/(254((a/9.81) + G)) + 7"))
    assert "EQ_SOURCE_NUMBER_NOT_FOUND" in codes(eqwork, "open", "BLOCK")


@pytest.mark.parametrize("expr", ["__import__('os').system('echo hi')", "V.__class__", "[x for x in V]", "V**V**V", "lambda: 1", "open('f')"])
def test_unsafe_expressions_are_rejected(expr):
    with pytest.raises(equations.ExprError):
        equations.parse_expr(expr)


def test_safe_eval_matches_math():
    assert equations.evaluate("sqrt(V)+max(1,2)", {"V": 16}) == 6.0


def test_equation_candidates_are_reported_so_none_is_forgotten(eqwork):
    assert "EQUATION_CANDIDATE" in codes(eqwork)


def test_emit_refuses_while_blocking_flags_open(tmp_path):
    from fixtures import make_png
    w = pipeline.extract(make_png(tmp_path / "s.png"), tmp_path / "w_png")
    with pytest.raises(ExtractError, match="blocking"):
        emit.guard(w, False)
    emit.guard(w, True)


def test_emit_all_verify_and_tamper_detection(eqwork, tmp_path):
    set_spec(eqwork, SPEC)
    out = tmp_path / "out"
    review = emit.guard(eqwork, False)
    md = emit.emit_md(eqwork, review, out, False)
    emit.emit_json(eqwork, review, out, False)
    emit.emit_csv(eqwork, out, False)
    emit.emit_code(eqwork, review, out, False)
    res = verify.run(eqwork, out)
    assert res["status"] == "PASS", res
    assert not list(out.rglob("__pycache__")), "verify must not leave bytecode caches in the deliverable"
    assert any(c["name"] == "generated equation tests pass" and c["status"] == "PASS" for c in res["checks"])
    # tamper: flip one digit in the Markdown
    txt = md.read_text(encoding="utf-8")
    assert "183.1" in txt
    md.write_text(txt.replace("183.1", "183.7"), encoding="utf-8")
    bad = verify.run(eqwork, out)
    assert bad["status"] == "FAIL" and any("183" in c["detail"] for c in bad["checks"] if c["status"] == "FAIL")
    # ... and the generated test would catch a wrong constant in the code
    mod = next(out.glob("eq_*.py"))
    mod.write_text(mod.read_text().replace("0.278", "0.287"))
    p = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(next(out.glob("test_eq_*.py")).name)],
                       cwd=out, capture_output=True, text=True, env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode != 0


def test_emit_will_not_overwrite_without_force(eqwork, tmp_path):
    review = emit.guard(eqwork, False)
    emit.emit_md(eqwork, review, tmp_path / "o", False)
    with pytest.raises(ExtractError, match="exists"):
        emit.emit_md(eqwork, review, tmp_path / "o", False)
    emit.emit_md(eqwork, review, tmp_path / "o", True)


def test_source_change_after_extraction_blocks_emit(eqwork):
    from pathlib import Path
    Path(eqwork.manifest()["source"]).write_text(SRC + "extra 5\n")
    with pytest.raises(ExtractError, match="changed"):
        emit.guard(eqwork, False)


def test_csv_round_trip_and_minus_sign_normalised(tmp_path):
    src = tmp_path / "t.csv"
    src.write_text("a,b\n−5,x\n2,3\n", encoding="utf-8")
    w = pipeline.extract(src, tmp_path / "w_t")
    out = tmp_path / "o"
    emit.emit_csv(w, out, False)
    rows = list(csv.reader(open(next((out / "tables").glob("table__t1.csv")), encoding="utf-8")))
    assert rows[1] == ["-5", "x"]                       # U+2212 -> '-' so pandas/float() can read it
    assert verify.run(w, out)["status"] == "PASS"


def test_skill_emit_creates_a_complete_skill_folder(eqwork, tmp_path):
    set_spec(eqwork, SPEC)
    review = emit.guard(eqwork, False)
    p = emit.emit_skill(eqwork, review, tmp_path / "skills", "geo-design-ref", "Use when you need stopping sight distance rules from the geometric design reference.", False)
    root = p.parent
    assert (root / "reference" / "geo.md").exists() and (root / "reference" / "geo.json").exists()
    assert list((root / "scripts").glob("eq_*.py")) and list((root / "scripts").glob("test_eq_*.py"))
    body = p.read_text()
    assert body.startswith("---\nname: geo-design-ref\ndescription: Use when") and "Never estimate" in body
    assert verify.run(eqwork, root)["status"] == "PASS"
    with pytest.raises(ExtractError):
        emit.emit_skill(eqwork, review, tmp_path / "s2", "Bad Name", "d", False)


def test_unverified_emit_carries_a_loud_banner(tmp_path):
    from fixtures import make_png
    w = pipeline.extract(make_png(tmp_path / "s.png"), tmp_path / "w_png")
    review = emit.guard(w, True)
    txt = emit.emit_md(w, review, tmp_path / "o", False).read_text(encoding="utf-8")
    assert "UNVERIFIED OUTPUT" in txt and "status: UNVERIFIED" in txt
