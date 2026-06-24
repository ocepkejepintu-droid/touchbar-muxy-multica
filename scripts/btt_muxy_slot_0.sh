#!/bin/sh
# BTT slot wrapper: slot 0 — daemon-driven Muxy-first label.
# Reads ~/.local/share/touchbar-muxy/state.json (daemon mode).
# Falls back to agentmax_status.py --slot 0 (pull-based) if snapshot is missing/stale.
MUXY_ROOT="/Users/yoseph/TouchBar"
. "$(dirname "$0")/btt_muxy_lib.sh"

case "${1:-}" in
    --self-test)
        line="$(muxy_emit_slot 0)"
        if [ -z "$line" ]; then
            muxy_emit_fallback 0
        else
            printf '%s\n' "$line"
        fi
        exit 0
        ;;
esac

line="$(muxy_emit_slot 0)"
if [ -z "$line" ]; then
    muxy_emit_fallback 0
else
    printf '%s\n' "$line"
fi
