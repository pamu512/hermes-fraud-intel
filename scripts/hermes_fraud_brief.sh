#!/usr/bin/env bash
# Hermes no-agent wrapper: write today's brief, print it to stdout for delivery.
# Install copies a ROOT-baked version to ~/.hermes/scripts/fraud-daily-brief.sh.
set -euo pipefail
ROOT="${HERMES_FRAUD_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"
python3 scripts/dump_recent.py --write-brief
DAY="$(date +%F)"
if [[ -f "briefs/${DAY}.md" ]]; then
  cat "briefs/${DAY}.md"
  exit 0
fi
if [[ -f "briefs/${DAY}.failed.md" ]]; then
  cat "briefs/${DAY}.failed.md"
  exit 1
fi
echo "fraud-daily-brief: no brief written for ${DAY}" >&2
exit 1
