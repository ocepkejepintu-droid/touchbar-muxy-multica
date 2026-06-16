#!/bin/sh
# Apply Vibe Island Touch Bar layout: disable Multica widget, refresh VI widget, restart BTT.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VI_WIDGET_UUID="${BTT_VI_WIDGET_UUID:-4EA2B0F6-983C-4DD9-8F30-5F7161DCB601}"
VI_TAP_UUID="${BTT_VI_TAP_UUID:-17D6AE4C-4829-4115-8709-AEDAC8F53552}"
MULTICA_WIDGET_UUID="${BTT_MULTICA_WIDGET_UUID:-7BFC3566-8488-421E-8805-933EE9C127D5}"
LEGACY_OMX_WIDGET_UUID="${BTT_LEGACY_OMX_WIDGET_UUID:-E9F2B1E5-8B3D-4B9A-A8B9-A0B1C200001}"
MULTICA_TRIGGER_UUID="${BTT_MULTICA_TRIGGER_UUID:-A8D3C893-B0ED-4FE6-849E-14885570101A}"
STATUS_TEXT="$("$ROOT/scripts/btt_agentmax_widget.sh" | sed -n '1p')"

stop_launch_agent() {
  label="$1"
  plist="$HOME/Library/LaunchAgents/${label}.plist"
  launchctl bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
  launchctl unload "$plist" >/dev/null 2>&1 || true
}

btt_update() {
  uuid="$1"
  json="$2"
  /usr/bin/osascript - "$uuid" "$json" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
  tell application "BetterTouchTool"
    update_trigger (item 1 of argv) json (item 2 of argv)
    refresh_widget (item 1 of argv)
  end tell
end run
APPLESCRIPT
}

printf '%s\n' "Stopping legacy Multica/OMX Touch Bar daemons..."
stop_launch_agent "com.multica.touchbar-status"
stop_launch_agent "com.omx.touchbar-status"

printf '%s\n' "Disabling Multica Touch Bar widget (${MULTICA_WIDGET_UUID})..."
btt_update "$MULTICA_WIDGET_UUID" '{"BTTEnabled":0,"BTTTouchBarAlwaysShowButton":0}'
btt_update "$MULTICA_TRIGGER_UUID" '{"BTTEnabled":0}'

printf '%s\n' "Disabling legacy OMX daemon widget (${LEGACY_OMX_WIDGET_UUID})..."
btt_update "$LEGACY_OMX_WIDGET_UUID" '{"BTTEnabled":0,"BTTTouchBarAlwaysShowButton":0}'

printf '%s\n' "Updating Vibe Island widget tap + label..."
btt_update "$VI_TAP_UUID" "{\"BTTEnabled\":1,\"BTTShellScriptPath\":\"$ROOT/scripts/btt_vibe_island_tap.sh\",\"BTTShellScriptExecuteOnStartup\":0}"
btt_update "$VI_WIDGET_UUID" "{\"BTTEnabled\":1,\"BTTTouchBarAlwaysShowButton\":1,\"BTTTouchBarShellScriptString\":\"$ROOT/scripts/btt_agentmax_widget.sh\",\"BTTTouchBarScriptUpdateInterval\":2,\"BTTTouchBarButtonWidth\":260,\"BTTTouchBarButtonUseFixedWidth\":1,\"BTTTouchBarButtonName\":\"$STATUS_TEXT\",\"BTTWidgetName\":\"Vibe Island Touch Bar\",\"BTTTriggerTypeDescription\":\"$STATUS_TEXT\"}"

printf '%s\n' "Restarting BetterTouchTool..."
/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1 || true
sleep 2
open -a "BetterTouchTool" >/dev/null 2>&1 || true
sleep 3

btt_update "$VI_WIDGET_UUID" "{\"BTTTouchBarButtonName\":\"$STATUS_TEXT\",\"BTTWidgetName\":\"Vibe Island Touch Bar\",\"BTTTriggerTypeDescription\":\"$STATUS_TEXT\"}"

printf '%s\n' "Done. Touch Bar status: $STATUS_TEXT"
printf '%s\n' "Multica widget disabled. Vibe Island widget active."