#!/usr/bin/env python3
"""Unit tests for scripts/btt_muxy_daemon.py.

Stdlib unittest only — no pytest dependency. Tests run in isolation by
monkeypatching the daemon's STATE_DIR / PID_FILE / STATE_JSON / SOCKET_FILE
to a tmp dir, and by stubbing the collector subprocess via _run_collector.

Coverage:
  1. State-color derivation: waiting→orange, working→green, error→red, idle→gray.
  2. Idempotent start: PID file exists with a live PID → exits 0 with
     'already running' message; stale PID file → cleans up and proceeds.
  3. Snapshot roundtrip: state.json parses back into the same shape.
  4. Jitter bounds: _jitter() returns durations in [POLL_INTERVAL_S - JITTER,
     POLL_INTERVAL_S + JITTER].

Validation: `python3 tests/test_btt_muxy_daemon.py` exits 0 with summary 'OK'.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Import the daemon module AFTER sys.path is set.
import btt_muxy_daemon as daemon  # noqa: E402


class _IsolatedStateDir:
    """Context manager that redirects daemon state files into a tmp dir."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self._orig = {
            "STATE_DIR": daemon.STATE_DIR,
            "STATE_JSON": daemon.STATE_JSON,
            "PID_FILE": daemon.PID_FILE,
            "SOCKET_FILE": daemon.SOCKET_FILE,
            "LOG_FILE": daemon.LOG_FILE,
            "REPO_ROOT": daemon.REPO_ROOT,
            "COLLECTOR": daemon.COLLECTOR,
            "ICONS_PATH": daemon.ICONS_PATH,
        }

    def __enter__(self) -> "_IsolatedStateDir":
        daemon.STATE_DIR = self.tmp
        daemon.STATE_JSON = self.tmp / "state.json"
        daemon.PID_FILE = self.tmp / "daemon.pid"
        daemon.SOCKET_FILE = self.tmp / "daemon.sock"
        daemon.LOG_FILE = self.tmp / "daemon.log"
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for name, value in self._orig.items():
            setattr(daemon, name, value)


class StateColorDerivationTests(unittest.TestCase):
    """_state_color maps (waiting, done, dead, active, session_attached) → color hex."""

    def test_waiting_is_orange(self) -> None:
        color = daemon._state_color({"waiting": True}, session_attached=False)
        self.assertEqual(color, daemon.COLOR_WAITING)
        self.assertEqual(color, "#FF9F0A")

    def test_working_is_green(self) -> None:
        # active=True OR session_attached=True (with no waiting/done/dead)
        color_attached = daemon._state_color({}, session_attached=True)
        color_active = daemon._state_color({"active": True}, session_attached=False)
        self.assertEqual(color_attached, daemon.COLOR_WORKING)
        self.assertEqual(color_active, daemon.COLOR_WORKING)
        self.assertEqual(color_attached, "#34C759")

    def test_error_is_red(self) -> None:
        color = daemon._state_color({"dead": True}, session_attached=False)
        self.assertEqual(color, daemon.COLOR_ERROR)
        self.assertEqual(color, "#FF3B30")

    def test_idle_is_gray(self) -> None:
        # No waiting/done/dead/active and session not attached
        color = daemon._state_color({}, session_attached=False)
        self.assertEqual(color, daemon.COLOR_IDLE)
        self.assertEqual(color, "#8E8E93")
        # Done also maps to idle color
        color_done = daemon._state_color({"done": True}, session_attached=False)
        self.assertEqual(color_done, daemon.COLOR_IDLE)

    def test_waiting_overrides_active(self) -> None:
        # Waiting takes precedence over active (most urgent state wins)
        color = daemon._state_color(
            {"waiting": True, "active": True, "dead": True},
            session_attached=True,
        )
        self.assertEqual(color, daemon.COLOR_WAITING)


