#!/bin/sh
# scripts/btt_muxy_lib.sh — shared helpers for daemon-driven Muxy Touch Bar widgets.
#
# Sourced by btt_muxy_slot_*.sh and btt_muxy_slot_summary.sh. Provides:
#   - muxy_state_age()        : age in seconds of the daemon snapshot (huge if missing).
#   - muxy_state_fresh()      : returns 0 if snapshot is fresh (<= MUXY_STATE_MAX_AGE_S).
#   - muxy_emit_slot N        : emit "project / agent" for slot N from state.json.
#   - muxy_emit_summary       : emit "MUXY ●W ◌Wa" working + waiting counts.
#   - muxy_emit_fallback SLOT : emit compact label from agentmax_status.py fallback.
#
# All emitters fall back gracefully (exit 0, never error).

set -u

MUXY_ROOT="${MUXY_ROOT:-/Users/yoseph/TouchBar}"
MUXY_STATE_JSON="${MUXY_STATE_JSON:-$HOME/.local/share/touchbar-muxy/state.json}"
MUXY_STATUS_SCRIPT="${MUXY_STATUS_SCRIPT:-$MUXY_ROOT/scripts/agentmax_status.py}"
MUXY_STATE_MAX_AGE_S="${MUXY_STATE_MAX_AGE_S:-30}"

# muxy_state_age — print age in seconds of state.json. Prints huge number if missing.
muxy_state_age() {
    if [ ! -f "$MUXY_STATE_JSON" ]; then
        printf '999999\n'
        return 0
    fi
    snapshot_mtime="$(stat -f %m "$MUXY_STATE_JSON" 2>/dev/null || stat -c %Y "$MUXY_STATE_JSON" 2>/dev/null || echo 0)"
    if [ -z "$snapshot_mtime" ] || [ "$snapshot_mtime" = "0" ]; then
        printf '999999\n'
        return 0
    fi
    now="$(date +%s)"
    printf '%s\n' "$((now - snapshot_mtime))"
}

# muxy_state_fresh — exit 0 if snapshot is fresh, 1 otherwise.
muxy_state_fresh() {
    age="$(muxy_state_age)"
    if [ -z "$age" ] || [ "$age" -gt "$MUXY_STATE_MAX_AGE_S" ]; then
        return 1
    fi
    return 0
}

# muxy_emit_slot N — emit "project / agent" from state.json for slot index N.
# Waiting slots get a pulse marker prepended (alternates between ● and ◌
# on each daemon poll via state.json's pulse_phase field). This produces a
# visible blink on waiting slots at the daemon's ~4s poll cadence.
# Falls back to empty (caller should use muxy_emit_fallback).
muxy_emit_slot() {
    n="$1"
    if ! muxy_state_fresh; then
        return 1
    fi
    python3 - "$MUXY_STATE_JSON" "$n" <<'PYEOF'
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
    project = str(entry.get("project") or "").strip()
    agent = str(entry.get("agent") or "").strip()
    state = str(entry.get("state") or "").strip().lower()
    pulse = int(snap.get("pulse_phase") or 0) if isinstance(snap, dict) else 0
    if not project and not agent:
        sys.exit(0)
    if project and agent and project != agent:
        body = f"{project} / {agent}"
    elif project:
        body = project
    else:
        body = agent
    if state == "waiting":
        marker = "\u25cf" if (pulse & 1) else "\u25cc"
        print(f"{marker} {body}")
    else:
        print(body)
except (OSError, ValueError, TypeError):
    sys.exit(0)
PYEOF
}

# muxy_emit_summary — emit "MUXY ●W ◌Wa" from state.json counts.
muxy_emit_summary() {
    if ! muxy_state_fresh; then
        return 1
    fi
    python3 - "$MUXY_STATE_JSON" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        snap = json.load(fh)
    counts = snap.get("counts") if isinstance(snap, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    working = int(counts.get("working") or 0)
    waiting = int(counts.get("waiting") or 0)
    print(f"MUXY \u25cf{working} \u25cc{waiting}")
except (OSError, ValueError, TypeError):
    sys.exit(0)
PYEOF
}

# muxy_emit_fallback SLOT — emit compact label from agentmax_status.py.
# SLOT can be 0..3 or "summary". Always emits one line.
muxy_emit_fallback() {
    slot="$1"
    output="$(python3 "$MUXY_STATUS_SCRIPT" --slot "$slot" --root "$MUXY_ROOT" 2>/dev/null | sed -n '1p')"
    if [ -z "$output" ]; then
        printf '%s\n' '·'
    else
        printf '%s\n' "$output"
    fi
}
