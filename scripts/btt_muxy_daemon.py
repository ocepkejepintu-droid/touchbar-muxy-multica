#!/usr/bin/env python3
"""TouchBar Muxy daemon — stdlib-only background poller for the BTT control panel.

Polls scripts/agentmax_status.py --json every ~4s with ±0.5s jitter, derives
per-pane state (waiting/working/idle/error) with project+agent labels and SF
Symbol icons, writes ~/.local/share/touchbar-muxy/state.json, and exposes a
Unix socket for --status / --shutdown commands.

Pure Python 3 standard library only.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import json
import logging
import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("btt_muxy_daemon")

STATE_DIR = Path.home() / ".local" / "share" / "touchbar-muxy"
STATE_JSON = STATE_DIR / "state.json"
PID_FILE = STATE_DIR / "daemon.pid"
SOCKET_FILE = STATE_DIR / "daemon.sock"
LOG_FILE = STATE_DIR / "daemon.log"

REPO_ROOT = Path("/Users/yoseph/TouchBar")
COLLECTOR = REPO_ROOT / "scripts" / "agentmax_status.py"
ICONS_PATH = REPO_ROOT / "config" / "project-icons.json"

POLL_INTERVAL_S = 4.0
POLL_JITTER_S = 0.5
COLLECTOR_TIMEOUT_S = 6.0

COLOR_WORKING = "#34C759"
COLOR_WAITING = "#FF9F0A"
COLOR_ERROR = "#FF3B30"
COLOR_IDLE = "#8E8E93"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> None:
    _ensure_state_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _load_icons() -> Dict[str, str]:
    try:
        with ICONS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Failed to load %s: %s; using defaults", ICONS_PATH, exc)
    return {"default": "terminal"}


def _icon_for(project: Optional[str], icons: Dict[str, str]) -> str:
    if not project:
        return icons.get("default", "terminal")
    key = str(project).strip().lower()
    if key in icons:
        return icons[key]
    return icons.get("default", "terminal")


def _agent_label(pane: Dict[str, Any]) -> str:
    for key in ("agent", "title", "command"):
        val = pane.get(key)
        if val:
            text = str(val).strip()
            if text:
                return text.split()[0][:24]
    return "shell"


def _state_color(pane: Dict[str, Any], session_attached: bool) -> str:
    if pane.get("waiting"):
        return COLOR_WAITING
    if pane.get("done"):
        return COLOR_IDLE
    if pane.get("dead"):
        return COLOR_ERROR
    if pane.get("active") or session_attached:
        return COLOR_WORKING
    return COLOR_IDLE


def _build_slot_entries(
    summary: Dict[str, Any],
    icons: Dict[str, str],
) -> List[Dict[str, Any]]:
    sessions_by_name = {
        str(s.get("name") or ""): s
        for s in summary.get("sessions", [])
        if isinstance(s, dict)
    }
    entries: List[Dict[str, Any]] = []
    for pane in summary.get("panes", []):
        if not isinstance(pane, dict):
            continue
        if pane.get("dead"):
            continue
        session_name = str(pane.get("session") or "")
        sess = sessions_by_name.get(session_name, {})
        project = pane.get("project") or sess.get("project") or session_name or "-"
        session_attached = bool(sess.get("attached"))
        color = _state_color(pane, session_attached)
        entries.append(
            {
                "pane_id": str(pane.get("pane_id") or ""),
                "session": session_name,
                "window": pane.get("window"),
                "project": str(project),
                "agent": _agent_label(pane),
                "state": _state_name(color),
                "color": color,
                "icon": _icon_for(str(project), icons),
                "waiting": bool(pane.get("waiting")),
                "active": bool(pane.get("active")),
                "title": pane.get("title") or "",
            }
        )
    # Stable order: waiting first, then active, then idle; project ascending
    state_priority = {"waiting": 0, "working": 1, "error": 2, "idle": 3}
    entries.sort(
        key=lambda e: (
            state_priority.get(e["state"], 99),
            e["project"],
            e["pane_id"],
        )
    )
    return entries


def _state_name(color: str) -> str:
    return {
        COLOR_WORKING: "working",
        COLOR_WAITING: "waiting",
        COLOR_ERROR: "error",
        COLOR_IDLE: "idle",
    }.get(color, "idle")


def _compute_counts(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "working": sum(1 for e in entries if e["state"] == "working"),
        "waiting": sum(1 for e in entries if e["state"] == "waiting"),
        "error": sum(1 for e in entries if e["state"] == "error"),
        "idle": sum(1 for e in entries if e["state"] == "idle"),
        "total": len(entries),
    }


def _run_collector(repo_root: Path) -> Optional[Dict[str, Any]]:
    cmd = [
        sys.executable,
        str(COLLECTOR),
        "--json",
        "--root",
        str(repo_root),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=COLLECTOR_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        LOG.warning("collector timed out after %.1fs", COLLECTOR_TIMEOUT_S)
        return None
    except OSError as exc:
        LOG.error("collector launch failed: %s", exc)
        return None
    if proc.returncode != 0:
        LOG.warning(
            "collector exited %d: %s", proc.returncode, (proc.stderr or "").strip()[:200]
        )
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        LOG.warning("collector output not JSON: %s", exc)
        return None
    if not isinstance(data, dict):
        LOG.warning("collector returned non-dict: %s", type(data).__name__)
        return None
    return data


def _build_snapshot(repo_root: Path, icons: Dict[str, str], pulse_phase: int = 0) -> Dict[str, Any]:
    raw = _run_collector(repo_root)
    if not raw:
        return {
            "generated_at": time.time(),
            "ok": False,
            "pulse_phase": pulse_phase,
            "summary": {},
            "slots": [],
            "counts": {"working": 0, "waiting": 0, "error": 0, "idle": 0, "total": 0},
        }
    muxy = raw.get("muxy_notification_center") if isinstance(raw.get("muxy_notification_center"), dict) else {}
    slots = _build_slot_entries(muxy, icons)
    counts = _compute_counts(slots)
    return {
        "generated_at": time.time(),
        "ok": True,
        "pulse_phase": pulse_phase,
        "summary": {
            "available": bool(muxy.get("available")),
            "source": muxy.get("source"),
            "open_panes": (muxy.get("counts") or {}).get("open_panes", 0),
            "open_sessions": (muxy.get("counts") or {}).get("open_sessions", 0),
            "urgent_project": muxy.get("urgent_project"),
            "urgent_session": muxy.get("urgent_session"),
            "urgent_pane_id": muxy.get("urgent_pane_id"),
        },
        "slots": slots,
        "counts": counts,
    }


def _write_state(snapshot: Dict[str, Any]) -> None:
    _ensure_state_dir()
    tmp = STATE_JSON.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
        os.replace(tmp, STATE_JSON)
    except OSError as exc:
        LOG.error("failed to write %s: %s", STATE_JSON, exc)


def _read_state() -> Optional[Dict[str, Any]]:
    if not STATE_JSON.exists():
        return None
    try:
        with STATE_JSON.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_pid_file() -> bool:
    """Acquire the PID lock. Returns True if acquired, False if another daemon holds it."""
    _ensure_state_dir()
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            existing = 0
        if existing and _pid_alive(existing):
            LOG.info("another daemon is already running (pid=%d)", existing)
            return False
        # Stale PID file; remove it.
        with contextlib.suppress(OSError):
            PID_FILE.unlink()
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        LOG.error("failed to write pid file: %s", exc)
        return False
    return True


def _release_pid_file() -> None:
    with contextlib.suppress(OSError):
        PID_FILE.unlink()


def _command_status() -> int:
    if not PID_FILE.exists():
        print("daemon: not running")
        return 0
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print("daemon: pid file unreadable")
        return 0
    if _pid_alive(pid):
        snap = _read_state()
        age = ""
        if snap:
            age = f", last poll {time.time() - float(snap.get('generated_at', 0)):.1f}s ago"
        print(f"daemon: running pid={pid}{age}")
    else:
        print(f"daemon: stale pid file (pid={pid} not alive)")
    return 0


def _command_shutdown() -> int:
    if not PID_FILE.exists():
        print("daemon: not running")
        return 0
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print("daemon: pid file unreadable")
        return 1
    if not _pid_alive(pid):
        print(f"daemon: stale pid file (pid={pid} not alive)")
        _release_pid_file()
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("daemon: already gone")
        _release_pid_file()
        return 0
    except PermissionError as exc:
        print(f"daemon: permission denied: {exc}", file=sys.stderr)
        return 1
    # wait up to 3 seconds for the daemon to exit
    for _ in range(30):
        if not _pid_alive(pid):
            print("daemon: shutdown complete")
            return 0
        time.sleep(0.1)
    print("daemon: shutdown timed out", file=sys.stderr)
    return 1


def _command_once(repo_root: Path) -> int:
    icons = _load_icons()
    snapshot = _build_snapshot(repo_root, icons, pulse_phase=0)
    _write_state(snapshot)
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


class _SocketServer:
    def __init__(self) -> None:
        self.server: Optional[asyncio.AbstractServer] = None
        self.stop_event = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        except asyncio.TimeoutError:
            writer.close()
            return
        cmd = data.decode("utf-8", errors="replace").strip().lower()
        response = self._dispatch(cmd)
        writer.write(response.encode("utf-8") + b"\n")
        await writer.drain()
        writer.close()

    def _dispatch(self, cmd: str) -> str:
        if cmd == "status":
            return _command_status_text()
        if cmd == "shutdown":
            self.stop_event.set()
            return "ok shutdown_requested"
        if cmd == "ping":
            return "ok pong"
        return f"error unknown_command: {cmd!r}"

    async def start(self) -> None:
        _ensure_state_dir()
        if SOCKET_FILE.exists():
            with contextlib.suppress(OSError):
                SOCKET_FILE.unlink()
        self.server = await asyncio.start_unix_server(self.handle, path=str(SOCKET_FILE))
        os.chmod(SOCKET_FILE, 0o600)
        LOG.info("socket listening at %s", SOCKET_FILE)

    async def wait_for_stop(self) -> None:
        await self.stop_event.wait()

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
        with contextlib.suppress(OSError):
            SOCKET_FILE.unlink()


def _command_status_text() -> str:
    pid = os.getpid()
    snap = _read_state() or {}
    counts = snap.get("counts", {}) if isinstance(snap, dict) else {}
    return f"ok running pid={pid} slots={counts.get('total', 0)} working={counts.get('working', 0)} waiting={counts.get('waiting', 0)}"


def _jitter() -> float:
    return POLL_INTERVAL_S + random.uniform(-POLL_JITTER_S, POLL_JITTER_S)


async def _poll_loop(repo_root: Path, icons: Dict[str, str], stop: asyncio.Event) -> None:
    LOG.info("poll loop started (interval=%.1fs±%.1fs)", POLL_INTERVAL_S, POLL_JITTER_S)
    pulse_counter = 0
    while not stop.is_set():
        snapshot = _build_snapshot(repo_root, icons, pulse_phase=pulse_counter & 1)
        _write_state(snapshot)
        counts = snapshot.get("counts", {})
        LOG.info(
            "snapshot ok=%s slots=%s working=%s waiting=%s pulse=%s",
            snapshot.get("ok"),
            counts.get("total"),
            counts.get("working"),
            counts.get("waiting"),
            snapshot.get("pulse_phase"),
        )
        pulse_counter += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=_jitter())
        except asyncio.TimeoutError:
            pass
    LOG.info("poll loop stopped")


async def _run_daemon(repo_root: Path) -> int:
    if not _write_pid_file():
        print("daemon: already running", file=sys.stderr)
        return 0
    icons = _load_icons()
    socket_server = _SocketServer()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
        LOG.info("signal %d received; stopping", signum)
        stop_event.set()
        socket_server.stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig)

    await socket_server.start()
    try:
        await asyncio.gather(
            _poll_loop(repo_root, icons, stop_event),
            socket_server.wait_for_stop(),
            return_exceptions=True,
        )
    finally:
        await socket_server.stop()
        _release_pid_file()
        LOG.info("daemon exited cleanly")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TouchBar Muxy daemon — polls Muxy state for BTT widgets.",
    )
    parser.add_argument("--once", action="store_true", help="run one poll, write state.json, exit")
    parser.add_argument("--status", action="store_true", help="print daemon status and exit")
    parser.add_argument("--shutdown", action="store_true", help="signal daemon to exit and exit")
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help=f"repo root (default: {REPO_ROOT})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    args = parse_args(argv)
    repo_root = Path(args.root).resolve()

    if args.status:
        return _command_status()
    if args.shutdown:
        return _command_shutdown()
    if args.once:
        return _command_once(repo_root)

    try:
        return asyncio.run(_run_daemon(repo_root))
    except KeyboardInterrupt:
        LOG.info("interrupted; exiting")
        _release_pid_file()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
