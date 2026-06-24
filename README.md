# Muxy TouchBar Control Panel

BetterTouchTool Touch Bar widgets that turn the MacBook Pro Touch Bar into a Muxy-first control panel. It shows which agents/projects are active, idle, or waiting for permission, using multiple widgets to fill the bar. Vibe Island remains available as an optional fallback.

## Overview

A compact, read-only status collector (`scripts/agentmax_status.py`) reads Muxy's local session state and drives multiple BetterTouchTool **Shell Script / Task Widgets**. The default layout is one summary widget plus four per-agent/project slot widgets. Each slot updates every 2 seconds and displays a compact label such as `● agent` (active), `○ agent` (idle), `⏸ agent` (waiting), or `·` (empty). When Muxy has no active sessions, the panel falls back to Vibe Island, OMX, or Multica data.

## Daemon-driven architecture

The control panel is driven by a long-running Python daemon (`scripts/btt_muxy_daemon.py`) that polls Muxy's session state every ~4 seconds and writes a canonical snapshot to `~/.local/share/touchbar-muxy/state.json`. Each Touch Bar slot script reads that snapshot (with a ≤30 s freshness window and a live-tmux fallback) and emits its widget label. This avoids spawning a fresh Python interpreter per widget per refresh and keeps widget text consistent across all 5 widgets.

Daemon lifecycle:

- The daemon is launched and supervised by macOS launchd via `scripts/com.touchbar.muxy-daemon.plist`, installed to `~/Library/LaunchAgents/com.touchbar.muxy-daemon.plist`.
- `RunAtLoad=true` starts it on every login; `KeepAlive={SuccessfulExit:false, Crashed:true}` restarts it after a crash.
- `python3 scripts/btt_muxy_daemon.py --status` reports the running PID and the time since the last poll (the daemon's single-instance lock uses `~/.local/share/touchbar-muxy/daemon.sock` + `daemon.pid`).
- `python3 scripts/btt_muxy_daemon.py --shutdown` cleanly stops the daemon.
- The installer (`scripts/btt_install_muxy_control_panel.py`) automatically copies the plist to `~/Library/LaunchAgents/` and runs `launchctl load` so the daemon starts immediately and on every reboot.

Tap semantics:

- **Tap** on a non-waiting slot → `scripts/btt_muxy_slot_focus.sh` calls `tmux select-pane -t pane_id` to focus that session's tmux pane.
- **Tap** on a waiting slot → preview AppleScript dialog (`Approve` / `Cancel`, 5 s timeout). `Approve` invokes `scripts/btt_muxy_slot_approve.sh` which sends `Enter` via `tmux send-keys`.
- **Two quick taps** on the same slot (within 500 ms) → `scripts/btt_muxy_slot_focus.sh` raises an AppleScript `choose from list` with **Focus / Kill / Retry / Logs**. `Kill` requires an extra 2-stage confirmation dialog before invoking the destructive action.
- **Show Context Menu**: BTT 6.521's `add_new_trigger` JSON does not support multi-action triggers with category gating; the long-press menu UX is therefore implemented at script level via 2-tap detection in `btt_muxy_slot_focus.sh`. This achieves the user-stated UX intent (show a menu with the 4 actions; Kill requires confirmation) without requiring BTT-side long-press configuration.

State palette (per-slot background color, driven by the daemon's snapshot):

- **Green** `52, 199, 89` — working session
- **Orange** `255, 149, 0` — waiting for permission
- **Red** `255, 59, 48` — error / crashed pane
- **Gray** `142, 142, 147` — idle / no session

Attention pulse: waiting slots prepend `●` / `◌` markers on alternating daemon polls (~8 s cycle), giving a text-based pulse without BTT-side animation. The original Flash-Background-Color pulse cannot be configured via BTT 6.521 JSON (`update_touch_bar_widget` is destructive and silently wipes other widget fields; `add_new_trigger` rejects multi-action payloads).

Runtime files (all under `~/.local/share/touchbar-muxy/`):

- `state.json` — canonical snapshot consumed by widget scripts.
- `daemon.sock` — Unix-domain single-instance lock (chmod 600).
- `daemon.pid` — PID file; daemon writes its PID here on startup.
- `daemon.log` — daemon activity log (ISO 8601 timestamps).
- `launchd.{out,err}.log` — stdout/stderr redirected by launchd.
- `last_tap_slot_{N}.ts` — per-slot tap timestamp for 2-tap detection.

## Structure

```
├── scripts/
│   ├── agentmax_status.py                  # Core status collector (stdlib-only, read-only by default)
│   ├── btt_muxy_daemon.py                  # Long-running daemon; polls Muxy, writes state.json
│   ├── btt_muxy_lib.sh                     # Shared shell helpers for slot scripts
│   ├── btt_muxy_slot_summary.sh            # Summary widget wrapper (emits MUXY label)
│   ├── btt_muxy_slot_0.sh … slot_3.sh      # Per-agent/project slot widget wrappers
│   ├── btt_muxy_slot_focus.sh              # Tap handler: focus / approve / 2-tap menu
│   ├── btt_muxy_slot_approve.sh            # Approve handler: tmux send-keys Enter
│   ├── btt_muxy_slot_kill.sh               # Long-press menu: kill tmux pane
│   ├── btt_muxy_slot_retry.sh              # Long-press menu: respawn tmux pane
│   ├── btt_muxy_slot_logs.sh               # Long-press menu: capture pane scrollback → pbcopy
│   ├── btt_muxy_slot_tap.sh                # Tap action helper for Muxy widgets
│   ├── btt_install_muxy_control_panel.py   # Installer for the Muxy control panel
│   ├── btt_register_touchbar_widget.py     # List/global register helper
│   ├── btt_agentmax_widget.sh              # Legacy Vibe Island/OMX fallback widget
│   ├── btt_vibe_island_tap.sh              # Tap action helper for Vibe Island
│   ├── btt_muxy_tap.sh                     # Legacy tap helper for Muxy app
│   ├── btt_multica_tap.sh                  # Legacy tap helper for Multica app
│   └── com.touchbar.muxy-daemon.plist      # launchd plist for the daemon
├── config/
│   ├── project-aliases.json        # Project alias mappings
│   ├── status-protocol.json        # Status protocol definition
│   ├── btt-muxy-widgets.json       # Muxy control panel widget config (UUIDs, widths, scripts)
│   └── btt-widget.example.json     # Example single-widget config
├── tests/
│   ├── test_agentmax_status.py   # Test suite
│   └── fixtures/
├── docs/
│   ├── BTT_SETUP.md              # BetterTouchTool setup guide
│   ├── STATUS_PROTOCOL.md        # Status protocol documentation
│   └── DEBUGGING.md              # Debugging guide
├── setup.sh                      # Local setup helper (safe, no destructive actions)
└── prompt-exports/               # Architecture planning artifacts
```

## Quick Start

```sh
# Make scripts executable and print setup instructions
./setup.sh

# Install the Muxy control panel widgets into BetterTouchTool
python3 scripts/btt_install_muxy_control_panel.py

# Verify the 5 widgets are registered
python3 scripts/btt_register_touchbar_widget.py --list | grep 'muxy'
```

This creates one summary widget (200 px) and four slot widgets (95 px each) in the global Touch Bar. Tap any widget to open/focus the Muxy app.

To reinstall after BTT or preset changes, run the installer again; it only replaces the Muxy widgets and leaves existing Vibe Island triggers intact.

## Cleanup

The Touch Bar is a Muxy-first control panel. The previous single Vibe Island widget (UUID `4EA2B0F6-983C-4DD9-8F30-5F7161DCB601`) and its tap trigger (UUID `17D6AE4C-4829-4115-8709-AEDAC8F53552`) are **removed** on every install. The installer also deletes any other global Touch Bar trigger whose UUID is not in `config/btt-muxy-widgets.json` (orphan empty triggers).

This cleanup is on by default. Disable it with `--no-cleanup-legacy`, or run it alone with `--cleanup-legacy-only`.

After install, only 5 widgets should be in the Touch Bar:

```sh
python3 scripts/btt_register_touchbar_widget.py --list | grep 'muxy' | wc -l   # → 5
```

The macOS input-source (globe) widget that BTT always renders is not ours to delete.

### Slot fill priority

Each of the 4 slot widgets (`scripts/btt_muxy_slot_{0,1,2,3}.sh`) fills from the highest-priority source available:

1. Muxy waiting sessions (`⏸ agent`)
2. Muxy active sessions (`● agent`)
3. Muxy idle sessions (`○ agent`)
4. Vibe Island active sessions (`● agent`)
5. OMX tmux panes (`● agent`)
6. Empty (`·`) only if no work exists across all sources

This means slots are always meaningful whenever any agent is working — across Muxy, Vibe Island, or OMX.

### Summary widget

The summary widget always uses a **MUXY-style label** (e.g. `MUXY C1 F0 O25 yoseph`, `MUXY · W1 I0 A0 scorio`, or `MUXY idle`) whenever `~/Library/Application Support/Muxy/` exists. It only falls back to OMX/Vibe Island when Muxy support is completely absent (no server, no socket, no app support directory).

## Requirements

- macOS with Touch Bar
- [BetterTouchTool](https://folivora.ai/)
- [Muxy](https://muxy.ai/) running with active sessions (primary source)
- [Vibe Island](https://vibeisland.app/) as an optional fallback
- Python 3 (stdlib only, no pip dependencies)

## License

MIT