#!/usr/bin/env bash
# Rebuild launcher for shard schema v3 (SupportLink demotion + external-content FTS)
# into the isolated v2-dev release env.
#
# Flow: (optional pilot staging) -> release build (materialize + shards + spine
# + manifest + verify, one transaction, NO promotion) -> release verify ->
# summary. All outputs land under ~/krw-ontology-data/releases/v2-dev/dev/.
# This script NEVER writes under ~/krw-ontology-data/releases/prod and never
# promotes the v2-dev current symlink.
#
# Usage:
#   scripts/rebuild_v2_dev.sh                          # full rebuild (all tickers)
#   scripts/rebuild_v2_dev.sh --tickers SO             # pilot: single ticker
#   scripts/rebuild_v2_dev.sh --tickers SO,AAPL --no-cache
#
# Options:
#   --tickers LIST   Comma-separated tickers. The CLI has no per-ticker build
#                    flag, so the launcher materializes a restricted staging
#                    source root (companies/<T> clone-copied from SOURCE_ROOT)
#                    and builds from it. This is the smallest legitimate unit.
#   --release-id ID  Explicit release id (default: UTC timestamp).
#   --no-cache       Bypass v3 artifact/shard/spine caches.
#   --force-release  Build even when the source manifest matches current.
#
# Environment overrides:
#   KRW_REBUILD_SOURCE_ROOT   Source ontology root (default: running-root
#                             ~/krw-ontology-data-running, all tickers).
#   KRW_REBUILD_RELEASES_ROOT Releases root (default:
#                             ~/krw-ontology-data/releases/v2-dev — pinned).
#   KRW_REBUILD_ENV           Env name (default: dev; enum-constrained to
#                             dev|staging|prod, so v2-dev isolation comes from
#                             the releases root, not the env name).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${KRW_REBUILD_SOURCE_ROOT:-$HOME/krw-ontology-data-running}"
RELEASES_ROOT="${KRW_REBUILD_RELEASES_ROOT:-$HOME/krw-ontology-data/releases/v2-dev}"
ENV_NAME="${KRW_REBUILD_ENV:-dev}"
PROD_CURRENT="$HOME/krw-ontology-data/releases/prod/current"

TICKERS=""
RELEASE_ID=""
NO_CACHE=0
FORCE_RELEASE=0

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tickers)
      [ "${2:-}" ] || { echo "ERROR --tickers requires a value" >&2; usage; }
      TICKERS="$2"; shift 2 ;;
    --release-id)
      [ "${2:-}" ] || { echo "ERROR --release-id requires a value" >&2; usage; }
      RELEASE_ID="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1; shift ;;
    --force-release|--force) FORCE_RELEASE=1; shift ;;
    -h|--help) usage ;;
    *) echo "ERROR unknown argument: $1" >&2; usage ;;
  esac
done

# --- Safety guards -----------------------------------------------------------
PROD_CURRENT_BEFORE="$(readlink "$PROD_CURRENT" 2>/dev/null || true)"
cleanup() {
  local after
  after="$(readlink "$PROD_CURRENT" 2>/dev/null || true)"
  if [ "$after" != "$PROD_CURRENT_BEFORE" ]; then
    echo "FATAL prod current symlink changed during run: '$PROD_CURRENT_BEFORE' -> '$after'" >&2
    exit 99
  fi
}
trap cleanup EXIT

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR uv not found on PATH" >&2
  exit 1
fi

if [ ! -d "$RELEASES_ROOT" ]; then
  mkdir -p "$RELEASES_ROOT"
fi
RESOLVED_RELEASES="$(cd "$RELEASES_ROOT" && pwd)"
case "$RESOLVED_RELEASES" in
  *$'\n'*|*' '*)
    echo "ERROR resolved releases root is not a clean single path: '$RESOLVED_RELEASES'" >&2
    exit 1
    ;;
esac
if [ "$RESOLVED_RELEASES" != "$HOME/krw-ontology-data/releases/v2-dev" ]; then
  echo "ERROR refusing to run: releases root '$RESOLVED_RELEASES' is not exactly ~/krw-ontology-data/releases/v2-dev" >&2
  exit 1
