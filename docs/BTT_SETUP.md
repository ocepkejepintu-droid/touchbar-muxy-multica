# BetterTouchTool setup

Manual setup is canonical. `config/btt-widget.example.json` is only a reference because BetterTouchTool import formats vary by version and local preset state.

## One-time repo setup

From `/Users/yoseph/TouchBar`, run:

```sh
./setup.sh
```

`setup.sh` is intentionally small and safe: it only marks the repo-local scripts executable and prints the exact BetterTouchTool commands/paths below. It installs no dependencies and performs no destructive actions.

## Recommended widget settings

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

## Tap action

Tapping the widget does nothing unless you assign a tap action in BetterTouchTool. The tap action is not configured automatically; you must set it manually in the BTT widget settings.

### Primary tap action (Vibe Island)

Assign a shell-script tap action that opens Vibe Island:

```sh
/Users/yoseph/TouchBar/scripts/btt_vibe_island_tap.sh
```

This helper runs `/usr/bin/open -a "Vibe Island"` and prints `OK:`, `NOOP:`, or `ERROR:` tokens. It supports `--dry-run` and `--self-test`.

### Legacy tap actions (optional)

Muxy:

```sh
/Users/yoseph/TouchBar/scripts/btt_muxy_tap.sh
```

Multica:

```sh
/Users/yoseph/TouchBar/scripts/btt_multica_tap.sh
```

### Diagnostic-only tap actions (not primary)

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
/Users/yoseph/TouchBar/scripts/btt_agentmax_widget.sh
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar
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