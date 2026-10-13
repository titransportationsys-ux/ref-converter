import json
import zipfile

import fixtures as F
from refconvert.cli import main


def run(*args):
    return main([str(a) for a in args])


def test_exit_codes_extract_status_emit_verify(tmp_path, capsys):
    src = F.make_text(tmp_path / "rules.md")
    w = tmp_path / "w"
    assert run("extract", src, "--work", w) == 0
    assert run("status", w) == 0
    assert run("emit", w, "--out", tmp_path / "o", "--as", "md,json") == 0
    assert run("verify", w, "--out", tmp_path / "o") == 0
    assert run("emit", w, "--out", tmp_path / "o", "--as", "md") == 1            # exists, no --force
    assert run("emit", w, "--out", tmp_path / "o2", "--as", "nonsense") == 1
    out = capsys.readouterr()
    assert "exists" in out.err and "unknown format" in out.err


def test_unsupported_exit_code_and_guidance(tmp_path, capsys):
    (tmp_path / "road.dwg").write_bytes(b"AC1032")
    assert run("extract", tmp_path / "road.dwg", "--work", tmp_path / "w") == 3
    assert "DXF" in capsys.readouterr().err


def test_blocking_flags_give_exit_2_and_guidance(tmp_path, capsys):
    png = F.make_png(tmp_path / "scan.png")
    w = tmp_path / "w"
    assert run("extract", png, "--work", w) == 2
    assert run("emit", w, "--out", tmp_path / "o") == 1
    assert run("status", w) == 2
    text = capsys.readouterr().out
    assert "NEEDS_TRANSCRIPTION" in text and "how to fix" in text and "blind" in text.lower() or "transcri" in text.lower()


def test_ingest_resolve_cli_round_trip(tmp_path):
    png = F.make_png(tmp_path / "scan.png")
    w = tmp_path / "w"
    run("extract", png, "--work", w)
    for eng in ("vision-A", "vision-B"):
        f = tmp_path / f"{eng}.yaml"
        f.write_text("units:\n  'img:scan': 'Lane width 3.65 m'\n")
        run("ingest", w, "--engine", eng, f)
    assert run("status", w) == 0
    assert run("resolve", w, "--code", "SINGLE_SOURCE", "--waive", "--note", "x", "--by", "me") == 1      # nothing selected -> error


def test_resolve_requires_who_and_why(tmp_path):
    png = F.make_png(tmp_path / "scan.png")
    w = tmp_path / "w"
    run("extract", png, "--work", w)
    assert run("resolve", w, "--code", "NEEDS_TRANSCRIPTION", "--override-text", "Lane 3.65 m") == 1        # no --by, no --note
    assert run("resolve", w, "--code", "NEEDS_TRANSCRIPTION", "--override-text", "Lane 3.65 m", "--by", "Yarah-B", "--note", "typed from scan p1") == 0
    assert run("status", w) == 0
    rs = json.loads((w / "resolutions.json").read_text())
    assert rs[0]["by"] == "Yarah-B"


def test_batch_directory_and_zip_with_slip_attempt(tmp_path, capsys):
    d = tmp_path / "refs"
    d.mkdir()
    F.make_text(d / "a.md")
    F.make_csv(d / "b.csv")
    (d / "c.dwg").write_bytes(b"AC1032")
    assert run("batch", d, "--work-root", tmp_path / "wr") == 2
    out = capsys.readouterr().out
    assert "ok  a.md" in out and "ok  b.csv" in out and "SKIPPED  c.dwg" in out
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(d / "a.md", "a.md")
        zf.writestr("../../escape.md", "evil 1")
    run("batch", z, "--work-root", tmp_path / "wz")
    assert not (tmp_path.parent / "escape.md").exists() and not (tmp_path / "escape.md").exists()
    assert "unsafe" in capsys.readouterr().out


def test_status_units_and_inspect(tmp_path, capsys):
    src = F.make_text(tmp_path / "r.md")
    run("extract", src, "--work", tmp_path / "w")
    capsys.readouterr()
    assert run("status", tmp_path / "w", "--units") == 0
    assert "l1-4" in capsys.readouterr().out
    assert run("inspect", src) == 0
