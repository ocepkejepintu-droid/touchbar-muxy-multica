#!/bin/sh
# BetterTouchTool tap helper for Muxy control panel slots.
# Contract: POSIX sh only, set -eu, absolute /usr/bin/open, deterministic OK/NOOP/ERROR tokens.
# Default action: /usr/bin/open -a Muxy

set -eu

OPEN_BIN=/usr/bin/open
MUXY_APP=Muxy
EXPECTED_OPEN='/usr/bin/open -a Muxy'
LOG_FILE="${TMPDIR:-/tmp}/touchbar-muxy-slot-tap.log"

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
    if "$OPEN_BIN" -a "$MUXY_APP" >/dev/null 2>&1; then
      printf '%s\n' 'OK: activated Muxy'
      exit 0
    fi
    printf '%s\n' 'NOOP: Muxy app not found or could not be opened'
    exit 0
    ;;
  --dry-run)
    printf '%s\n' "OK: muxy slot tap action would run: $EXPECTED_OPEN"
    exit 0
    ;;
  --self-test)
    if [ -x "$OPEN_BIN" ]; then
      printf '%s\n' 'OK: muxy slot tap helper ready'
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
