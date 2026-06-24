#!/bin/sh
# BTT slot wrapper: summary (Muxy-first compact status).
# Emits "MUXY ●W ◌Wa" from daemon state.json counts (daemon mode).
# Falls back to agentmax_status.py --slot summary (pull-based) if snapshot missing/stale.
MUXY_ROOT="/Users/yoseph/TouchBar"
. "$(dirname "$0")/btt_muxy_lib.sh"

case "${1:-}" in
    --self-test)
        line="$(muxy_emit_summary)"
        if [ -z "$line" ]; then
            muxy_emit_fallback summary
        else
            printf '%s\n' "$line"
        fi
        exit 0
        ;;
esac

line="$(muxy_emit_summary)"
if [ -z "$line" ]; then
    muxy_emit_fallback summary
else
    printf '%s\n' "$line"
fi
