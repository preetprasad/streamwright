#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH=/opt/homebrew/bin:$PATH
[ -d .venv ] || uv venv --python 3.12 .venv
uv pip install -q -r requirements.txt --python .venv/bin/python
exec .venv/bin/python -m kit
