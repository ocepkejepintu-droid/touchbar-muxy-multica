#!/bin/sh
# scripts/btt_muxy_slot_kill.sh — kill a Touch Bar slot's tmux pane.
#
# Usage:
#   scripts/btt_muxy_slot_kill.sh <slot_index|pane_id>    # kill that pane
#   scripts/btt_muxy_slot_kill.sh --self-test             # verify tmux kill-pane, exit 0
#
# The argument can be either a slot_index 0..3 (resolved from daemon
# state.json at runtime) or a direct pane_id (e.g. "%0" or "@43").
# Resolution order:
#   1. slot_index 0..3 → look up pane_id in ~/.local/share/touchbar-muxy/state.json
#      (if snapshot fresh ≤ 30s); fall through to live tmux scan otherwise.
#   2. arg starts with % or @ → treat as literal pane_id.
#   3. otherwise → use arg as-is.
#
# Destructive: this is the long-press menu's Kill entry. The BTT side
# adds an "Are you sure?" confirmation prompt before firing this script,
# so the user has a second gate beyond the long-press itself.
#
# Safe: no tmux server → exit 0 silently; stale snapshot → fall through;
# out-of-range slot → exit 0; kill-pane failure → logged to daemon.log.

set -u

STATE_JSON="${HOME}/.local/share/touchbar-muxy/state.json"
DAEMON_LOG="${HOME}/.local/share/touchbar-muxy/daemon.log"
MAX_AGE_S=30

print_usage() {
    printf 'usage: %s <slot_index 0..3|pane_id> | --self-test\n' "$(basename "$0")" >&2
}

log_action() {
    msg="$1"
    mkdir -p "$(dirname "$DAEMON_LOG")" 2>/dev/null || true
    printf '[%s] kill: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$msg" >> "$DAEMON_LOG" 2>/dev/null || true
}

# resolve_pane_id ARG — print pane_id (empty if unresolved)
resolve_pane_id() {
    arg="$1"

    # Path 1: numeric slot_index 0..3 → state.json lookup
    case "$arg" in
        0|1|2|3)
            if [ -f "$STATE_JSON" ]; then
                snapshot_mtime="$(stat -f %m "$STATE_JSON" 2>/dev/null || stat -c %Y "$STATE_JSON" 2>/dev/null || echo 0)"
                if [ -n "$snapshot_mtime" ] && [ "$snapshot_mtime" != "0" ]; then
                    now="$(date +%s)"
                    snapshot_age=$((now - snapshot_mtime))
                    if [ "$snapshot_age" -le "$MAX_AGE_S" ]; then
                        pane_id="$(python3 - "$STATE_JSON" "$arg" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        snap = json.load(fh)
    slots = snap.get("slots") if isinstance(snap, dict) else None
    idx = int(sys.argv[2])
    if not isinstance(slots, list) or idx < 0 or idx >= len(slots):
        sys.exit(0)
    entry = slots[idx] or {}
    if not isinstance(entry, dict):
        sys.exit(0)
    pane_id = str(entry.get("pane_id") or "").strip()
    if pane_id:
        print(pane_id)
except (OSError, ValueError, TypeError):
    sys.exit(0)
PYEOF
                        )"
                        if [ -n "$pane_id" ]; then
                            printf '%s\n' "$pane_id"
                            return 0
                        fi
                    fi
                fi
            fi
            # Live tmux fallback: pick first attached session's pane
            if command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null; then
                tmux list-panes -a -F '#{pane_id}\t#{session_attached}' 2>/dev/null \
                    | awk -F'\t' '$2 == "1" { print $1; exit }'
            fi
            return 0
            ;;
    esac

    # Path 2: literal pane_id (%N or @UUID)
    case "$arg" in
        %*|@*)
            printf '%s\n' "$arg"
            return 0
            ;;
    esac

    # Path 3: unknown format — pass through unchanged
    printf '%s\n' "$arg"
    return 0
}

kill_pane() {
    arg="$1"
    pane_id="$(resolve_pane_id "$arg")"
    if [ -z "$pane_id" ]; then
        log_action "no-op arg=$arg (no pane_id resolved)"
        return 0
    fi

    if ! command -v tmux >/dev/null 2>&1; then
        printf 'tmux not installed; cannot kill pane %s\n' "$pane_id" >&2
        log_action "FAIL tmux-not-installed pane=$pane_id"
        return 0
    fi

    if ! tmux has-session 2>/dev/null; then
        printf 'no tmux server running; cannot kill pane %s\n' "$pane_id" >&2
        log_action "FAIL no-tmux-server pane=$pane_id"
        return 0
    fi

    err="$(tmux kill-pane -t "$pane_id" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        log_action "OK pane=$pane_id"
    else
        log_action "FAIL kill-pane pane=$pane_id err=$err"
        printf 'kill-pane failed: %s\n' "$err" >&2
    fi
    return 0
}

self_test() {
    if ! command -v tmux >/dev/null 2>&1; then
        printf 'tmux: NOT INSTALLED\n'
        return 0
    fi
    if ! tmux has-session 2>/dev/null; then
        printf 'tmux: NOT RUNNING\n'
        return 0
    fi
    if tmux list-commands 2>/dev/null | grep -q '^kill-pane\b'; then
        printf 'tmux kill-pane: OK (binary callable, version=%s)\n' "$(tmux -V)"
    else
        printf 'tmux kill-pane: UNUSABLE (binary error)\n'
    fi
    return 0
}

main() {
    if [ "$#" -eq 0 ]; then
        print_usage
        exit 2
    fi

    case "$1" in
        --self-test|-h|--help)
            self_test
            exit 0
            ;;
        -*)
            print_usage
            exit 2
            ;;
    esac

    kill_pane "$1"
    exit 0
}

main "$@"
