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
- Name: `Muxy Notification Center`
- Refresh interval: `2 seconds`
- Width: `240 px` recommended; `210–280 px` works well
- Script path:
  ```sh
  /Users/yoseph/TouchBar/scripts/btt_agentmax_widget.sh
  ```
- Label/font/color: user preference; keep enough width for one compact `OMX ...` line

The wrapper calls:

```sh
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
```

If the collector fails or returns no output, the widget prints the safe fallback:

```text
MUXY !err
```

The collector does not print `MUXY !err` for ordinary compact truncation; that
fallback is reserved for actual wrapper/collector failures. The compact BTT path
uses a bounded JSONL file set plus bounded byte-tail reads so the 2-second
refresh does not scan unbounded historical logs.

## Tap action

Tapping the widget does nothing unless you assign a tap action in BetterTouchTool. The tap action is not configured automatically; you must set it manually in the BTT widget settings.

### Muxy widget tap action

Assign a shell-script tap action that opens the Muxy app:

```sh
/Users/yoseph/TouchBar/scripts/btt_muxy_tap.sh
```

This helper runs `/usr/bin/open -a Muxy` and prints `OK:`, `NOOP:`, or `ERROR:` tokens. It supports `--dry-run` and `--self-test`.

### Multica widget tap action

If you also have a Multica widget, assign its tap action with the Multica helper:

```sh
/Users/yoseph/TouchBar/scripts/btt_multica_tap.sh
```

This helper discovers the Multica app name (`MULTICA_APP_NAME` env override > `Multica` > `Multica Desktop`) and opens it with `/usr/bin/open`. It also supports `--dry-run` and `--self-test`.

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
/Users/yoseph/TouchBar/scripts/btt_muxy_tap.sh --self-test
/Users/yoseph/TouchBar/scripts/btt_multica_tap.sh --self-test
```

The first command should print exactly one compact line beginning with `MUXY` when live muxy/tmux sessions are visible, otherwise `OMX`, for example:

```text
MUXY C1 F0 O3 scorio
OMX ◒ W1 I0 A1 now:touch wait:tmux
```

For `MUXY`, `C` means confirmation-needed panes, `F` means finished/successful panes, and `O` means still-live panes. The empty state is `MUXY C0 F0 O0 -` and failure is `MUXY !err`. For fallback `OMX`, `W` is working, `I` is idle/stale, `B` is blocking/stalled, `A` is non-blocking attention, `now:` is the current project, and `wait:` is an inferred wait/attention label.

The collector is read-only by default. It writes only if
`config/status-protocol.json` explicitly enables optional snapshot logging.
