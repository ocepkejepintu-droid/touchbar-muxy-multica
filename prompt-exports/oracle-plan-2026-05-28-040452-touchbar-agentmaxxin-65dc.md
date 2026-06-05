## Final Prompt
<taskname="TouchBar Agentmaxxing"/>

<task>
Create an implementation-oriented plan for a BetterTouchTool Touch Bar dashboard in `/Users/yoseph/TouchBar` that gives maximum useful progress visibility for an agent-heavy workflow using oh-my-codex (OMX), Codex agents, and multica. The repo currently has no app code; it is mostly `.omx` runtime state and logs. Do not assume a full framework exists. Plan a local, script-driven BTT integration with scripts/config/docs/tests that can be added to this repo.

The plan should cover at least: a data collector reading OMX/Codex state JSON and JSONL logs, a compact Touch Bar status text protocol suitable for BTT shell-script widgets, a richer expanded view/log/debug command, BTT widget definitions or an import/setup guide, and validation/smoke tests. Keep it feasible and focused on continuous work visibility: the user wants "agentmaxxing" so they can know what is running, what finished, what needs attention, and where progress stalled without stopping their flow.
</task>

<architecture>
The selected repo context is the current source of truth. There is no existing application source under `/Users/yoseph/TouchBar`; the selected files are the entire `.omx` tree.

Key state surfaces:
- `.omx/metrics.json`: session-level counters and last activity (`total_turns`, `session_turns`, token counters, limit percentages, `last_activity`).
- `.omx/hud-config.json`: HUD preset currently `focused`; useful for aligning output with OMX HUD style but not enough by itself.
- `.omx/state/session.json`: current native session id, cwd, pid, platform.
- `.omx/state/skill-active-state.json`: global skill-active snapshot, currently inactive/stale compared with per-session state.
- `.omx/state/sessions/*/skill-active-state.json`: per-session workflow/skill activation, active flag, skill name, phase, keyword/source, turn/session ids, initialized state path.
- `.omx/state/sessions/*/ultrawork-state.json`: mode-specific ultrawork lifecycle fields including `current_phase`, tmux pane/window ids when present, completion outcome, and verification text.
- `.omx/state/sessions/*/hud-state.json`: last progress turn data (`last_turn_at`, `turn_count`, `last_progress_at`, `last_agent_output`) where available.
- `.omx/state/sessions/*/notify-hook-state.json`: recent turn event keys and `last_event_at` per session.
- `.omx/state/subagent-tracking.json`: leader/subagent thread topology, turn counts, first/last seen times, and session-to-thread mapping.
- `.omx/state/notify-fallback-state.json` and `.omx/state/notify-fallback-authority-owner.json`: watcher/notification health, adaptive polling, leader nudge, fallback auto-nudge, authority/backoff state, and current owner heartbeat.
- `.omx/tmux-hook.json` and `.omx/state/tmux-hook-state.json`: tmux injection config/status. Current config still contains placeholder target `replace-with-tmux-pane-id`; state/logs show repeated `invalid_config`, so dashboard should surface this as attention/debug info rather than silently ignoring it.
- `.omx/state/team-leader-nudge.json`: currently empty nudge/progress maps but should be part of the collector schema for future team workflow visibility.
- `.omx/state/native-stop-state.json`: contains prior completion/guard fingerprints with useful historical Touch Bar status examples and the user’s preferred signals.
- `.omx/logs/*.jsonl`: event history for session starts/ends, notification sends, tmux hook skips, turn outputs, and prior Touch Bar iteration notes.
</architecture>