class SlotEntryTests(unittest.TestCase):
    """_build_slot_entries produces per-pane entries with the expected shape."""

    def test_empty_summary_returns_empty_list(self) -> None:
        entries = daemon._build_slot_entries({}, {"default": "terminal"})
        self.assertEqual(entries, [])

    def test_known_project_uses_mapped_icon(self) -> None:
        icons = {"default": "terminal", "hermes-agent": "bolt.fill"}
        summary = {
            "sessions": [{"name": "hermes-agent", "project": "hermes-agent", "attached": True}],
            "panes": [
                {
                    "pane_id": "%1",
                    "session": "hermes-agent",
                    "project": "hermes-agent",
                    "title": "hermes",
                    "active": True,
                }
            ],
        }
        entries = daemon._build_slot_entries(summary, icons)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["pane_id"], "%1")
        self.assertEqual(e["project"], "hermes-agent")
        self.assertEqual(e["state"], "working")
        self.assertEqual(e["color"], daemon.COLOR_WORKING)
        self.assertEqual(e["icon"], "bolt.fill")
        self.assertFalse(e["waiting"])

    def test_unknown_project_uses_default_icon(self) -> None:
        icons = {"default": "terminal"}
        summary = {
            "sessions": [{"name": "mystery", "project": "mystery"}],
            "panes": [{"pane_id": "%2", "session": "mystery", "project": "mystery"}],
        }
        entries = daemon._build_slot_entries(summary, icons)
        self.assertEqual(entries[0]["icon"], "terminal")

    def test_dead_panes_excluded(self) -> None:
        summary = {
            "sessions": [],
            "panes": [
                {"pane_id": "%3", "session": "x", "dead": True},
                {"pane_id": "%4", "session": "x"},
            ],
        }
        entries = daemon._build_slot_entries(summary, {"default": "terminal"})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["pane_id"], "%4")

    def test_waiting_sorted_before_working(self) -> None:
        summary = {
            "sessions": [
                {"name": "a", "project": "a", "attached": True},
                {"name": "b", "project": "b", "attached": True},
            ],
            "panes": [
                {"pane_id": "%5", "session": "a", "project": "a", "active": True},
                {"pane_id": "%6", "session": "b", "project": "b", "waiting": True},
            ],
        }
        entries = daemon._build_slot_entries(summary, {"default": "terminal"})
        self.assertEqual(entries[0]["pane_id"], "%6")  # waiting first
        self.assertEqual(entries[1]["pane_id"], "%5")  # working second

    def test_counts_reflect_entries(self) -> None:
        # Two sessions: attached "live" with waiting + active panes, plus a
        # detached "idle" session with one unflagged pane. The detached session
        # yields an idle pane (session_attached=False, no waiting/done/dead/active).
        summary = {
            "sessions": [
                {"name": "live", "project": "live", "attached": True},
                {"name": "away", "project": "away", "attached": False},
            ],
            "panes": [
                {"pane_id": "%7", "session": "live", "project": "live", "waiting": True},
                {"pane_id": "%8", "session": "live", "project": "live", "active": True},
                {"pane_id": "%9", "session": "away", "project": "away"},
            ],
        }
        entries = daemon._build_slot_entries(summary, {"default": "terminal"})
        counts = daemon._compute_counts(entries)
        self.assertEqual(counts["waiting"], 1)
        self.assertEqual(counts["working"], 1)
        self.assertEqual(counts["idle"], 1)
        self.assertEqual(counts["total"], 3)


