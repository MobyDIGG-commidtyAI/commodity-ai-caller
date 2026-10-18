#!/usr/bin/env bash
# Overnight chain: wait for the Reddit firehose jobs, then comments, GDELT, rebuild + export.
# Logs in logs/overnight.log. Does NOT commit/push - review the export first.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
log() { echo "[$(date '+%F %T')] $*"; }
log "waiting for reddit firehose"
while pgrep -f "run_reddit_firehos[e]" >/dev/null; do sleep 60; done
log "comments";  python -u scripts/run_reddit_thread_comments.py 10 1000
rebuild() { python scripts/build_deal_prices.py && python scripts/run_partner_chin.py && python scripts/run_genre.py && python scripts/run_dedup.py && \
            python scripts/build_features.py && python scripts/export_for_team.py && python scripts/plot.py && python scripts/lead_lag.py; }
log "rebuild";   rebuild
# GDELT last: from this IP it often answers 429 and each window backs off ~5 min
log "gdelt";     python -u scripts/run_gdelt.py
log "rebuild";   rebuild
log "done"