<selected_context>
.omx/hud-config.json: Minimal HUD config (`preset: focused`).
.omx/metrics.json: Session metrics and activity/limit counters for high-level freshness and budget display.
.omx/setup-scope.json: OMX installation mode (`scope: user`, `mcpMode: none`, `installMode: legacy`).
.omx/tmux-hook.json: Hook config, allowed modes, cooldown, dry-run flag, placeholder target; a known source of invalid-config attention.
.omx/state/session.json: Current session id/native id, cwd `/Users/yoseph/TouchBar`, pid, darwin platform.
.omx/state/skill-active-state.json: Global skill state, stale/inactive snapshot useful as fallback but not sole truth.
.omx/state/sessions/019e6b3d-2602-7392-a793-6bb619b0dcee/skill-active-state.json: Current per-session active ultrawork planning state from keyword `parallel`.
.omx/state/sessions/019e6b3d-2602-7392-a793-6bb619b0dcee/ultrawork-state.json: Current ultrawork mode active in planning phase.
.omx/state/sessions/019e6854-aef0-7a92-b1f3-e17e14297a8c/skill-active-state.json: Earlier ultrawork activation snapshot.
.omx/state/sessions/019e6854-aef0-7a92-b1f3-e17e14297a8c/ultrawork-state.json: Earlier ultrawork completed/finished lifecycle with tmux pane/window ids and verification text.
.omx/state/sessions/019e6854-aef0-7a92-b1f3-e17e14297a8c/prompt-routing-state.json: Example prompt-routing triage state.
.omx/state/sessions/019e698d-7665-7333-b519-5bb97c1cb104/hud-state.json: Example HUD progress fields and truncated last agent output.
.omx/state/sessions/019e698d-7665-7333-b519-5bb97c1cb104/notify-hook-state.json: Recent turn completion event keys and last event time.
.omx/state/sessions/019e698d-7665-7333-b519-5bb97c1cb104/prompt-routing-state.json: Another prompt-routing state example.
.omx/state/sessions/omx-1779866836919-ux5o2d/*: Legacy OMX session state, notify turns, skill, and ultrawork lifecycle snapshots.
.omx/state/sessions/omx-1779893265922-rx7zfa/notify-hook-state.json: Minimal legacy notify state.
.omx/state/subagent-tracking.json: Leader/subagent topology and turn counts across multiple sessions; key for agent-heavy visibility.
.omx/state/notify-fallback-state.json: Watcher health, polling cadence, leader nudge status, fallback auto-nudge reason, authority backoff, and adaptive poll state.
.omx/state/notify-fallback-authority-owner.json: Authority owner `hud`, owner pid, heartbeat time.
.omx/state/tmux-hook-state.json: Tmux hook counters and last invalid-config reason.
.omx/state/team-leader-nudge.json: Empty current team nudge/progress structure; include in schema for future active teams.
.omx/state/native-stop-state.json: Prior stop/guard fingerprints containing concrete status strings and completion examples.
.omx/state/update-check.json: Last OMX version check and latest seen version.
.omx/logs/turns-2026-05-27.jsonl: Richest historical source for prior user preferences and status iterations. Important examples include `W:3 A:3 Idle:7 ...`, `OMX:!2 scorio:rotate,touch:startup`, `OMX ◑1 touch`, real spinner request, 2s cadence, and final preferred style `OMX ◒1 touch done:touch 2h`.
.omx/logs/tmux-hook-2026-05-27.jsonl: Repeated `injection_skipped` events, mainly `invalid_config` and `unmanaged_session`, useful for attention/debug status.
.omx/logs/notify-fallback-2026-05-27.jsonl: Watcher lifecycle and fallback notification events, including parent-gone/signal/max-lifetime stops and successful sends.
.omx/logs/omx-2026-05-27.jsonl: Session start/end/reconcile events; useful for reconstructing active/stale sessions.
.omx/logs/session-history.jsonl: Completed session records with cwd/pid/native ids.
.omx/logs/update-2026-05-27T07-27-19-049Z.log: Setup transcript confirming user-scope legacy OMX install, HUD configured, Team CLI interop available, and MCP mode none.
</selected_context>

<relationships>
- Current session identity: `.omx/state/session.json` → per-session directory `.omx/state/sessions/<session_id>/` → `skill-active-state.json` + `ultrawork-state.json`.
- Workflow status: per-session `skill-active-state.json.active/skill/phase` should be reconciled with `ultrawork-state.json.active/current_phase/completed_at/run_outcome/verification` because global `.omx/state/skill-active-state.json` can be stale.
- Agent topology: `.omx/state/subagent-tracking.json.sessions[*].threads` maps leader/subagent threads and turn counts; combine with `.omx/logs/turns-2026-05-27.jsonl` and per-session notify state for activity/freshness.
- Attention signals: `.omx/state/tmux-hook-state.json.last_reason` + `.omx/logs/tmux-hook-2026-05-27.jsonl` repeated invalid configs + `.omx/tmux-hook.json` placeholder target should become visible diagnostics.
- Notification/freshness health: `.omx/state/notify-fallback-state.json` + `.omx/state/notify-fallback-authority-owner.json` + `.omx/logs/notify-fallback-2026-05-27.jsonl` describe whether fallback notifications and leader nudges are healthy/stale.
- Compact status protocol should synthesize counts and labels from multiple sources: active/running sessions, completed source, attention count, stale/idle durations, active skill/phase, subagent counts, spinner frame/cadence.
- Prior user preference from logs: prefer lean but informative Touch Bar text; keep label as `OMX`, refresh/push spinner around every 2s, show running item shorthand and the last finished/sound source (example `OMX ◒1 touch done:touch 2h`), and include a detail mode for full diagnosis.
- Existing multica Touch Bar work is referenced in logs as living outside this repo (`~/.local/share/multica-touchbar` and BTT/LaunchAgent paths), but those files are NOT selected and should not be assumed present in this repo. The plan can mention integration points but should focus on creating repo-local scripts/config/docs under `/Users/yoseph/TouchBar`.
</relationships>

<ambiguities>
- The repository has only `.omx` runtime artifacts selected; there is no existing script directory, BTT preset file, tests, package metadata, or app framework in this repo.
- The task asks for a plan, not implementation. The next step should produce an implementation-oriented plan with proposed files, data flow, status protocol, setup/import guide, and validation strategy.
- Logs reference previous live files outside this repo (`~/.local/share/multica-touchbar/scripts/omx_touchbar_status.py`, LaunchAgent, BetterTouchTool triggers). Those are useful as history but not available in the selected context.
- `multica` state is not present in the selected repo except historical turn-output references. Treat multica integration as an external input/optional collector path unless the implementation later obtains concrete local files/CLI schemas.
- Some state is intentionally stale or contradictory: a global skill state can be inactive while a per-session ultrawork state is active; an earlier ultrawork state says active true but phase complete with completed fields. The plan should include reconciliation rules and stale detection rather than trusting one file blindly.
</ambiguities>

## Selection
- Files: 31 total (31 full)
- Total tokens: 15527 (Auto view)
- Token breakdown: full 15527

### Files
### Selected Files
/Users/yoseph/TouchBar/
└── .omx/
    ├── logs/
    │   ├── notify-fallback-2026-05-27.jsonl — 2.714 tokens (full)
    │   ├── omx-2026-05-27.jsonl — 926 tokens (full)
    │   ├── session-history.jsonl — 323 tokens (full)
    │   ├── tmux-hook-2026-05-27.jsonl — 2.353 tokens (full)
    │   ├── turns-2026-05-27.jsonl — 3.804 tokens (full)
    │   └── update-2026-05-27T07-27-19-049Z.log — 644 tokens (full)
    ├── state/
    │   ├── sessions/
    │   │   ├── 019e6854-aef0-7a92-b1f3-e17e14297a8c/
    │   │   │   ├── prompt-routing-state.json — 95 tokens (full)
    │   │   │   ├── skill-active-state.json — 224 tokens (full)
    │   │   │   └── ultrawork-state.json — 152 tokens (full)
    │   │   ├── 019e698d-7665-7333-b519-5bb97c1cb104/
    │   │   │   ├── hud-state.json — 64 tokens (full)
    │   │   │   ├── notify-hook-state.json — 110 tokens (full)
    │   │   │   └── prompt-routing-state.json — 95 tokens (full)
    │   │   ├── 019e6b3d-2602-7392-a793-6bb619b0dcee/
    │   │   │   ├── skill-active-state.json — 224 tokens (full)
    │   │   │   └── ultrawork-state.json — 71 tokens (full)
    │   │   ├── omx-1779866836919-ux5o2d/
    │   │   │   ├── notify-hook-state.json — 597 tokens (full)
    │   │   │   ├── skill-active-state.json — 70 tokens (full)
    │   │   │   └── ultrawork-state.json — 144 tokens (full)
    │   │   └── omx-1779893265922-rx7zfa/
    │   │       └── notify-hook-state.json — 49 tokens (full)
    │   ├── native-stop-state.json — 1.208 tokens (full)
    │   ├── notify-fallback-authority-owner.json — 30 tokens (full)
    │   ├── notify-fallback-state.json — 641 tokens (full)
    │   ├── session.json — 63 tokens (full)
    │   ├── skill-active-state.json — 70 tokens (full)
    │   ├── subagent-tracking.json — 569 tokens (full)
    │   ├── team-leader-nudge.json — 23 tokens (full)
    │   ├── tmux-hook-state.json — 52 tokens (full)
    │   └── update-check.json — 21 tokens (full)
    ├── hud-config.json — 6 tokens (full)
    ├── metrics.json — 60 tokens (full)
    ├── setup-scope.json — 18 tokens (full)
    └── tmux-hook.json — 107 tokens (full)


---

## Generated Plan

## Chat Send ✅
- **Chat**: `touchbar-agentmaxxing-8440F2` | **Mode**: plan

### Response
## 1. **Summary**

Build a repo-local, script-driven BetterTouchTool Touch Bar dashboard under `/Users/yoseph/TouchBar` using Python standard-library collectors, shell-friendly status commands, BTT setup docs/config, and smoke tests. The dashboard will synthesize OMX/Codex runtime state from `.omx/state/*.json`, `.omx/state/sessions/*`, and `.omx/logs/*.jsonl` into a compact 2-second Touch Bar label plus richer detail/debug commands so the user can see what is running, what finished, what stalled, and what needs attention without opening terminals.

---

## 2. **Current-state analysis**

### Existing repository shape

The repo currently has no application code. The selected source of truth is only:

- `/Users/yoseph/TouchBar/.omx/state/*.json`
- `/Users/yoseph/TouchBar/.omx/state/sessions/*/*.json`
- `/Users/yoseph/TouchBar/.omx/logs/*.jsonl`
- `/Users/yoseph/TouchBar/.omx/*.json`

Therefore this should be a **targeted local utility addition**, not a framework refactor.

### Relevant state ownership and data flow today

OMX owns runtime state under `.omx/`:

1. Current session identity:
   - `.omx/state/session.json`
   - points to `.omx/state/sessions/<session_id>/`

2. Per-session workflow state:
   - `.omx/state/sessions/*/skill-active-state.json`
   - `.omx/state/sessions/*/ultrawork-state.json`

3. Agent topology:
   - `.omx/state/subagent-tracking.json`
   - maps leader/subagent threads, turn counts, first/last seen timestamps.

4. Progress/freshness:
   - `.omx/state/sessions/*/hud-state.json`
   - `.omx/state/sessions/*/notify-hook-state.json`
   - `.omx/metrics.json`
   - `.omx/logs/turns-*.jsonl`

5. Attention/debug signals:
   - `.omx/tmux-hook.json`
   - `.omx/state/tmux-hook-state.json`
   - `.omx/logs/tmux-hook-*.jsonl`
   - currently shows repeated `invalid_config` because target is still `replace-with-tmux-pane-id`.

6. Notification health:
   - `.omx/state/notify-fallback-state.json`
   - `.omx/state/notify-fallback-authority-owner.json`
   - `.omx/logs/notify-fallback-*.jsonl`

### Important constraints

- Do **not** trust global `.omx/state/skill-active-state.json` as sole truth; it is stale/inactive while per-session state can be active.
- Per-session files can also be contradictory, e.g. `active: true` with `current_phase: complete`; reconciliation logic must prefer completion lifecycle fields over stale active flags.
- Logs can be large over time; collector should tail bounded recent records, not load unbounded history.
- Multica state is not present in this repo. Treat multica as optional external integration, not required baseline behavior.
- BetterTouchTool should consume simple shell output; the primary interface should be a fast CLI command returning one compact line.

---

## 3. **Design**

### 3.1 Proposed repo layout

Add a small local utility structure:

```text
/Users/yoseph/TouchBar
├── scripts/
│   ├── agentmax_status.py
│   └── btt_agentmax_widget.sh
├── config/
│   ├── status-protocol.json
│   └── btt-widget.example.json
├── tests/
│   ├── fixtures/
│   │   └── omx-sample/
│   ├── test_collector.py
│   ├── test_reconciliation.py
│   └── test_status_protocol.py
└── docs/
    ├── BTT_SETUP.md
    ├── STATUS_PROTOCOL.md
    └── DEBUGGING.md
```

Use Python standard library only for the first implementation. No package manager, daemon framework, or external dependency is needed.

---

### 3.2 Main collector CLI

#### File

`scripts/agentmax_status.py`

#### Kind

Single Python CLI module.

This fits the repo because there is no existing app framework and BTT shell widgets can directly execute scripts.

#### Public command interface

Partial command shape:

```text
python3 scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --json --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --debug --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
```

#### Owned data model

Internal normalized snapshot shape:

```python
AgentmaxSnapshot:
  generated_at: datetime
  root: Path
  current_session_id: str | None
  active_runs: list[RunSummary]
  completed_runs: list[RunSummary]
  stalled_runs: list[RunSummary]
  attention: list[AttentionItem]
  subagents: SubagentSummary
  notify_health: NotifyHealth
  tmux_health: TmuxHealth
  metrics: MetricsSummary
  last_finished: FinishedSummary | None
```

Run summary shape:

```python
RunSummary:
  session_id: str
  short_name: str
  mode: str              # ultrawork, ralph, team, unknown
  phase: str             # planning, executing, complete, failed, unknown
  active: bool
  started_at: datetime | None
  updated_at: datetime | None
  completed_at: datetime | None
  tmux_pane_id: str | None
  turn_id: str | None
  verification: str | None
  freshness_seconds: int | None
```

Attention item variants:

```python
AttentionItem:
  kind:
    - "tmux_invalid_config"
    - "workflow_stalled"
    - "notify_unhealthy"
    - "authority_stale"
    - "state_contradiction"
    - "log_error"
  severity: "info" | "warn" | "critical"
  label: str
  detail: str
  source_path: str
```

---

### 3.3 State reconciliation rules

The collector should read all relevant files and normalize contradictions deterministically.

#### Input priority

For each session directory in `.omx/state/sessions/*`:

1. Read `ultrawork-state.json` if present.
2. Read `skill-active-state.json` if present.
3. Read `hud-state.json` and `notify-hook-state.json` if present.
4. Overlay session topology from `.omx/state/subagent-tracking.json`.
5. Use `.omx/logs/omx-*.jsonl` and `.omx/logs/session-history.jsonl` to infer ended sessions.

#### Active/completed decision

Rules:

1. If `completed_at` exists or `current_phase == "complete"` or `lifecycle_outcome in {"finished", "cancelled", "failed"}`:
   - classify as completed unless updated timestamp is newer and phase is non-complete.
2. Else if per-session `skill-active-state.json.active == true` or mode state `active == true`:
   - classify as active.
3. Else if session appears in recent logs with no end event and recent activity is within threshold:
   - classify as maybe active/stale.
4. Else:
   - classify as historical.

#### Stale detection

Default thresholds in `config/status-protocol.json`:

```json
{
  "fresh_seconds": 120,
  "stale_seconds": 600,
  "critical_stale_seconds": 1800,
  "log_tail_lines": 500,
  "compact_max_chars": 80,
  "spinner_cadence_seconds": 2
}
```

A run is stalled if:

- active is true, and
- newest of `updated_at`, `last_progress_at`, `last_event_at`, `last_turn_at` is older than `stale_seconds`.

#### Known current attention rule

For `.omx/tmux-hook.json`:

- If `target.value == "replace-with-tmux-pane-id"`, emit:

```text
tmux_invalid_config
severity: warn
label: tmux
detail: tmux hook target is placeholder; injections skipped as invalid_config
```

This must be visible in detail/debug output and optionally compact output when there are no higher-priority running items.

---

### 3.4 Compact Touch Bar status protocol

#### Goal

One lean text line, suitable for BTT shell-script widget refresh every 2 seconds.

#### Format

Primary compact format:

```text
OMX <spinner><active_count> <run_label> done:<last_done> <age>
```

Examples:

```text
OMX ◒1 touch done:touch 2h
OMX ◐2 ultra,team !tmux
OMX ✓ idle done:touch 9m
OMX !2 tmux,notify
OMX … loading
```

#### Spinner frames

Use four frames:

```text
◐ ◓ ◑ ◒
```

Frame is derived from current epoch time and `spinner_cadence_seconds`, so no persistent state file is required.

Pseudo-rule:

```python
frame = frames[int(time.time() / cadence_seconds) % len(frames)]
```

#### Label rules

Session labels should be short and stable:

- Current repo `/Users/yoseph/TouchBar` → `touch`
- `ultrawork` → `ultra` if no better project label exists
- `team` → `team`
- `ralph` → `ralph`
- Unknown session → first 5 chars of session directory or derived cwd basename

#### Priority order for compact display

1. Critical attention:
   - `OMX !<count> <labels>`
2. Active runs:
   - `OMX <spinner><count> <labels> done:<last_done> <age>`
3. Stalled active runs:
   - `OMX !stall <labels> <age>`
4. Recently completed:
   - `OMX ✓ done:<label> <age>`
5. Idle:
   - `OMX ✓ idle`

#### Character budget

Target max: 80 chars.

If too long:

1. Keep `OMX`.
2. Keep spinner/active count.
3. Keep first 1–2 active labels.
4. Keep attention count if nonzero.
5. Drop age/detail last.

---

### 3.5 Detail, JSON, and debug views

#### `--detail`

Human-readable expanded view for terminal or BTT tap action.

Example shape:

```text
Agentmax TouchBar detail
Root: /Users/yoseph/TouchBar
Generated: 2026-05-28T...

Compact:
  OMX ◒1 touch done:touch 2h

Running:
  - touch ultrawork planning active age=3m session=019e6b3d...

Completed:
  - touch ultrawork finished 9h ago verification="label output verified..."

Attention:
  - warn tmux: target is placeholder in .omx/tmux-hook.json
  - warn notify: fallback auto-nudge reason hud_state_missing

Subagents:
  leaders=2 subagents=1 active_threads=...

Notify:
  owner=hud pid=20477 heartbeat_age=...
```

#### `--json`

Machine-readable normalized snapshot for tests, future widgets, or debugging.

#### `--debug`

Include source-path-level diagnostics:

- Which files were read.
- Which files were missing.
- Which JSON parse errors occurred.
- Latest JSONL event counts.
- Reconciliation decisions, e.g.:

```text
session 019e6854... classified completed because completed_at exists despite active=true
```

#### `--smoke`

Exit `0` if:

- root exists,
- `.omx` exists,
- compact status can be generated,
- output length is under configured limit,
- JSON snapshot can be serialized.

Exit nonzero with a concise diagnostic otherwise.

---

### 3.6 BTT integration

#### Shell wrapper

File:

`scripts/btt_agentmax_widget.sh`

Responsibilities:

- Set repo root to `/Users/yoseph/TouchBar` by default.
- Call:

```text
python3 /Users/yoseph/TouchBar/scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
```

- On failure, print a safe fallback:

```text
OMX !err
```

Do not emit stack traces to Touch Bar compact output.

#### BTT widget setup guide

File:

`docs/BTT_SETUP.md`

Document manual setup because BTT import formats can vary.

Recommended BTT widget:

- Type: Touch Bar shell script widget.
- Name: `Agentmax OMX`.
- Refresh interval: `2s`.
- Width: `210–280`, user adjustable.
- Script:

```text
/Users/yoseph/TouchBar/scripts/btt_agentmax_widget.sh
```

- Tap action:

```text
open -a Terminal /Users/yoseph/TouchBar
```

or preferred richer debug command:

```text
osascript -e 'tell application "Terminal" to do script "cd /Users/yoseph/TouchBar && python3 scripts/agentmax_status.py --detail"'
```

#### Example widget config

File:

`config/btt-widget.example.json`

Purpose:

- Not guaranteed direct import unless validated against installed BTT.
- Acts as reference for:
  - title,
  - shell script path,
  - refresh interval,
  - colors,
  - width,
  - tap action.

The docs should clearly state: “Use this as a reference if BTT import fails; manual setup is canonical.”

---

### 3.7 Optional Multica integration

Because multica state is not present in selected repo, implement as optional collector extension only.

Config field:

```json
{
  "multica": {
    "enabled": false,
    "status_file": "~/Library/Caches/multica-touchbar-status.txt",
    "cli_command": null
  }
}
```

Initial behavior:

- If disabled or missing, do nothing.
- If enabled and status file exists, include one `MulticaSummary`.
- Do not block OMX status if multica read fails.

Compact protocol should remain OMX-first, not depend on multica.

---

### 3.8 Tests and validation

Use Python `unittest` or simple `pytest`-compatible tests without requiring pytest-specific features.

#### Fixture strategy

Create:

```text
tests/fixtures/omx-sample/.omx/...
```

Include minimized copies of representative selected state:

- current active ultrawork planning session,
- completed ultrawork with stale `active:true`,
- tmux invalid config,
- notify fallback health,
- sample turns JSONL,
- sample session history.

#### Test cases

1. `test_reconciliation_completed_overrides_active`
   - Given `active:true` and `completed_at` exists.
   - Expect run classified completed.

2. `test_current_active_session_detected`
   - Given `019e6b3d...` active planning.
   - Expect active count `1`.

3. `test_tmux_placeholder_attention`
   - Given placeholder target.
   - Expect attention item `tmux_invalid_config`.

4. `test_compact_status_under_budget`
   - Expect compact string starts with `OMX`.
   - Length <= configured max.

5. `test_detail_contains_sources`
   - Detail output mentions `.omx/tmux-hook.json` when invalid config exists.

6. `test_json_output_serializable`
   - CLI `--json` returns valid JSON with expected top-level keys.

#### Smoke commands

Document in README/setup:

```text
python3 scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --compact --root /Users/yoseph/TouchBar
python3 scripts/agentmax_status.py --detail --root /Users/yoseph/TouchBar
python3 -m unittest discover -s tests
```

---

## 4. **File-by-file impact**

### `scripts/agentmax_status.py`

- Add main collector, reconciliation, formatting, CLI modes.
- Depends on `.omx` state/log schema.
- Must be implemented before wrapper, docs, and tests can be finalized.

### `scripts/btt_agentmax_widget.sh`

- Add BTT-safe wrapper.
- Depends on `agentmax_status.py`.
- Handles compact fallback on error.

### `config/status-protocol.json`

- Add thresholds and display tuning:
  - stale seconds,
  - compact max chars,
  - spinner frames/cadence,
  - optional multica settings.

### `config/btt-widget.example.json`

- Add illustrative BTT widget definition/reference.
- Depends on final wrapper path and protocol.

### `docs/BTT_SETUP.md`

- Add manual BetterTouchTool setup.
- Include refresh interval, shell script command, tap action, width/color suggestions.

### `docs/STATUS_PROTOCOL.md`

- Define compact protocol, priority order, examples, truncation rules.

### `docs/DEBUGGING.md`

- Explain `--detail`, `--json`, `--debug`, `--smoke`.
- Explain known attention items like tmux placeholder invalid config.

### `tests/fixtures/omx-sample/.omx/...`

- Add minimized runtime fixtures based on selected context.
- Must avoid copying large logs wholesale.

### `tests/test_collector.py`

- Validate JSON/JSONL loading and missing-file tolerance.

### `tests/test_reconciliation.py`

- Validate active/completed/stalled classification.

### `tests/test_status_protocol.py`

- Validate compact output and detail/debug formatting.

---

## 5. **Risks and migration**

No persistence migration is required because this plan only adds repo-local scripts/config/docs/tests.

Main risks:

- **BTT import instability:** mitigate by making manual setup canonical and JSON example advisory.
- **Stale OMX state contradictions:** mitigate with explicit reconciliation rules and debug output.
- **Large JSONL logs over time:** mitigate by bounded tail reads.
- **Multica unknown schema:** keep multica disabled/optional until concrete local source is validated.
- **Touch Bar text too wide:** enforce compact max length and document width tuning.

---

## 6. **Implementation order**

1. Create repo folders:
   - `scripts/`
   - `config/`
   - `docs/`
   - `tests/fixtures/`

2. Implement `config/status-protocol.json`.

3. Implement `scripts/agentmax_status.py` with:
   - JSON safe-read helpers,
   - bounded JSONL tail reader,
   - session discovery,
   - reconciliation,
   - compact/detail/json/debug/smoke modes.

4. Add minimized fixtures under `tests/fixtures/omx-sample/.omx`.

5. Add unit tests for reconciliation and compact formatting.

6. Add `scripts/btt_agentmax_widget.sh`.

7. Add docs:
   - `docs/BTT_SETUP.md`
   - `docs/STATUS_PROTOCOL.md`
   - `docs/DEBUGGING.md`

8. Add `config/btt-widget.example.json`.

9. Validate:
   - `python3 -m unittest discover -s tests`
   - `python3 scripts/agentmax_status.py --smoke --root /Users/yoseph/TouchBar`
   - `scripts/btt_agentmax_widget.sh`

10. Manual BTT validation:
   - Create shell-script widget.
   - Set refresh to 2 seconds.
   - Confirm compact output resembles:

```text
OMX ◒1 touch done:touch 2h
```

or, if current tmux config remains placeholder:

```text
OMX !1 tmux
```<chatName="TouchBar Agentmaxxing Stop Hook Recovery"/>

```json
{
  "decision": "approve",
  "reason": "The implementation-oriented TouchBar Agentmaxxing plan has been produced. No tool calls were made, runtime state was not modified, and no further continuation is required from this text-only response."
}


## Orchestration progress

- [x] Item 1: Core Agentmax collector/protocol implemented in `scripts/agentmax_status.py` and `config/status-protocol.json`.
  - Verified: `--compact` outputs `OMX !stall touch 12m !tmux`; `--json` is valid and compact <= 80 chars; `--smoke` exits 0.
- [x] Item 2: Tests and fixtures for collector/reconciliation/status protocol.
  - Verified: `python3 -m unittest discover -s tests` passes with 8 tests.
- [x] Item 3: BTT wrapper, setup docs, status protocol docs, debugging docs, and example BTT config.
  - Verified: `scripts/btt_agentmax_widget.sh` prints the compact OMX line.
- [x] Follow-up: Operator-centric status semantics for working/idle/blocking/current/waiting visibility.
  - Verified: compact output `OMX · W0 I1 B1 now:- wait:touch,tmux`; JSON includes `operator_summary`.

> 💡 Continue this plan conversation with ask_oracle(chat_id: "touchbar-agentmaxxing-8440F2", new_chat: false)