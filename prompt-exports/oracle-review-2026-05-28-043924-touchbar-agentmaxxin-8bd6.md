# Oracle Review

## Summary

The changes implement a local, standard-library-only Agentmax/OMX Touch Bar dashboard: a Python collector, BTT shell wrapper, setup/docs, config, aliases, fixtures, and unit tests. The direction is sound and validation already passed, but a few daily-driver risks remain around BTT refresh cost, missed Codex-agent visibility, and overly optimistic/heuristic “done” reporting.

## Findings

### P1

- **`scripts/agentmax_status.py` — compact refresh scans all JSONL logs every 2 seconds**
  - `collect_snapshot()` always calls `collect_logs()`, and `read_jsonl_tail()` iterates entire files into a bounded deque. The tail is memory-bounded, but I/O is not. As `.omx/logs/turns-*.jsonl` grows, the BTT widget can become slow or pile up refreshes.
  - **Suggestion:** for `--compact`, skip log scanning by default or use a bounded byte-tail reader. Reserve full JSONL scans for `--detail`, `--debug`, or `--json`.

- **`scripts/agentmax_status.py` — Codex/subagent-only activity may not count as working**
  - `operator_summary.working` only comes from `active_runs`, which require `skill-active-state` or `ultrawork-state` active flags. Sessions with only `hud-state`, `notify-hook-state`, or `subagent-tracking` activity can appear idle/historical even when Codex agents are active.
  - **Suggestion:** infer a lightweight `codex`/`agent` active run when subagent or HUD activity is fresh, even without an active OMX skill.

- **`scripts/agentmax_status.py` — terminal failure/cancel and heuristic native-stop data can become `done:`**
  - `COMPLETE_OUTCOMES` includes failed/cancelled states, and `native_stop_finished_at()` uses text heuristics to synthesize completion. These can feed `completed_runs` and `last_finished`, causing compact output like `done:touch` for failed/cancelled/ambiguous guard state.
  - **Suggestion:** split terminal states into `succeeded`, `failed`, `cancelled`, and `heuristic`. Only successful/explicit completions should drive `done:`/`last_finished`; failures should become attention.

### P2

- **`scripts/agentmax_status.py` — compact truncation can falsely report `OMX !err`**
  - `format_compact()` uses `["OMX", "!err"]` as the final length fallback even when collection succeeded. With a small `compact_max_chars`, a truthful status can become a fake error.
  - **Suggestion:** make the last fallback truthful, e.g. `OMX W1 I0 A2` or `OMX …`, and reserve `OMX !err` only for actual collector/wrapper failures.

- **`scripts/agentmax_status.py` — info-only contradictions inflate attention counts**
  - `state_contradiction` is severity `info`, but `operator_summary.counts.attention` includes it. Compact output can show `A1` even when there is no actionable warning.
  - **Suggestion:** keep info items in detail/debug, but count only warn/critical items in compact/operator attention.

- **`tests/test_agentmax_status.py` — tests miss important daily-driver regressions**
  - Existing tests are useful, but they do not catch the false `OMX !err` truncation fallback, failed/cancelled-as-done behavior, Codex-only/subagent-only activity, or large-log scan cost. Static timestamps may also become brittle over time.
  - **Suggestion:** add targeted fixtures/tests for those cases and inject/freeze `now` for reconciliation tests.

- **`config/status-protocol.json` / `scripts/agentmax_status.py` — multica config is currently dead**
  - Config exposes `multica`, but the collector never reads it. That is fine if intentionally future-facing, but it can mislead users expecting multica visibility.
  - **Suggestion:** either implement optional multica collection or mark it clearly as reserved/future in config/docs.

- **`scripts/agentmax_status.py` / docs — read-only contract is slightly undermined by optional logging**
  - The module docstring says the collector is read-only, but `maybe_log_snapshot()` can write/rotate `.omx/logs/agentmax-status.jsonl` if enabled.
  - **Suggestion:** update the contract/docs to say “read-only by default,” or remove write logging from the BTT-facing collector.{
  "decision": "approve",
  "reason": "Text-only continuation completed with no tool calls and no runtime-state mutation. The code review response was already delivered; no further task work remains.",
  "chatName": "TouchBar Agentmaxxing Stop Hook Recovery"