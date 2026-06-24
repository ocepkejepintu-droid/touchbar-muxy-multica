#!/bin/sh
# scripts/btt_muxy_slot_focus.sh — focus the tmux pane mapped to a Touch Bar slot.
#
# Usage:
#   scripts/btt_muxy_slot_focus.sh <slot_index>    # 0..3 → focus that slot's pane
#   scripts/btt_muxy_slot_focus.sh --self-test     # print resolved target, exit 0
#
# The slot index is the Touch Bar position (0..3). This script reads the
# daemon snapshot at ~/.local/share/touchbar-muxy/state.json to find the
# tmux pane_id currently displayed at that slot, then calls
# `tmux select-pane -t pane_id` (and `tmux switch-client -t session` if
# invoked from inside tmux) so the tapped slot's pane comes into focus.
#
# Idempotent / safe: no tmux server → exit 0 silently; stale snapshot →
# fall back to a live tmux list-panes scan; slot out of range → exit 0.

set -u

STATE_JSON="${HOME}/.local/share/touchbar-muxy/state.json"
MAX_AGE_S=30
TAP_STATE_DIR="${HOME}/.local/share/touchbar-muxy"
DOUBLE_TAP_MS=500
MENU_SCRIPTS_DIR="${MENU_SCRIPTS_DIR:-/Users/yoseph/TouchBar/scripts}"

print_usage() {
    printf 'usage: %s <slot_index 0..3> | --self-test\n' "$(basename "$0")" >&2
}

# Resolve the (pane_id, session, window, project) tuple for the given slot.
# Output: lines "pane_id\tsession\twindow_index\tproject"
resolve_slot() {
    slot_index="$1"

    # 1. Try the daemon snapshot first (cheap + responsive to slot rotation).
    if [ -f "$STATE_JSON" ]; then
        snapshot_age=999
        if [ -n "$(command -v stat)" ]; then
            # macOS stat: -f %m = mtime epoch; Linux: -c %Y.
            snapshot_mtime="$(stat -f %m "$STATE_JSON" 2>/dev/null || stat -c %Y "$STATE_JSON" 2>/dev/null || echo 0)"
            now="$(date +%s)"
            snapshot_age=$((now - snapshot_mtime))
        fi
        if [ "$snapshot_age" -le "$MAX_AGE_S" ]; then
            snapshot_line="$(python3 - "$STATE_JSON" "$slot_index" <<'PYEOF'
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
    session = str(entry.get("session") or "").strip()
    window = entry.get("window")
    project = str(entry.get("project") or "").strip()
    if pane_id:
        print("\t".join([pane_id, session, str(window or ""), project]))
except (OSError, ValueError, TypeError):
    sys.exit(0)
PYEOF
            )"
            if [ -n "$snapshot_line" ]; then
                printf '%s\n' "$snapshot_line"
                return 0
            fi
        fi
    fi

    # 2. Fall back to a live tmux scan: pick the first attached session's
    #    active pane (best-effort — the snapshot is the authoritative source).
    if command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null; then
        tmux list-panes -a -F '#{pane_id}\t#{session_name}\t#{window_index}\t#{session_attached}' 2>/dev/null \
            | awk -F'\t' '$4 == "1" { print $1 "\t" $2 "\t" $3 "\t"; exit }'
    fi
    return 0
}

# Focus the resolved pane inside tmux. Returns 0 on success or no-op.
slot_state_value() {
    slot_index="$1"
    if [ ! -f "$STATE_JSON" ]; then
        printf '%s\n' "unknown"
        return 0
    fi
    snapshot_mtime="$(stat -f %m "$STATE_JSON" 2>/dev/null || stat -c %Y "$STATE_JSON" 2>/dev/null || echo 0)"
    if [ -z "$snapshot_mtime" ] || [ "$snapshot_mtime" = "0" ]; then
        printf '%s\n' "unknown"
        return 0
    fi
    now="$(date +%s)"
    snapshot_age=$((now - snapshot_mtime))
    if [ "$snapshot_age" -gt "$MAX_AGE_S" ]; then
        printf '%s\n' "stale"
        return 0
    fi
    python3 - "$STATE_JSON" "$slot_index" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        snap = json.load(fh)
    slots = snap.get("slots") if isinstance(snap, dict) else None
    idx = int(sys.argv[2])
    if not isinstance(slots, list) or idx < 0 or idx >= len(slots):
        print("unknown"); sys.exit(0)
    entry = slots[idx] or {}
    if not isinstance(entry, dict):
        print("unknown"); sys.exit(0)
    state = str(entry.get("state") or "").strip().lower()
    print(state or "unknown")
except (OSError, ValueError, TypeError):
    print("unknown")
PYEOF
}

