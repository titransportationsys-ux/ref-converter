# Reference converter

Converts any reference (PDF design codes and papers, Word, PowerPoint, Excel, web pages, scans and images, BibTeX/RIS libraries, DXF/CSV/JSON data) into content a project can rely on: citable Markdown rule books, CSV/JSON tables, tested Python equations, or a complete ready-made Claude skill.

**Principle:** nothing is accepted on one engine's word. Each unit (page, slide, sheet, table, entry) is read by at least two independent engines and accepted only when every number and the wording agree. Any disagreement is a flag with the evidence; it is never guessed or silently fixed, and output is blocked until a named person or agent decides. Every decision goes in an audit trail.

## Install
- Linux / cloud: automatic (session-start hook), or `python3 -m pip install --pre -r requirements.txt`.
- Windows: double-click `setup.bat` once.
- Optional: poppler (`pdftotext`, third PDF engine), LibreOffice (legacy `.doc/.xls/.ppt/.rtf/.odt`).

## Use it from Claude Code
Open this folder in Claude Code and say, for example: *"Convert `AASHTO_ch3.pdf` to a Markdown rule book."* The `reference-converter` skill drives the `ref-converter` agent (extract, resolve, equations) and the `ref-verifier` agent (blind second transcription and independent audit against the original).

## Use it directly
```bash
python3 -m refconvert extract FILE --work refwork/name     # read with 2+ engines, build flags
python3 -m refconvert status refwork/name                  # what needs a decision, and how
python3 -m refconvert ingest refwork/name --engine vision-A A.yaml   # add a transcription (scans/images)
python3 -m refconvert resolve refwork/name --flag ID --accept pdfplumber --by "Yarah-B" --note "checked vs page"
python3 -m refconvert build refwork/name                   # re-run after editing equations.yaml / sanity.yaml
python3 -m refconvert emit refwork/name --out out --as md,csv,json,code
python3 -m refconvert emit refwork/name --out skills --as skill --skill-name my-ref --description "Use when ..."
python3 -m refconvert verify refwork/name --out out        # re-check files against merged data and raw engines
python3 -m refconvert batch FOLDER_OR_ZIP                  # every file attempted or reported
```
Exit codes: 0 ok, 1 error, 2 decisions needed, 3 unsupported format (message says how to export).

## Layout
- `refconvert/` the package; `reference/conversion-rules.md` the rule book (thresholds, flags, formats).
- `skill/SKILL.md` master skill (copied to `.claude/skills/reference-converter/`); `.claude/agents/` the two agents.
- `tests/` 69 tests incl. real files in every format; `python3 -m pytest -q`.
- `refwork/` working folders (git-ignored): engines, flags, resolutions, equations, merged data.

## What it does not do
Convert figures, charts or diagrams; prove that an engine read a page correctly (the verifier agent audits that against the original); handle DWG/SHP/audio/video (it tells you how to export).
