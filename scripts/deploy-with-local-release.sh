#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/deploy-with-local-release.sh [OPTIONS] [-- DEPLOY_COMMAND...]

Pause the KRW ontology queue, rebuild the stable agent index, create an
immutable local release snapshot, atomically move the local current symlink,
optionally run a deploy command against that frozen snapshot, then restart the
queue if it was running before this script.

Options:
  --help, -h                 Show this help.
  --dry-run                  Print actions without mutating state.
  --no-rebuild-index         Skip the stable agent index rebuild.
  --no-restart-queue         Do not restart the queue even if it was running.
  --queue-timeout SECONDS    Queue stop wait timeout. Default: 7200.
  --release-id ID            Release id. Default: UTC timestamp.

Environment:
  KRW_ONTOLOGY_DATA_DIR       Defaults to $HOME/krw-ontology-data
  KRW_ONTOLOGY_QUEUE_ROOT     Defaults to $HOME/krw-ontology-data-running
  KRW_ONTOLOGY_RELEASE_ROOT   Defaults to $HOME/krw-ontology-data-releases
  KRW_ONTOLOGY_CLI            Defaults to krw-ontology
  QUEUE_POLL_INTERVAL         Defaults to 15.0

If DEPLOY_COMMAND is provided after --, it runs with:
  KRW_ONTOLOGY_DATA_DIR=<release-root>/current
  KRW_ONTOLOGY_RELEASE_DIR=<release-root>/current
  KRW_ONTOLOGY_RELEASE_ID=<release-id>
EOF
}

DRY_RUN=0
REBUILD_INDEX=1
RESTART_QUEUE=1
QUEUE_STOP_TIMEOUT_SECONDS=${QUEUE_STOP_TIMEOUT_SECONDS:-7200}
RELEASE_ID=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --no-rebuild-index)
      REBUILD_INDEX=0
      ;;
    --no-restart-queue)
      RESTART_QUEUE=0
      ;;
    --queue-timeout)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 1
      fi
      QUEUE_STOP_TIMEOUT_SECONDS=$1
      ;;
    --release-id)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 1
      fi
      RELEASE_ID=$1
      ;;
    --)
      shift
      break
      ;;
    *)
      usage
      exit 1
      ;;
  esac
  shift
done

DATA_DIR=${KRW_ONTOLOGY_DATA_DIR:-"$HOME/krw-ontology-data"}
QUEUE_ROOT=${KRW_ONTOLOGY_QUEUE_ROOT:-"$HOME/krw-ontology-data-running"}
RELEASE_ROOT=${KRW_ONTOLOGY_RELEASE_ROOT:-"$HOME/krw-ontology-data-releases"}
KRW_ONTOLOGY_CLI=${KRW_ONTOLOGY_CLI:-krw-ontology}
QUEUE_POLL_INTERVAL=${QUEUE_POLL_INTERVAL:-15.0}
RELEASE_ID=${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}

INDEX_PATH="$DATA_DIR/indexes/agent_index.sqlite"
RELEASES_DIR="$RELEASE_ROOT/releases"
RELEASE_TMP="$RELEASES_DIR/$RELEASE_ID.tmp"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_ID"
CURRENT_LINK="$RELEASE_ROOT/current"

QUEUE_WAS_RUNNING=0

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

run() {
  echo "+ $*"
  if [ "$DRY_RUN" = "0" ]; then
    "$@"
  fi
}

queue_status() {
  "$KRW_ONTOLOGY_CLI" queue status --root "$QUEUE_ROOT"
}

queue_is_running() {
  queue_status | grep -q '^Worker: running'
}

assert_queue_stopped_and_idle() {
  status=$(queue_status)
  echo "$status"
  echo "$status" | grep -q '^Worker: stopped'
  echo "$status" | grep -q 'running=0'
}

checkpoint_index() {
  if [ -f "$INDEX_PATH" ]; then
    run sqlite3 "$INDEX_PATH" 'PRAGMA wal_checkpoint(TRUNCATE);'
  fi
}

