#!/usr/bin/env bash
# Deploy the daily refresh (this repo) as a Cloud Run Job + Cloud Scheduler, state in the shared private bucket.
# The API (commodity-ai-backend) reads the bucket's export/. Idempotent.
#
#   PROJECT=my-project DATA_REPO=../commodity-ai-data deploy/gcp.sh all   # setup seed build deploy monitor run-job
#   PROJECT=my-project deploy/gcp.sh build deploy                           # ship a new version
#
# Steps: setup | seed | build | deploy | monitor | run-job | all.  Details: README.md
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PROJECT:?set PROJECT to the GCP project id}"
REGION=${REGION:-asia-southeast1}
BUCKET=${BUCKET:-$PROJECT-cai-tank}
REPO=${REPO:?set REPO to the Artifact Registry repository name}
SERVICE=${SERVICE:-cai-api}
JOB=${JOB:-cai-refresh}
SCHEDULE=${SCHEDULE:-0 9 * * *}
TIME_ZONE=${TIME_ZONE:-Asia/Bangkok}
MIN_INSTANCES=${MIN_INSTANCES:-0}      # 1 removes cold starts (~870 MB image) for about US$10-15/month
TH_MARKUP=${TH_MARKUP:-1.0}
CORS_ORIGINS=${CORS_ORIGINS:-}          # empty: no CORS headers (the extension does not need them)
HTTPS_PROXY_SECRET=${HTTPS_PROXY_SECRET:-}   # Secret Manager secret holding a proxy URL for collectors blocked on GCP IPs
ALERT_EMAIL=${ALERT_EMAIL:-}
DATA_REPO=${DATA_REPO:-../commodity-ai-data}   # private checkout that holds tank.db, raw cursors, export and partner data (seed only)
TAG=${TAG:-$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . || echo -dirty)}

IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/commodity-ai-caller:$TAG
API_SA=cai-api@$PROJECT.iam.gserviceaccount.com
JOB_SA=cai-refresh@$PROJECT.iam.gserviceaccount.com
SCHED_SA=cai-scheduler@$PROJECT.iam.gserviceaccount.com
MOUNT=/mnt/tank
gc() { gcloud --project "$PROJECT" --quiet "$@"; }
log() { printf '\n== %s\n' "$*"; }

setup() {
  log "enable APIs"
  gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com monitoring.googleapis.com storage.googleapis.com iam.googleapis.com \
    ${HTTPS_PROXY_SECRET:+secretmanager.googleapis.com}

  log "Artifact Registry repo $REPO"
  gc artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
    gc artifacts repositories create "$REPO" --location "$REGION" --repository-format docker

  log "bucket gs://$BUCKET (versioned: each refresh keeps the previous tank and export for rollback)"
  gc storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 ||
    gc storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
  gc storage buckets update "gs://$BUCKET" --versioning --lifecycle-file deploy/bucket-lifecycle.json

  log "service accounts"
  for sa in cai-api cai-refresh cai-scheduler; do
    gc iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
      gc iam service-accounts create "$sa" --display-name "commodity-ai $sa"
  done
  # API reads the export, the job owns the bucket, the scheduler may only start the job (granted in deploy)
  gc storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$API_SA" --role roles/storage.objectViewer >/dev/null
  gc storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$JOB_SA" --role roles/storage.objectAdmin >/dev/null
  if [ -n "$HTTPS_PROXY_SECRET" ]; then
    gc secrets add-iam-policy-binding "$HTTPS_PROXY_SECRET" --member "serviceAccount:$JOB_SA" --role roles/secretmanager.secretAccessor >/dev/null
  fi
  # Cloud Build runs as the default compute account in new projects, which cannot push images until granted this
  local number; number=$(gc projects describe "$PROJECT" --format 'value(projectNumber)')
  gc projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$number-compute@developer.gserviceaccount.com" \
    --role roles/cloudbuild.builds.builder --condition None >/dev/null
}

seed() {
  log "seed gs://$BUCKET from the private data repo at $DATA_REPO (first deploy only)"
  if gc storage objects describe "gs://$BUCKET/tank.db" >/dev/null 2>&1 && [ "${FORCE_SEED:-}" != 1 ]; then
    echo "gs://$BUCKET/tank.db exists; the job keeps it current. FORCE_SEED=1 overwrites it with the local tank."
    return
  fi
  [ -f "$DATA_REPO/data/tank.db" ] || { echo "no $DATA_REPO/data/tank.db; set DATA_REPO to a commodity-ai-data checkout"; exit 1; }
  local tmp; tmp=$(mktemp -d)
  python3 -c 'import sqlite3,sys; d=sqlite3.connect(sys.argv[2]); sqlite3.connect(sys.argv[1]).backup(d); d.close()' "$DATA_REPO/data/tank.db" "$tmp/tank.db"
  gc storage cp "$tmp/tank.db" "gs://$BUCKET/tank.db"
  for f in reddit_cursor.json comments_done.txt; do
    [ -f "$DATA_REPO/data/raw/$f" ] && gc storage cp "$DATA_REPO/data/raw/$f" "gs://$BUCKET/raw/$f"
  done
  gc storage rsync "$DATA_REPO/data/export" "gs://$BUCKET/export"
  [ -d "$DATA_REPO/data/partner" ] && gc storage rsync --recursive "$DATA_REPO/data/partner" "gs://$BUCKET/partner"
  rm -rf "$tmp"
}

