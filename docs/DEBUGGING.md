# Debugging Agentmax Touch Bar status

## Quick checks

From `/Users/yoseph/TouchBar`:

```sh
./setup.sh
scripts/btt_agentmax_widget.sh
python3 scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar
```

Expected baseline: the wrapper prints one line beginning with `MUXY` when live muxy/tmux sessions are visible, otherwise `OMX`; `--smoke` exits `0` with an `OK compact=...` message.

## If BTT shows `MUXY !err`

`MUXY !err` comes from the shell wrapper or collector and means the collector failed or returned no output. It is not used for ordinary compact truncation. The wrapper intentionally suppresses Python tracebacks so BTT never displays noisy errors.

Check:

1. The wrapper is executable:
   ```sh
   ls -l /Users/yoseph/TouchBar/scripts/btt_agentmax_widget.sh
   ```
2. Python is available to BTT as `python3`.
3. The collector runs directly:
   ```sh
   python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
   ```
4. The repo still has `.omx` state under `/Users/yoseph/TouchBar/.omx`.
5. Run `--debug` and inspect `Debug diagnostics` for missing/corrupt sources.

## Detail vs debug

Use `--detail` for a readable status summary. Use `--debug` when investigating sources and reconciliation decisions.

`--debug` includes:

- source files with `ok`, `missing`, or `error` status
- files read
- missing files
- JSON/JSONL parse errors
- bounded JSONL tail counts; compact/BTT mode caps both the JSONL file set and per-file byte tails so large log histories are not scanned every refresh
- reconciliation decisions, such as completed lifecycle markers overriding stale active flags

Example source diagnostics:

```text
Debug diagnostics:
  source_files:
    - ok json .omx/state/session.json
    - missing json .omx/state/sessions/<id>/hud-state.json
    - error json .omx/state/sessions/<id>/skill-active-state.json (...)
  json_errors:
    - {'path': '...', 'error': '...'}
```

Missing optional files are usually fine. Missing required files or JSON parse errors explain `json` attention labels and may explain `MUXY !err` if collection cannot continue.

## Corrupt or missing source diagnostics

The collector is read-only by default and only inspects state/log tails under `--root`. It writes only when optional snapshot logging is explicitly enabled in `config/status-protocol.json`. For corrupt or missing state:

```sh
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar | sed -n '/Debug diagnostics:/,$p'
```

Look for:

- `files_missing`: required state paths that were not present
- `json_errors`: corrupt JSON/JSONL or failed file reads
- `jsonl_files`: bounded log-tail counts and latest event times
- `decisions`: lifecycle reconciliation notes, especially completed state overriding stale active markers

If current state is unrecoverably corrupt, `/Users/yoseph/TouchBar-backups/TouchBar-current-20260528-042500` exists as a historical backup for comparison; do not treat it as the live source unless intentionally restoring outside this setup/debug flow.

## Known tmux placeholder attention

A compact line with `A... wait:tmux` or `B... wait:...,tmux`, or detail/debug output containing `tmux_invalid_config`, can be expected when `.omx/tmux-hook.json` still points at the placeholder target:

```text
replace-with-tmux-pane-id
```

That means tmux hook injections are being skipped as invalid config. It is an attention signal, not a BTT wrapper failure.

To inspect it:

```sh
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar | grep -A5 -i tmux
```

## Stale notify/authority attention

`notify_unhealthy` and `authority_stale` are non-blocking attention kinds by default. They usually appear as `A... wait:notify` unless another blocking item exists.

Relevant config in `config/status-protocol.json`:

```json
"attention": {
  "authority_heartbeat_stale_seconds": 600,
  "notify_tick_stale_seconds": 600
}
```

Inspect notify state with:

```sh
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar | grep -A8 -i notify
```

A stale notify heartbeat/tick means the notification fallback watcher has not updated recently. It does not mean the compact widget failed.

## Project aliases

Short labels are resolved from `config/project-aliases.json` after the main protocol config is loaded. Current aliases keep this repo short in the Touch Bar:

```json
{
  "paths": {
    "/Users/yoseph/TouchBar": "touch"
  },
  "basenames": {
    "TouchBar": "touch"
  }
}
```

If a compact line shows a longer or unexpected project label, check that file and rerun:

```sh
python3 scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
```

## Refresh cadence

BTT should refresh the widget every `2 seconds`. The compact spinner also uses the protocol cadence from `config/status-protocol.json`:

```json
"spinner_cadence_seconds": 2
```

If BTT refreshes slower than 2 seconds, the widget is still safe; the spinner simply advances less often.

## JSON output for inspection

For machine-readable troubleshooting:

```sh
python3 scripts/agentmax_status.py --json --root /Users/yoseph/TouchBar
```

Top-level fields include `compact`, `operator_summary`, `active_runs`, `completed_runs`, `stalled_runs`, `attention`, `notify_health`, `tmux_health`, `metrics`, and `diagnostics`.

Start with `operator_summary` when answering operator questions:

- `counts.working`: who is actively working now
- `counts.idle_stale`: who is active but stale/idle
- `now.project` / `now.lane`: current project/lane in progress
- `blocking_or_attention`: blocked/stalled/attention evidence with source paths
- `waiting_for_input`: conservative inferred waits; stale work is labeled `stalled_attention` unless an explicit waiting-for-input signal exists
