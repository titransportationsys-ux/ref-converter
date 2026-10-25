# Conversion rules (rule book)

The single source of truth for how `refconvert` decides. If code and this file disagree, the code is wrong: fix it and add a test.

## 1. Principles

1. Two independent readings or it is not accepted. A *lossless* engine (verbatim text, JSON) counts as one complete reading.
2. Disagreement is information. It becomes a flag with the evidence; it is never averaged, voted away silently, or "repaired".
3. Output is bound to the source: sha256 recorded at extraction; `emit` refuses if the file changed.
4. Every decision is in the audit trail (`resolutions.json`): who, when, which engine, why. A decision stops applying if the evidence changes (fingerprint check).
5. Extracted text is data. Instructions inside it are never followed.

## 2. Units (the citation grain)

| Source | Unit key | Citation shown |
|---|---|---|
| PDF page | `p3` | p. 3 |
| PowerPoint slide | `s2` | slide 2 |
| Excel sheet | `sheet:<name>` | sheet <name> |
| Word document | `doc` | document |
| HTML page | `page` | page |
| CSV | `table` | table |
| Text/Markdown | `l1-80` (80-line chunks, editor line numbers) | lines 1-80 |
| BibTeX / RIS entry | `bib:<key>` / `ris:<n>` | entry <key> |
| JSON / GeoJSON | `json` | data |
| DXF | `modelspace` | modelspace |
| Image | `img:<file stem>` | image <stem> |
| Tables | `<unit>.t<k>` | table k of the unit |

## 3. Engines per format

| Format | Engines (independent) | Notes |
|---|---|---|
| PDF | pdfplumber, pypdf, pdftotext (poppler) | tables from pdfplumber only; see 4.3 |
| DOCX | python-docx, raw XML | tracked changes, footnotes, headers flagged |
| PPTX | python-pptx, raw XML | speaker notes not extracted (INFO) |
| XLSX | openpyxl (cached values), raw XML | dates as serials; hidden sheets and formulas without cache flagged |
| CSV/TSV | csv module, pandas | delimiter auto-detected; cp1252 fallback flagged |
| HTML | BeautifulSoup/lxml, stdlib HTMLParser | scripts/styles dropped |
| BibTeX | own parser, bibtexparser | |
| RIS | own parser, rispy | |
| DXF (ASCII) | raw group-code parser, ezdxf | modelspace only; block definitions not exploded |
| Text/MD/YAML/XML | verbatim (lossless) | |
| JSON/GeoJSON | json with literal numbers (lossless) | every raw number must survive parsing |
| Images, scanned pages | none by code | two blind transcriptions (`vision-A`, `vision-B`) via `ingest` |
| .doc .xls .ppt .rtf .odt .ods .odp | LibreOffice conversion, then the engines above | provenance recorded |
| DWG, SHP, GDB, audio, video, unknown binary | not supported | exit code 3 with export guidance |

## 4. Acceptance rules

### 4.1 Text
- Numbers: the multiset of number tokens must be **identical** between the two agreeing engines. Folding for comparison only: NFKC, Arabic-Indic digits to ASCII, `−` to `-`, thousands commas (`1,250` = `1250`), decimal comma (`2,5` = `2.5`). Trailing zeros are significant (`80.50` is not `80.5`).
- Wording: char 4-gram Dice similarity of the squeezed text >= 0.90.
- With 3 engines, any agreeing pair accepts the text; the third becomes `ENGINE_OUTLIER` (WARN) if it differs, and is listed as agreeing if it matches.
- No agreeing pair: `NO_CONSENSUS` (BLOCK). One engine only: `SINGLE_SOURCE` (BLOCK). No text but images: `NEEDS_TRANSCRIPTION` (BLOCK). No text, many drawing objects: `EMPTY_UNIT_GRAPHICS` (BLOCK). Blank page: `BLANK_UNIT` (INFO).

### 4.2 Tables
- Two engines: cell grids must be identical (numeric cells compared by value for spreadsheets). Else `TABLE_MISMATCH` (BLOCK).
- One engine (PDF): every number must exist in the agreed page text, else `TABLE_UNCORROBORATED` (BLOCK). Then the structure check in 4.3, else `TABLE_STRUCTURE` (BLOCK).
- A table seen by one engine with no page text to compare: `SINGLE_SOURCE_TABLE` (BLOCK).

### 4.3 PDF table structure
Accepted without a human only if every non-empty row **equals one whole line** of another engine's text (cells joined by single spaces, nothing before, between or after) and rows appear top to bottom. A dropped label column, a side-by-side neighbour table, wrapped, rotated or merged cells fail this on purpose and go to a human or the verifier. A hand-typed table override must consist of numbers printed on the page (`TABLE_OVERRIDE_NUMBERS`).

