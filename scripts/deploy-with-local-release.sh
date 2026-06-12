#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/deploy-with-local-release.sh [OPTIONS] [-- DEPLOY_COMMAND...]

Pause the KRW ontology queue, create a foreground v3 immutable release with
`krw-ontology release force`, promote <releases-root>/<env>/current, optionally
run a deploy command against that current release, then restart the queue if it
was running before this script.

This script is v3-only. It never builds or snapshots the legacy monolith index.

Options:
  --help, -h                 Show this help.
  --dry-run                  Print actions without mutating state.
  --no-cache                 Bypass v3 build caches.
  --no-restart-queue         Do not restart the queue even if it was running.
  --queue-timeout SECONDS    Queue stop wait timeout. Default: 7200.
  --release-id ID            Release id. Default: CLI timestamp id.
  --env ENV                  Release environment. Default: KRW_ONTOLOGY_ENV or dev.
  --from-root PATH           Source ontology root. Default: configured running-root.
  --releases-root PATH       Releases root. Default: configured publish-root.

Environment:
  KRW_ONTOLOGY_QUEUE_ROOT     Defaults to $HOME/krw-ontology-data-running
  KRW_ONTOLOGY_CLI            Defaults to krw-ontology
  QUEUE_POLL_INTERVAL         Defaults to 15.0

If DEPLOY_COMMAND is provided after --, it runs with:
  KRW_ONTOLOGY_ENV=<env>
  KRW_ONTOLOGY_RELEASE_ROOT=<releases-root>/<env>/current
  KRW_ONTOLOGY_RELEASE_DIR=<releases-root>/<env>/current
  KRW_ONTOLOGY_RELEASE_ID=<release-id if provided>
EOF
}

DRY_RUN=0
NO_CACHE=0
RESTART_QUEUE=1
QUEUE_STOP_TIMEOUT_SECONDS=${QUEUE_STOP_TIMEOUT_SECONDS:-7200}
RELEASE_ID=""
ENV_NAME=${KRW_ONTOLOGY_ENV:-dev}
FROM_ROOT=""
RELEASES_ROOT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --no-cache)
      NO_CACHE=1
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
    --env)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 1
      fi
      ENV_NAME=$1
      ;;
    --from-root)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 1
      fi
      FROM_ROOT=$1
      ;;
    --releases-root)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 1
      fi
      RELEASES_ROOT=$1
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

QUEUE_ROOT=${KRW_ONTOLOGY_QUEUE_ROOT:-"$HOME/krw-ontology-data-running"}
KRW_ONTOLOGY_CLI=${KRW_ONTOLOGY_CLI:-krw-ontology}
QUEUE_POLL_INTERVAL=${QUEUE_POLL_INTERVAL:-15.0}
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

run_release_force() {
  set -- "$KRW_ONTOLOGY_CLI" release force --foreground --env "$ENV_NAME"
  if [ -n "$FROM_ROOT" ]; then
    set -- "$@" --from-root "$FROM_ROOT"
  fi
  if [ -n "$RELEASES_ROOT" ]; then
    set -- "$@" --releases-root "$RELEASES_ROOT"
  fi
  if [ -n "$RELEASE_ID" ]; then
    set -- "$@" --release-id "$RELEASE_ID"
  fi
  if [ "$NO_CACHE" = "1" ]; then
    set -- "$@" --no-cache
  fi
  run "$@"
}

release_current_path() {
  if [ -n "$RELEASES_ROOT" ]; then
    printf '%s/%s/current\n' "$RELEASES_ROOT" "$ENV_NAME"
    return
  fi
  "$KRW_ONTOLOGY_CLI" release status --env "$ENV_NAME" | awk -F': ' '/^current: / {print $2; exit}'
}

require_command "$KRW_ONTOLOGY_CLI"

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

run_release_force

CURRENT_LINK=$(release_current_path)
if [ -z "$CURRENT_LINK" ]; then
  echo "Could not resolve current release path for env=$ENV_NAME" >&2
  exit 1
fi

echo "Local v3 release activated: $CURRENT_LINK"

if [ "$#" -gt 0 ]; then
  echo "+ env KRW_ONTOLOGY_ENV=$ENV_NAME KRW_ONTOLOGY_RELEASE_ROOT=$CURRENT_LINK KRW_ONTOLOGY_RELEASE_DIR=$CURRENT_LINK KRW_ONTOLOGY_RELEASE_ID=$RELEASE_ID $*"
  if [ "$DRY_RUN" = "0" ]; then
    KRW_ONTOLOGY_ENV=$ENV_NAME \
    KRW_ONTOLOGY_RELEASE_ROOT=$CURRENT_LINK \
    KRW_ONTOLOGY_RELEASE_DIR=$CURRENT_LINK \
    KRW_ONTOLOGY_RELEASE_ID=$RELEASE_ID \
      "$@"
  fi
fi
