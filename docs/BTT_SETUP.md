# BetterTouchTool setup

The recommended setup uses the Muxy control panel installer. Manual single-widget setup remains documented as a fallback.

## Muxy control panel (recommended)

The control panel uses multiple BetterTouchTool widgets to fill the Touch Bar: one summary widget plus four per-agent/project slot widgets. A long-running daemon (`scripts/btt_muxy_daemon.py`) polls Muxy's session state every ~4 seconds and writes a canonical snapshot to `~/.local/share/touchbar-muxy/state.json`. Each Touch Bar slot script reads that snapshot (with a ≤30 s freshness window and a live-tmux fallback) and emits its widget label.

### One-time install

From `/Users/yoseph/TouchBar`, run:

```sh
./setup.sh
python3 scripts/btt_install_muxy_control_panel.py
```

The installer:

- Registers 5 global Touch Bar widgets using the UUIDs/widths in `config/btt-muxy-widgets.json`:
  - `summary` (200 px)
  - `slot_0` … `slot_3` (95 px each)
- Leaves existing Vibe Island and other global triggers intact.
- Sets each widget to refresh every 2 seconds.
- Wires each slot's tap action to `scripts/btt_muxy_slot_focus.sh {slot_index}` (state-aware: focus pane for non-waiting slots; preview/approve dialog for waiting slots).
- Installs the daemon plist (`scripts/com.touchbar.muxy-daemon.plist`) to `~/Library/LaunchAgents/com.touchbar.muxy-daemon.plist` and runs `launchctl load` so the daemon starts immediately and on every reboot.

### Widget Group structure

Each of the 5 Muxy widgets is registered with three layers (color + SF Symbol icon + label), driven by the daemon snapshot at install time:

- **Background color** — set from the per-slot state palette (green working / orange waiting / red error / gray idle).
- **SF Symbol icon** — set from `config/project-icons.json` (e.g. `building.2`, `chart.bar`, `bolt.fill`).
- **Label** — set from the daemon snapshot (e.g. `MUXY ●22 ◌0` for summary; `bpo-zipang / ~/bpo-zipang` for a slot).

Re-running the installer pulls the freshest daemon snapshot and re-registers the widgets with the new color/icon/label.

### Tap action semantics

| Gesture | Behavior |
|---------|----------|
| Tap on non-waiting slot | `scripts/btt_muxy_slot_focus.sh` calls `tmux select-pane -t pane_id` to focus that session's tmux pane. |
| Tap on waiting slot | AppleScript dialog (`Approve` / `Cancel`, 5 s timeout). `Approve` invokes `scripts/btt_muxy_slot_approve.sh` which sends `Enter` via `tmux send-keys`. |
| Two quick taps on same slot (within 500 ms) | **Show Context Menu** via AppleScript `choose from list` with `Focus / Kill / Retry / Logs`. `Kill` requires an extra 2-stage confirmation before invoking the destructive action. |
| Single tap after long pause (>500 ms) | Treated as a fresh single-tap focus (resets the 2-tap chain). |

> **Why script-level menu instead of BTT-side long-press**: BTT 6.521's `add_new_trigger` JSON does not support multi-action triggers with category gating — `BTTAdditionalActions` payloads are silently rejected (the widget is added but the multi-action structure is dropped). The 2-tap fallback is the cleanest pragmatic UX that achieves the user-stated intent without requiring BTT-side long-press configuration.

### Hold-2s approve (AC-7)

The original spec's "hold 2 seconds to approve" UX is delivered via the script-level preview dialog above: tap a waiting slot, see the dialog, click `Approve` to send `Enter`. The literal BTT-side "Long Press 2s" trigger condition cannot be configured via JSON in BTT 6.521 (same multi-action limitation). Tap-only emits no `tmux send-keys` call; the dialog's `Approve` button emits `tmux send-keys -t pane_id Enter` via `scripts/btt_muxy_slot_approve.sh`. The "hold-2s" gesture is therefore implemented as a single-tap preview dialog with an explicit `Approve` confirmation rather than a BTT-side timer.

### Daemon lifecycle

