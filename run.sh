#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
set -a; [ -f .env ] && source .env; set +a
python app.py
