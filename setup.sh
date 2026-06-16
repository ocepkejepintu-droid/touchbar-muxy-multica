#!/bin/sh
# Repo-local setup helper for the Vibe Island BetterTouchTool widget.
# Safe by design: no dependency installs, no destructive actions.

set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WIDGET_SCRIPT="$ROOT/scripts/btt_agentmax_widget.sh"
STATUS_SCRIPT="$ROOT/scripts/agentmax_status.py"
VIBE_ISLAND_TAP="$ROOT/scripts/btt_vibe_island_tap.sh"
MUXY_TAP="$ROOT/scripts/btt_muxy_tap.sh"
MULTICA_TAP="$ROOT/scripts/btt_multica_tap.sh"

chmod +x "$WIDGET_SCRIPT" "$STATUS_SCRIPT" "$VIBE_ISLAND_TAP" "$MUXY_TAP" "$MULTICA_TAP"

cat <<EOF
Vibe Island TouchBar setup complete.

Made executable:
  $WIDGET_SCRIPT
  $STATUS_SCRIPT
  $VIBE_ISLAND_TAP
  $MUXY_TAP
  $MULTICA_TAP

Recommended BetterTouchTool widget:
  Type: Shell Script / Task Widget for Touch Bar
  Name: Vibe Island Touch Bar
  Refresh interval: 2 seconds
  Width: 240 px (210-280 px works well)
  Script path:
    $WIDGET_SCRIPT

The widget wrapper runs:
  python3 "$STATUS_SCRIPT" --compact --root "$ROOT"

Tap actions must be assigned manually in BetterTouchTool.
Tapping the widget does nothing unless you assign a tap action.

Primary tap action (opens Vibe Island):
  "$VIBE_ISLAND_TAP"

Legacy tap helpers (optional):
  Muxy:    "$MUXY_TAP"
  Multica: "$MULTICA_TAP"

All tap helpers support --dry-run and --self-test.

Diagnostic-only tap actions (not primary):
  Detail: tell application "Terminal" to do script "cd '$ROOT' && python3 scripts/agentmax_status.py --detail --root '$ROOT'"
          activate application "Terminal"
  Debug:  tell application "Terminal" to do script "cd '$ROOT' && python3 scripts/agentmax_status.py --debug --root '$ROOT'"
          activate application "Terminal"

Validate:
  "$WIDGET_SCRIPT"
  python3 "$STATUS_SCRIPT" --smoke --root "$ROOT"
  "$VIBE_ISLAND_TAP" --self-test
  "$MUXY_TAP" --self-test
  "$MULTICA_TAP" --self-test
EOF