#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 scripts/intel_radar.py run
python3 src/competitive_procurement.py --date today