class IdempotentStartTests(unittest.TestCase):
    """PID file lock: second invocation detects existing live PID and exits 0."""

    def test_fresh_dir_acquires_pid(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                ok = daemon._write_pid_file()
                self.assertTrue(ok, "first call should acquire the lock")
                self.assertTrue(daemon.PID_FILE.exists())
                content = daemon.PID_FILE.read_text(encoding="utf-8").strip()
                self.assertEqual(int(content), os.getpid())

    def test_stale_pid_is_replaced(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            tmp.mkdir(parents=True, exist_ok=True)
            stale_pid_file = tmp / "daemon.pid"
            # Use a PID that is extremely unlikely to exist (max PID + 1 doesn't
            # work portably; use 1 which is launchd on macOS but is a valid
            # alive PID — so instead use a definitely-dead PID by spawning
            # then reaping a subprocess).
            fake = subprocess.run(
                [sys.executable, "-c", "import os, sys; sys.stdout.write(str(os.getpid()))"],
                capture_output=True,
                text=True,
                check=True,
            )
            dead_pid = int(fake.stdout.strip())
            # Confirm the dead_pid is actually dead
            try:
                os.kill(dead_pid, 0)
                self.skipTest(f"subprocess pid {dead_pid} unexpectedly alive")
            except ProcessLookupError:
                pass
            stale_pid_file.write_text(str(dead_pid), encoding="utf-8")
            with _IsolatedStateDir(tmp):
                ok = daemon._write_pid_file()
                self.assertTrue(ok, "stale PID file should be cleaned and lock re-acquired")
                content = daemon.PID_FILE.read_text(encoding="utf-8").strip()
                self.assertEqual(int(content), os.getpid())

    def test_live_pid_blocked(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                # Write our own PID as 'already running'.
                daemon.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
                ok = daemon._write_pid_file()
                self.assertFalse(ok, "second invocation must NOT acquire the lock")


class SnapshotRoundtripTests(unittest.TestCase):
    """state.json parses back into the same shape after atomic write."""

    def test_roundtrip_preserves_shape(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                # Build a synthetic snapshot directly without spawning collector.
                snapshot = {
                    "generated_at": time.time(),
                    "ok": True,
                    "summary": {
                        "available": True,
                        "open_sessions": 2,
                        "open_panes": 5,
                    },
                    "slots": [
                        {
                            "pane_id": "%10",
                            "session": "alpha",
                            "project": "alpha",
                            "agent": "alpha",
                            "state": "waiting",
                            "color": daemon.COLOR_WAITING,
                            "icon": "bell",
                            "waiting": True,
                        },
                        {
                            "pane_id": "%11",
                            "session": "beta",
                            "project": "beta",
                            "agent": "beta",
                            "state": "working",
                            "color": daemon.COLOR_WORKING,
                            "icon": "terminal",
                            "waiting": False,
                        },
                    ],
                    "counts": {"working": 1, "waiting": 1, "error": 0, "idle": 0, "total": 2},
                }
                daemon._write_state(snapshot)
                self.assertTrue(daemon.STATE_JSON.exists())
                with daemon.STATE_JSON.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                self.assertEqual(loaded["ok"], True)
                self.assertEqual(loaded["counts"]["waiting"], 1)
                self.assertEqual(loaded["counts"]["working"], 1)
                self.assertEqual(len(loaded["slots"]), 2)
                self.assertEqual(loaded["slots"][0]["state"], "waiting")
                self.assertEqual(loaded["slots"][1]["color"], daemon.COLOR_WORKING)
                # The tmp sidecar must not remain on disk after os.replace.
                self.assertFalse((daemon.STATE_DIR / "state.json.tmp").exists())

    def test_read_state_handles_missing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                self.assertIsNone(daemon._read_state())

    def test_collector_failure_returns_sentinel(self) -> None:
        """When the collector subprocess fails, _build_snapshot returns ok=False."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                # Point REPO_ROOT at a path with NO collector script so subprocess fails.
                fake_root = tmp / "fake_repo"
                fake_root.mkdir(parents=True, exist_ok=True)
                daemon.REPO_ROOT = fake_root
                daemon.COLLECTOR = fake_root / "scripts" / "agentmax_status.py"
                snap = daemon._build_snapshot(fake_root, {"default": "terminal"}, pulse_phase=0)
                self.assertFalse(snap["ok"])
                self.assertEqual(snap["counts"]["total"], 0)
                self.assertEqual(snap["pulse_phase"], 0)
                snap1 = daemon._build_snapshot(fake_root, {"default": "terminal"}, pulse_phase=1)
                self.assertEqual(snap1["pulse_phase"], 1)
                self.assertEqual(snap["slots"], [])


class JitterBoundsTests(unittest.TestCase):
    """_jitter() returns durations strictly inside [POLL_JITTER band]."""

    def test_jitter_within_bounds(self) -> None:
        low = daemon.POLL_INTERVAL_S - daemon.POLL_JITTER_S
        high = daemon.POLL_INTERVAL_S + daemon.POLL_JITTER_S
        for _ in range(200):
            v = daemon._jitter()
            self.assertGreaterEqual(v, low)
            self.assertLessEqual(v, high)

    def test_jitter_varies(self) -> None:
        # Two consecutive samples should not be identical (overwhelmingly likely).
        samples = {daemon._jitter() for _ in range(20)}
        self.assertGreater(len(samples), 1, "jitter is degenerate (always same value)")


class _CommandModeTests(unittest.TestCase):
    """--status / --shutdown / --once modes behave correctly against fake state."""

    def test_status_no_pid_file(self) -> None:
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = daemon._command_status()
                self.assertEqual(rc, 0)
                self.assertIn("not running", buf.getvalue())

    def test_shutdown_no_pid_file(self) -> None:
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = daemon._command_shutdown()
                self.assertEqual(rc, 0)
                self.assertIn("not running", buf.getvalue())

    def test_status_with_live_pid(self) -> None:
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            with _IsolatedStateDir(tmp):
                # Write a fake state.json so --status prints the age.
                daemon.STATE_JSON.write_text(
                    json.dumps({"generated_at": time.time() - 1.5}),
                    encoding="utf-8",
                )
                daemon.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = daemon._command_status()
                self.assertEqual(rc, 0)
                self.assertIn("running", buf.getvalue())
                self.assertIn(f"pid={os.getpid()}", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
