#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is missing. Install python3 and python3-venv on Ubuntu." >&2
  exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
  echo "Python 3.10 or newer is required." >&2; exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || { echo "Could not create the virtual environment. Install python3-venv and retry." >&2; exit 1; }
fi
if ! .venv/bin/python -c '
from importlib.metadata import version, PackageNotFoundError
from packaging.requirements import Requirement
from pathlib import Path
for line in Path("requirements.txt").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        requirement=Requirement(line)
        assert version(requirement.name) in requirement.specifier
' >/dev/null 2>&1; then
  echo "Installing application dependencies into .venv…"
  .venv/bin/python -m pip install -r requirements.txt || { echo "Dependency installation failed. Check your internet connection and rerun ./start.sh." >&2; exit 1; }
fi
mkdir -p data/cv data/cache output
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
PFE_PORT="$(.venv/bin/python -c 'from dotenv import load_dotenv; import os; load_dotenv(); p=int(os.getenv("PORT","8501")); assert 1024 <= p <= 65535; print(p)')"
echo "PFE Opportunity Finder: http://localhost:${PFE_PORT}"
echo "Press Ctrl+C to stop the dashboard. Session jobs have their own deadline."
exec .venv/bin/python -m streamlit run app.py --server.address=127.0.0.1 --server.port="$PFE_PORT" --browser.gatherUsageStats=false "$@"