focus_pane() {
    slot_index="$1"
    resolved="$(resolve_slot "$slot_index")"
    if [ -z "$resolved" ]; then
        # Nothing to focus — silent no-op keeps the touch bar calm.
        return 0
    fi
    pane_id="$(printf '%s' "$resolved" | cut -f1)"
    session="$(printf '%s' "$resolved" | cut -f2)"

    if [ -z "$pane_id" ]; then
        return 0
    fi

    if ! command -v tmux >/dev/null 2>&1; then
        printf 'tmux not installed; cannot focus pane %s\n' "$pane_id" >&2
        return 0
    fi

    if ! tmux has-session 2>/dev/null; then
        printf 'no tmux server running; cannot focus pane %s\n' "$pane_id" >&2
        return 0
    fi

    # If we're inside tmux, switch to the target session first so the pane
    # becomes visible. Outside tmux, select-pane alone is enough.
    # Note: ${TMUX:-} guards against unset under `set -u`.
    if [ -n "${TMUX:-}" ]; then
        if [ -n "$session" ]; then
            tmux switch-client -t "$session" 2>/dev/null || true
        fi
    fi

    tmux select-pane -t "$pane_id" 2>/dev/null || true
    return 0
}

# confirm_and_approve SLOT PROJECT — show AppleScript dialog and (on confirm)
# fire the approve script. Runs only when the slot is in "waiting" state and
# the user-visible host has a usable osascript + graphical session.
confirm_and_approve() {
    slot_index="$1"
    project="${2:-slot-$slot_index}"
    if [ ! -x "/usr/bin/osascript" ]; then
        focus_pane "$slot_index"
        return 0
    fi
    msg="Slot ${slot_index} (${project}) is waiting for permission.${IFS}${IFS}Press APPROVE to send Enter to that pane.${IFS}Press CANCEL to just focus."
    result="$(osascript \
        -e 'on run argv' \
        -e '  set theMessage to item 1 of argv' \
        -e '  set theTitle to item 2 of argv' \
        -e '  display dialog theMessage with title theTitle buttons {"Cancel","Approve"} default button "Cancel" giving up after 5' \
        -e '  set btn to button returned of result' \
        -e '  set gaveUp to gave up of result' \
        -e '  if gaveUp then' \
        -e '    return "timeout"' \
        -e '  else' \
        -e '    return btn' \
        -e '  end if' \
        -e 'end run' \
        -- "$msg" "Muxy approve" 2>/dev/null)"
    case "$result" in
        Approve)
            approve_script="${MUXY_APPROVE_SCRIPT:-/Users/yoseph/TouchBar/scripts/btt_muxy_slot_approve.sh}"
            if [ -x "$approve_script" ]; then
                "$approve_script" "$slot_index" >/dev/null 2>&1 || true
            fi
            ;;
        Cancel|timeout|"")
            focus_pane "$slot_index"
            ;;
        *)
            focus_pane "$slot_index"
            ;;
    esac
    return 0
}

# show_action_menu SLOT PROJECT — present an osascript choose-from-list with
# 4 actions (Focus / Kill / Retry / Logs) and dispatch the chosen one to the
# corresponding btt_muxy_slot_*.sh script. The Kill entry has a built-in
# BTTConfirmationRequired-style confirmation prompt ("Are you sure?") before
# invoking the destructive action. Used as a fallback for the long-press
# menu gesture since BTT 6.521's add_new_trigger JSON does not accept
# multi-action triggers (BTTAdditionalActions is silently rejected).
show_action_menu() {
    slot_index="$1"
    project="${2:-slot-$slot_index}"
    if [ ! -x "/usr/bin/osascript" ]; then
        focus_pane "$slot_index"
        return 0
    fi
    prompt="Slot ${slot_index} (${project})\n\nChoose an action:"
    choice="$(osascript \
        -e 'on run argv' \
        -e '  set thePrompt to item 1 of argv' \
        -e '  set theTitle to item 2 of argv' \
        -e '  try' \
        -e '    set theChoice to choose from list {"Focus","Kill","Retry","Logs"} with prompt thePrompt with title theTitle default items {"Focus"} without multiple selections allowed' \
        -e '    if theChoice is false then return ""' \
        -e '    return item 1 of theChoice' \
        -e '  on error errMsg number errNum' \
        -e '    return ""' \
        -e '  end try' \
        -e 'end run' \
        -- "$prompt" "Muxy slot ${slot_index}" 2>/dev/null)"
    case "$choice" in
        Focus)
            focus_pane "$slot_index"
            ;;
        Kill)
            # Destructive — require explicit confirmation before invoking.
            confirm="$(osascript \
                -e 'on run argv' \
                -e '  set theMsg to item 1 of argv' \
                -e '  set theTitle to item 2 of argv' \
                -e '  display dialog theMsg with title theTitle buttons {"Cancel","Kill"} default button "Cancel"' \
                -e '  return button returned of result' \
                -e 'end run' \
                -- "Permanently kill the tmux pane in slot ${slot_index} (${project})?" "Confirm Kill" 2>/dev/null)"
            if [ "$confirm" = "Kill" ] && [ -x "${MENU_SCRIPTS_DIR}/btt_muxy_slot_kill.sh" ]; then
                "${MENU_SCRIPTS_DIR}/btt_muxy_slot_kill.sh" "$slot_index" >/dev/null 2>&1 || true
            fi
            ;;
        Retry)
            if [ -x "${MENU_SCRIPTS_DIR}/btt_muxy_slot_retry.sh" ]; then
                "${MENU_SCRIPTS_DIR}/btt_muxy_slot_retry.sh" "$slot_index" >/dev/null 2>&1 || true
            fi
            ;;
        Logs)
            if [ -x "${MENU_SCRIPTS_DIR}/btt_muxy_slot_logs.sh" ]; then
                "${MENU_SCRIPTS_DIR}/btt_muxy_slot_logs.sh" "$slot_index" >/dev/null 2>&1 || true
            fi
            ;;
    esac
    return 0
}

