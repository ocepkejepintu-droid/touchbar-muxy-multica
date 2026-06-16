import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "omx-sample"
SCRIPT_PATH = REPO_ROOT / "scripts" / "agentmax_status.py"

spec = importlib.util.spec_from_file_location("agentmax_status", SCRIPT_PATH)
agentmax_status = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(agentmax_status)


class AgentmaxStatusTests(unittest.TestCase):
    def snapshot(self):
        fixture_now = datetime(2026, 5, 28, 0, 6, tzinfo=timezone.utc)
        with mock.patch.object(agentmax_status, "utc_now", return_value=fixture_now):
            return agentmax_status.collect_snapshot(FIXTURE_ROOT)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args, "--root", str(FIXTURE_ROOT)],
            check=True,
            capture_output=True,
            text=True,
        )

    def copy_fixture(self) -> Path:
        tmp_root = Path(tempfile.mkdtemp()) / "omx-sample"
        shutil.copytree(FIXTURE_ROOT, tmp_root)
        self.addCleanup(lambda: shutil.rmtree(tmp_root.parent, ignore_errors=True))
        return tmp_root

    def write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def test_collector_loads_minimized_fixture(self):
        snapshot = self.snapshot()

        self.assertTrue(snapshot["omx_exists"])
        self.assertEqual(snapshot["current_session"]["session_id"], "active-session")
        self.assertGreaterEqual(len(snapshot["runs"]), 2)
        self.assertIn(".omx/state/session.json", snapshot["diagnostics"]["files_read"])
        self.assertIn(".omx/logs/turns-2026-05-28.jsonl", snapshot["logs"]["files"])

    def test_reconciliation_completed_overrides_active(self):
        snapshot = self.snapshot()
        completed = {
            run["session_id"]: run for run in snapshot["completed_runs"]
        }

        self.assertIn("completed-stale-active", completed)
        self.assertTrue(completed["completed-stale-active"]["completed"])
        self.assertFalse(completed["completed-stale-active"]["active"])
        self.assertTrue(
            any(
                "completion marker overrides active=true" in decision
                for decision in snapshot["diagnostics"]["decisions"]
            )
        )

    def test_current_active_session_detected(self):
        snapshot = self.snapshot()
        active = {run["session_id"]: run for run in snapshot["active_runs"]}

        self.assertIn("active-session", active)
        self.assertTrue(active["active-session"]["current"])
        self.assertFalse(active["active-session"]["stalled"])

    def test_stale_active_session_is_reported_without_marking_completed(self):
        snapshot = self.snapshot()
        active = {run["session_id"]: run for run in snapshot["active_runs"]}
        stalled = {run["session_id"]: run for run in snapshot["stalled_runs"]}

        self.assertIn("stale-session", active)
        self.assertIn("stale-session", stalled)
        self.assertTrue(active["stale-session"]["active"])
        self.assertFalse(active["stale-session"]["completed"])
        self.assertTrue(active["stale-session"]["stalled"])
        self.assertEqual(active["stale-session"]["stale_severity"], "critical")
        self.assertTrue(
            any(
                item["kind"] == "workflow_stalled" and item["label"] == "teamlane"
                for item in snapshot["attention"]
            )
        )

    def test_tmux_placeholder_attention(self):
        snapshot = self.snapshot()
        attention = snapshot["attention"]

        self.assertTrue(
            any(item["kind"] == "tmux_invalid_config" for item in attention),
            attention,
        )
        self.assertTrue(snapshot["tmux_health"]["placeholder_target"])

    def test_operator_summary_answers_working_idle_attention(self):
        snapshot = self.snapshot()
        summary = snapshot["operator_summary"]
        counts = summary["counts"]

        self.assertIn("working", summary)
        self.assertIn("idle_or_stale", summary)
        self.assertIn("blocking_or_attention", summary)
        self.assertEqual(counts["working"] + counts["idle_stale"], len(snapshot["active_runs"]))
        self.assertGreaterEqual(counts["attention"], 1)
        self.assertTrue(summary["working"] or summary["idle_or_stale"])
        visible_run = (summary["working"] or summary["idle_or_stale"])[0]
        self.assertEqual(visible_run["project"], "touch")
        self.assertIn("ultrawork:", visible_run["lane"])
        self.assertIn("who", visible_run)

    def test_compact_attention_count_deduplicates_non_actionable_noise(self):
        snapshot = {
            "config": agentmax_status.DEFAULT_CONFIG,
            "active_runs": [],
            "stalled_runs": [],
            "completed_runs": [],
            "attention": [
                agentmax_status.attention_item("terminal_non_success", "warn", "touch", "failed", "a"),
                agentmax_status.attention_item("terminal_non_success", "warn", "touch", "failed", "b"),
                agentmax_status.attention_item("tmux_invalid_config", "warn", "tmux", "placeholder", "c"),
                agentmax_status.attention_item("authority_stale", "warn", "notify", "owner stale", "d"),
                agentmax_status.attention_item("notify_unhealthy", "warn", "notify", "tick stale", "e"),
            ],
        }

        summary = agentmax_status.build_operator_summary(snapshot)
        snapshot["operator_summary"] = summary

        self.assertEqual(summary["counts"]["attention"], 5)
        self.assertEqual(summary["counts"]["compact_attention"], 0)
        compact = agentmax_status.format_compact(snapshot)
        self.assertEqual(compact, "OMX · W0 I0 A0 -")

    def test_compact_attention_shows_tmux_only_with_active_context(self):
        run = {
            "session_id": "active-session",
            "label": "touch",
            "mode": "codex",
            "phase": "active",
            "current": True,
            "subagents": {},
        }
        snapshot = {
            "config": agentmax_status.DEFAULT_CONFIG,
            "active_runs": [run],
            "stalled_runs": [],
            "completed_runs": [],
            "attention": [
                agentmax_status.attention_item("terminal_non_success", "warn", "touch", "failed", "a"),
                agentmax_status.attention_item("tmux_invalid_config", "warn", "tmux", "placeholder", "b"),
            ],
        }

        summary = agentmax_status.build_operator_summary(snapshot)
        snapshot["operator_summary"] = summary

        self.assertEqual(summary["counts"]["attention"], 2)
        self.assertEqual(summary["counts"]["compact_attention"], 1)
        compact = agentmax_status.format_compact(snapshot)
        self.assertEqual(compact, "OMX · W1 I0 A1 touch")

    def test_compact_idle_finished_run_does_not_show_stale_project_label(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {
                "counts": {"working": 0, "idle_stale": 0, "attention": 0, "compact_attention": 0, "blocking": 0},
                "compact_parts": {"now_labels": [], "wait_labels": [], "attention_labels": []},
                "finished": [{"project": "touch", "label": "touch", "age": "26h"}],
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "OMX · W0 I0 A0 -")

    def test_compact_open_muxy_pane_does_not_override_omx_summary(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 0, "done": 0, "open_panes": 1, "open_sessions": 1},
                "panes": [{"project": "applemusic", "dead": False, "done": False}],
                "compact_parts": {"project_labels": ["applemusic"], "waiting_labels": []},
            },
            "operator_summary": {
                "counts": {"working": 0, "idle_stale": 0, "attention": 0, "compact_attention": 0, "blocking": 0},
                "compact_parts": {"now_labels": [], "wait_labels": [], "attention_labels": []},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "OMX · W0 I0 A0 -")

    def test_compact_attached_open_muxy_session_counts_as_working(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 0, "done": 0, "open_panes": 2, "open_sessions": 1},
                "sessions": [
                    {
                        "name": "omx-scorio-bi-copy-feature-live-scoring-court-tv-1780302222688-d6w02g",
                        "project": "scorio",
                        "attached": True,
                    }
                ],
                "panes": [
                    {
                        "session": "omx-scorio-bi-copy-feature-live-scoring-court-tv-1780302222688-d6w02g",
                        "project": "scorio",
                        "dead": False,
                        "waiting": False,
                        "done": False,
                    }
                ],
                "compact_parts": {"project_labels": ["scorio"], "waiting_labels": []},
            },
            "operator_summary": {
                "counts": {"working": 0, "idle_stale": 0, "attention": 0, "compact_attention": 0, "blocking": 0},
                "compact_parts": {"now_labels": [], "wait_labels": [], "attention_labels": []},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "OMX · W1 I0 A0 scorio")

    def test_current_alive_pid_keeps_quiet_session_working(self):
        tmp_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        sid = "quiet-current-session"
        self.write_json(
            tmp_root / ".omx" / "state" / "session.json",
            {
                "session_id": sid,
                "native_session_id": sid,
                "started_at": "2026-05-31T09:00:00Z",
                "cwd": str(tmp_root),
                "pid": 12345,
                "platform": "darwin",
            },
        )
        (tmp_root / ".omx" / "state" / "sessions" / sid).mkdir(parents=True)

        with mock.patch.object(agentmax_status, "pid_running", return_value=True), mock.patch.object(
            agentmax_status,
            "run_status_command",
            return_value=(1, "", "no server running on /private/tmp/tmux-501/default"),
        ):
            snapshot = agentmax_status.collect_snapshot(tmp_root, compact=True)

        active = {run["session_id"]: run for run in snapshot["active_runs"]}
        self.assertIn(sid, active)
        self.assertTrue(active[sid]["process"]["active_evidence"])
        self.assertFalse(active[sid]["stalled"])
        self.assertEqual(snapshot["compact"], "OMX · W1 I0 A0 touch")

    def test_compact_status_under_budget_and_operator_protocol(self):
        snapshot = self.snapshot()
        compact = snapshot["compact"]
        max_chars = snapshot["config"]["compact_max_chars"]

        self.assertTrue(compact.startswith(("OMX", "MUXY")), compact)
        self.assertLessEqual(len(compact), max_chars)
        if compact.startswith("OMX"):
            self.assertNotIn("now:", compact)
            self.assertNotIn("wait:", compact)
        self.assertNotIn("!stall", compact)

        cli_compact = self.run_cli("--compact").stdout.strip()
        self.assertTrue(cli_compact.startswith(("OMX", "MUXY")), cli_compact)
        self.assertLessEqual(len(cli_compact), max_chars)
        self.assertNotIn("!stall", cli_compact)

    def test_compact_truncation_respects_very_small_char_budget(self):
        snapshot = self.snapshot()
        snapshot["config"] = dict(snapshot["config"], compact_max_chars=12)

        compact = agentmax_status.format_compact(snapshot)

        self.assertLessEqual(len(compact), 12)
        self.assertTrue(compact.startswith("OMX"), compact)
        self.assertNotIn("!err", compact)

        snapshot["config"] = dict(snapshot["config"], compact_max_chars=5)
        tiny = agentmax_status.format_compact(snapshot)
        self.assertLessEqual(len(tiny), 5)
        self.assertTrue(tiny.startswith("OMX"), tiny)
        self.assertNotIn("!err", tiny)

    def test_compact_log_reads_use_bounded_byte_tails(self):
        tmp_root = self.copy_fixture()
        cfg_path = tmp_root / "config" / "status-protocol.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["compact_log_tail_bytes"] = 256
        cfg["compact_log_file_limit"] = 3
        self.write_json(cfg_path, cfg)
        turns = tmp_root / ".omx" / "logs" / "turns-2026-05-28.jsonl"
        old_record = '{"timestamp":"2026-05-27T00:00:00Z","event":"old","session_id":"old"}\n'
        recent_record = '{"timestamp":"2026-05-28T00:05:00Z","event":"recent","session_id":"active-session"}\n'
        turns.write_text(old_record * 2000 + recent_record, encoding="utf-8")
        for index in range(10):
            extra = tmp_root / ".omx" / "logs" / f"old-extra-{index}.jsonl"
            extra.write_text(old_record, encoding="utf-8")
            os.utime(extra, (1, 1))

        compact_snapshot = agentmax_status.collect_snapshot(tmp_root, compact=True)
        detail_snapshot = agentmax_status.collect_snapshot(tmp_root)

        self.assertLessEqual(len(compact_snapshot["logs"]["files"]), 3)
        compact_info = compact_snapshot["diagnostics"]["jsonl_files"][".omx/logs/turns-2026-05-28.jsonl"]
        detail_info = detail_snapshot["diagnostics"]["jsonl_files"][".omx/logs/turns-2026-05-28.jsonl"]
        self.assertEqual(compact_info["byte_tail_limit"], 256)
        self.assertLessEqual(compact_info["bytes_read"], 256)
        self.assertLess(compact_info["lines_seen"], detail_info["lines_seen"])
        self.assertEqual(detail_info["byte_tail_limit"], None)

    def test_failed_cancelled_and_heuristic_native_stop_are_not_done(self):
        tmp_root = self.copy_fixture()
        now = agentmax_status.utc_now()
        native_path = tmp_root / ".omx" / "state" / "native-stop-state.json"
        native = json.loads(native_path.read_text(encoding="utf-8"))
        native["sessions"].update(
            {
                "native-failed": {
                    "outcome": "failed",
                    "updated_at": agentmax_status.iso(now),
                    "last_signature": "validation complete but failed",
                },
                "native-cancelled": {
                    "status": "cancelled",
                    "updated_at": agentmax_status.iso(now - timedelta(seconds=5)),
                    "last_signature": "cancelled by operator",
                },
                "native-heuristic": {
                    "updated_at": agentmax_status.iso(now - timedelta(seconds=10)),
                    "last_signature": "done.",
                    "ordinary_no_progress_guard": {
                        "fingerprint": "done.",
                        "first_seen_at": agentmax_status.iso(now - timedelta(seconds=20)),
                        "last_seen_at": agentmax_status.iso(now - timedelta(seconds=10)),
                    },
                },
            }
        )
        self.write_json(native_path, native)

        snapshot = agentmax_status.collect_snapshot(tmp_root)
        completed_ids = {run["session_id"] for run in snapshot["completed_runs"]}
        terminal = {run["session_id"]: run for run in snapshot["terminal_runs"]}

        self.assertNotIn("native-failed", completed_ids)
        self.assertNotIn("native-cancelled", completed_ids)
        self.assertNotIn("native-heuristic", completed_ids)
        self.assertEqual(terminal["native-failed"]["terminal"]["outcome"], "failed")
        self.assertEqual(terminal["native-cancelled"]["terminal"]["outcome"], "cancelled")
        self.assertEqual(terminal["native-heuristic"]["terminal"]["outcome"], "heuristic")
        self.assertTrue(any(item["kind"] == "terminal_non_success" and item["severity"] == "warn" for item in snapshot["attention"]))
        self.assertTrue(any(item["kind"] == "terminal_heuristic" and item["severity"] == "info" for item in snapshot["attention"]))

    def test_info_only_attention_does_not_inflate_operator_counts(self):
        snapshot = {
            "active_runs": [],
            "stalled_runs": [],
            "completed_runs": [],
            "attention": [
                {
                    "kind": "state_contradiction",
                    "severity": "info",
                    "label": "touch",
                    "detail": "completed marker overrides active flag",
                    "source_path": ".omx/state/sessions/example",
                }
            ],
            "config": agentmax_status.DEFAULT_CONFIG,
        }

        summary = agentmax_status.build_operator_summary(snapshot)
        snapshot["operator_summary"] = summary
        snapshot["config"] = dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80)
        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(summary["counts"]["attention"], 0)
        self.assertEqual(summary["counts"]["blocking"], 0)
        self.assertEqual(summary["counts"]["info_attention"], 1)
        self.assertEqual(summary["blocking_or_attention"], [])
        self.assertEqual(compact, "OMX · W0 I0 A0 -")

    def test_codex_only_fresh_activity_counts_as_working(self):
        tmp_root = self.copy_fixture()
        now = agentmax_status.utc_now()
        session_id = "codex-only"
        self.write_json(
            tmp_root / ".omx" / "state" / "session.json",
            {
                "session_id": session_id,
                "native_session_id": "native-codex",
                "cwd": str(tmp_root),
                "pid": 98765,
                "platform": "darwin",
                "started_at": agentmax_status.iso(now - timedelta(minutes=5)),
            },
        )
        self.write_json(
            tmp_root / ".omx" / "state" / "sessions" / session_id / "hud-state.json",
            {
                "turn_count": 1,
                "last_turn_at": agentmax_status.iso(now - timedelta(seconds=20)),
                "last_progress_at": agentmax_status.iso(now - timedelta(seconds=20)),
                "last_agent_output": "subagent is working",
            },
        )
        subagents_path = tmp_root / ".omx" / "state" / "subagent-tracking.json"
        subagents = json.loads(subagents_path.read_text(encoding="utf-8"))
        subagents["sessions"][session_id] = {
            "threads": {
                "sub-2": {
                    "kind": "subagent",
                    "turn_count": 1,
                    "last_seen_at": agentmax_status.iso(now - timedelta(seconds=10)),
                }
            }
        }
        self.write_json(subagents_path, subagents)

        snapshot = agentmax_status.collect_snapshot(tmp_root)
        active = {run["session_id"]: run for run in snapshot["active_runs"]}
        working = {run["session_id"]: run for run in snapshot["operator_summary"]["working"]}

        self.assertIn(session_id, active)
        self.assertTrue(active[session_id]["active_inferred"])
        self.assertFalse(active[session_id]["stalled"])
        self.assertIn(session_id, working)

    def test_detail_source_diagnostics_mentions_tmux_source(self):
        detail = self.run_cli("--detail").stdout

        self.assertIn("Agentmax TouchBar detail", detail)
        self.assertIn("Operator summary", detail)
        self.assertIn("working=", detail)
        self.assertIn(".omx/tmux-hook.json", detail)
        self.assertIn("tmux hook target is placeholder", detail)

    def test_json_output_serializable(self):
        result = self.run_cli("--json")
        snapshot = json.loads(result.stdout)

        json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        for key in ("active_runs", "completed_runs", "attention", "compact", "diagnostics", "operator_summary"):
            self.assertIn(key, snapshot)
        operator = snapshot["operator_summary"]
        self.assertIn("counts", operator)
        self.assertIn("now", operator)
        self.assertIn("waiting_for_input", operator)
        self.assertIn("blocking_or_attention", operator)
        self.assertIn("working", operator)
        self.assertIn("idle_or_stale", operator)
        self.assertIn("finished", operator)
        self.assertIn("compact_parts", operator)
        self.assertIsInstance(operator["counts"]["working"], int)
        self.assertIn("muxy_notification_center", snapshot)
        for entry in operator["working"] + operator["idle_or_stale"] + operator["finished"]:
            for key in (
                "session_id",
                "short_session_id",
                "project",
                "label",
                "lane",
                "mode",
                "phase",
                "who",
                "status",
                "reason",
                "age",
                "freshness_seconds",
                "last_activity_at",
                "source_dir",
            ):
                self.assertIn(key, entry)

    def test_muxy_notification_center_collects_waiting_and_open_panes(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t1\t1779951649\nplain\t$1\t1\t0\t1779951650\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t1\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-scorio-feature-1779951648884-oguscm\thud\t%1\t0\tnode\tHUD\t0\tomx hud\n"
                    "plain\tbash\t%2\t1\tbash\tignored\t0\tzsh\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertTrue(summary["available"])
        self.assertEqual(summary["counts"]["waiting"], 1)
        self.assertEqual(summary["counts"]["open_panes"], 2)
        self.assertEqual(summary["counts"]["open_sessions"], 1)
        self.assertEqual(summary["waiting"][0]["project"], "scorio")

    def test_muxy_strict_success_only_counts_explicit_success(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t1\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t1\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-scorio-feature-1779951648884-oguscm\thud\t%1\t0\tnode\tHUD\t0\tomx hud\n"
                ), ""
            return 1, "", "unexpected"

        pane_success_map = {"%1": True}
        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init(), pane_success_map)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["counts"]["waiting"], 1)
        self.assertEqual(summary["counts"]["done"], 1)
        self.assertEqual(summary["counts"]["open_panes"], 1)
        self.assertEqual(summary["done"][0]["pane_id"], "%1")

    def test_muxy_strict_success_dead_without_proof(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t1\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t1\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-scorio-feature-1779951648884-oguscm\thud\t%1\t0\tnode\tHUD\t1\tomx hud\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertTrue(summary["available"])
        self.assertEqual(summary["counts"]["done"], 0)
        self.assertEqual(summary["counts"]["open_panes"], 1)

    def test_muxy_strict_success_ignores_done_keywords(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                    "done_keywords": ["completed", "finished"],
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t1\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t1\tbash\tcompleted and finished\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertTrue(summary["available"])
        self.assertEqual(summary["counts"]["done"], 0)
        self.assertEqual(summary["counts"]["open_panes"], 1)

    def test_muxy_urgent_target_attached_priority(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t1\t1779951649\n"
                    "omx-touch-feature-1779951650000-abcd\t$1\t1\t0\t1779951650\n"
                ), ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t1\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-touch-feature-1779951650000-abcd\tbash\t%1\t0\tbash\t[ . ] Action Required | TOUCH\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertEqual(summary["urgent_pane_id"], "%0")
        self.assertEqual(summary["urgent_project"], "scorio")
        self.assertEqual(summary["urgent_reason"], "attached")

    def test_muxy_urgent_target_active_priority(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t0\t1779951649\n"
                    "omx-touch-feature-1779951650000-abcd\t$1\t1\t0\t1779951650\n"
                ), ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t0\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-touch-feature-1779951650000-abcd\tbash\t%1\t1\tbash\t[ . ] Action Required | TOUCH\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertEqual(summary["urgent_pane_id"], "%1")
        self.assertEqual(summary["urgent_project"], "touch")
        self.assertEqual(summary["urgent_reason"], "active")

    def test_muxy_urgent_target_oldest_session(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t0\t1779951650\n"
                    "omx-touch-feature-1779951650000-abcd\t$1\t1\t0\t1779951649\n"
                ), ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t0\tbash\t[ . ] Action Required | SCORIO\t0\tcodex\n"
                    "omx-touch-feature-1779951650000-abcd\tbash\t%1\t0\tbash\t[ . ] Action Required | TOUCH\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertEqual(summary["urgent_pane_id"], "%1")
        self.assertEqual(summary["urgent_project"], "touch")
        self.assertEqual(summary["urgent_reason"], "oldest_session")

    def test_muxy_urgent_target_no_waiting(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t0\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t0\tbash\tHUD\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertIsNone(summary["urgent"])
        self.assertIsNone(summary["urgent_pane_id"])
        self.assertIsNone(summary["urgent_project"])
        self.assertIsNone(summary["urgent_reason"])

    def test_compact_prefers_muxy_notification_center(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 1, "done": 0, "open_panes": 3, "open_sessions": 1},
                "compact_parts": {"waiting_labels": ["scorio"], "project_labels": ["scorio"]},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "MUXY C1 F0 O3 scorio")

    def test_muxy_compact_canonical_counts(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 1, "done": 2, "open_panes": 5, "open_sessions": 1},
                "compact_parts": {"waiting_labels": ["scorio"], "project_labels": ["scorio"]},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "MUXY C1 F2 O5 scorio")

    def test_muxy_multiple_success_panes(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t0\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t0\tbash\tHUD\t0\tcodex\n"
                    "omx-scorio-feature-1779951648884-oguscm\thud\t%1\t0\tnode\tHUD\t0\tomx hud\n"
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%2\t0\tbash\tDone\t0\tcodex\n"
                ), ""
            return 1, "", "unexpected"

        pane_success_map = {"%1": True, "%2": True}
        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init(), pane_success_map)

        self.assertEqual(summary["counts"]["done"], 2)
        self.assertEqual(summary["counts"]["open_panes"], 1)
        self.assertEqual(len(summary["done"]), 2)

    def test_muxy_open_panes_no_confirmations(self):
        now = agentmax_status.utc_now()
        cfg = agentmax_status.deep_merge(
            agentmax_status.DEFAULT_CONFIG,
            {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1.5,
                }
            },
        )

        def fake_command(args, timeout_seconds):
            del timeout_seconds
            if args[1] == "list-sessions":
                return 0, "omx-scorio-feature-1779951648884-oguscm\t$0\t1\t0\t1779951649\n", ""
            if args[1] == "list-panes":
                return 0, (
                    "omx-scorio-feature-1779951648884-oguscm\tbash\t%0\t0\tbash\tHUD\t0\tcodex\n"
                    "omx-scorio-feature-1779951648884-oguscm\thud\t%1\t0\tnode\tHUD\t0\tomx hud\n"
                ), ""
            return 1, "", "unexpected"

        with mock.patch.object(agentmax_status, "run_status_command", side_effect=fake_command):
            summary = agentmax_status.collect_muxy_runtime(FIXTURE_ROOT, cfg, now, agentmax_status.diag_init())

        self.assertEqual(summary["counts"]["waiting"], 0)
        self.assertEqual(summary["counts"]["done"], 0)
        self.assertEqual(summary["counts"]["open_panes"], 2)
        self.assertIsNone(summary["urgent"])

    def test_muxy_compact_empty_state(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 0, "done": 0, "open_panes": 0, "open_sessions": 0},
                "compact_parts": {"waiting_labels": [], "project_labels": []},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "MUXY C0 F0 O0 -")

    def test_muxy_compact_failure_token(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "muxy_notification_center": {
                "enabled": True,
                "available": False,
                "reason": "tmux unavailable",
                "counts": {},
                "compact_parts": {},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "MUXY !err")

    def test_muxy_runtime_no_tmux_server_falls_back_to_operator_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cfg = {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1,
                    "session_prefixes": ["omx-"],
                    "waiting_keywords": [],
                }
            }
            fake_tmux = tmp_root / "fake_tmux"
            fake_tmux.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'no server running on /private/tmp/tmux-501/default' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_tmux.chmod(0o755)
            cfg["muxy"]["tmux_command"] = str(fake_tmux)
            diag = {"decisions": []}

            muxy = agentmax_status.collect_muxy_runtime(tmp_root, cfg, agentmax_status.utc_now(), diag)
            snapshot = {
                "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
                "operator_summary": {
                    "counts": {"working": 1, "idle_stale": 0, "attention": 0, "blocking": 0},
                    "compact_parts": {"now_labels": ["touch"], "wait_labels": []},
                },
                "muxy_notification_center": muxy,
            }

            self.assertFalse(muxy["available"])
            self.assertTrue(muxy["empty_runtime"])
            self.assertEqual(agentmax_status.format_compact(snapshot), "OMX · W1 I0 A0 touch")

    def test_muxy_runtime_missing_tmux_socket_falls_back_to_operator_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cfg = {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1,
                    "session_prefixes": ["omx-"],
                    "waiting_keywords": [],
                }
            }
            fake_tmux = tmp_root / "fake_tmux"
            fake_tmux.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'error connecting to /private/tmp/tmux-501/default (No such file or directory)' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_tmux.chmod(0o755)
            cfg["muxy"]["tmux_command"] = str(fake_tmux)
            diag = {"decisions": []}

            muxy = agentmax_status.collect_muxy_runtime(tmp_root, cfg, agentmax_status.utc_now(), diag)
            snapshot = {
                "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
                "operator_summary": {
                    "counts": {"working": 1, "idle_stale": 0, "attention": 0, "blocking": 0},
                    "compact_parts": {"now_labels": ["touch"], "wait_labels": []},
                },
                "muxy_notification_center": muxy,
            }

            self.assertFalse(muxy["available"])
            self.assertTrue(muxy["empty_runtime"])
            self.assertEqual(agentmax_status.format_compact(snapshot), "OMX · W1 I0 A0 touch")

    def test_muxy_runtime_tmux_permission_denied_falls_back_to_operator_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cfg = {
                "muxy": {
                    "enabled": True,
                    "tmux_command": "tmux",
                    "command_timeout_seconds": 1,
                    "session_prefixes": ["omx-"],
                    "waiting_keywords": [],
                }
            }
            fake_tmux = tmp_root / "fake_tmux"
            fake_tmux.write_text(
                "#!/bin/sh\n"
                "printf '%s\n' 'error connecting to /private/tmp/tmux-501/default (Operation not permitted)' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_tmux.chmod(0o755)
            cfg["muxy"]["tmux_command"] = str(fake_tmux)
            diag = {"decisions": []}

            muxy = agentmax_status.collect_muxy_runtime(tmp_root, cfg, agentmax_status.utc_now(), diag)
            snapshot = {
                "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
                "operator_summary": {
                    "counts": {"working": 1, "idle_stale": 0, "attention": 0, "blocking": 0},
                    "compact_parts": {"now_labels": ["touch"], "wait_labels": []},
                },
                "muxy_notification_center": muxy,
            }

            self.assertFalse(muxy["available"])
            self.assertTrue(muxy["empty_runtime"])
            self.assertEqual(agentmax_status.format_compact(snapshot), "OMX · W1 I0 A0 touch")

    def test_project_alias_labels_from_paths_and_skills(self):
        snapshot = self.snapshot()
        by_session = {run["session_id"]: run for run in snapshot["runs"]}

        self.assertEqual(by_session["active-session"]["label"], "touch")
        self.assertEqual(by_session["history-finished"]["label"], "customerport")
        self.assertEqual(by_session["stale-session"]["label"], "teamlane")

    def test_last_finished_prefers_native_stop_completion_then_history(self):
        snapshot = self.snapshot()
        completed = {run["session_id"]: run for run in snapshot["completed_runs"]}

        self.assertEqual(snapshot["last_finished"]["session_id"], "native-finished")
        self.assertEqual(snapshot["last_finished"]["completed_at"], "2026-05-28T00:10:00Z")
        self.assertEqual(completed["native-finished"]["native_stop"]["finished_at"], "2026-05-28T00:10:00Z")
        self.assertEqual(completed["history-finished"]["completed_at"], "2026-05-28T00:07:00Z")
        self.assertEqual(completed["history-finished"]["history"]["pid"], 333)

    def test_missing_and_corrupt_state_files_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp) / "omx-sample"
            shutil.copytree(FIXTURE_ROOT, tmp_root)
            (tmp_root / ".omx" / "state" / "session.json").unlink()
            corrupt_path = tmp_root / ".omx" / "state" / "sessions" / "active-session" / "hud-state.json"
            corrupt_path.write_text("{not valid json", encoding="utf-8")

            snapshot = agentmax_status.collect_snapshot(tmp_root)

        self.assertIsNone(snapshot["current_session"]["session_id"])
        self.assertIn(".omx/state/session.json", snapshot["diagnostics"]["files_missing"])
        self.assertTrue(
            any(error["path"].endswith("hud-state.json") for error in snapshot["diagnostics"]["json_errors"])
        )
        self.assertTrue(
            any(item["kind"] == "state_parse_error" for item in snapshot["attention"])
        )
        self.assertTrue(snapshot["compact"].startswith("OMX"), snapshot["compact"])

    def test_vibe_project_label_prefers_cwd_basename(self):
        label = agentmax_status.vibe_project_label(
            {
                "cwd": "/Users/yoseph/rsvp-reader/covers",
                "firstUserMessage": "install this https://github.com/example/repo",
            }
        )
        self.assertEqual(label, "covers")

    def test_vibe_project_label_uses_parent_for_home_cwd(self):
        label = agentmax_status.vibe_project_label(
            {
                "cwd": "/Users/yoseph",
                "firstUserMessage": "fix auth bug in middleware",
            }
        )
        self.assertEqual(label, "fix-auth")

    def test_vibe_island_collects_active_sessions(self):
        now = datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc)
        unix_now = now.timestamp()
        cf_now = unix_now - agentmax_status.CF_ABSOLUTE_TIME_OFFSET
        with tempfile.TemporaryDirectory() as tmp:
            session_file = Path(tmp) / "session-terminals.json"
            session_file.write_text(
                json.dumps(
                    {
                        "83897b76-041d-4db6-8f9f-b70ec9ebfb8e": {
                            "source": "claude",
                            "status": "processing",
                            "cwd": "/Users/yoseph/rsvp-reader/covers",
                            "currentTool": "Read",
                            "lastActivityAt": cf_now,
                        }
                    }
                ),
                encoding="utf-8",
            )
            cfg = agentmax_status.deep_merge(
                agentmax_status.DEFAULT_CONFIG,
                {
                    "vibe_island": {
                        "enabled": True,
                        "session_file": str(session_file),
                        "log_file": str(Path(tmp) / "missing.log"),
                        "stale_seconds": 300,
                    }
                },
            )
            summary = agentmax_status.collect_vibe_island(cfg, now, agentmax_status.diag_init())

        self.assertTrue(summary["available"])
        self.assertEqual(summary["counts"]["active"], 1)
        self.assertEqual(summary["active_sessions"][0]["agent"], "Claude")
        self.assertEqual(summary["active_sessions"][0]["project"], "covers")
        self.assertEqual(summary["active_sessions"][0]["tool"], "Read")

    def test_compact_prefers_vibe_island_over_muxy(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "vibe_island": {
                "enabled": True,
                "available": True,
                "counts": {"active": 1, "permissions": 0},
                "active_sessions": [
                    {
                        "agent": "Claude",
                        "project": "covers",
                        "tool": "Read",
                        "age": "12s",
                    }
                ],
                "permissions": [],
                "urgent": {
                    "agent": "Claude",
                    "project": "covers",
                    "tool": "Read",
                    "age": "12s",
                },
                "compact_parts": {"project_labels": ["covers"], "agent_labels": ["Claude"]},
            },
            "muxy_notification_center": {
                "enabled": True,
                "available": True,
                "counts": {"waiting": 1, "done": 0, "open_panes": 3, "open_sessions": 1},
                "compact_parts": {"waiting_labels": ["scorio"], "project_labels": ["scorio"]},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "VI Claude covers · Read")

    def test_vibe_island_permission_compact(self):
        snapshot = {
            "config": dict(agentmax_status.DEFAULT_CONFIG, compact_max_chars=80),
            "operator_summary": {"counts": {"working": 0, "idle_stale": 0, "attention": 0, "blocking": 0}},
            "vibe_island": {
                "enabled": True,
                "available": True,
                "counts": {"active": 1, "permissions": 1},
                "active_sessions": [{"agent": "Claude", "project": "covers", "tool": "Read", "age": "12s"}],
                "permissions": [{"agent": "Claude", "project": "covers", "tool": "Bash", "age": "3s"}],
                "urgent": {"agent": "Claude", "project": "covers", "tool": "Bash", "age": "3s"},
                "compact_parts": {"project_labels": ["covers"], "agent_labels": ["Claude"]},
            },
        }

        compact = agentmax_status.format_compact(snapshot)

        self.assertEqual(compact, "VI ! Claude covers")


if __name__ == "__main__":
    unittest.main()