fi
if [ "$RESOLVED_RELEASES" = "$HOME/krw-ontology-data/releases" ] || [ "$RESOLVED_RELEASES" = "$HOME/krw-ontology-data/releases/prod" ]; then
  echo "ERROR refusing to run against the shared/prod releases root" >&2
  exit 1
fi

if [ ! -d "$SOURCE_ROOT/companies" ]; then
  echo "ERROR source root has no companies/ dir: $SOURCE_ROOT" >&2
  exit 1
fi

# --- Logging -----------------------------------------------------------------
mkdir -p "$REPO_ROOT/logs"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="$REPO_ROOT/logs/rebuild_v2_dev_${STAMP}.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "== rebuild_v2_dev $(date -u +%FT%TZ) =="
echo "repo:            $REPO_ROOT"
echo "source_root:     $SOURCE_ROOT"
echo "releases_root:   $RESOLVED_RELEASES"
echo "env:             $ENV_NAME (release_env_root appends env dir; final layout <root>/dev/<id>)"
echo "tickers:         ${TICKERS:-<all>}"
echo "log:             $LOG_FILE"
echo "prod current:    $PROD_CURRENT -> $PROD_CURRENT_BEFORE (must be unchanged at exit)"

# CLI env must not leak an unrelated env selection.
export KRW_ONTOLOGY_ENV="$ENV_NAME"

# --- Pilot staging (per-ticker restriction) ----------------------------------
EFFECTIVE_SOURCE_ROOT="$SOURCE_ROOT"
if [ -n "$TICKERS" ]; then
  IFS=',' read -r -a TICKER_LIST <<< "$TICKERS"
  STAGING_NAME="$(echo "$TICKERS" | tr ',' '-')"
  STAGING_ROOT="$RESOLVED_RELEASES/pilot-source/${STAGING_NAME}-${STAMP}"
  echo "== pilot staging source: $STAGING_ROOT"
  rm -rf "$STAGING_ROOT"
  mkdir -p "$STAGING_ROOT/companies"
  for T in "${TICKER_LIST[@]}"; do
    T="$(echo "$T" | xargs)"
    if [ ! -d "$SOURCE_ROOT/companies/$T" ]; then
      echo "ERROR ticker '$T' not found under $SOURCE_ROOT/companies" >&2
      exit 1
    fi
    echo "  clone-copy companies/$T"
    cp -Rc "$SOURCE_ROOT/companies/$T" "$STAGING_ROOT/companies/$T" 2>/dev/null \
      || cp -R "$SOURCE_ROOT/companies/$T" "$STAGING_ROOT/companies/$T"
  done
  EFFECTIVE_SOURCE_ROOT="$STAGING_ROOT"
fi

RELEASE_ID="${RELEASE_ID:-$STAMP}"
RELEASE_ROOT="$RESOLVED_RELEASES/$ENV_NAME/$RELEASE_ID"

BUILD_ARGS=(
  release build
  --from-root "$EFFECTIVE_SOURCE_ROOT"
  --releases-root "$RESOLVED_RELEASES"
  --env "$ENV_NAME"
  --release-id "$RELEASE_ID"
)
if [ "$NO_CACHE" -eq 1 ]; then BUILD_ARGS+=("--no-cache"); fi
if [ "$FORCE_RELEASE" -eq 1 ]; then BUILD_ARGS+=("--force-release"); fi

echo "== build: uv run krw-ontology ${BUILD_ARGS[*]}"
cd "$REPO_ROOT"
uv run krw-ontology "${BUILD_ARGS[@]}"

echo "== verify: uv run krw-ontology release verify --root $RELEASE_ROOT --env $ENV_NAME"
uv run krw-ontology release verify --root "$RELEASE_ROOT" --env "$ENV_NAME"

echo "== summary =="
echo "release_root: $RELEASE_ROOT"
echo "manifest:     $RELEASE_ROOT/manifest.json"
echo "verify:       $RELEASE_ROOT/verify/release_verify.json"
echo "log:          $LOG_FILE"
echo "== rebuild_v2_dev done $(date -u +%FT%TZ) =="
