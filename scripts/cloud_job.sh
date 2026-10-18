#!/usr/bin/env bash
# Cloud Run Job entry point: runs daily_cron.sh against state kept in a Cloud Storage bucket mounted at DATA_MOUNT.
#   $DATA_MOUNT/tank.db, $DATA_MOUNT/raw/{reddit_cursor.json,comments_done.txt}   job state
#   $DATA_MOUNT/export/                                                           what the API serves (its DATA_DIR)
#   $DATA_MOUNT/partner/                                                          team archive, read only
# SQLite never runs on the mount (gcsfuse has no file locking): copy in, work on local disk, copy back.
# The tank is saved even when the rebuild fails (upserts are idempotent, the next run rebuilds); the export is
# published only when scripts/check_export.py passes, so the API keeps serving the last good data otherwise.
# Without DATA_MOUNT it is plain daily_cron.sh (Railway, VPS, local).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
CRON=${CRON:-scripts/daily_cron.sh}
[ -z "${DATA_MOUNT:-}" ] && exec "$CRON"
M=$DATA_MOUNT
log() { echo "[cloud_job] $*" >&2; }

[ -f "$M/tank.db" ] || { log "no $M/tank.db; seed the bucket first: deploy/gcp.sh seed"; exit 1; }
mkdir -p data/raw data/export
cp "$M/tank.db" data/tank.db || exit 1
rm -f data/tank.db-wal data/tank.db-shm
for f in reddit_cursor.json comments_done.txt; do [ -f "$M/raw/$f" ] && cp "$M/raw/$f" data/raw/; done
[ -d "$M/export" ] && cp -R "$M/export/." data/export/
# team archive (not in the public repo): read-only input for scripts/run_partner_chin.py
[ -d "$M/partner" ] && mkdir -p data/partner && cp -R "$M/partner/." data/partner/
log "restored state from $M"

"$CRON"; status=$?
log "daily_cron exit $status (0 ok, 3 = rebuilt but a collector failed)"

# sqlite backup API folds the WAL in; refuse to overwrite the bucket copy with a corrupt tank
snapshot=$(mktemp -d)/tank.db
if python3 - data/tank.db "$snapshot" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
assert src.execute("pragma integrity_check").fetchone()[0] == "ok", "integrity_check failed"
dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close()
PY
then
  if cp "$snapshot" "$M/tank.db"; then log "saved tank"; else log "could not write $M/tank.db"; status=1; fi
  mkdir -p "$M/raw"
  for f in reddit_cursor.json comments_done.txt; do [ -f "data/raw/$f" ] && cp "data/raw/$f" "$M/raw/"; done
else
  log "tank failed integrity check; bucket copy left unchanged"; status=1
fi

if { [ "$status" -eq 0 ] || [ "$status" -eq 3 ]; } && python3 scripts/check_export.py data/export; then
  mkdir -p "$M/export"
  # data files first, the two the API watches last, so a reload never pairs new series with an old snapshot for long
  find data/export -maxdepth 1 -type f ! -name price_daily.parquet ! -name assessment_snapshot.json -exec cp {} "$M/export/" \;
  cp data/export/price_daily.parquet data/export/assessment_snapshot.json "$M/export/"
  log "published export"
else
  log "export not published; the API keeps the previous data"; [ "$status" -eq 0 ] && status=1
fi
exit "$status"