build() {
  log "build $IMAGE"
  gc builds submit --region "$REGION" --tag "$IMAGE" .
}

deploy() {
  local volume="name=tank,type=cloud-storage,bucket=$BUCKET"
  log "job $JOB"
  gc run jobs deploy "$JOB" --region "$REGION" --image "$IMAGE" --service-account "$JOB_SA" \
    --command /app/scripts/cloud_job.sh \
    --add-volume "$volume" --add-volume-mount "volume=tank,mount-path=$MOUNT" \
    --set-env-vars "DATA_MOUNT=$MOUNT" ${HTTPS_PROXY_SECRET:+--set-secrets "HTTPS_PROXY=$HTTPS_PROXY_SECRET:latest"} \
    --memory 2Gi --cpu 1 --task-timeout 2h --max-retries 0 --tasks 1 --parallelism 1

  log "scheduler: $JOB at '$SCHEDULE' $TIME_ZONE"
  gc run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member "serviceAccount:$SCHED_SA" --role roles/run.invoker >/dev/null
  local verb=create
  gc scheduler jobs describe "$JOB-daily" --location "$REGION" >/dev/null 2>&1 && verb=update
  gc scheduler jobs "$verb" http "$JOB-daily" --location "$REGION" --schedule "$SCHEDULE" --time-zone "$TIME_ZONE" \
    --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$JOB:run" --http-method POST \
    --oauth-service-account-email "$SCHED_SA" --attempt-deadline 60s

}

api_url() { gc run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)'; }

monitor() {
  local channel=""
  if [ -n "$ALERT_EMAIL" ]; then
    log "alert channel $ALERT_EMAIL"
    channel=$(gc beta monitoring channels list --filter "labels.email_address=\"$ALERT_EMAIL\"" --format 'value(name)' | head -1)
    [ -n "$channel" ] || channel=$(gc beta monitoring channels create --display-name "commodity-ai $ALERT_EMAIL" \
      --type email --channel-labels "email_address=$ALERT_EMAIL" --format 'value(name)')
  else
    echo "ALERT_EMAIL not set: alert policies are created without a notification channel (add one in the console)"
  fi

  log "alert policies"
  local f name policy
  for f in deploy/alert-*.json; do
    policy=$(sed -e "s#__JOB__#$JOB#g" "$f")
    name=$(printf '%s' "$policy" | python3 -c 'import json,sys; print(json.load(sys.stdin)["displayName"])')
    if [ -z "$(gc monitoring policies list --filter "displayName=\"$name\"" --format 'value(name)')" ]; then
      printf '%s' "$policy" > "${TMPDIR:-/tmp}/cai-policy.json"
      gc monitoring policies create --policy-from-file "${TMPDIR:-/tmp}/cai-policy.json" ${channel:+--notification-channels "$channel"} >/dev/null
      echo "created: $name"
    else
      echo "exists: $name"
    fi
  done
}

dataset_version() {
  curl -fsS "$1/api/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["dataset_version"], d["dataset_asof"])'
}

run_job() {
  log "run $JOB once and wait (acceptance gate: collectors reach their sources from GCP, and the API reloads)"
  local url before after
  url=$(api_url); before=$(dataset_version "$url")
  gc run jobs execute "$JOB" --region "$REGION" --wait || {
    echo "job failed. Logs: gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=$JOB' --project $PROJECT --limit 100 --freshness 3h"
    echo "exit 3 = exports published but a collector failed (likely blocked from GCP IPs): see README.md (Troubleshooting)"
    return 1
  }
  # the API sees the new export through gcsfuse's metadata cache, so allow a few minutes
  for _ in $(seq 1 18); do
    after=$(dataset_version "$url")
    [ "$after" != "$before" ] && { echo "API reloaded: $before -> $after"; return; }
    sleep 10
  done
  # same version is also what a rerun on a day without new data gives, so warn instead of failing
  echo "WARNING: API still serves $after after 3 minutes. If this run collected new data, check the volume's metadata cache (README.md, Troubleshooting)"
}

[ $# -gt 0 ] || { sed -n '2,9p' "$0"; exit 1; }
for step in "$@"; do
  case $step in
    setup) setup ;; seed) seed ;; build) build ;; deploy) deploy ;; monitor) monitor ;; run-job) run_job ;;
    all) setup; seed; build; deploy; monitor; run_job ;;
    *) echo "unknown step: $step"; exit 1 ;;
  esac
done
