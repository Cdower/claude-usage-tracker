#!/bin/bash
cd "$(dirname "$0")"
set -a; [ -f .env ] && source .env; set +a
# --locked: refuse to run if uv.lock is out of date with pyproject.toml.
# Packages are installed from uv.lock with hash verification.
exec uv run --locked python app.py