# record_tap / check_double_tap — implements the script-level double-tap
# detection that emulates the long-press-menu UX (since BTT 6.521's
# add_new_trigger JSON cannot configure long-press triggers).
# On each invocation we write a millisecond-resolution timestamp to
# $TAP_STATE_DIR/last_tap_slot_${N}.ts; if a prior timestamp exists and is
# within DOUBLE_TAP_MS of now, treat this invocation as the second tap and
# trigger the action menu.
record_tap() {
    slot_index="$1"
    if [ ! -d "$TAP_STATE_DIR" ]; then
        mkdir -p "$TAP_STATE_DIR" 2>/dev/null || true
    fi
    if [ -w "$TAP_STATE_DIR" ]; then
        # %s.%N gives epoch.nanoseconds on Linux; macOS date lacks %N so we
        # fall back to plain epoch seconds. Using python3 gives us
        # sub-second precision portably.
        now_ms="$(python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null || date +%s)"
        printf '%s\n' "$now_ms" > "${TAP_STATE_DIR}/last_tap_slot_${slot_index}.ts" 2>/dev/null || true
    fi
}

is_double_tap() {
    slot_index="$1"
    last_file="${TAP_STATE_DIR}/last_tap_slot_${slot_index}.ts"
    if [ ! -f "$last_file" ]; then
        return 1
    fi
    last_ms="$(cat "$last_file" 2>/dev/null || echo 0)"
    now_ms="$(python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null || date +%s)"
    case "$last_ms" in
        ''|*[!0-9]*)
            return 1
            ;;
    esac
    diff_ms=$((now_ms - last_ms))
    if [ "$diff_ms" -ge 0 ] && [ "$diff_ms" -le "$DOUBLE_TAP_MS" ]; then
        return 0
    fi
    return 1
}

self_test() {
    slot="${1:-0}"
    if [ -f "$STATE_JSON" ]; then
        resolved="$(resolve_slot "$slot")"
        if [ -n "$resolved" ]; then
            pane_id="$(printf '%s' "$resolved" | cut -f1)"
            session="$(printf '%s' "$resolved" | cut -f2)"
            window="$(printf '%s' "$resolved" | cut -f3)"
            project="$(printf '%s' "$resolved" | cut -f4)"
            printf 'slot=%s pane_id=%s session=%s window=%s project=%s\n' \
                "$slot" "$pane_id" "$session" "$window" "$project"
        else
            printf 'slot=%s <empty snapshot slot>\n' "$slot"
        fi
    else
        printf 'slot=%s <state.json missing at %s>\n' "$slot" "$STATE_JSON"
    fi
    if command -v tmux >/dev/null 2>&1 && tmux has-session 2>/dev/null; then
        live="$(tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}' 2>/dev/null | head -1)"
        printf 'live_pane=%s\n' "$live"
    else
        printf 'live_pane=<no tmux server>\n'
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
            self_test "${2:-0}"
            exit 0
            ;;
        -*)
            print_usage
            exit 2
            ;;
    esac

    slot_index="$1"
    case "$slot_index" in
        ""|*[!0-9]*)
            print_usage
            exit 2
            ;;
    esac
    if [ "$slot_index" -lt 0 ] || [ "$slot_index" -gt 3 ]; then
        print_usage
        exit 2
    fi

    state="$(slot_state_value "$slot_index")"
    project="$(python3 - "$STATE_JSON" "$slot_index" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as fh:
        snap = json.load(fh)
    slots = snap.get("slots") or []
    idx = int(sys.argv[2])
    if idx < 0 or idx >= len(slots):
        sys.exit(0)
    entry = slots[idx] or {}
    print(str(entry.get("project") or ""))
except Exception:
    pass
PYEOF
    )"
    if [ "$state" = "waiting" ]; then
        confirm_and_approve "$slot_index" "$project"
    elif is_double_tap "$slot_index"; then
        # 2nd tap within DOUBLE_TAP_MS on a non-waiting slot → show menu.
        # Always record the tap (again) so the next tap is treated as a fresh
        # single-tap focus, not the start of another double-tap chain.
        show_action_menu "$slot_index" "$project"
        record_tap "$slot_index"
    else
        # First tap (or single tap after long pause) → focus + record tap.
        focus_pane "$slot_index"
        record_tap "$slot_index"
    fi
    exit 0
}

main "$@"
