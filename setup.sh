#!/bin/sh
# Repo-local setup helper for the Agentmax/OMX BetterTouchTool widget.
# Safe by design: no dependency installs, no destructive actions.

set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WIDGET_SCRIPT="$ROOT/scripts/btt_agentmax_widget.sh"
STATUS_SCRIPT="$ROOT/scripts/agentmax_status.py"
MUXY_TAP="$ROOT/scripts/btt_muxy_tap.sh"
MULTICA_TAP="$ROOT/scripts/btt_multica_tap.sh"

chmod +x "$WIDGET_SCRIPT" "$STATUS_SCRIPT" "$MUXY_TAP" "$MULTICA_TAP"

cat <<EOF
Agentmax TouchBar setup complete.

Made executable:
  $WIDGET_SCRIPT
  $STATUS_SCRIPT
  $MUXY_TAP
  $MULTICA_TAP

Recommended BetterTouchTool widget:
  Type: Shell Script / Task Widget for Touch Bar
  Name: Muxy Notification Center
  Refresh interval: 2 seconds
  Width: 240 px (210-280 px works well)
  Script path:
    $WIDGET_SCRIPT

The widget wrapper runs:
  python3 "$STATUS_SCRIPT" --compact --root "$ROOT"

Tap actions must be assigned manually in BetterTouchTool.
Tapping the widget does nothing unless you assign a tap action.

Muxy tap action (opens Muxy app):
  "$MUXY_TAP"

Multica tap action (opens Multica app):
  "$MULTICA_TAP"

Both helpers support --dry-run and --self-test.

Diagnostic-only tap actions (not primary):
  Detail: tell application "Terminal" to do script "cd '$ROOT' && python3 scripts/agentmax_status.py --detail --root '$ROOT'"
          activate application "Terminal"
  Debug:  tell application "Terminal" to do script "cd '$ROOT' && python3 scripts/agentmax_status.py --debug --root '$ROOT'"
          activate application "Terminal"

Validate:
  "$WIDGET_SCRIPT"
  python3 "$STATUS_SCRIPT" --smoke --root "$ROOT"
  "$MUXY_TAP" --self-test
  "$MULTICA_TAP" --self-test
EOF