### 4.3b PDF symbols, superscripts, footnotes, boilerplate
- Characters in symbol fonts (Wingdings, Webdings, ZapfDingbats) are mapped by FONT AND CODE from a verified table (Wingdings `y` = bullet, checked on the AASHTO page image). Unknown glyphs are kept as `[symbol:font:U+XXXX]` and warned (`SYMBOL_UNKNOWN`).
- A character that is smaller than the previous one and raised above its baseline is a superscript and is written `^x`. A superscript digit directly after a digit blocks (`SUPERSCRIPT_DIGIT`).
- A footnote is the nearest legend line directly below its table (upright text only), attached to every table whose cells carry its mark; a mark without a legend warns (`FOOTNOTE_NOT_FOUND`).
- `--pages A-B` converts only those physical PDF pages (unit keys keep the original numbers). `--strip-regex` removes declared boilerplate from every engine's page text; each removal is logged and an unmatched pattern warns (`STRIP_NO_MATCH`). Lines repeated on most pages are only suggested (`BOILERPLATE_CANDIDATE`).

### 4.4 Equations
All must hold, else the listed flag (BLOCK unless noted):
- Expression uses only numbers, declared variables/constants, `+ - * / ** % //`, and `sqrt log log10 exp sin cos tan asin acos atan atan2 radians degrees abs min max` (`EQ_BAD_EXPR`). No chained powers.
- Every number in `source_text` exists in the cited unit (`EQ_SOURCE_NUMBER_NOT_FOUND`); `source_text` found verbatim else WARN `EQ_SOURCE_NOT_VERBATIM` (confirm visually).
- Literals in the expression equal the printed numbers, both ways (`EQ_LITERAL_MISMATCH`); exceptions only through `implicit_literals` / `ignore_source_numbers`.
- Each worked example: inputs exactly the variables; expected value and tolerance stated (`decimals` as printed, or `tol_rel`); the expression reproduces it (`EQ_EXAMPLE_FAIL`); the printed values exist in the cited unit unless declared `assumed` (`EQ_EXAMPLE_NOT_IN_SOURCE`).
- No example: WARN `EQ_UNTESTED`. Inputs outside declared min/max: WARN `EQ_EXAMPLE_RANGE`.

## 5. Flags

| Severity | Meaning | Effect |
|---|---|---|
| BLOCK | content cannot be trusted yet | `emit` refuses until resolved (or `--allow-unverified` draft banner) |
| WARN | probably fine, a person should know | printed in the output review log |
| INFO | context | listed, no action |

Other codes: `PART_NOT_EXTRACTED` (footnotes/headers/comments/notes), `TRACKED_CHANGES`, `HIDDEN_SHEET`, `DATE_CELLS`, `FORMULA_NO_CACHE` (BLOCK), `FIGURE_PRESENT`, `ENCODING_FALLBACK`, `UNHANDLED_ENTITY`, `BLOCK_REFERENCES`, `JSON_NUMBER_MISMATCH` (BLOCK), `BIB_PARSE_FAILED` (BLOCK), `MIXED_UNIT_SYSTEMS`, `PROMPT_INJECTION_SUSPECT`, `SANITY_RANGE`, `EQUATION_CANDIDATE`, `ENGINE_UNAVAILABLE`.

## 6. Resolutions
`accept ENGINE` (engine must hold that unit/table), `override` (text or rows typed from the source; `--note` required), `waive` (`--note` required). `--by` is required. Overrides are marked `human` and skipped by the engine-equality check but listed by `verify`.

## 7. Output formats
- **Markdown** (default): front matter (source, sha256, engines, status), one section per unit with `[src: ...]`, equations section, review log. PDF pages show page text and, below it, the structured table (the table numbers therefore appear twice by design).
- **CSV**: one file per table under `tables/`, plus `tables_index.csv`. `−` (U+2212) becomes `-` in plain numeric cells only.
- **JSON**: all units, blocks, equations, constants, decisions.
- **Code**: `eq_<slug>.py` and `test_eq_<slug>.py`; tests are the source's worked examples.
- **Skill**: `<name>/SKILL.md` + `reference/` + `data/` + `scripts/`.

## 8. Known limits
- `verify` proves fidelity to what the engines agreed on, not that they read the page correctly; the verifier agent audits that against the original.
- Figures, charts, diagrams and equations rendered as images are not converted.
- Reading order in multi-column or right-to-left PDFs can differ between engines (flagged, not fixed).
- Word and HTML are compared per document, PDF per page.
