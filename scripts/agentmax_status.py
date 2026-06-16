#!/usr/bin/env python3
"""Repo-local Agentmax/OMX Touch Bar status collector.

Standard-library-only CLI for compact BetterTouchTool output plus richer
machine/human diagnostics. The collector is read-only by default: it reads
.omx runtime state and JSONL log tails under --root. Optional snapshot logging
writes only when config logging.enabled is explicitly true.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 1,
    "fresh_seconds": 120,
    "stale_seconds": 600,
    "critical_stale_seconds": 1800,
    "log_tail_lines": 500,
    "compact_log_tail_bytes": 65536,
    "compact_log_file_limit": 6,
    "compact_max_chars": 80,
    "spinner_cadence_seconds": 2,
    "spinner_frames": ["◐", "◓", "◑", "◒"],
    "operator_summary": {
        "blocking_attention_kinds": ["workflow_stalled"],
        "inferred_attention_kinds": ["workflow_stalled", "tmux_invalid_config", "notify_unhealthy", "authority_stale"],
    },
    "labels": {
        "repo_default": "touch",
        "ultrawork": "ultra",
        "team": "team",
        "ralph": "ralph",
    },
    "attention": {
        "tmux_placeholder_target": "replace-with-tmux-pane-id",
        "authority_heartbeat_stale_seconds": 600,
        "notify_tick_stale_seconds": 600,
    },
    "multica": {
        "enabled": False,
        "reserved": True,
        "note": "Reserved for future optional multica status-file support; not collected by agentmax_status.py yet.",
        "status_file": "~/Library/Caches/multica-touchbar-status.txt",
        "cli_command": None,
    },
    "vibe_island": {
        "enabled": True,
        "session_file": "~/Library/Application Support/vibe-island/session-terminals.json",
        "log_file": "~/Library/Logs/VibeIsland/vibe-island.log",
        "permission_log_seconds": 120,
        "stale_seconds": 86400,
        "active_statuses": ["processing", "working", "active", "running"],
    },
    "muxy": {
        "enabled": False,
        "tmux_command": "tmux",
        "command_timeout_seconds": 1.5,
        "waiting_keywords": ["action required", "permission", "approve", "approval", "confirm", "waiting for input"],
        "done_keywords": ["press enter to close", "exited with code", "completed", "finished", "done"],
        "session_prefixes": ["omx-"],
    },
    "project_aliases_file": "config/project-aliases.json",
    "logging": {
        "enabled": False,
        "path": ".omx/logs/agentmax-status.jsonl",
        "max_bytes": 262144,
    },
}

COMPLETE_PHASES = {"complete", "completed", "done", "finished", "success", "succeeded"}
FAILED_PHASES = {"fail", "failed", "failure", "error"}
CANCELLED_PHASES = {"cancel", "cancelled", "canceled"}
SUCCESS_OUTCOMES = {"finish", "finished", "success", "succeeded", "complete", "completed", "done", "ok"}
FAILED_OUTCOMES = {"fail", "failed", "failure", "error", "errored"}
CANCELLED_OUTCOMES = {"cancel", "cancelled", "canceled", "aborted", "abort"}
CF_ABSOLUTE_TIME_OFFSET = 978307200
VIBE_AGENT_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
    "cursor": "Cursor",
    "opencode": "OpenCode",
    "droid": "Droid",
    "qoder": "Qoder",
    "qwen": "Qwen",
    "kimi": "Kimi",
    "deepseek": "DeepSeek",
    "copilot": "Copilot",
    "codebuddy": "CodeBuddy",
    "kiro": "Kiro",
    "hermes": "Hermes",
    "amp": "Amp",
}
GENERIC_PROJECT_NAMES = {
    "",
    "users",
    "home",
    "tmp",
    "var",
    "private",
    "volumes",
    "documents",
    "desktop",
    "downloads",
}
TIMESTAMP_KEYS = (
    "timestamp",
    "ts",
    "time",
    "event_at",
    "last_event_at",
    "last_tick_at",
    "last_activity_at",
    "last_turn_at",
    "updated_at",
    "completed_at",
    "ended_at",
    "started_at",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def deep_merge(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    result = dict(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if value is None or value == "" or value == "unknown":
        return None
    if isinstance(value, (int, float)):
        # OMX recent_turns values are epoch milliseconds; accept seconds too.
        seconds = float(value) / 1000.0 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == "unknown":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def max_time(values: Iterable[Any]) -> Optional[datetime]:
    parsed = [dt for dt in (parse_time(value) for value in values) if dt is not None]
    return max(parsed) if parsed else None


def age_seconds(now: datetime, dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    return max(0, int((now - dt).total_seconds()))


def fmt_age(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?"
    if seconds < 10:
        return "now"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def compact_text(text: Any, max_len: int = 160) -> str:
    raw = "" if text is None else str(text)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw if len(raw) <= max_len else raw[: max_len - 1].rstrip() + "…"


def slug(text: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", text.lower())
    return cleaned[:12] or fallback


def diag_init() -> Dict[str, Any]:
    return {
        "files_read": [],
        "files_missing": [],
        "json_errors": [],
        "jsonl_files": {},
        "decisions": [],
        "sources": [],
    }


def diag_source(
    diag: Dict[str, Any],
    path: Path,
    root: Path,
    status: str,
    *,
    detail: Optional[str] = None,
    kind: str = "file",
) -> None:
    entry: Dict[str, Any] = {"path": rel(path, root), "status": status, "kind": kind}
    if detail:
        entry["detail"] = compact_text(detail, 220)
    diag.setdefault("sources", []).append(entry)


def path_exists(path: Path, root: Path, diag: Dict[str, Any], *, required: bool = False, kind: str = "file") -> bool:
    try:
        exists = path.exists()
    except OSError as exc:
        diag["json_errors"].append({"path": rel(path, root), "error": f"stat failed: {exc}"})
        diag_source(diag, path, root, "error", detail=f"stat failed: {exc}", kind=kind)
        return False
    if not exists and required:
        diag["files_missing"].append(rel(path, root))
        diag_source(diag, path, root, "missing", kind=kind)
    return exists


def read_json(path: Path, root: Path, diag: Dict[str, Any], *, required: bool = False) -> Any:
    if not path_exists(path, root, diag, required=required, kind="json"):
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        diag["files_read"].append(rel(path, root))
        diag_source(diag, path, root, "read", kind="json")
        return data
    except Exception as exc:  # noqa: BLE001 - diagnostic collector must stay alive.
        diag["json_errors"].append({"path": rel(path, root), "error": str(exc)})
        diag_source(diag, path, root, "error", detail=str(exc), kind="json")
        return None


def read_jsonl_tail(
    path: Path,
    root: Path,
    max_lines: int,
    diag: Dict[str, Any],
    *,
    max_bytes: Optional[int] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    tail: deque[str] = deque(maxlen=max(1, max_lines))
    if not path_exists(path, root, diag, kind="jsonl"):
        return records
    line_count = 0
    parsed = 0
    errors = 0
    bytes_read: Optional[int] = None
    truncated_prefix = False
    try:
        if max_bytes and max_bytes > 0:
            with path.open("rb") as handle:
                try:
                    size = handle.seek(0, os.SEEK_END)
                except OSError:
                    size = path.stat().st_size
                    handle.seek(size)
                start = max(0, size - int(max_bytes))
                handle.seek(start)
                if start > 0:
                    handle.readline()
                    truncated_prefix = True
                data = handle.read(int(max_bytes))
            bytes_read = len(data)
            for line in data.decode("utf-8", errors="replace").splitlines(True):
                line_count += 1
                tail.append(line)
        else:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line_count += 1
                    tail.append(line)
        diag_source(diag, path, root, "read", kind="jsonl")
    except Exception as exc:  # noqa: BLE001
        diag["json_errors"].append({"path": rel(path, root), "error": f"read failed: {exc}"})
        diag_source(diag, path, root, "error", detail=f"read failed: {exc}", kind="jsonl")
        return records

    first_line_number = None if max_bytes else max(1, line_count - len(tail) + 1)
    for offset, line in enumerate(tail):
        index = (first_line_number + offset) if first_line_number is not None else None
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                records.append(value)
                parsed += 1
            else:
                errors += 1
        except json.JSONDecodeError as exc:
            errors += 1
            error_entry: Dict[str, Any] = {"path": rel(path, root), "error": str(exc)}
            if index is not None:
                error_entry["line"] = index
            diag["json_errors"].append(error_entry)
    diag["jsonl_files"][rel(path, root)] = {
        "lines_seen": line_count,
        "tail_limit": max_lines,
        "byte_tail_limit": int(max_bytes) if max_bytes and max_bytes > 0 else None,
        "bytes_read": bytes_read,
        "truncated_prefix": truncated_prefix,
        "records": parsed,
        "errors": errors,
    }
    return records


def event_time(record: Dict[str, Any]) -> Optional[datetime]:
    for key in TIMESTAMP_KEYS:
        if key in record:
            dt = parse_time(record.get(key))
            if dt:
                return dt
    # Some logs nest the event payload.
    for value in record.values():
        if isinstance(value, dict):
            nested = event_time(value)
            if nested:
                return nested
    return None


def collect_logs(
    omx: Path,
    root: Path,
    cfg: Dict[str, Any],
    diag: Dict[str, Any],
    *,
    compact: bool = False,
) -> Dict[str, Any]:
    logs_dir = omx / "logs"
    max_lines = int(cfg.get("log_tail_lines", 500) or 500)
    max_bytes = int(cfg.get("compact_log_tail_bytes", 65536) or 65536) if compact else None
    summary: Dict[str, Any] = {"files": {}, "latest_event_at": None, "latest_event": None, "latest_activity_by_session": {}}
    latest_dt: Optional[datetime] = None
    latest_record: Optional[Dict[str, Any]] = None
    latest_by_session: Dict[str, datetime] = {}

    if not path_exists(logs_dir, root, diag, required=True, kind="dir"):
        return summary

    try:
        log_paths = sorted(logs_dir.glob("*.jsonl"))
    except OSError as exc:
        diag["json_errors"].append({"path": rel(logs_dir, root), "error": f"glob failed: {exc}"})
        diag_source(diag, logs_dir, root, "error", detail=f"glob failed: {exc}", kind="dir")
        return summary
    if compact:
        file_limit = max(1, int(cfg.get("compact_log_file_limit", 6) or 6))

        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        if len(log_paths) > file_limit:
            newest = sorted(log_paths, key=lambda candidate: (mtime(candidate), candidate.name), reverse=True)[:file_limit]
            skipped = len(log_paths) - len(newest)
            diag["decisions"].append(
                f"compact log scan limited to newest {file_limit} jsonl files; skipped {skipped}"
            )
            log_paths = sorted(newest)

    for path in log_paths:
        records = read_jsonl_tail(path, root, max_lines, diag, max_bytes=max_bytes)
        file_latest: Optional[datetime] = None
        event_types: Dict[str, int] = {}
        for record in records:
            event_name = record.get("event") or record.get("type") or record.get("reason") or "unknown"
            event_types[str(event_name)] = event_types.get(str(event_name), 0) + 1
            dt = event_time(record)
            if dt and (file_latest is None or dt > file_latest):
                file_latest = dt
            session_ref = record.get("session_id") or record.get("thread_id") or record.get("preserved_active_session_id")
            if dt and isinstance(session_ref, str):
                previous = latest_by_session.get(session_ref)
                if previous is None or dt > previous:
                    latest_by_session[session_ref] = dt
            if dt and (latest_dt is None or dt > latest_dt):
                latest_dt = dt
                latest_record = record
        summary["files"][rel(path, root)] = {
            "records": len(records),
            "latest_event_at": iso(file_latest),
            "event_types": dict(sorted(event_types.items())[:20]),
        }

    summary["latest_event_at"] = iso(latest_dt)
    summary["latest_activity_by_session"] = {key: iso(value) for key, value in sorted(latest_by_session.items())}
    if latest_record is not None:
        summary["latest_event"] = {k: latest_record.get(k) for k in list(latest_record.keys())[:8]}
    return summary


def run_status_command(args: List[str], timeout_seconds: float) -> Tuple[int, str, str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(0.25, timeout_seconds),
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"
    except Exception as exc:  # noqa: BLE001 - status collection must not break the Touch Bar.
        return 1, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def muxy_project_label(session_name: str) -> str:
    name = session_name
    if name.startswith("omx-"):
        name = name[4:]
    name = re.sub(r"-\d{10,}(?:-[A-Za-z0-9]+)?$", "", name)
    first = name.split("-")[0] if name else "muxy"
    return slug(first, fallback="muxy")


def muxy_session_matches(session_name: str, prefixes: List[str]) -> bool:
    if not prefixes:
        prefixes = ["omx-"]
    return any(session_name.startswith(prefix) for prefix in prefixes)


def cf_absolute_to_unix(value: Any) -> Optional[float]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds + CF_ABSOLUTE_TIME_OFFSET


def vibe_agent_label(source: Any) -> str:
    text = str(source or "agent").strip().lower()
    return VIBE_AGENT_LABELS.get(text, text[:1].upper() + text[1:] if text else "Agent")


def vibe_project_label(session: Dict[str, Any]) -> str:
    cwd = str(session.get("cwd") or "").strip()
    if cwd:
        try:
            cwd_path = Path(cwd).expanduser().resolve()
            home = Path.home().resolve()
            basename = (cwd_path.name or "").strip().lower()
            parent = (cwd_path.parent.name or "").strip().lower()
            if cwd_path != home and basename and basename not in GENERIC_PROJECT_NAMES:
                return slug(basename, fallback="agent")
            if cwd_path != home and parent and parent not in GENERIC_PROJECT_NAMES:
                return slug(parent, fallback="agent")
        except Exception:  # noqa: BLE001
            pass
    message = compact_text(session.get("firstUserMessage"), 80)
    if message:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", message)
        if words:
            return slug("-".join(words[:2]), fallback="agent")
    return "agent"


def vibe_session_activity_at(session: Dict[str, Any]) -> Optional[datetime]:
    return parse_time(cf_absolute_to_unix(session.get("lastActivityAt")))


def vibe_session_is_active(session: Dict[str, Any], now: datetime, stale_seconds: int) -> bool:
    status = str(session.get("status") or "").strip().lower()
    inactive_statuses = {"ended", "complete", "completed", "done", "finished", "idle", "stopped"}
    if status in inactive_statuses:
        return False
    active_statuses = {"processing", "working", "active", "running"}
    activity_at = vibe_session_activity_at(session)
    freshness = age_seconds(now, activity_at)
    if status in active_statuses:
        return freshness is None or freshness <= stale_seconds
    return freshness is not None and freshness <= max(120, stale_seconds // 4)


def read_vibe_island_log_tail(path: Path, max_bytes: int = 65536) -> List[str]:
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            if start > 0:
                handle.readline()
            data = handle.read()
        return data.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def parse_vibe_log_timestamp(line: str) -> Optional[datetime]:
    match = re.search(r'"t":"([^"]+)"', line)
    if not match:
        return None
    return parse_time(match.group(1))


def collect_vibe_island_permissions(
    cfg: Dict[str, Any],
    now: datetime,
    diag: Dict[str, Any],
) -> List[Dict[str, Any]]:
    vi_cfg = cfg.get("vibe_island") if isinstance(cfg.get("vibe_island"), dict) else {}
    log_path = Path(str(vi_cfg.get("log_file") or "~/Library/Logs/VibeIsland/vibe-island.log")).expanduser()
    window_seconds = int(vi_cfg.get("permission_log_seconds", 120) or 120)
    pending: Dict[str, Dict[str, Any]] = {}
    for line in read_vibe_island_log_tail(log_path):
        lowered = line.lower()
        if "permissionrequest" not in lowered and "permission-enter" not in lowered:
            continue
        event_at = parse_vibe_log_timestamp(line)
        if not event_at or age_seconds(now, event_at) is None or age_seconds(now, event_at) > window_seconds:
            continue
        session_match = re.search(r"session=([0-9a-f-]{8,})", line, flags=re.IGNORECASE)
        session_id = session_match.group(1) if session_match else None
        tool_match = re.search(r"tool=([A-Za-z0-9_./-]+)", line)
        project_match = re.search(r"target=([A-Za-z0-9_./-]+)", line)
        source_match = re.search(r"source=([A-Za-z0-9_-]+)", line)
        key = session_id or f"perm-{len(pending)}"
        pending[key] = {
            "session_id": session_id,
            "agent": vibe_agent_label(source_match.group(1) if source_match else "agent"),
            "tool": compact_text(tool_match.group(1) if tool_match else None, 24),
            "project": slug(project_match.group(1) if project_match else "perm", fallback="perm"),
            "event_at": iso(event_at),
            "age": fmt_age(age_seconds(now, event_at)),
        }
    return list(pending.values())


def collect_vibe_island(
    cfg: Dict[str, Any],
    now: datetime,
    diag: Dict[str, Any],
) -> Dict[str, Any]:
    vi_cfg = cfg.get("vibe_island") if isinstance(cfg.get("vibe_island"), dict) else {}
    if vi_cfg.get("enabled") is False:
        return {"enabled": False, "available": False, "reason": "disabled", "sessions": [], "counts": {}}

    session_path = Path(str(vi_cfg.get("session_file") or "~/Library/Application Support/vibe-island/session-terminals.json")).expanduser()
    stale_seconds = int(vi_cfg.get("stale_seconds", 300) or 300)
    if not session_path.exists():
        diag["decisions"].append("vibe island session file missing; falling back")
        return {
            "enabled": True,
            "available": False,
            "empty_runtime": True,
            "reason": "session file missing",
            "session_file": str(session_path),
            "sessions": [],
            "permissions": [],
            "counts": {"active": 0, "permissions": 0},
            "compact_parts": {"project_labels": [], "agent_labels": []},
        }

    try:
        with session_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        diag["json_errors"].append({"path": str(session_path), "error": str(exc)})
        return {
            "enabled": True,
            "available": False,
            "reason": compact_text(exc, 120),
            "session_file": str(session_path),
            "sessions": [],
            "permissions": [],
            "counts": {"active": 0, "permissions": 0},
            "compact_parts": {"project_labels": [], "agent_labels": []},
        }

    if not isinstance(raw, dict):
        return {
            "enabled": True,
            "available": False,
            "reason": "invalid session file",
            "session_file": str(session_path),
            "sessions": [],
            "permissions": [],
            "counts": {"active": 0, "permissions": 0},
            "compact_parts": {"project_labels": [], "agent_labels": []},
        }

    sessions: List[Dict[str, Any]] = []
    for session_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        activity_at = vibe_session_activity_at(entry)
        project = vibe_project_label(entry)
        agent = vibe_agent_label(entry.get("source"))
        terminal = compact_text(entry.get("termProgram"), 16) or None
        tool = compact_text(entry.get("currentTool") or entry.get("toolTarget"), 24) or None
        active = vibe_session_is_active(entry, now, stale_seconds)
        sessions.append(
            {
                "session_id": str(session_id),
                "short_session_id": str(session_id)[:8],
                "agent": agent,
                "project": project,
                "terminal": terminal,
                "tool": tool,
                "status": entry.get("status"),
                "active": active,
                "cwd": entry.get("cwd"),
                "last_activity_at": iso(activity_at),
                "age": fmt_age(age_seconds(now, activity_at)),
                "freshness_seconds": age_seconds(now, activity_at),
            }
        )

    sessions.sort(
        key=lambda item: (
            0 if item.get("active") else 1,
            -(item.get("freshness_seconds") if item.get("freshness_seconds") is not None else 10**9),
        ),
    )
    active_sessions = [session for session in sessions if session.get("active")]
    permissions = collect_vibe_island_permissions(cfg, now, diag)
    urgent = permissions[0] if permissions else (active_sessions[0] if active_sessions else None)

    return {
        "enabled": True,
        "available": True,
        "session_file": str(session_path),
        "sessions": sessions,
        "active_sessions": active_sessions,
        "permissions": permissions,
        "urgent": urgent,
        "urgent_kind": "permission" if permissions else ("active" if active_sessions else None),
        "counts": {
            "active": len(active_sessions),
            "permissions": len(permissions),
            "tracked": len(sessions),
        },
        "compact_parts": {
            "project_labels": list(dict.fromkeys(session["project"] for session in active_sessions)),
            "agent_labels": list(dict.fromkeys(session["agent"] for session in active_sessions)),
        },
    }


def parse_tmux_rows(output: str, width: int) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < width:
            parts.extend([""] * (width - len(parts)))
        rows.append(parts[:width])
    return rows


def muxy_runtime_empty_reason(reason: str) -> bool:
    normalized = reason.lower()
    return (
        "no server running" in normalized
        or "no sessions" in normalized
        or ("error connecting to" in normalized and "no such file or directory" in normalized)
        or "operation not permitted" in normalized
        or "permission denied" in normalized
    )


def collect_muxy_runtime(
    root: Path,
    cfg: Dict[str, Any],
    now: datetime,
    diag: Dict[str, Any],
    pane_success_map: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    muxy_cfg = cfg.get("muxy") if isinstance(cfg.get("muxy"), dict) else {}
    if muxy_cfg.get("enabled") is False:
        return {"enabled": False, "available": False, "reason": "disabled", "sessions": [], "panes": [], "counts": {}}

    tmux_command = str(muxy_cfg.get("tmux_command") or "tmux")
    timeout_seconds = float(muxy_cfg.get("command_timeout_seconds") or 1.5)
    prefixes = [str(item) for item in muxy_cfg.get("session_prefixes", ["omx-"]) if str(item)] if isinstance(muxy_cfg.get("session_prefixes"), list) else ["omx-"]
    waiting_keywords = [str(item).lower() for item in muxy_cfg.get("waiting_keywords", []) if str(item)] if isinstance(muxy_cfg.get("waiting_keywords"), list) else []

    session_rc, session_out, session_err = run_status_command(
        [tmux_command, "list-sessions", "-F", "#{session_name}\t#{session_id}\t#{session_windows}\t#{session_attached}\t#{session_created}"],
        timeout_seconds,
    )
    pane_rc, pane_out, pane_err = run_status_command(
        [tmux_command, "list-panes", "-a", "-F", "#{session_name}\t#{window_name}\t#{pane_id}\t#{pane_active}\t#{pane_current_command}\t#{pane_title}\t#{pane_dead}\t#{pane_start_command}"],
        timeout_seconds,
    )

    if session_rc != 0 and pane_rc != 0:
        reason = compact_text(session_err or pane_err or "tmux unavailable", 160)
        diag["decisions"].append(f"muxy runtime unavailable: {reason}")
        if muxy_runtime_empty_reason(reason):
            return {
                "enabled": True,
                "available": False,
                "empty_runtime": True,
                "reason": reason,
                "generated_at": iso(now),
                "sessions": [],
                "panes": [],
                "waiting": [],
                "done": [],
                "urgent": None,
                "urgent_session": None,
                "urgent_pane_id": None,
                "urgent_project": None,
                "urgent_reason": None,
                "counts": {"waiting": 0, "done": 0, "open_panes": 0, "open_sessions": 0},
                "compact_parts": {"project_labels": [], "waiting_labels": []},
            }
        return {
            "enabled": True,
            "available": False,
            "reason": reason,
            "sessions": [],
            "panes": [],
            "counts": {"waiting": 0, "done": 0, "open_panes": 0, "open_sessions": 0},
        }

    sessions: List[Dict[str, Any]] = []
    muxy_session_names = set()
    for name, session_id, windows, attached, created in parse_tmux_rows(session_out, 5):
        if not muxy_session_matches(name, prefixes):
            continue
        muxy_session_names.add(name)
        sessions.append(
            {
                "name": name,
                "session_id": session_id,
                "project": muxy_project_label(name),
                "windows": int(windows) if str(windows).isdigit() else None,
                "attached": attached == "1",
                "created_at_epoch": int(created) if str(created).isdigit() else None,
            }
        )

    success_map = pane_success_map if isinstance(pane_success_map, dict) else {}
    panes: List[Dict[str, Any]] = []
    waiting: List[Dict[str, Any]] = []
    done: List[Dict[str, Any]] = []
    open_panes = 0
    for session_name, window_name, pane_id, active, command, title, dead, start_command in parse_tmux_rows(pane_out, 8):
        if session_name not in muxy_session_names and not muxy_session_matches(session_name, prefixes):
            continue
        project = muxy_project_label(session_name)
        text = f"{title} {command} {start_command}".lower()
        is_dead = dead == "1"
        is_waiting = any(keyword in text for keyword in waiting_keywords)
        is_success = bool(success_map.get(pane_id, False))
        if not is_dead and not is_success:
            open_panes += 1
        pane = {
            "session": session_name,
            "window": window_name,
            "pane_id": pane_id,
            "project": project,
            "active": active == "1",
            "command": command,
            "title": compact_text(title, 120),
            "dead": is_dead,
            "waiting": is_waiting,
            "done": is_success,
        }
        panes.append(pane)
        if is_waiting:
            waiting.append(pane)
        elif is_success:
            done.append(pane)

    projects = list(dict.fromkeys([pane["project"] for pane in waiting] + [pane["project"] for pane in panes]))

    urgent = None
    urgent_reason = None
    if waiting:
        session_map = {s["name"]: s for s in sessions}
        attached_waiting = [p for p in waiting if session_map.get(p["session"], {}).get("attached")]
        if attached_waiting:
            urgent = attached_waiting[0]
            urgent_reason = "attached"
        else:
            active_waiting = [p for p in waiting if p.get("active")]
            if active_waiting:
                urgent = active_waiting[0]
                urgent_reason = "active"
            else:
                waiting_sessions = list(dict.fromkeys(p["session"] for p in waiting))
                oldest_session = None
                oldest_time = None
                for s in waiting_sessions:
                    sess = session_map.get(s)
                    if sess and sess.get("created_at_epoch"):
                        if oldest_time is None or sess["created_at_epoch"] < oldest_time:
                            oldest_time = sess["created_at_epoch"]
                            oldest_session = s
                if oldest_session:
                    for p in waiting:
                        if p["session"] == oldest_session:
                            urgent = p
                            urgent_reason = "oldest_session"
                            break
                else:
                    urgent = waiting[0]
                    urgent_reason = "first_waiting"

    return {
        "enabled": True,
        "available": session_rc == 0 or pane_rc == 0,
        "reason": None,
        "generated_at": iso(now),
        "sessions": sessions,
        "panes": panes,
        "waiting": waiting,
        "done": done,
        "urgent": urgent,
        "urgent_session": urgent["session"] if urgent else None,
        "urgent_pane_id": urgent["pane_id"] if urgent else None,
        "urgent_project": urgent["project"] if urgent else None,
        "urgent_reason": urgent_reason,
        "counts": {
            "waiting": len(waiting),
            "done": len(done),
            "open_panes": open_panes,
            "open_sessions": len(sessions),
        },
        "compact_parts": {
            "project_labels": projects,
            "waiting_labels": list(dict.fromkeys(pane["project"] for pane in waiting)),
        },
    }


def load_project_aliases(root: Path, cfg: Dict[str, Any], diag: Dict[str, Any]) -> Dict[str, Any]:
    alias_file = str(cfg.get("project_aliases_file") or "config/project-aliases.json")
    alias_path = Path(alias_file).expanduser()
    if not alias_path.is_absolute():
        alias_path = root / alias_path
    data = read_json(alias_path, root, diag)
    if isinstance(data, dict):
        return data
    return {}


def alias_lookup(cwd: Optional[str], root: Path, cfg: Dict[str, Any], aliases: Dict[str, Any]) -> Optional[str]:
    if not cwd:
        return None
    labels = cfg.get("labels", {}) if isinstance(cfg.get("labels"), dict) else {}
    root_default = str(labels.get("repo_default") or "touch")
    try:
        cwd_path = Path(cwd).expanduser().resolve()
    except Exception:  # noqa: BLE001 - labels should never break status output.
        cwd_path = Path(str(cwd)).expanduser()
    basename = cwd_path.name or str(cwd).rstrip("/").split("/")[-1]

    paths = aliases.get("paths") if isinstance(aliases.get("paths"), dict) else {}
    for key in (str(cwd), str(cwd_path)):
        value = paths.get(key)
        if value:
            return slug(str(value), fallback=root_default)

    basenames = aliases.get("basenames") if isinstance(aliases.get("basenames"), dict) else {}
    for key in (basename, basename.lower()):
        value = basenames.get(key)
        if value:
            return slug(str(value), fallback=root_default)

    try:
        if cwd_path == root.resolve() or basename.lower() in {root.name.lower(), "touchbar"}:
            return root_default
    except Exception:  # noqa: BLE001
        if basename.lower() in {root.name.lower(), "touchbar"}:
            return root_default

    if basename:
        return slug(basename, fallback=root_default)
    return None


def skill_label(mode: str, skill: Dict[str, Any], prompt_routing: Dict[str, Any], cfg: Dict[str, Any], aliases: Dict[str, Any]) -> Optional[str]:
    labels = cfg.get("labels", {}) if isinstance(cfg.get("labels"), dict) else {}
    skills = aliases.get("skills") if isinstance(aliases.get("skills"), dict) else {}
    keywords = aliases.get("keywords") if isinstance(aliases.get("keywords"), dict) else {}
    keyword = str(skill.get("keyword") or "").lstrip("$")
    skill_name = str(skill.get("name") or skill.get("skill") or mode or "")
    route = str(prompt_routing.get("route") or "").lstrip("$")
    for table, key in ((keywords, keyword), (skills, skill_name), (labels, mode), (labels, skill_name), (skills, route)):
        if key and isinstance(table, dict) and table.get(key):
            return slug(str(table[key]), fallback="sess")
    for value in (keyword, skill_name, route, mode):
        if value and value != "unknown":
            return slug(value, fallback="sess")
    return None


def repo_label(
    root: Path,
    cfg: Dict[str, Any],
    mode: str = "unknown",
    cwd: Optional[str] = None,
    skill: Optional[Dict[str, Any]] = None,
    prompt_routing: Optional[Dict[str, Any]] = None,
    aliases: Optional[Dict[str, Any]] = None,
) -> str:
    aliases = aliases if isinstance(aliases, dict) else {}
    label = alias_lookup(cwd, root, cfg, aliases)
    if label:
        return label
    labels = cfg.get("labels", {}) if isinstance(cfg.get("labels"), dict) else {}
    if root.name.lower() == "touchbar":
        return str(labels.get("repo_default") or "touch")
    label = skill_label(mode, skill or {}, prompt_routing or {}, cfg, aliases)
    if label:
        return label
    return slug(root.name or mode, fallback=(mode[:5] if mode and mode != "unknown" else "sess"))


def session_label(
    session_id: str,
    root: Path,
    cfg: Dict[str, Any],
    mode: str,
    current_session: Any,
    skill: Optional[Dict[str, Any]] = None,
    history_entry: Optional[Dict[str, Any]] = None,
    prompt_routing: Optional[Dict[str, Any]] = None,
    aliases: Optional[Dict[str, Any]] = None,
) -> str:
    cwd = None
    if isinstance(current_session, dict):
        cwd = current_session.get("cwd")
    if not cwd and isinstance(history_entry, dict):
        cwd = history_entry.get("cwd")
    label = repo_label(root, cfg, mode, cwd, skill, prompt_routing, aliases)
    if label:
        return label
    return slug(session_id[:8], fallback="sess")


def recent_turn_latest(notify_state: Any) -> Optional[datetime]:
    if not isinstance(notify_state, dict):
        return None
    turns = notify_state.get("recent_turns")
    if not isinstance(turns, dict) or not turns:
        return None
    return max_time(turns.values())


def terminal_word(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def classify_terminal_value(value: Any) -> Optional[str]:
    text = terminal_word(value)
    if not text:
        return None
    if text in SUCCESS_OUTCOMES:
        return "success"
    if text in FAILED_OUTCOMES:
        return "failed"
    if text in CANCELLED_OUTCOMES:
        return "cancelled"
    return None

def pid_running(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def native_stop_terminal(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return {"outcome": None, "confidence": None, "finished_at": None}
    guard = entry.get("ordinary_no_progress_guard") if isinstance(entry.get("ordinary_no_progress_guard"), dict) else {}

    for key in ("outcome", "terminal_outcome", "status", "result", "lifecycle_outcome", "run_outcome"):
        outcome = classify_terminal_value(entry.get(key))
        if outcome:
            return {
                "outcome": outcome,
                "confidence": "explicit",
                "finished_at": max_time([entry.get("completed_at"), entry.get("ended_at"), entry.get("updated_at"), guard.get("last_seen_at")]),
                "source": key,
            }

    guard = entry.get("ordinary_no_progress_guard") if isinstance(entry.get("ordinary_no_progress_guard"), dict) else {}
    text = " ".join(
        str(value or "")
        for value in (entry.get("last_signature"), guard.get("fingerprint"))
    ).lower()
    failed_words = ("failed", "failure", "error", "traceback", "exception")
    cancelled_words = ("cancelled", "canceled", "cancelled.", "canceled.", "aborted", "abort")
    completion_words = ("|done", "done.", " implemented", " complete", " completed", " finished", " validation output")
    finished_at = max_time([entry.get("updated_at"), guard.get("last_seen_at"), guard.get("first_seen_at")])
    if any(word in text for word in failed_words):
        return {"outcome": "failed", "confidence": "heuristic", "finished_at": finished_at, "source": "native_stop_text"}
    if any(word in text for word in cancelled_words):
        return {"outcome": "cancelled", "confidence": "heuristic", "finished_at": finished_at, "source": "native_stop_text"}
    if any(word in text for word in completion_words):
        return {"outcome": "heuristic", "confidence": "heuristic", "finished_at": finished_at, "source": "native_stop_text"}
    return {"outcome": None, "confidence": None, "finished_at": None}


def session_thread_latest(session: Dict[str, Any]) -> Optional[datetime]:
    threads = session.get("threads") if isinstance(session, dict) else None
    if not isinstance(threads, dict):
        return None
    latest: Optional[datetime] = None
    for thread in threads.values():
        if not isinstance(thread, dict):
            continue
        seen = parse_time(thread.get("last_seen_at"))
        if seen and (latest is None or seen > latest):
            latest = seen
    return latest


def classify_session(
    session_id: str,
    session_dir: Optional[Path],
    root: Path,
    cfg: Dict[str, Any],
    now: datetime,
    diag: Dict[str, Any],
    current_session: Any,
    subagent_session: Optional[Dict[str, Any]],
    history_entry: Optional[Dict[str, Any]],
    native_stop_entry: Optional[Dict[str, Any]],
    aliases: Optional[Dict[str, Any]],
    log_activity_at: Optional[Any] = None,
) -> Dict[str, Any]:
    skill = read_json(session_dir / "skill-active-state.json", root, diag) if session_dir else None
    ultra = read_json(session_dir / "ultrawork-state.json", root, diag) if session_dir else None
    hud = read_json(session_dir / "hud-state.json", root, diag) if session_dir else None
    notify = read_json(session_dir / "notify-hook-state.json", root, diag) if session_dir else None
    prompt_routing = read_json(session_dir / "prompt-routing-state.json", root, diag) if session_dir else None

    skill = skill if isinstance(skill, dict) else {}
    ultra = ultra if isinstance(ultra, dict) else {}
    hud = hud if isinstance(hud, dict) else {}
    notify = notify if isinstance(notify, dict) else {}
    prompt_routing = prompt_routing if isinstance(prompt_routing, dict) else {}
    subagent_session = subagent_session if isinstance(subagent_session, dict) else {}
    history_entry = history_entry if isinstance(history_entry, dict) else {}
    native_stop_entry = native_stop_entry if isinstance(native_stop_entry, dict) else {}
    aliases = aliases if isinstance(aliases, dict) else {}
    current = isinstance(current_session, dict) and current_session.get("session_id") == session_id
    current_pid_alive = bool(current and pid_running(current_session.get("pid")))

    mode = str(ultra.get("mode") or skill.get("initialized_mode") or skill.get("skill") or "unknown")
    phase = str(ultra.get("current_phase") or skill.get("phase") or "unknown")
    active_flag = bool(ultra.get("active") or skill.get("active"))
    lifecycle = str(ultra.get("lifecycle_outcome") or ultra.get("run_outcome") or "").lower()
    lifecycle_outcome = classify_terminal_value(lifecycle)
    phase_lower = phase.lower()
    phase_outcome = (
        "success" if phase_lower in COMPLETE_PHASES
        else "failed" if phase_lower in FAILED_PHASES
        else "cancelled" if phase_lower in CANCELLED_PHASES
        else None
    )
    history_outcome = classify_terminal_value(
        history_entry.get("outcome")
        or history_entry.get("status")
        or history_entry.get("result")
        or history_entry.get("lifecycle_outcome")
        or history_entry.get("run_outcome")
    )

    native_terminal = native_stop_terminal(native_stop_entry)
    native_success_at = (
        native_terminal.get("finished_at")
        if native_terminal.get("outcome") == "success" and native_terminal.get("confidence") == "explicit"
        else None
    )
    native_terminal_at = native_terminal.get("finished_at")
    started_at = max_time([ultra.get("started_at"), skill.get("activated_at"), history_entry.get("started_at")])
    explicit_non_success = next(
        (
            outcome
            for outcome in (lifecycle_outcome, phase_outcome, history_outcome, native_terminal.get("outcome"))
            if outcome in {"failed", "cancelled"}
        ),
        None,
    )
    completed_at = max_time(
        [
            ultra.get("completed_at") if lifecycle_outcome not in {"failed", "cancelled"} else None,
            history_entry.get("ended_at") if history_outcome not in {"failed", "cancelled"} else None,
            native_success_at,
        ]
    )
    recent_turn_at = recent_turn_latest(notify)
    subagent_seen_at = session_thread_latest(subagent_session)
    live_updated_at = max_time(
        [
            ultra.get("updated_at"),
            skill.get("updated_at"),
            hud.get("last_progress_at"),
            hud.get("last_turn_at"),
            notify.get("last_event_at"),
            subagent_session.get("updated_at"),
            recent_turn_at,
            subagent_seen_at,
        ]
    )
    updated_at = max_time(
        [
            ultra.get("updated_at"),
            skill.get("updated_at"),
            hud.get("last_progress_at"),
            hud.get("last_turn_at"),
            notify.get("last_event_at"),
            subagent_session.get("updated_at"),
            recent_turn_at,
            subagent_seen_at,
            log_activity_at,
            history_entry.get("ended_at"),
            native_terminal_at,
            native_stop_entry.get("updated_at"),
        ]
    )

    terminal_outcome = explicit_non_success or lifecycle_outcome or phase_outcome or history_outcome or native_terminal.get("outcome")
    terminal_confidence = "explicit" if terminal_outcome in {"success", "failed", "cancelled"} else native_terminal.get("confidence")
    if terminal_outcome == "success" and native_terminal.get("outcome") == "heuristic":
        terminal_outcome = "heuristic"
        terminal_confidence = "heuristic"

    completion_marker = bool(
        not explicit_non_success
        and (
            completed_at
            or phase_outcome == "success"
            or lifecycle_outcome == "success"
            or history_entry.get("ended_at")
            or native_success_at
        )
    )
    completed = completion_marker
    if native_success_at and not (ultra.get("completed_at") or history_entry.get("ended_at")):
        diag["decisions"].append(
            f"session {session_id} classified completed from explicit native-stop-state at {iso(native_success_at)}"
        )
    if native_terminal.get("outcome") == "heuristic":
        diag["decisions"].append(
            f"session {session_id} kept out of completed_runs because native-stop-state completion was heuristic"
        )
    if explicit_non_success:
        completed = False
        diag["decisions"].append(
            f"session {session_id} classified terminal_non_success={explicit_non_success}; not eligible for done"
        )
    if completed_at and live_updated_at and live_updated_at > completed_at and phase_outcome != "success" and lifecycle_outcome != "success":
        completed = False
        diag["decisions"].append(
            f"session {session_id} kept active/maybe-active because live_updated_at is newer than completed_at with phase={phase}"
        )
    if completion_marker and active_flag and completed:
        diag["decisions"].append(
            f"session {session_id} classified completed because completion marker overrides active=true"
        )

    codex_activity_at = max_time([hud.get("last_progress_at"), hud.get("last_turn_at"), notify.get("last_event_at"), recent_turn_at, subagent_seen_at])
    codex_activity_age = age_seconds(now, codex_activity_at)
    stale_seconds = int(cfg.get("stale_seconds", 600) or 600)
    codex_inferred_active = bool(
        not active_flag
        and not completed
        and terminal_outcome not in {"failed", "cancelled", "heuristic"}
        and codex_activity_at
        and codex_activity_age is not None
        and codex_activity_age <= stale_seconds
    )
    if codex_inferred_active:
        diag["decisions"].append(
            f"session {session_id} inferred active from fresh hud/notify/subagent activity at {iso(codex_activity_at)}"
        )
    current_process_active = bool(
        current_pid_alive
        and not completed
        and terminal_outcome not in {"failed", "cancelled", "heuristic"}
    )
    if current_process_active and not (active_flag or codex_inferred_active):
        diag["decisions"].append(
            f"session {session_id} kept active because current session pid {current_session.get('pid')} is alive"
        )
    active = bool((active_flag or codex_inferred_active or current_process_active) and not completed and terminal_outcome not in {"failed", "cancelled", "heuristic"})
    latest_activity_at = max_time([now if current_process_active else None, updated_at, completed_at, started_at])
    freshness = age_seconds(now, latest_activity_at)
    critical_stale_seconds = int(cfg.get("critical_stale_seconds", 1800) or 1800)
    stalled = bool(active and freshness is not None and freshness > stale_seconds)
    severity = "critical" if stalled and freshness and freshness > critical_stale_seconds else "warn"

    label = session_label(
        session_id,
        root,
        cfg,
        mode,
        current_session if current else {},
        skill,
        history_entry,
        prompt_routing,
        aliases,
    )
    if mode == "unknown" and current:
        mode = "session"
        phase = "current"
    if codex_inferred_active and mode == "unknown":
        mode = "codex"
        phase = "subagent" if (subagent_session.get("threads") if isinstance(subagent_session, dict) else None) else "active"

    return {
        "session_id": session_id,
        "label": label,
        "mode": mode,
        "phase": phase,
        "active": active,
        "active_inferred": codex_inferred_active,
        "completed": completed,
        "stalled": stalled,
        "stale_severity": severity if stalled else None,
        "current": current,
        "started_at": iso(started_at),
        "updated_at": iso(updated_at),
        "completed_at": iso(completed_at),
        "latest_activity_at": iso(latest_activity_at),
        "freshness_seconds": freshness,
        "age": fmt_age(freshness),
        "tmux_pane_id": ultra.get("tmux_pane_id"),
        "tmux_window_id": ultra.get("tmux_window_id"),
        "turn_id": ultra.get("turn_id") or skill.get("turn_id"),
        "verification": compact_text(ultra.get("verification"), 240) or None,
        "skill": {
            "active": bool(skill.get("active")),
            "name": skill.get("skill"),
            "phase": skill.get("phase"),
            "keyword": skill.get("keyword"),
            "source": skill.get("source"),
            "active_skills": skill.get("active_skills") if isinstance(skill.get("active_skills"), list) else [],
        },
        "hud": {
            "turn_count": hud.get("turn_count"),
            "last_turn_at": hud.get("last_turn_at"),
            "last_progress_at": hud.get("last_progress_at"),
            "last_agent_output": compact_text(hud.get("last_agent_output"), 160) or None,
        },
        "notify": {
            "last_event_at": notify.get("last_event_at"),
            "recent_turn_count": len(notify.get("recent_turns", {})) if isinstance(notify.get("recent_turns"), dict) else 0,
            "latest_recent_turn_at": iso(recent_turn_at),
        },
        "prompt_routing": {
            "active": bool(prompt_routing),
            "route": prompt_routing.get("route") or prompt_routing.get("workflow") or prompt_routing.get("skill"),
        },
        "subagents": summarize_session_threads(subagent_session, now),
        "history": {
            "started_at": history_entry.get("started_at"),
            "ended_at": history_entry.get("ended_at"),
            "pid": history_entry.get("pid"),
        },
        "process": {
            "pid": current_session.get("pid") if current else None,
            "alive": current_pid_alive,
            "active_evidence": current_process_active,
        },
        "native_stop": {
            "available": bool(native_stop_entry),
            "finished_at": iso(native_terminal_at),
            "outcome": native_terminal.get("outcome"),
            "confidence": native_terminal.get("confidence"),
            "updated_at": native_stop_entry.get("updated_at"),
        },
        "terminal": {
            "outcome": terminal_outcome,
            "confidence": terminal_confidence,
            "finished_at": iso(native_terminal_at if native_terminal.get("outcome") else completed_at),
            "non_success": terminal_outcome in {"failed", "cancelled"},
        },
        "source_dir": rel(session_dir, root) if session_dir else None,
    }


def summarize_session_threads(session: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    threads = session.get("threads") if isinstance(session, dict) else None
    if not isinstance(threads, dict):
        return {"leaders": 0, "subagents": 0, "threads": 0, "turns": 0, "last_seen_at": None}
    leaders = 0
    subagents = 0
    turns = 0
    latest: Optional[datetime] = None
    for thread in threads.values():
        if not isinstance(thread, dict):
            continue
        if thread.get("kind") == "subagent":
            subagents += 1
        else:
            leaders += 1
        turns += int(thread.get("turn_count") or 0)
        seen = parse_time(thread.get("last_seen_at"))
        if seen and (latest is None or seen > latest):
            latest = seen
    return {
        "leaders": leaders,
        "subagents": subagents,
        "threads": leaders + subagents,
        "turns": turns,
        "last_seen_at": iso(latest),
        "last_seen_age_seconds": age_seconds(now, latest),
    }


def summarize_all_subagents(data: Any, now: datetime, stale_seconds: int) -> Dict[str, Any]:
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, dict):
        return {"sessions": 0, "leaders": 0, "subagents": 0, "threads": 0, "active_threads": 0, "turns": 0, "last_seen_at": None}
    leaders = subagents = threads_count = active_threads = turns = 0
    latest: Optional[datetime] = None
    for session in sessions.values():
        threads = session.get("threads") if isinstance(session, dict) else None
        if not isinstance(threads, dict):
            continue
        for thread in threads.values():
            if not isinstance(thread, dict):
                continue
            threads_count += 1
            if thread.get("kind") == "subagent":
                subagents += 1
            else:
                leaders += 1
            turns += int(thread.get("turn_count") or 0)
            seen = parse_time(thread.get("last_seen_at"))
            if seen:
                if age_seconds(now, seen) is not None and age_seconds(now, seen) <= stale_seconds:
                    active_threads += 1
                if latest is None or seen > latest:
                    latest = seen
    return {
        "sessions": len(sessions),
        "leaders": leaders,
        "subagents": subagents,
        "threads": threads_count,
        "active_threads": active_threads,
        "turns": turns,
        "last_seen_at": iso(latest),
        "last_seen_age_seconds": age_seconds(now, latest),
    }


def summarize_notify(state: Any, owner: Any, cfg: Dict[str, Any], now: datetime) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    state = state if isinstance(state, dict) else {}
    owner = owner if isinstance(owner, dict) else {}
    attn_cfg = cfg.get("attention", {}) if isinstance(cfg.get("attention"), dict) else {}
    owner_stale = int(attn_cfg.get("authority_heartbeat_stale_seconds", 600) or 600)
    tick_stale = int(attn_cfg.get("notify_tick_stale_seconds", 600) or 600)

    dispatch = state.get("dispatch_drain") if isinstance(state.get("dispatch_drain"), dict) else {}
    leader_nudge = state.get("leader_nudge") if isinstance(state.get("leader_nudge"), dict) else {}
    fallback = state.get("fallback_auto_nudge") if isinstance(state.get("fallback_auto_nudge"), dict) else {}
    adaptive = state.get("adaptive_poll") if isinstance(state.get("adaptive_poll"), dict) else {}
    authority_backoff = state.get("authority_backoff") if isinstance(state.get("authority_backoff"), dict) else {}

    owner_heartbeat = parse_time(owner.get("heartbeat_at"))
    owner_age = age_seconds(now, owner_heartbeat)
    tick_at = max_time([dispatch.get("last_tick_at"), leader_nudge.get("last_tick_at"), fallback.get("last_tick_at"), adaptive.get("last_tick_at")])
    tick_age = age_seconds(now, tick_at)

    attention: List[Dict[str, Any]] = []
    if owner and owner_age is not None and owner_age > owner_stale:
        attention.append(
            attention_item(
                "authority_stale",
                "warn",
                "notify",
                f"notify authority owner heartbeat is stale ({fmt_age(owner_age)})",
                ".omx/state/notify-fallback-authority-owner.json",
            )
        )
    if tick_age is not None and tick_age > tick_stale:
        attention.append(
            attention_item(
                "notify_unhealthy",
                "warn",
                "notify",
                f"notify fallback last tick is stale ({fmt_age(tick_age)})",
                ".omx/state/notify-fallback-state.json",
            )
        )
    if authority_backoff.get("active"):
        attention.append(
            attention_item(
                "notify_unhealthy",
                "warn",
                "notify",
                f"authority backoff active: {authority_backoff.get('reason') or 'unknown'}",
                ".omx/state/notify-fallback-state.json",
            )
        )
    for name, section in (("dispatch", dispatch), ("leader_nudge", leader_nudge), ("fallback", fallback)):
        if section.get("last_error"):
            attention.append(
                attention_item(
                    "notify_unhealthy",
                    "warn",
                    "notify",
                    f"{name} error: {compact_text(section.get('last_error'), 120)}",
                    ".omx/state/notify-fallback-state.json",
                )
            )

    healthy = not any(item["severity"] in {"warn", "critical"} for item in attention)
    summary = {
        "healthy": healthy,
        "pid": state.get("pid"),
        "parent_pid": state.get("parent_pid"),
        "authority_only": state.get("authority_only"),
        "effective_poll_ms": state.get("effective_poll_ms"),
        "tracked_files": state.get("tracked_files"),
        "seen_turns": state.get("seen_turns"),
        "last_cycle_activity": state.get("last_cycle_activity"),
        "owner": owner.get("owner"),
        "owner_pid": owner.get("pid"),
        "owner_heartbeat_at": iso(owner_heartbeat),
        "owner_heartbeat_age_seconds": owner_age,
        "last_tick_at": iso(tick_at),
        "last_tick_age_seconds": tick_age,
        "dispatch_drain": {
            "enabled": dispatch.get("enabled"),
            "run_count": dispatch.get("run_count"),
            "last_result": dispatch.get("last_result"),
        },
        "leader_nudge": {
            "enabled": leader_nudge.get("enabled"),
            "precomputed_leader_stale": leader_nudge.get("precomputed_leader_stale"),
            "run_count": leader_nudge.get("run_count"),
        },
        "fallback_auto_nudge": {
            "enabled": fallback.get("enabled"),
            "last_reason": fallback.get("last_reason"),
            "last_turn_at": fallback.get("last_turn_at"),
            "last_nudged_at": fallback.get("last_nudged_at"),
        },
        "authority_backoff": authority_backoff,
        "adaptive_poll": {
            "enabled": adaptive.get("enabled"),
            "current_ms": adaptive.get("current_ms"),
            "idle_streak": adaptive.get("idle_streak"),
            "last_activity_reason": adaptive.get("last_activity_reason"),
        },
    }
    return summary, attention


def summarize_tmux(config: Any, state: Any, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    config = config if isinstance(config, dict) else {}
    state = state if isinstance(state, dict) else {}
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    target_value = target.get("value")
    attn_cfg = cfg.get("attention", {}) if isinstance(cfg.get("attention"), dict) else {}
    placeholder = attn_cfg.get("tmux_placeholder_target", "replace-with-tmux-pane-id")
    attention: List[Dict[str, Any]] = []
    if config.get("enabled") and target_value == placeholder:
        attention.append(
            attention_item(
                "tmux_invalid_config",
                "warn",
                "tmux",
                "tmux hook target is placeholder; injections skipped as invalid_config",
                ".omx/tmux-hook.json",
            )
        )
    last_reason = state.get("last_reason")
    if last_reason and last_reason not in {"ok", "injected", "none"} and not any(item["kind"] == "tmux_invalid_config" for item in attention):
        attention.append(
            attention_item(
                "tmux_invalid_config",
                "warn",
                "tmux",
                f"tmux hook last reason: {last_reason}",
                ".omx/state/tmux-hook-state.json",
            )
        )
    return (
        {
            "enabled": config.get("enabled"),
            "target_type": target.get("type"),
            "target_value": target_value,
            "placeholder_target": target_value == placeholder,
            "dry_run": config.get("dry_run"),
            "allowed_modes": config.get("allowed_modes") if isinstance(config.get("allowed_modes"), list) else [],
            "total_injections": state.get("total_injections"),
            "last_reason": state.get("last_reason"),
            "last_event_at": state.get("last_event_at"),
        },
        attention,
    )


def summarize_metrics(metrics: Any, now: datetime) -> Dict[str, Any]:
    metrics = metrics if isinstance(metrics, dict) else {}
    last = parse_time(metrics.get("last_activity"))
    return {
        "total_turns": metrics.get("total_turns"),
        "session_turns": metrics.get("session_turns"),
        "last_activity": iso(last),
        "last_activity_age_seconds": age_seconds(now, last),
        "session_input_tokens": metrics.get("session_input_tokens"),
        "session_output_tokens": metrics.get("session_output_tokens"),
        "session_total_tokens": metrics.get("session_total_tokens"),
        "five_hour_limit_pct": metrics.get("five_hour_limit_pct"),
        "weekly_limit_pct": metrics.get("weekly_limit_pct"),
    }


def summarize_team_nudge(data: Any) -> Dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    return {
        "teams_with_nudges": len(data.get("last_nudged_by_team", {})) if isinstance(data.get("last_nudged_by_team"), dict) else 0,
        "teams_with_idle_nudges": len(data.get("last_idle_nudged_by_team", {})) if isinstance(data.get("last_idle_nudged_by_team"), dict) else 0,
        "teams_with_progress": len(data.get("progress_by_team", {})) if isinstance(data.get("progress_by_team"), dict) else 0,
        "raw": data,
    }


def actor_summary(run: Dict[str, Any]) -> str:
    sub = run.get("subagents") if isinstance(run.get("subagents"), dict) else {}
    leaders = int(sub.get("leaders") or 0)
    subs = int(sub.get("subagents") or 0)
    parts: List[str] = []
    if run.get("current"):
        parts.append("current")
    if leaders:
        parts.append(f"L{leaders}")
    if subs:
        parts.append(f"S{subs}")
    if not parts:
        parts.append("session")
    return "+".join(parts)


def operator_run_entry(run: Dict[str, Any], status: str, reason: str) -> Dict[str, Any]:
    """Small, operator-facing view of a run: who, lane, project, and evidence."""
    return {
        "session_id": run.get("session_id"),
        "short_session_id": str(run.get("session_id") or "")[:12],
        "project": run.get("label") or "unknown",
        "label": run.get("label") or "unknown",
        "lane": f"{run.get('mode') or 'unknown'}:{run.get('phase') or 'unknown'}",
        "mode": run.get("mode"),
        "phase": run.get("phase"),
        "who": actor_summary(run),
        "status": status,
        "reason": reason,
        "age": run.get("age"),
        "freshness_seconds": run.get("freshness_seconds"),
        "last_activity_at": run.get("latest_activity_at"),
        "source_dir": run.get("source_dir"),
    }


def attention_is_blocking(item: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    # This is intentionally conservative: non-critical infrastructure issues are
    # surfaced as attention, not as certain user-blocking work.
    op_cfg = cfg.get("operator_summary", {}) if isinstance(cfg.get("operator_summary"), dict) else {}
    blocking_kinds = set(op_cfg.get("blocking_attention_kinds") or ["workflow_stalled"])
    return item.get("severity") == "critical" or item.get("kind") in blocking_kinds


def compact_attention_buckets(items: List[Dict[str, Any]], *, has_active_context: bool) -> List[str]:
    # The compact Touch Bar badge is for user-actionable buckets, not raw warning
    # rows. One stale notify owner plus one stale notify tick should not read as
    # two active things, and five historical failed sessions should not read as
    # five active tabs.
    infrastructure_only = {"authority_stale", "notify_unhealthy"}
    historical_only = {"terminal_non_success"}
    inactive_only = {"tmux_invalid_config"}
    buckets: List[str] = []
    for item in items:
        kind = item.get("kind")
        if kind in infrastructure_only or kind in historical_only:
            continue
        if not has_active_context and kind in inactive_only:
            continue
        label = str(item.get("label") or item.get("kind") or "attention")
        if label and label not in buckets:
            buckets.append(label)
    return buckets


def build_operator_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    active = snapshot.get("active_runs") or []
    stalled = snapshot.get("stalled_runs") or []
    completed = snapshot.get("completed_runs") or []
    attention = snapshot.get("attention") or []
    actionable_attention = [item for item in attention if item.get("severity") in {"warn", "critical"}]
    informational_attention = [item for item in attention if item.get("severity") == "info"]
    cfg = snapshot.get("config", DEFAULT_CONFIG)
    op_cfg = cfg.get("operator_summary", {}) if isinstance(cfg.get("operator_summary"), dict) else {}
    inferred_kinds = set(op_cfg.get("inferred_attention_kinds") or ["workflow_stalled", "tmux_invalid_config", "notify_unhealthy", "authority_stale"])
    stalled_ids = {run.get("session_id") for run in stalled}

    working = [
        operator_run_entry(run, "working", "active recent run")
        for run in active
        if run.get("session_id") not in stalled_ids
    ]
    idle_or_stale = [
        operator_run_entry(run, "idle_stale", "active run has stale activity")
        for run in stalled
    ]
    finished = [
        operator_run_entry(run, "finished", "completion marker present")
        for run in completed[:5]
    ]

    blocking_or_attention: List[Dict[str, Any]] = []
    for item in actionable_attention:
        label = item.get("label") or item.get("kind") or "attention"
        certainty = "inferred" if item.get("kind") in inferred_kinds else "observed"
        blocking_or_attention.append(
            {
                "label": label,
                "kind": item.get("kind"),
                "severity": item.get("severity"),
                "blocking": attention_is_blocking(item, cfg),
                "certainty": certainty,
                "detail": item.get("detail"),
                "source_path": item.get("source_path"),
            }
        )

    waiting_for_input: List[Dict[str, Any]] = []
    if idle_or_stale:
        for run in idle_or_stale:
            waiting_for_input.append(
                {
                    "project": run["project"],
                    "lane": run["lane"],
                    "status": "stalled_attention",
                    "certainty": "inferred",
                    "reason": "no explicit waiting-for-input flag found; active run is stale",
                    "age": run.get("age"),
                    "session_id": run.get("session_id"),
                }
            )
    elif not working and finished:
        waiting_for_input.append(
            {
                "project": finished[0]["project"],
                "lane": finished[0]["lane"],
                "status": "finished_waiting_next_instruction",
                "certainty": "inferred",
                "reason": "latest run is finished and no active working run was found",
                "age": finished[0].get("age"),
                "session_id": finished[0].get("session_id"),
            }
        )

    active_project = working[0]["project"] if working else None
    active_lane = working[0]["lane"] if working else None
    wait_labels = [entry.get("project") for entry in waiting_for_input if entry.get("project")]
    wait_labels.extend(
        str(item.get("label")) for item in blocking_or_attention
        if item.get("label") and item.get("label") not in wait_labels and item.get("kind") != "state_contradiction"
    )
    has_active_context = bool(working or idle_or_stale)
    compact_attention = compact_attention_buckets(blocking_or_attention, has_active_context=has_active_context)
    compact_wait_labels = wait_labels if has_active_context else []

    return {
        "counts": {
            "working": len(working),
            "idle_stale": len(idle_or_stale),
            "attention": len(blocking_or_attention),
            "compact_attention": len(compact_attention),
            "blocking": sum(1 for item in blocking_or_attention if item.get("blocking")),
            "info_attention": len(informational_attention),
            "finished": len(finished),
        },
        "now": {
            "project": active_project,
            "lane": active_lane,
            "who": working[0]["who"] if working else None,
        },
        "working": working,
        "idle_or_stale": idle_or_stale,
        "blocking_or_attention": blocking_or_attention,
        "informational_attention": informational_attention,
        "finished": finished,
        "waiting_for_input": waiting_for_input,
        "compact_parts": {
            "now_labels": list(dict.fromkeys(entry["project"] for entry in working if entry.get("project"))),
            "wait_labels": list(dict.fromkeys([*compact_wait_labels, *compact_attention])),
            "attention_labels": compact_attention,
        },
        "interpretation_note": "waiting_for_input is inferred from stale/finished/attention signals unless an explicit source says otherwise",
    }


def attention_item(kind: str, severity: str, label: str, detail: str, source_path: str) -> Dict[str, str]:
    return {
        "kind": kind,
        "severity": severity,
        "label": label,
        "detail": compact_text(detail, 220),
        "source_path": source_path,
    }


def collect_snapshot(root: Path, *, compact: bool = False) -> Dict[str, Any]:
    root = root.expanduser().resolve()
    now = utc_now()
    diag = diag_init()
    cfg_path = root / "config" / "status-protocol.json"
    file_cfg = read_json(cfg_path, root, diag)
    cfg = deep_merge(DEFAULT_CONFIG, file_cfg)
    aliases = load_project_aliases(root, cfg, diag)

    omx = root / ".omx"
    state = omx / "state"
    sessions_dir = state / "sessions"

    session_json = read_json(state / "session.json", root, diag, required=True)
    metrics_json = read_json(omx / "metrics.json", root, diag)
    global_skill = read_json(state / "skill-active-state.json", root, diag)
    subagent_json = read_json(state / "subagent-tracking.json", root, diag)
    notify_state_json = read_json(state / "notify-fallback-state.json", root, diag)
    notify_owner_json = read_json(state / "notify-fallback-authority-owner.json", root, diag)
    tmux_config_json = read_json(omx / "tmux-hook.json", root, diag)
    tmux_state_json = read_json(state / "tmux-hook-state.json", root, diag)
    team_nudge_json = read_json(state / "team-leader-nudge.json", root, diag)
    native_stop_json = read_json(state / "native-stop-state.json", root, diag)
    hud_config_json = read_json(omx / "hud-config.json", root, diag)
    setup_scope_json = read_json(omx / "setup-scope.json", root, diag)

    logs = collect_logs(omx, root, cfg, diag, compact=compact)

    # Build historical session end map from the bounded tail of session-history.jsonl.
    compact_log_tail_bytes = int(cfg.get("compact_log_tail_bytes", 65536) or 65536) if compact else None
    history_records = read_jsonl_tail(
        omx / "logs" / "session-history.jsonl",
        root,
        int(cfg.get("log_tail_lines", 500)),
        diag,
        max_bytes=compact_log_tail_bytes,
    )
    history_by_session: Dict[str, Dict[str, Any]] = {}
    for record in history_records:
        sid = record.get("session_id")
        if isinstance(sid, str):
            previous = history_by_session.get(sid)
            if previous is None or (event_time(record) or parse_time(record.get("ended_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= (
                event_time(previous) or parse_time(previous.get("ended_at")) or datetime.min.replace(tzinfo=timezone.utc)
            ):
                history_by_session[sid] = record
            preserved = record.get("preserved_active_session_id")
            if isinstance(preserved, str) and preserved not in history_by_session:
                history_by_session[preserved] = {
                    "session_id": preserved,
                    "preserved_by": sid,
                    "cwd": record.get("cwd"),
                    "ended_at": record.get("ended_at"),
                }

    sub_sessions = subagent_json.get("sessions") if isinstance(subagent_json, dict) and isinstance(subagent_json.get("sessions"), dict) else {}
    native_stop_sessions = native_stop_json.get("sessions") if isinstance(native_stop_json, dict) and isinstance(native_stop_json.get("sessions"), dict) else {}
    log_activity_by_session = logs.get("latest_activity_by_session") if isinstance(logs.get("latest_activity_by_session"), dict) else {}
    current_session_id = session_json.get("session_id") if isinstance(session_json, dict) else None
    session_ids = set(history_by_session.keys()) | set(sub_sessions.keys()) | set(native_stop_sessions.keys())
    session_dirs: Dict[str, Path] = {}
    if path_exists(sessions_dir, root, diag, required=True, kind="dir"):
        try:
            children = sorted(sessions_dir.iterdir())
        except OSError as exc:
            children = []
            diag["json_errors"].append({"path": rel(sessions_dir, root), "error": f"list failed: {exc}"})
            diag_source(diag, sessions_dir, root, "error", detail=f"list failed: {exc}", kind="dir")
        for child in children:
            try:
                is_dir = child.is_dir()
            except OSError as exc:
                diag["json_errors"].append({"path": rel(child, root), "error": f"stat failed: {exc}"})
                diag_source(diag, child, root, "error", detail=f"stat failed: {exc}", kind="dir")
                continue
            if is_dir:
                session_dirs[child.name] = child
                session_ids.add(child.name)
    if isinstance(current_session_id, str):
        session_ids.add(current_session_id)

    runs: List[Dict[str, Any]] = []
    attentions: List[Dict[str, Any]] = []
    for sid in sorted(session_ids):
        run = classify_session(
            sid,
            session_dirs.get(sid),
            root,
            cfg,
            now,
            diag,
            session_json,
            sub_sessions.get(sid) if isinstance(sub_sessions, dict) else None,
            history_by_session.get(sid),
            native_stop_sessions.get(sid) if isinstance(native_stop_sessions, dict) else None,
            aliases,
            log_activity_by_session.get(sid),
        )
        # Suppress pure historical entries without local state unless they ended; keep useful ended records.
        if run["source_dir"] or run["completed"] or run["current"] or run["subagents"]["threads"] or run["terminal"].get("outcome"):
            runs.append(run)
        if run["stalled"]:
            attentions.append(
                attention_item(
                    "workflow_stalled",
                    run.get("stale_severity") or "warn",
                    run["label"],
                    f"{run['mode']} {run['phase']} stale for {run['age']}",
                    run.get("source_dir") or ".omx/state/sessions",
                )
            )
        if run["completed"] and (run["skill"].get("active") or run.get("active")):
            attentions.append(
                attention_item(
                    "state_contradiction",
                    "info",
                    run["label"],
                    "completed lifecycle marker overrides stale active flag",
                    run.get("source_dir") or ".omx/state/sessions",
                )
            )
        if run["terminal"].get("outcome") in {"failed", "cancelled"}:
            attentions.append(
                attention_item(
                    "terminal_non_success",
                    "warn",
                    run["label"],
                    f"{run['mode']} {run['phase']} ended with {run['terminal'].get('outcome')}; not shown as done",
                    run.get("source_dir") or ".omx/state/native-stop-state.json",
                )
            )
        elif run["terminal"].get("outcome") == "heuristic":
            attentions.append(
                attention_item(
                    "terminal_heuristic",
                    "info",
                    run["label"],
                    f"{run['mode']} {run['phase']} has heuristic native-stop completion text; not shown as done",
                    run.get("source_dir") or ".omx/state/native-stop-state.json",
                )
            )

    notify_summary, notify_attention = summarize_notify(notify_state_json, notify_owner_json, cfg, now)
    tmux_summary, tmux_attention = summarize_tmux(tmux_config_json, tmux_state_json, cfg)
    pane_success_map: Dict[str, bool] = {}
    for run in runs:
        pane_id = run.get("tmux_pane_id")
        if pane_id and run.get("completed"):
            pane_success_map[str(pane_id)] = True
    vibe_island_summary = collect_vibe_island(cfg, now, diag)
    muxy_summary = collect_muxy_runtime(root, cfg, now, diag, pane_success_map)
    attentions.extend(tmux_attention)
    attentions.extend(notify_attention)
    vibe_permissions = vibe_island_summary.get("permissions") if isinstance(vibe_island_summary.get("permissions"), list) else []
    for permission in vibe_permissions[:3]:
        attentions.append(
            attention_item(
                "vibe_island_permission",
                "critical",
                str(permission.get("project") or "agent"),
                f"{permission.get('agent') or 'agent'} permission: {permission.get('tool') or 'approval needed'}",
                str(vibe_island_summary.get("session_file") or "vibe-island"),
            )
        )
    muxy_waiting = muxy_summary.get("waiting") if isinstance(muxy_summary.get("waiting"), list) else []
    for pane in muxy_waiting[:5]:
        attentions.append(
            attention_item(
                "muxy_waiting_for_permission",
                "critical",
                str(pane.get("project") or "muxy"),
                f"muxy pane {pane.get('pane_id') or '?'} is waiting: {pane.get('title') or pane.get('command') or 'action required'}",
                "tmux list-panes",
            )
        )
    for error in diag["json_errors"]:
        attentions.append(
            attention_item(
                "log_error" if str(error.get("path", "")).endswith(".jsonl") else "state_parse_error",
                "warn",
                "json",
                f"{error.get('path')}: {error.get('error')}",
                str(error.get("path") or "unknown"),
            )
        )

    active_runs = sorted([run for run in runs if run["active"]], key=lambda r: r.get("updated_at") or "", reverse=True)
    completed_runs = sorted([run for run in runs if run["completed"]], key=lambda r: r.get("completed_at") or r.get("updated_at") or "", reverse=True)
    stalled_runs = [run for run in active_runs if run["stalled"]]
    historical_runs = [run for run in runs if not run["active"] and not run["completed"]]
    terminal_runs = [run for run in historical_runs if run.get("terminal", {}).get("outcome")]
    last_finished = completed_runs[0] if completed_runs else None

    snapshot: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": iso(now),
        "root": str(root),
        "omx_exists": omx.exists(),
        "config": cfg,
        "project_aliases": aliases,
        "current_session": {
            "session_id": current_session_id,
            "native_session_id": session_json.get("native_session_id") if isinstance(session_json, dict) else None,
            "started_at": session_json.get("started_at") if isinstance(session_json, dict) else None,
            "cwd": session_json.get("cwd") if isinstance(session_json, dict) else None,
            "pid": session_json.get("pid") if isinstance(session_json, dict) else None,
            "platform": session_json.get("platform") if isinstance(session_json, dict) else None,
            "state_dir_exists": path_exists(sessions_dir / str(current_session_id), root, diag, kind="dir") if current_session_id else False,
        },
        "runs": runs,
        "active_runs": active_runs,
        "completed_runs": completed_runs[:10],
        "stalled_runs": stalled_runs,
        "historical_runs": historical_runs[:10],
        "terminal_runs": terminal_runs[:10],
        "last_finished": last_finished,
        "attention": sorted(attentions, key=lambda item: {"critical": 0, "warn": 1, "info": 2}.get(item.get("severity"), 3)),
        "subagents": summarize_all_subagents(subagent_json, now, int(cfg.get("stale_seconds", 600) or 600)),
        "notify_health": notify_summary,
        "tmux_health": tmux_summary,
        "muxy_notification_center": muxy_summary,
        "vibe_island": vibe_island_summary,
        "metrics": summarize_metrics(metrics_json, now),
        "team_nudge": summarize_team_nudge(team_nudge_json),
        "global_skill": {
            "active": bool(global_skill.get("active")) if isinstance(global_skill, dict) else False,
            "skill": global_skill.get("skill") if isinstance(global_skill, dict) else None,
            "phase": global_skill.get("phase") if isinstance(global_skill, dict) else None,
            "updated_at": global_skill.get("updated_at") if isinstance(global_skill, dict) else None,
        },
        "hud_config": hud_config_json if isinstance(hud_config_json, dict) else {},
        "setup_scope": setup_scope_json if isinstance(setup_scope_json, dict) else {},
        "native_stop": {
            "available": isinstance(native_stop_json, dict),
            "keys": sorted(native_stop_json.keys()) if isinstance(native_stop_json, dict) else [],
        },
        "logs": logs,
        "diagnostics": diag,
    }
    snapshot["operator_summary"] = build_operator_summary(snapshot)
    snapshot["compact"] = format_compact(snapshot)
    return snapshot


def unique_labels(items: Iterable[Dict[str, Any]], limit: int = 2) -> str:
    labels: List[str] = []
    for item in items:
        label = str(item.get("label") or "item")
        if label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return ",".join(labels)


def attention_labels(attention: List[Dict[str, Any]], limit: int = 2, severities: Optional[set[str]] = None) -> str:
    labels: List[str] = []
    for item in attention:
        if severities and item.get("severity") not in severities:
            continue
        label = str(item.get("label") or item.get("kind") or "!")
        if label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return ",".join(labels)


def truncate_compact(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 5:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


def compact_label_list(labels: Iterable[Any], *, limit: int = 2) -> str:
    seen: List[str] = []
    for raw in labels:
        label = slug(str(raw), fallback="item")
        if label and label not in seen:
            seen.append(label)
    if not seen:
        return "-"
    visible = seen[:limit]
    suffix = f"+{len(seen) - limit}" if len(seen) > limit else ""
    return ",".join(visible) + suffix


def compose_compact(parts: List[str], max_chars: int) -> str:
    text = " ".join(part for part in parts if part)
    return truncate_compact(text, max_chars)


def spinner(cfg: Dict[str, Any]) -> str:
    frames = cfg.get("spinner_frames") if isinstance(cfg.get("spinner_frames"), list) else DEFAULT_CONFIG["spinner_frames"]
    frames = [str(frame) for frame in frames if str(frame)] or DEFAULT_CONFIG["spinner_frames"]
    cadence = float(cfg.get("spinner_cadence_seconds", 2) or 2)
    cadence = max(0.25, cadence)
    return frames[int(time.time() / cadence) % len(frames)]


def format_vibe_island_compact(snapshot: Dict[str, Any], max_chars: int) -> Optional[str]:
    vibe = snapshot.get("vibe_island") if isinstance(snapshot.get("vibe_island"), dict) else {}
    if not vibe or vibe.get("enabled") is False:
        return None
    if not vibe.get("available"):
        if vibe.get("empty_runtime"):
            return None
        return "VI !err"

    counts = vibe.get("counts") if isinstance(vibe.get("counts"), dict) else {}
    permissions = vibe.get("permissions") if isinstance(vibe.get("permissions"), list) else []
    active_sessions = vibe.get("active_sessions") if isinstance(vibe.get("active_sessions"), list) else []
    active_count = int(counts.get("active") or 0)
    permission_count = int(counts.get("permissions") or 0)

    if permission_count == 0 and active_count == 0:
        return None

    urgent = vibe.get("urgent") if isinstance(vibe.get("urgent"), dict) else None
    if permission_count > 0 and isinstance(urgent, dict):
        project = urgent.get("project") or "perm"
        agent = urgent.get("agent") or "Agent"
        candidates = [
            f"VI ! {agent} {project}",
            f"VI ! {project}",
            "VI ! perm",
        ]
        for candidate in candidates:
            if len(candidate) <= max_chars:
                return candidate
        return truncate_compact(candidates[-1], max_chars)

    if active_count == 1 and isinstance(urgent, dict):
        agent = urgent.get("agent") or "Agent"
        project = urgent.get("project") or "agent"
        tool = urgent.get("tool")
        age = urgent.get("age")
        candidates = [
            f"VI {agent} {project} · {tool}" if tool else None,
            f"VI {agent} {project} {age}" if age else None,
            f"VI {agent} {project}",
            f"VI {project}",
        ]
        for candidate in candidates:
            if candidate and len(candidate) <= max_chars:
                return candidate

    parts = vibe.get("compact_parts") if isinstance(vibe.get("compact_parts"), dict) else {}
    labels = parts.get("project_labels") if isinstance(parts.get("project_labels"), list) else []
    label_text = compact_label_list(labels, limit=2)
    candidates = [
        f"VI · {active_count} agents {label_text}" if label_text != "-" else None,
        f"VI · {active_count} agents",
        "VI · agents",
    ]
    for candidate in candidates:
        if candidate and len(candidate) <= max_chars:
            return candidate
    return truncate_compact(f"VI · {active_count}", max_chars)


def format_muxy_compact(snapshot: Dict[str, Any], max_chars: int) -> Optional[str]:
    muxy = snapshot.get("muxy_notification_center") if isinstance(snapshot.get("muxy_notification_center"), dict) else {}
    if not muxy or muxy.get("enabled") is False:
        return None
    if not muxy.get("available"):
        reason = str(muxy.get("reason") or "")
        if muxy.get("empty_runtime") or muxy_runtime_empty_reason(reason):
            return None
        return "MUXY !err"
    counts = muxy.get("counts") if isinstance(muxy.get("counts"), dict) else {}
    waiting = int(counts.get("waiting") or 0)
    done = int(counts.get("done") or 0)
    open_panes = int(counts.get("open_panes") or 0)

    parts = muxy.get("compact_parts") if isinstance(muxy.get("compact_parts"), dict) else {}
    top = "-"
    if waiting > 0:
        top = muxy.get("urgent_project") or compact_label_list(parts.get("waiting_labels") or parts.get("project_labels") or [], limit=1)
    elif done > 0:
        done_panes = muxy.get("done") if isinstance(muxy.get("done"), list) else []
        top = done_panes[0].get("project") if done_panes else compact_label_list(parts.get("project_labels") or [], limit=1)
    elif open_panes > 0:
        sessions = muxy.get("sessions") if isinstance(muxy.get("sessions"), list) else []
        panes = muxy.get("panes") if isinstance(muxy.get("panes"), list) else []
        attached_sessions = {str(session.get("name")) for session in sessions if isinstance(session, dict) and session.get("attached")}
        attached_open_panes = [
            pane
            for pane in panes
            if isinstance(pane, dict)
            and str(pane.get("session")) in attached_sessions
            and not pane.get("dead")
            and not pane.get("waiting")
            and not pane.get("done")
        ]
        if attached_open_panes:
            active_sessions = list(dict.fromkeys(str(pane.get("session")) for pane in attached_open_panes))
            labels = list(dict.fromkeys(str(pane.get("project") or "muxy") for pane in attached_open_panes))
            top = compact_label_list(labels, limit=1)
            return compose_compact(["OMX", f"· W{len(active_sessions)}", "I0", "A0", top], max_chars)
        # Open Muxy/tmux panes are background context, not user-actionable work.
        # Do not let stale detached sessions (for example a timed-out Codex login
        # pane) override the OMX summary on the compact Touch Bar label.
        return None

    candidate = f"MUXY C{waiting} F{done} O{open_panes} {top}"
    if len(candidate) <= max_chars:
        return candidate
    return compose_compact(["MUXY", f"C{waiting}", f"F{done}", f"O{open_panes}", top], max_chars)


def format_compact(snapshot: Dict[str, Any]) -> str:
    cfg = snapshot.get("config", DEFAULT_CONFIG)
    max_chars = int(cfg.get("compact_max_chars", 80) or 80)
    vibe_compact = format_vibe_island_compact(snapshot, max_chars)
    if vibe_compact:
        return vibe_compact
    muxy_compact = format_muxy_compact(snapshot, max_chars)
    if muxy_compact:
        return muxy_compact
    summary = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    parts = summary.get("compact_parts") if isinstance(summary.get("compact_parts"), dict) else {}
    now_labels = parts.get("now_labels") if isinstance(parts.get("now_labels"), list) else []
    wait_labels = parts.get("wait_labels") if isinstance(parts.get("wait_labels"), list) else []
    attention_labels = parts.get("attention_labels") if isinstance(parts.get("attention_labels"), list) else []
    working = int(counts.get("working") or 0)
    idle_stale = int(counts.get("idle_stale") or 0)
    blocking = int(counts.get("blocking") or 0)
    attention = int(counts.get("compact_attention") if counts.get("compact_attention") is not None else counts.get("attention") or 0)
    problem_count = blocking if blocking else attention
    problem_label = "B" if blocking else "A"
    status_text = f"· W{working} I{idle_stale} {problem_label}{problem_count}"

    primary_labels = now_labels or attention_labels or wait_labels
    primary_text = compact_label_list(primary_labels, limit=2)

    candidates = [
        ["OMX", status_text, primary_text],
        ["OMX", status_text, compact_label_list(primary_labels, limit=1)],
        ["OMX", status_text],
        ["OMX"],
    ]
    for candidate in candidates:
        text = " ".join(part for part in candidate if part)
        if len(text) <= max_chars:
            return text
    return compose_compact(candidates[-1], max_chars)


def format_detail(snapshot: Dict[str, Any], *, debug: bool = False) -> str:
    lines: List[str] = []
    lines.append("Agentmax TouchBar detail")
    lines.append(f"Root: {snapshot.get('root')}")
    lines.append(f"Generated: {snapshot.get('generated_at')}")
    lines.append("")
    lines.append("Compact:")
    lines.append(f"  {snapshot.get('compact')}")
    lines.append("")

    current = snapshot.get("current_session") or {}
    lines.append("Current session:")
    lines.append(
        f"  id={current.get('session_id') or '-'} pid={current.get('pid') or '-'} cwd={current.get('cwd') or '-'} state_dir={current.get('state_dir_exists')}"
    )
    lines.append("")

    operator = snapshot.get("operator_summary") if isinstance(snapshot.get("operator_summary"), dict) else {}
    counts = operator.get("counts") if isinstance(operator.get("counts"), dict) else {}
    now = operator.get("now") if isinstance(operator.get("now"), dict) else {}
    lines.append("Operator summary:")
    lines.append(
        f"  counts: working={counts.get('working', 0)} idle_stale={counts.get('idle_stale', 0)} "
        f"blocking={counts.get('blocking', 0)} attention={counts.get('attention', 0)} finished={counts.get('finished', 0)}"
    )
    lines.append(f"  now: project={now.get('project') or '-'} lane={now.get('lane') or '-'} who={now.get('who') or '-'}")
    waiting = operator.get("waiting_for_input") if isinstance(operator.get("waiting_for_input"), list) else []
    if waiting:
        lines.append("  waiting/attention:")
        for item in waiting[:5]:
            lines.append(
                f"    - {item.get('project')} {item.get('lane')} {item.get('status')} "
                f"certainty={item.get('certainty')} age={item.get('age')}"
            )
    else:
        lines.append("  waiting/attention: none inferred")
    lines.append("")

    def add_runs(title: str, runs: List[Dict[str, Any]], limit: int = 8) -> None:
        lines.append(f"{title}:")
        if not runs:
            lines.append("  - none")
        for run in runs[:limit]:
            current_mark = " current" if run.get("current") else ""
            stale_mark = " stalled" if run.get("stalled") else ""
            lines.append(
                f"  - {run.get('label')} {run.get('mode')} {run.get('phase')}{current_mark}{stale_mark} "
                f"age={run.get('age')} session={str(run.get('session_id'))[:12]}"
            )
            if run.get("verification"):
                lines.append(f"    verification={run.get('verification')}")
            if run.get("hud", {}).get("last_agent_output"):
                lines.append(f"    last_output={run['hud']['last_agent_output']}")

    add_runs("Running", snapshot.get("active_runs") or [])
    lines.append("")
    add_runs("Stalled", snapshot.get("stalled_runs") or [])
    lines.append("")
    add_runs("Completed", snapshot.get("completed_runs") or [], limit=5)
    lines.append("")

    lines.append("Attention:")
    attention = snapshot.get("attention") or []
    if not attention:
        lines.append("  - none")
    for item in attention:
        lines.append(f"  - {item.get('severity')} {item.get('label')}: {item.get('detail')} [{item.get('source_path')}]")
    lines.append("")

    sub = snapshot.get("subagents") or {}
    lines.append("Subagents:")
    lines.append(
        f"  sessions={sub.get('sessions')} leaders={sub.get('leaders')} subagents={sub.get('subagents')} "
        f"threads={sub.get('threads')} active_threads={sub.get('active_threads')} turns={sub.get('turns')} "
        f"last_seen_age={fmt_age(sub.get('last_seen_age_seconds'))}"
    )
    lines.append("")

    notify = snapshot.get("notify_health") or {}
    lines.append("Notify:")
    lines.append(
        f"  healthy={notify.get('healthy')} owner={notify.get('owner')} owner_pid={notify.get('owner_pid')} "
        f"heartbeat_age={fmt_age(notify.get('owner_heartbeat_age_seconds'))} tick_age={fmt_age(notify.get('last_tick_age_seconds'))} "
        f"fallback_reason={notify.get('fallback_auto_nudge', {}).get('last_reason')}"
    )
    lines.append("")

    tmux = snapshot.get("tmux_health") or {}
    lines.append("Tmux:")
    lines.append(
        f"  enabled={tmux.get('enabled')} target={tmux.get('target_type')}:{tmux.get('target_value')} "
        f"placeholder={tmux.get('placeholder_target')} injections={tmux.get('total_injections')} last_reason={tmux.get('last_reason')}"
    )
    lines.append("")

    vibe = snapshot.get("vibe_island") if isinstance(snapshot.get("vibe_island"), dict) else {}
    vibe_counts = vibe.get("counts") if isinstance(vibe.get("counts"), dict) else {}
    lines.append("Vibe Island:")
    lines.append(
        f"  available={vibe.get('available')} active={vibe_counts.get('active', 0)} "
        f"permissions={vibe_counts.get('permissions', 0)} tracked={vibe_counts.get('tracked', 0)}"
    )
    active_sessions = vibe.get("active_sessions") if isinstance(vibe.get("active_sessions"), list) else []
    if active_sessions:
        lines.append("  active sessions:")
        for session in active_sessions[:5]:
            lines.append(
                f"    - {session.get('agent')} {session.get('project')} "
                f"tool={session.get('tool') or '-'} age={session.get('age') or '?'}"
            )
    else:
        lines.append("  active sessions: none")
    permission_sessions = vibe.get("permissions") if isinstance(vibe.get("permissions"), list) else []
    if permission_sessions:
        lines.append("  permissions:")
        for permission in permission_sessions[:3]:
            lines.append(
                f"    - {permission.get('agent')} {permission.get('project')} "
                f"tool={permission.get('tool') or '-'} age={permission.get('age') or '?'}"
            )
    else:
        lines.append("  permissions: none")
    lines.append("")

    muxy = snapshot.get("muxy_notification_center") if isinstance(snapshot.get("muxy_notification_center"), dict) else {}
    muxy_counts = muxy.get("counts") if isinstance(muxy.get("counts"), dict) else {}
    lines.append("Muxy notification center:")
    lines.append(
        f"  available={muxy.get('available')} waiting={muxy_counts.get('waiting', 0)} "
        f"done={muxy_counts.get('done', 0)} open_panes={muxy_counts.get('open_panes', 0)} "
        f"open_sessions={muxy_counts.get('open_sessions', 0)}"
    )
    waiting_panes = muxy.get("waiting") if isinstance(muxy.get("waiting"), list) else []
    if waiting_panes:
        lines.append("  waiting for permission/action:")
        for pane in waiting_panes[:5]:
            lines.append(
                f"    - {pane.get('project')} pane={pane.get('pane_id')} title={pane.get('title') or '-'} session={pane.get('session')}"
            )
    else:
        lines.append("  waiting for permission/action: none")
    lines.append("")
    if muxy.get("urgent"):
        lines.append(
            f"  urgent target: {muxy.get('urgent_project')} pane={muxy.get('urgent_pane_id')} "
            f"session={muxy.get('urgent_session')} reason={muxy.get('urgent_reason')}"
        )
    else:
        lines.append("  urgent target: none")
    lines.append("")

    metrics = snapshot.get("metrics") or {}
    lines.append("Metrics:")
    lines.append(
        f"  turns={metrics.get('session_turns')}/{metrics.get('total_turns')} "
        f"tokens={metrics.get('session_total_tokens')} last_activity_age={fmt_age(metrics.get('last_activity_age_seconds'))} "
        f"limits={metrics.get('five_hour_limit_pct')}%/{metrics.get('weekly_limit_pct')}%"
    )
    lines.append("")

    log_summary = snapshot.get("logs") or {}
    lines.append("Logs:")
    lines.append(f"  latest_event_at={log_summary.get('latest_event_at')}")
    for path, info in (log_summary.get("files") or {}).items():
        lines.append(f"  - {path}: records={info.get('records')} latest={info.get('latest_event_at')}")

    if debug:
        diag = snapshot.get("diagnostics") or {}
        lines.append("")
        lines.append("Debug diagnostics:")
        lines.append("  source_files:")
        for source in diag.get("sources", []):
            detail = f" ({source.get('detail')})" if source.get("detail") else ""
            lines.append(f"    - {source.get('status')} {source.get('kind')} {source.get('path')}{detail}")
        lines.append("  files_read:")
        for path in diag.get("files_read", []):
            lines.append(f"    - {path}")
        lines.append("  files_missing:")
        for path in diag.get("files_missing", []):
            lines.append(f"    - {path}")
        lines.append("  json_errors:")
        for error in diag.get("json_errors", []):
            lines.append(f"    - {error}")
        lines.append("  jsonl_files:")
        for path, info in (diag.get("jsonl_files") or {}).items():
            lines.append(f"    - {path}: {info}")
        lines.append("  decisions:")
        for decision in diag.get("decisions", []):
            lines.append(f"    - {decision}")

    return "\n".join(lines)


def smoke(root: Path) -> Tuple[int, str]:
    if not root.exists():
        return 2, f"FAIL root missing: {root}"
    if not (root / ".omx").exists():
        return 2, f"FAIL .omx missing under: {root}"
    snapshot = collect_snapshot(root, compact=True)
    compact = str(snapshot.get("compact") or "")
    max_chars = int(snapshot.get("config", {}).get("compact_max_chars", 80) or 80)
    if not (compact.startswith("OMX") or compact.startswith("MUXY") or compact.startswith("VI")):
        return 2, f"FAIL compact does not start with VI, OMX, or MUXY: {compact!r}"
    if len(compact) > max_chars:
        return 2, f"FAIL compact too long: {len(compact)} > {max_chars}"
    try:
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    except TypeError as exc:
        return 2, f"FAIL snapshot is not JSON serializable: {exc}"
    return 0, f"OK compact={compact!r} active={len(snapshot.get('active_runs') or [])} attention={len(snapshot.get('attention') or [])}"


def maybe_log_snapshot(snapshot: Dict[str, Any]) -> None:
    cfg = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    log_cfg = cfg.get("logging") if isinstance(cfg.get("logging"), dict) else {}
    if not log_cfg.get("enabled"):
        return
    root = Path(str(snapshot.get("root") or ".")).expanduser()
    path_value = str(log_cfg.get("path") or ".omx/logs/agentmax-status.jsonl")
    log_path = Path(path_value).expanduser()
    if not log_path.is_absolute():
        log_path = root / log_path
    max_bytes = int(log_cfg.get("max_bytes") or 262144)
    record = {
        "generated_at": snapshot.get("generated_at"),
        "compact": snapshot.get("compact"),
        "counts": (snapshot.get("operator_summary") or {}).get("counts") if isinstance(snapshot.get("operator_summary"), dict) else {},
        "last_finished": {
            "label": (snapshot.get("last_finished") or {}).get("label") if isinstance(snapshot.get("last_finished"), dict) else None,
            "age": (snapshot.get("last_finished") or {}).get("age") if isinstance(snapshot.get("last_finished"), dict) else None,
        },
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if max_bytes > 0 and log_path.exists() and log_path.stat().st_size > max_bytes:
            log_path.replace(log_path.with_suffix(log_path.suffix + ".1"))
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        # Logging is optional and must never affect BTT status output.
        return


def fallback_snapshot(root: Path, exc: Exception) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": iso(utc_now()),
        "root": str(root.expanduser()),
        "compact": "VI !err",
        "error": {"type": type(exc).__name__, "message": compact_text(exc, 240)},
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agentmax OMX TouchBar status collector")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--compact", action="store_true", help="print one BTT-safe status line (default)")
    mode.add_argument("--detail", action="store_true", help="print human-readable expanded status")
    mode.add_argument("--json", action="store_true", help="print normalized snapshot JSON")
    mode.add_argument("--debug", action="store_true", help="print detail plus source diagnostics")
    mode.add_argument("--smoke", action="store_true", help="run basic collector/self-check validation")
    parser.add_argument("--root", default=str(Path.cwd()), help="repo root containing .omx (default: cwd)")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser()

    if args.smoke:
        try:
            code, message = smoke(root)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL smoke error: {exc}")
            return 2
        print(message)
        return code

    if args.compact or not (args.detail or args.json or args.debug):
        try:
            snapshot = collect_snapshot(root, compact=True)
            maybe_log_snapshot(snapshot)
            print(str(snapshot.get("compact") or "VI !err"))
            return 0
        except Exception:  # noqa: BLE001 - compact is BTT-facing: no stack traces.
            print("VI !err")
            return 0

    try:
        snapshot = collect_snapshot(root)
        maybe_log_snapshot(snapshot)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
        elif args.debug:
            print(format_detail(snapshot, debug=True))
        else:
            print(format_detail(snapshot, debug=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        fallback = fallback_snapshot(root, exc)
        if args.json:
            print(json.dumps(fallback, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print("MUXY !err")
            print(f"ERROR agentmax_status: {type(exc).__name__}: {compact_text(exc, 240)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
