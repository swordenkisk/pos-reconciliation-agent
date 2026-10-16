#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
posix_date="${1:-$(date +%F)}"
python -m posix_reconciler --date "$posix_date"
