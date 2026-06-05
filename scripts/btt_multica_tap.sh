#!/bin/sh
# BetterTouchTool tap helper for Multica.
# Contract: POSIX sh only, set -eu, absolute /usr/bin/open, deterministic OK/NOOP/ERROR tokens.
# Default action: /usr/bin/open -a Multica (or MULTICA_APP_NAME env override)

set -eu

OPEN_BIN=/usr/bin/open
MULTICA_APP="${MULTICA_APP_NAME:-Multica}"
EXPECTED_OPEN="/usr/bin/open -a ${MULTICA_APP}"
LOG_FILE="${TMPDIR:-/tmp}/touchbar-multica-tap.log"

log_invocation() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

usage_error() {
  printf '%s\n' "ERROR: invalid argument: ${1:-}"
  exit 1
}

case "${1:-}" in
  "")
    log_invocation 'default'
    if "$OPEN_BIN" -a "$MULTICA_APP" >/dev/null 2>&1; then
      printf '%s\n' "OK: activated ${MULTICA_APP}"
      exit 0
    fi
    # Fallback: try "Multica Desktop"
    if "$OPEN_BIN" -a "Multica Desktop" >/dev/null 2>&1; then
      printf '%s\n' 'OK: activated Multica Desktop'
      exit 0
    fi
    printf '%s\n' 'NOOP: Multica app not found or could not be opened'
    exit 0
    ;;
  --dry-run)
    printf '%s\n' "OK: multica tap action would run: ${EXPECTED_OPEN}"
    exit 0
    ;;
  --self-test)
    if [ -x "$OPEN_BIN" ]; then
      printf '%s\n' 'OK: multica tap helper ready'
      exit 0
    fi
    printf '%s\n' 'NOOP: /usr/bin/open not available'
    exit 0
    ;;
  -h|--help)
    usage_error "${1:-}"
    ;;
  *)
    usage_error "$1"
    ;;
esac
