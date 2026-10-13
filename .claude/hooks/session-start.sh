#!/bin/bash
# Installs Python dependencies when a Claude Code session starts (cloud or local Linux/macOS).
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || cd "$(dirname "$0")/../.."
if python3 -c "import pypdf, pdfplumber, docx, pptx, openpyxl, pandas, bs4, lxml, yaml, ezdxf, rispy, bibtexparser, pytest" 2>/dev/null; then
  exit 0
fi
python3 -m pip install -q --pre -r requirements.txt 2>/dev/null \
  || python3 -m pip install -q --pre --break-system-packages -r requirements.txt
# poppler (third PDF engine) is optional; install it if apt is available and we are root
if ! command -v pdftotext >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
  apt-get install -y -q poppler-utils >/dev/null 2>&1 || true
fi
exit 0
