@echo off
REM One-time setup: installs Python packages, runs the tests, and registers the Claude Code skill and agents.
cd /d "%~dp0"
echo Installing Python packages...
python -m pip install --pre -r requirements.txt || (echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
echo Running tests...
python -m pytest -q -W ignore
echo Registering the Claude Code skill...
if not exist ".claude\skills\reference-converter" mkdir ".claude\skills\reference-converter"
copy /Y "skill\SKILL.md" ".claude\skills\reference-converter\SKILL.md" >nul
echo.
echo Optional: poppler (pdftotext) gives PDFs a third reading engine, LibreOffice converts old .doc/.xls/.ppt.
echo Done. Open this folder in Claude Code.
pause