verify_serving_indexes() {
  missing=$(
    sqlite3 "$INDEX_PATH" <<'SQL'
WITH expected(name) AS (
  VALUES
    ('idx_object_traceability_status'),
    ('idx_company_topic_ticker'),
    ('idx_company_topic_scope'),
    ('idx_company_topic_source_object'),
    ('idx_company_topic_source_topic')
)
SELECT expected.name
FROM expected
LEFT JOIN sqlite_master
  ON sqlite_master.type = 'index'
 AND sqlite_master.name = expected.name
WHERE sqlite_master.name IS NULL
ORDER BY expected.name;
SQL
  )
  if [ -n "$missing" ]; then
    echo "Missing required serving index(es):" >&2
    echo "$missing" >&2
    exit 1
  fi
}

start_queue_if_needed() {
  if [ "$RESTART_QUEUE" != "1" ] || [ "$QUEUE_WAS_RUNNING" != "1" ]; then
    return
  fi
  if queue_is_running; then
    echo "Queue worker already running; not starting another worker."
    return
  fi
  run "$KRW_ONTOLOGY_CLI" queue start \
    --root "$QUEUE_ROOT" \
    --poll-interval "$QUEUE_POLL_INTERVAL" \
    --no-publish-prod
}

require_command "$KRW_ONTOLOGY_CLI"
require_command sqlite3
require_command rsync

if queue_is_running; then
  QUEUE_WAS_RUNNING=1
fi

trap start_queue_if_needed EXIT INT TERM

run "$KRW_ONTOLOGY_CLI" queue stop \
  --root "$QUEUE_ROOT" \
  --wait \
  --timeout "$QUEUE_STOP_TIMEOUT_SECONDS"

if [ "$DRY_RUN" = "0" ]; then
  assert_queue_stopped_and_idle
fi

if [ "$REBUILD_INDEX" = "1" ]; then
  run "$KRW_ONTOLOGY_CLI" build-agent-index \
    --root "$DATA_DIR" \
    --index-path "$INDEX_PATH" \
    --force
fi

checkpoint_index

if [ "$DRY_RUN" = "0" ]; then
  verify_serving_indexes
fi

run mkdir -p "$RELEASES_DIR"
run rm -rf "$RELEASE_TMP"
if [ -e "$RELEASE_DIR" ]; then
  echo "Release already exists: $RELEASE_DIR" >&2
  exit 1
fi
run mkdir -p "$RELEASE_TMP"
run rsync -a --delete \
  --exclude '.krw_pipeline/' \
  "$DATA_DIR"/ "$RELEASE_TMP"/

if [ "$DRY_RUN" = "0" ]; then
  snapshot_db="$RELEASE_TMP/indexes/agent_index.sqlite"
  INDEX_PATH=$snapshot_db verify_serving_indexes
fi

run mv "$RELEASE_TMP" "$RELEASE_DIR"
run ln -sfn "releases/$RELEASE_ID" "$RELEASE_ROOT/current.next"
run mv -f "$RELEASE_ROOT/current.next" "$CURRENT_LINK"

echo "Local release activated: $CURRENT_LINK -> releases/$RELEASE_ID"

if [ "$#" -gt 0 ]; then
  echo "+ env KRW_ONTOLOGY_DATA_DIR=$CURRENT_LINK KRW_ONTOLOGY_RELEASE_DIR=$CURRENT_LINK KRW_ONTOLOGY_RELEASE_ID=$RELEASE_ID $*"
  if [ "$DRY_RUN" = "0" ]; then
    KRW_ONTOLOGY_DATA_DIR=$CURRENT_LINK \
    KRW_ONTOLOGY_RELEASE_DIR=$CURRENT_LINK \
    KRW_ONTOLOGY_RELEASE_ID=$RELEASE_ID \
      "$@"
  fi
fi
