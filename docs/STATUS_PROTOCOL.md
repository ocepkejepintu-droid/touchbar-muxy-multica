# Agentmax status protocol

The compact protocol is designed for a BetterTouchTool shell-script widget refreshing every 2 seconds. It answers the operator question: which muxy/terminal needs permission, what is done, how many terminals are still open, who is working, who is idle/stale, and what else needs attention.

## Compact line

Compact status lines start with `MUXY` for the muxy notification center or `OMX` for the operator summary fallback, and are capped by `config/status-protocol.json` (`compact_max_chars`, currently `80`). The same config controls freshness thresholds, spinner cadence, label aliases, and which attention kinds count as blocking vs inferred attention under `operator_summary`.

Examples:

```text
MUXY C1 F0 O3 scorio
MUXY C0 F0 O2 scorio
MUXY C0 F1 O0 scorio
OMX ◒ W1 I0 A1 now:touch wait:tmux
OMX · W0 I1 B1 now:- wait:touch,tmux
OMX ✓ W0 I0 A0 now:- wait:- done:touch 2h
OMX ✓ W0 I0 A0 now:- wait:-
OMX !err
MUXY !err
```

Muxy notification-center fields take priority when live muxy/OMX tmux sessions are visible:

- `C`: confirmation-needed count; panes waiting for action, permission, approval, or input
- `F`: finished count; panes that have completed successfully (strict success-only)
- `O`: open count; still-live muxy/OMX panes
- trailing label: top project/session needing attention, such as `scorio`
- empty state: `MUXY C0 F0 O0 -`
- failure state: `MUXY !err`

When muxy/tmux state is unavailable, fields fall back to the older OMX operator summary:

- `W`: working count; active runs with recent activity
- `I`: idle/stale count; active runs with stale activity, not safely considered working
- `B`: blocking/stalled attention count; used when an attention item is critical or configured as blocking
- `A`: non-blocking attention count; used when there is attention but not a certain blocker
- `now:<project>`: current recent active project/lane, or `-`
- `wait:<label>`: inferred waiting/stalled/attention labels, or `-`
- `done:<project> <age>`: latest finished run when there is no active work or attention to show

The BTT wrapper adds one extra failure-only fallback: `MUXY !err`. The collector itself does not use `MUXY !err` as a truncation fallback; very small `compact_max_chars` budgets return the shortest truthful `MUXY...` or `OMX...` prefix that fits.

## Spinner and freshness

When active recent work exists, the collector shows a spinner frame:

```text
◐ ◓ ◑ ◒
```

The frame is derived from wall-clock time and `spinner_cadence_seconds` in `config/status-protocol.json` (currently `2`). No persistent spinner state is written.

Current freshness thresholds:

- `fresh_seconds`: `120`
- `stale_seconds`: `600`
- `critical_stale_seconds`: `1800`

## Age formatting

Ages are compact and operator-facing:

- missing/unknown: `?`
- under 10 seconds: `now`
- under 60 seconds: `<Ns>`
- under 60 minutes: `<Nm>`
- under 48 hours: `<Nh>`
- 48 hours or older: `<Nd>`

## Compact summary rules

The compact formatter is derived from the JSON `operator_summary`:

1. Count active recent runs as `W`.
2. Count active stale runs as `I` and infer `wait:<project>` with `stalled_attention`.
3. Count critical or configured blocking attention as `B`; otherwise count current non-blocking warn/critical items as `A`. Historical terminal failures, stale notify infrastructure, and inactive tmux placeholder diagnostics stay in detail/debug and do not inflate compact counts.
4. Show active projects in `now:<project>` when recent active runs exist.
5. Show waiting/attention labels in `wait:<label>` only when active or stale work gives the label current context.
6. Treat fresh HUD, notify, or subagent activity as current Codex work even when no OMX skill-active/ultrawork flag is present.
7. If no active/attention items remain, compact output shows `-` rather than a stale completed project label. Completed run details remain available in `--detail` and JSON output.
8. Prefer the full compact line, then progressively shorten `now:` / `wait:` labels and finally omit them if needed to satisfy `compact_max_chars`.

## Truncation and char budget

`compact_max_chars` is currently `80`. The formatter first tries these candidates, in order:

1. `OMX <state> Wn In A/Bn now:<up to 2 labels> wait:<up to 2 labels> done:<label> <age>`
2. Same line with one `now:` and one `wait:` label
3. Same line without `wait:`
4. Counts plus `done:` if present
5. `OMX Wn In A/Bn`
6. `OMX`

Label lists are slugged to short alphanumeric aliases, deduplicated, limited to two visible labels, and suffixed like `+2` when more labels exist.

The BTT-facing compact path uses a bounded JSONL file set (`compact_log_file_limit`, currently `6`) and bounded byte-tail reads per file (`compact_log_tail_bytes`, currently `65536`) so a 2-second refresh does not scan unbounded historical logs. `--detail`, `--debug`, and `--json` retain the richer line-tail diagnostics configured by `log_tail_lines`.

## Labels and project aliases

Default labels come from `config/status-protocol.json` and `config/project-aliases.json`:

- repo default: `touch`
- `/Users/yoseph/TouchBar`: `touch`
- basename `TouchBar`: `touch`
- `ultrawork`: `ultra`
- `team`: `team`
- `ralph`: `ralph`

The TouchBar repo itself is intentionally displayed as `touch` to keep the widget short.

## Attention labels

Known attention labels include:

- `tmux`: tmux hook config/state needs attention
- `notify`: notification/fallback watcher needs attention
- `json`: JSON or JSONL parse/read issue
- a run label such as `touch`: workflow stalled or contradictory state
- `terminal_non_success`: failed/cancelled terminal metadata; not eligible for `done:`

The known tmux placeholder case is expected until `.omx/tmux-hook.json` has a real target pane instead of `replace-with-tmux-pane-id`. It appears in detail/debug output as `tmux_invalid_config`; compact output only surfaces it while there is active or stale operator work, so an idle Touch Bar does not look stuck on `tmux`.

## JSON operator summary

`--json` exposes an `operator_summary` object with:

- `counts`: `working`, `idle_stale`, `blocking`, actionable `attention`, `info_attention`, `finished`
- `now`: current `project`, `lane`, and `who`
- `working`: operator-facing run entries with `project`, `lane`, `who`, and freshness evidence
- `idle_or_stale`: active but stale entries
- `blocking_or_attention`: attention items with `blocking` and `certainty`
- `informational_attention`: info-only diagnostics kept out of compact/actionable counts
- `waiting_for_input`: conservative inferences; explicit waiting is not invented when state only proves staleness/attention
- `compact_parts`: normalized `now_labels` and `wait_labels`

Use these commands outside BTT:

```sh
python3 scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --json --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
```

## Read-only and optional writes

The collector is read-only by default. It reads `.omx` state and logs under
`--root`; it writes only when `logging.enabled` is explicitly set to `true` in
`config/status-protocol.json`, in which case it appends a compact snapshot to
the configured logging path and rotates that optional file by size. The BTT
wrapper reserves `MUXY !err` for actual collector/wrapper failure or empty output.

## Reserved multica config

The `multica` block in `config/status-protocol.json` is reserved/future-facing.
It documents a possible status-file integration point but is not collected by
`scripts/agentmax_status.py` yet. Leave `multica.enabled` as `false` unless a
future implementation wires it into the snapshot.
