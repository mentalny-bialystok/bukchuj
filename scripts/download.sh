#!/usr/bin/env bash
# Download football-data.co.uk season CSVs into data/raw/.
# Usage: scripts/download.sh [LEAGUES...]   (default: E0 D1 SP1 I1 F1)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
LEAGUES=${*:-"E0 D1 SP1 I1 F1"}
SEASONS="1920 2021 2122 2223 2324 2425 2526"
for lg in $LEAGUES; do for s in $SEASONS; do
  curl -sSfL --max-time 30 -o "data/raw/${lg}_${s}.csv" "https://www.football-data.co.uk/mmz4281/$s/$lg.csv" \
    && echo "ok   $lg $s" || echo "fail $lg $s"
done; done
