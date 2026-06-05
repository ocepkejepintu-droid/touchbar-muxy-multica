#!/bin/sh
# BetterTouchTool shell-script widget wrapper for Agentmax/OMX compact status.
# Keep stdout to one safe Touch Bar line; never leak Python tracebacks into BTT.

ROOT="/Users/yoseph/TouchBar"
STATUS_SCRIPT="$ROOT/scripts/agentmax_status.py"

if ! OUTPUT="$(python3 "$STATUS_SCRIPT" --compact --root "$ROOT" 2>/dev/null)"; then
  printf '%s\n' 'MUXY !err'
  exit 0
fi

if [ -z "$OUTPUT" ]; then
  printf '%s\n' 'MUXY !err'
  exit 0
fi

# Print only the first line so BTT receives a compact widget label.
printf '%s\n' "$OUTPUT" | sed -n '1p'
