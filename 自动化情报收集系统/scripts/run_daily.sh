#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 scripts/run_daily_ops.py --date today --trigger launchd
