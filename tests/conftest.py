import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import fixtures as F  # noqa: E402
from refconvert import pipeline  # noqa: E402


@pytest.fixture
def make(tmp_path):
    """make('docx') -> (source_path, work) after extraction."""
    def _make(kind, name=None, **kw):
        fn = {"pdf": F.make_pdf, "docx": F.make_docx, "pptx": F.make_pptx, "xlsx": F.make_xlsx, "csv": F.make_csv,
              "html": F.make_html, "bib": F.make_bib, "ris": F.make_ris, "dxf": F.make_dxf, "md": F.make_text,
              "json": F.make_json, "png": F.make_png}[kind]
        src = tmp_path / (name or f"src.{kind}")
        if kind == "pdf":
            fn(src, kw.pop("pages"))
        else:
            fn(src, **kw)
        return src, pipeline.extract(src, tmp_path / ("work_" + src.name.replace(".", "_")))
    return _make


def codes(work, status=None, severity=None):
    return sorted({f["code"] for f in work.review()["flags"]
                   if (status is None or f["status"] == status) and (severity is None or f["severity"] == severity)})
