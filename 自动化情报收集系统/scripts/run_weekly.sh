#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 scripts/intel_radar.py collect
python3 scripts/intel_radar.py export
python3 scripts/intel_radar.py weekly
