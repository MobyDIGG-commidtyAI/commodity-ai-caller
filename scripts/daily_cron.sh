#!/usr/bin/env bash
# cron: 0 9 * * *  — keeps the tank current (all upserts are idempotent)
# Collectors may fail (429 / Cloudflare, more often from cloud IPs); the rebuild still runs on what the tank has.
# Exit 0 = all good, 3 = rebuilt but a core collector failed (exports are still valid), other = rebuild failed.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ -f .venv/bin/activate ] && source .venv/bin/activate
SINCE=$(python3 -c "import datetime as d; print(d.date.today() - d.timedelta(days=21))")   # portable (BSD date has no -d)
failed=""
collect() { "$@" || { echo "collector failed: $*" >&2; failed="$failed $2"; }; }
collect python3 scripts/run_trendforce.py "$SINCE"
python3 scripts/run_thai_retail.py || true   # no thai_urls yet / Cloudflare -> never block the rest
python3 scripts/run_rss.py || true
collect python3 scripts/run_reddit_firehose.py buildapcsales,hardware
set -e
python3 scripts/build_deal_prices.py
python3 scripts/run_partner_chin.py   # static archive, idempotent: keeps a fresh tank equal to the exports
python3 scripts/run_genre.py
python3 scripts/run_dedup.py
python3 scripts/build_features.py
python3 scripts/export_for_team.py
python3 scripts/build_assessment_snapshot.py
python3 scripts/plot.py
python3 scripts/lead_lag.py
if [ -n "$failed" ]; then echo "rebuilt, but collectors failed:$failed" >&2; exit 3; fi