| Command | Effect |
|---------|--------|
| `python3 scripts/btt_muxy_daemon.py --status` | Prints `daemon: running pid=<PID>, last poll <secs> ago` and exits 0; second invocation is idempotent (also exits 0). |
| `python3 scripts/btt_muxy_daemon.py --shutdown` | Sends SIGTERM to the running daemon; exits 0 when the daemon exits cleanly. |
| `launchctl unload ~/Library/LaunchAgents/com.touchbar.muxy-daemon.plist` | Stops the daemon (launchd will not restart it until `launchctl load`). |
| `launchctl load ~/Library/LaunchAgents/com.touchbar.muxy-daemon.plist` | Starts the daemon and configures autostart on every login (launchd's `KeepAlive` handles crash restart). |

The launchd plist itself has `RunAtLoad=true` and `KeepAlive={SuccessfulExit:false, Crashed:true}` so launchd starts the daemon on login and restarts it on crash, but does not fight the daemon's own single-instance lock on a clean exit.

### Verify the install

```sh
python3 scripts/btt_register_touchbar_widget.py --list | grep 'muxy'
```

You should see 5 lines marked `[muxy]`, one for each widget.

To inspect the compact status the widgets display:

```sh
/Users/yoseph/TouchBar/scripts/btt_muxy_slot_summary.sh
/Users/yoseph/TouchBar/scripts/btt_muxy_slot_0.sh
```

### Reinstall or repair

If BTT presets change or widgets disappear, rerun the installer:

```sh
python3 scripts/btt_install_muxy_control_panel.py
```

It purges only the Muxy widget UUIDs and recreates them; Vibe Island and other triggers are preserved.

### Self-test

```sh
python3 scripts/btt_install_muxy_control_panel.py --self-test
```

This checks that config, scripts, UUIDs, and payload construction are valid.

## Manual single-widget setup (fallback)

Manual setup is canonical. `config/btt-widget.example.json` is only a reference because BetterTouchTool import formats vary by version and local preset state.

### One-time repo setup

From `/Users/yoseph/TouchBar`, run:

```sh
./setup.sh
```

`setup.sh` is intentionally small and safe: it only marks the repo-local scripts executable and prints the exact BetterTouchTool commands/paths below. It installs no dependencies and performs no destructive actions.

### Recommended widget settings

Create a BetterTouchTool Touch Bar shell script widget:

- Type: `Shell Script / Task Widget` for Touch Bar
- Name: `Vibe Island Touch Bar`
- Refresh interval: `2 seconds`
- Width: `240 px` recommended; `210–280 px` works well
- Script path:
  ```sh
  /Users/yoseph/TouchBar/scripts/btt_agentmax_widget.sh
  ```
- Label/font/color: user preference; keep enough width for one compact `VI ...` line

The wrapper calls:

```sh
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
```

If the collector fails or returns no output, the widget prints the safe fallback:

```text
VI !err
```

### Tap action

Tapping the widget does nothing unless you assign a tap action in BetterTouchTool. The tap action is not configured automatically; you must set it manually in the BTT widget settings.

#### Primary tap action (Vibe Island)

Assign a shell-script tap action that opens Vibe Island:

```sh
/Users/yoseph/TouchBar/scripts/btt_vibe_island_tap.sh
```

This helper runs `/usr/bin/open -a "Vibe Island"` and prints `OK:`, `NOOP:`, or `ERROR:` tokens. It supports `--dry-run` and `--self-test`.

#### Legacy tap actions (optional)

Muxy:

```sh
/Users/yoseph/TouchBar/scripts/btt_muxy_tap.sh
```

Multica:

```sh
/Users/yoseph/TouchBar/scripts/btt_multica_tap.sh
```

#### Diagnostic-only tap actions (not primary)

For expanded status detail or debugging, you can assign additional tap actions that open Terminal. These are diagnostic tools, not the primary tap action:

Detail status:

```applescript
tell application "Terminal" to do script "cd /Users/yoseph/TouchBar && python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar"
activate application "Terminal"
```

Debug status:

```applescript
tell application "Terminal" to do script "cd /Users/yoseph/TouchBar && python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar"
activate application "Terminal"
```

## Validate before adding to BTT

Run:

```sh
/Users/yoseph/TouchBar/scripts/btt_muxy_slot_summary.sh --self-test
/Users/yoseph/TouchBar/scripts/btt_muxy_slot_0.sh --self-test
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar
/Users/yoseph/TouchBar/scripts/btt_muxy_slot_tap.sh --self-test
/Users/yoseph/TouchBar/scripts/btt_vibe_island_tap.sh --self-test
/Users/yoseph/TouchBar/scripts/btt_muxy_tap.sh --self-test
/Users/yoseph/TouchBar/scripts/btt_multica_tap.sh --self-test
```

The first command should print exactly one compact line beginning with `VI` when Vibe Island has live sessions, otherwise `OMX` or legacy `MUXY`, for example:

```text
VI Claude covers · Read
VI ! Claude covers
VI · 2 agents covers,agent
OMX · W0 I7 B7 touch,tmux
```

For `VI`, the collector reads Vibe Island's local `session-terminals.json`, shows the active agent/project, and uses `!` when a recent permission request is visible in the Vibe Island log. Failure is `VI !err`.

The collector is read-only by default. It writes only if
`config/status-protocol.json` explicitly enables optional snapshot logging.

## Cleanup

The Touch Bar is a Muxy-first control panel. The previous single Vibe Island widget (UUID `4EA2B0F6-983C-4DD9-8F30-5F7161DCB601`) and its tap trigger (UUID `17D6AE4C-4829-4115-8709-AEDAC8F53552`) are removed automatically on every install. The installer also deletes any other global Touch Bar trigger whose UUID is not listed in `config/btt-muxy-widgets.json` — these are orphan empty triggers left behind from earlier single-widget setups.

Run cleanup alone (without re-installing widgets):

```sh
python3 scripts/btt_install_muxy_control_panel.py --cleanup-legacy-only
```

Skip cleanup during install:

```sh
python3 scripts/btt_install_muxy_control_panel.py --no-cleanup-legacy
```

Cleanup is safe to rerun; it deletes by UUID, so the operation is idempotent. The macOS input-source (globe) widget that BTT always renders is not deleted — BTT owns it.

### Slot fill priority

The 4 slot widgets fill in this priority order, so a slot is meaningful whenever any agent is working:

1. Muxy waiting (`⏸ agent`) — takes priority over Muxy active
2. Muxy active (`● agent`)
3. Muxy idle (`○ agent`)
4. Vibe Island active (`● agent`)
5. OMX tmux panes (`● agent`)
6. Empty (`·`) only when no work exists across all sources

Same agent reported by both Muxy and Vibe Island is deduped by `(source, agent, active)` tuple.

### Summary widget label

The summary widget always emits a MUXY-style label whenever Muxy state exists (`MUXY C{w} F{d} O{o} {top}`, `MUXY · W{n} I0 A0 {top}`, or `MUXY idle`). It only falls back to `OMX …` / `VI …` when `~/Library/Application Support/Muxy/` is completely absent.

## Troubleshooting

### Installer reports fewer than 5 Muxy widgets

Run the list command and compare with `config/btt-muxy-widgets.json`:

```sh
python3 scripts/btt_register_touchbar_widget.py --list
```

If the count is wrong, rerun the installer:

```sh
python3 scripts/btt_install_muxy_control_panel.py
```

### Widgets show `·` or `!err`

1. Confirm Muxy is running and has active sessions.
2. Run the collector directly to see the raw output:
   ```sh
   python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
   ```
3. If Muxy state is missing or corrupt, the collector falls back to Vibe Island or OMX.
4. Check `TMPDIR/touchbar-muxy-slot.log` for wrapper errors.

### Muxy data is missing but Vibe Island is active

The Muxy source has size-bounded reads and short timeouts. If Muxy returns no sessions, the collector automatically tries Vibe Island/OMX next. This is expected behavior, not an error.

### BTT crashes or widgets disappear after install

The installer backs up nothing; BTT syncs its own presets. To recover:

1. Quit BetterTouchTool.
2. Restore the last BTT preset backup from `~/Library/Application Support/BetterTouchTool/` if you have one.
3. Rerun the installer.

### Tap does not open Muxy

1. Verify the tap helper is executable:
   ```sh
   /Users/yoseph/TouchBar/scripts/btt_muxy_slot_tap.sh --self-test
   ```
2. Check that Muxy.app is installed in `/Applications` or `/System/Applications`.
3. Use the macOS `open -a Muxy` command manually to confirm the app launches.

### Daemon / runtime files

All daemon runtime files live under `~/.local/share/touchbar-muxy/`:

| File | Purpose |
|------|---------|
| `state.json` | Canonical snapshot consumed by widget scripts. Must be refreshed within the last 30 s for slot scripts to use it; otherwise they fall back to a live `tmux list-panes` scan. |
| `daemon.sock` | Unix-domain single-instance lock (chmod 600). The daemon refuses to start a second instance while this socket is held. |
| `daemon.pid` | PID file written on daemon startup. Used by `--status` and `--shutdown`. |
| `daemon.log` | Daemon activity log (ISO 8601 timestamps). |
| `launchd.out.log` | launchd stdout (process startup banners, occasional prints). |
| `launchd.err.log` | launchd stderr (uncaught exceptions). |
| `last_tap_slot_{N}.ts` | Per-slot tap timestamp for 2-tap detection. Used by `btt_muxy_slot_focus.sh` to decide between focus and menu. |

### Daemon won't start

1. Check `~/.local/share/touchbar-muxy/launchd.err.log` for the most recent exception.
2. Run `python3 scripts/btt_muxy_daemon.py --status` — if it reports `not running`, run `launchctl load ~/Library/LaunchAgents/com.touchbar.muxy-daemon.plist` and watch `launchd.err.log` for the next attempt.
3. Verify the plist is valid: `plutil -lint scripts/com.touchbar.muxy-daemon.plist`.

### Slot shows stale data

If a slot keeps showing the same label even though the tmux pane state changed:

1. `cat ~/.local/share/touchbar-muxy/state.json | python3 -m json.tool | head -40` — inspect the snapshot's `last_poll` and the slot's `state` field.
2. If `last_poll` is more than 30 s old, the daemon may be wedged — check `daemon.log` and restart with `launchctl unload && launchctl load` of the plist.
3. If `last_poll` is fresh but the slot still shows stale data, run `scripts/btt_muxy_slot_{N}.sh --self-test` to see what the script emits directly.
