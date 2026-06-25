#!/usr/bin/env python3
"""Install the Muxy Touch Bar control panel widgets in BetterTouchTool.

Registers 5 Touch Bar widgets (summary + slots 0-3) in BTT global state via
AppleScript add_new_trigger, then runs an offline SQLite repair pass to ensure
each widget is entity 15 (TOUCHBAR_ENTITY) with a proper ZBEZIERPATH blob and
linked to the global app.  Non-destructive to existing Vibe Island rows.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("/Users/yoseph/TouchBar")
BTT_SUPPORT = Path.home() / "Library/Application Support/BetterTouchTool"
VI_WIDGET = "4EA2B0F6-983C-4DD9-8F30-5F7161DCB601"
VI_TAP = "17D6AE4C-4829-4115-8709-AEDAC8F53552"
VI_TAP_SCRIPT = ROOT / "scripts/btt_vibe_island_tap.sh"
TOUCHBAR_ENTITY = 15
GLOBAL_APP_PK = 5

WIDGET_CONFIG = ROOT / "config" / "btt-muxy-widgets.json"
ICON_CONFIG = ROOT / "config" / "project-icons.json"
STATE_JSON = Path.home() / ".local/share/touchbar-muxy/state.json"

# State colors (BTT RGBA string format, alpha 255). Mirrored from
# scripts/btt_muxy_daemon.py so the installer can render the same palette
# when the daemon's state.json is unavailable at install time.
COLOR_WORKING = "52.000000, 199.000000, 89.000000, 255.000000"   # #34C759 green
COLOR_WAITING = "255.000000, 159.000000, 10.000000, 255.000000"  # #FF9F0A orange
COLOR_ERROR = "255.000000, 59.000000, 48.000000, 255.000000"     # #FF3B30 red
COLOR_IDLE = "142.000000, 142.000000, 147.000000, 255.000000"    # #8E8E93 gray
COLOR_DEFAULT = COLOR_IDLE

FOCUS_SCRIPT = ROOT / "scripts/btt_muxy_slot_focus.sh"

# launchd plist (autostart) for the Muxy daemon.
LAUNCHD_PLIST_SRC = ROOT / "scripts" / "com.touchbar.muxy-daemon.plist"
LAUNCHD_PLIST_DST = Path.home() / "Library/LaunchAgents/com.touchbar.muxy-daemon.plist"
LAUNCHD_LABEL = "com.touchbar.muxy-daemon"


def load_widget_config(path: Path = WIDGET_CONFIG) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_icons(path: Path = ICON_CONFIG) -> Dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            cleaned = {
                str(k): str(v)
                for k, v in data.items()
                if isinstance(k, str) and isinstance(v, str) and not k.startswith("_")
            }
            if "default" not in cleaned:
                cleaned["default"] = "terminal"
            return cleaned
    except (OSError, json.JSONDecodeError):
        pass
    return {"default": "terminal"}


def hex_to_btt_rgba(hex_color: str) -> str:
    """Convert '#RRGGBB' to BTT RGBA string 'R.000000, G.000000, B.000000, 255.000000'.

    Falls back to COLOR_DEFAULT for any malformed input.
    """
    if not isinstance(hex_color, str):
        return COLOR_DEFAULT
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return COLOR_DEFAULT
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return COLOR_DEFAULT
    return f"{r}.000000, {g}.000000, {b}.000000, 255.000000"


_STATE_TO_BTT_COLOR = {
    "working": COLOR_WORKING,
    "waiting": COLOR_WAITING,
    "error": COLOR_ERROR,
    "idle": COLOR_IDLE,
}


def load_state_snapshot(path: Path = STATE_JSON) -> Dict[str, Any]:
    """Read daemon state.json if present and recent. Returns empty dict on failure."""
    if not path.exists():
        return {}
    try:
        age = time.time() - path.stat().st_mtime
        if age > 30.0:
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def slot_state(snapshot: Dict[str, Any], slot_index: int, icons: Dict[str, str]) -> Dict[str, str]:
    """Resolve state for slot N from the daemon snapshot.

    Returns dict with keys: color (BTT RGBA), icon (SF Symbol), label (str).
    Uses idle color + 'terminal' icon when slot N is missing.
    """
    fallback = {"color": COLOR_IDLE, "icon": icons.get("default", "terminal"), "label": "·"}
    if not snapshot or slot_index is None or slot_index < 0:
        return fallback
    slots = snapshot.get("slots") if isinstance(snapshot.get("slots"), list) else []
    if slot_index >= len(slots):
        return fallback
    entry = slots[slot_index]
    if not isinstance(entry, dict):
        return fallback
    state = str(entry.get("state", "idle")).lower()
    color = _STATE_TO_BTT_COLOR.get(state, COLOR_IDLE)
    icon = str(entry.get("icon") or icons.get("default", "terminal"))
    label_parts = [str(entry.get("project") or ""), str(entry.get("agent") or "")]
    label = " / ".join(p for p in label_parts if p and p != "-") or "·"
    return {"color": color, "icon": icon, "label": label}


def run_btt_applescript(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"osascript exit {proc.returncode}")
    return proc.stdout.strip()


def run_btt_applescript_argv(*argv: str, script: str) -> str:
    proc = subprocess.run(["osascript", "-", *argv], input=script, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"osascript exit {proc.returncode}")
    return proc.stdout.strip()


def find_db() -> Path:
    stores = [
        p
        for p in sorted(BTT_SUPPORT.glob("btt_data_store.version_*"))
        if p.is_file()
        and "-wal" not in p.name
        and "-shm" not in p.name
        and ".backup" not in p.name
        and ".before" not in p.name
        and ".touchbar" not in p.name
        and ".merge" not in p.name
        and ".agentmax" not in p.name
        and ".vibe-reset" not in p.name
    ]
    if not stores:
        raise SystemExit("BetterTouchTool datastore not found")
    return stores[-1]


def btt_running() -> bool:
    return subprocess.run(["pgrep", "-x", "BetterTouchTool"], capture_output=True).returncode == 0


def stop_btt() -> None:
    subprocess.run(["osascript", "-e", 'tell application "BetterTouchTool" to quit'], check=False)
    time.sleep(2)
    subprocess.run(["pkill", "-9", "-x", "BetterTouchTool"], check=False)
    subprocess.run(["pkill", "-9", "-f", "BTTRelaunch"], check=False)
    time.sleep(1)


def start_btt() -> None:
    subprocess.run(["open", "-a", "BetterTouchTool"], check=False)


def set_visibility_defaults() -> None:
    for args in (
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTTouchBarVisible", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTTBWasVisibleBeforeSleep", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTAlwaysShowBTTTouchBarOnStartup", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTForcedHidden", "-bool", "false"],
    ):
        subprocess.run(args, check=False)


def show_touch_bar() -> None:
    for action_type, action_name in (
        (282, "Show Touch Bar"),
        (190, "Toggle Global Touch Bar"),
    ):
        subprocess.run(
            [
                "osascript",
                "-e",
                "tell application \"BetterTouchTool\" to trigger_action "
                f"\"{{\\\"BTTPredefinedActionType\\\":{action_type},\\\"BTTPredefinedActionName\\\":\\\"{action_name}\\\"}}\"",
            ],
            check=False,
        )


def widget_payload(
    uuid: str,
    name: str,
    script_path: Path,
    width: int,
    interval: int,
    tap_script_path: Path,
    status: str,
    order: int = 0,
    color: str = COLOR_IDLE,
    icon: str = "terminal",
    slot_index: Optional[int] = None,
) -> dict[str, Any]:
    if slot_index is not None and slot_index >= 0:
        tap_action = f'do shell script "{tap_script_path} {slot_index}"'
    else:
        # Summary widget (slot_index is None or negative): keep the legacy
        # tap-script invocation so the tap script's existing behavior
        # (refresh / status) continues to work.
        tap_action = f'do shell script "{tap_script_path}"'
    return {
        "BTTUUID": uuid,
        "BTTTriggerClass": "BTTTriggerTypeTouchBar",
        "BTTTriggerType": 642,
        "BTTEnabled": 1,
        "BTTBelongsToApp": "Global",
        "BTTTriggerBelongsToPreset": "Default",
        "BTTWidgetName": name,
        "BTTTriggerTypeDescription": status,
        "BTTOrder": order,
        "BTTTouchBarAlwaysShowButton": 1,
        "BTTTouchBarShellScriptString": str(script_path),
        "BTTTouchBarScriptUpdateInterval": interval,
        "BTTTouchBarButtonWidth": width,
        "BTTTouchBarButtonUseFixedWidth": 1,
        "BTTTouchBarButtonMonoSpace": 1,
        "BTTTouchBarButtonFontSize": 12,
        "BTTTouchBarItemPadding": 3,
        "BTTTouchBarButtonColor": color,
        "BTTTouchBarButtonIcon": icon,
        "BTTTouchBarFontColor": "255.000000, 255.000000, 255.000000, 255.000000",
        "BTTTouchBarButtonName": status,
        "BTTTouchBarAppleScriptString": tap_action,
    }


def add_live_trigger(payload: dict[str, Any]) -> None:
    run_btt_applescript(
        "tell application \"BetterTouchTool\" to add_new_trigger "
        f"{json.dumps(json.dumps(payload))}"
    )


def delete_trigger(uuid: str) -> None:
    """Tell BTT to drop a trigger with the given UUID from its in-memory
    cache. Used before add_new_trigger to avoid BTT's "Keep / Merge / Replace
    / Create New" modal that appears when add_new_trigger is called with a
    UUID that already exists in BTT's runtime cache.

    Errors (e.g. UUID not present) are swallowed: BTT returns "missing value"
    and we don't want to fail the install because of a benign no-op.
    """
    try:
        run_btt_applescript(
            f'tell application "BetterTouchTool" to delete_trigger "{uuid}"'
        )
    except Exception:
        pass


def delete_existing_triggers(uuids: set[str]) -> int:
    """Delete every UUID in `uuids` from BTT's in-memory cache. Returns the
    count of UUIDs we sent delete_trigger for (best-effort; BTT may silently
    no-op on already-absent triggers)."""
    if not uuids:
        return 0
    for uuid in uuids:
        delete_trigger(uuid)
    return len(uuids)


def refresh_widget(uuid: str) -> None:
    """Tell BTT to refresh a widget by re-running its shell script.

    We intentionally do NOT call `update_touch_bar_widget text` here.
    Setting a static custom text via that API overrides the shell script
    output, so the widget appears "stuck" on the install-time value
    even though the daemon state changes every ~4 seconds and the
    script produces fresh output every 2 seconds.
    """
    run_btt_applescript_argv(
        uuid,
        script="""on run argv
  tell application "BetterTouchTool"
    refresh_widget (item 1 of argv)
  end tell
end run""",
    )


def global_touchbar_uuids() -> list[str]:
    raw = run_btt_applescript(
        "tell application \"BetterTouchTool\" to get_triggers "
        "\"{\\\"trigger_type\\\":\\\"BTTTriggerTypeTouchBar\\\",\\\"trigger_app_bundle_identifier\\\":\\\"BT.G\\\"}\""
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item.get("BTTUUID")) for item in data if isinstance(item, dict) and item.get("BTTUUID")]


# Legacy Vibe Island widget and tap UUIDs that are no longer part of the
# Muxy control panel. These must be deleted on every (re)install so the
# Touch Bar shows only the 5 Muxy widgets.
LEGACY_UUIDS: set[str] = {
    "4EA2B0F6-983C-4DD9-8F30-5F7161DCB601",  # old VI Touch Bar widget
    "17D6AE4C-4829-4115-8709-AEDAC8F53552",  # old VI tap trigger
}


def purge_rows(db: Path, uuids: set[str]) -> int:
    """Delete any number of base-entity rows by UUID. Returns the count removed.

    Safe to call with an empty set (returns 0). Strips link-table rows first.
    """
    if not uuids:
        return 0
    placeholders = ",".join("?" * len(uuids))
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER IN ("
            + placeholders
            + ")",
            tuple(uuids),
        ).fetchall()
        for (pk,) in rows:
            conn.execute("DELETE FROM Z_2APPS_GESTURES WHERE Z_9APPS_GESTURES=?", (pk,))
        conn.execute(
            "DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER IN ("
            + placeholders
            + ")",
            tuple(uuids),
        )
        conn.commit()
    return len(rows)


def purge_muxy_rows(db: Path, uuids: set[str]) -> None:
    purge_rows(db, uuids)


def dedupe_uuid(conn: sqlite3.Connection, uuid: str) -> bool:
    rows = conn.execute(
        "SELECT Z_PK, Z_ENT FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=? ORDER BY Z_ENT DESC, Z_PK",
        (uuid,),
    ).fetchall()
    if len(rows) <= 1:
        return False
    keep_pk = next((pk for pk, ent in rows if ent == TOUCHBAR_ENTITY), rows[0][0])
    changed = False
    for pk, _ent in rows:
        if pk == keep_pk:
            continue
        conn.execute("DELETE FROM Z_2APPS_GESTURES WHERE Z_9APPS_GESTURES=?", (pk,))
        conn.execute("DELETE FROM ZBTTBASEENTITY WHERE Z_PK=?", (pk,))
        changed = True
    return changed


def repair_widget(
    conn: sqlite3.Connection,
    uuid: str,
    width: int,
    status: str,
    script_path: Path,
    tap_script_path: Path,
    color: str = COLOR_IDLE,
    icon: str = "terminal",
    slot_index: Optional[int] = None,
) -> bool:
    blob = json.dumps(
        widget_payload(
            uuid,
            "",
            script_path,
            width,
            2,
            tap_script_path,
            status,
            color=color,
            icon=icon,
            slot_index=slot_index,
        ),
        separators=(",", ":"),
    ).encode()
    changed = dedupe_uuid(conn, uuid)
    row = conn.execute(
        """
        SELECT Z_PK, Z_ENT, ZBEZIERPATH, ZITEMPLACEMENT
        FROM ZBTTBASEENTITY
        WHERE ZUNIQUEIDENTIFIER=?
        ORDER BY CASE WHEN Z_ENT=? THEN 0 ELSE 1 END, Z_PK
        LIMIT 1
        """,
        (uuid, TOUCHBAR_ENTITY),
    ).fetchone()
    if row:
        _pk, ent, bezier, placement = row
        needs_repair = ent != TOUCHBAR_ENTITY or not bezier or placement != 2
        if needs_repair:
            conn.execute(
                """
                UPDATE ZBTTBASEENTITY
                SET Z_ENT=?, ZBEZIERPATH=?, ZITEMPLACEMENT=?,
                    ZISENABLED=NULL, ZENABLEDNEW=NULL, ZACTION=NULL, ZGESTURETYPE=NULL,
                    ZKEYCODE=NULL, ZMODIFIERKEYS=NULL, ZSHORTCUT=NULL,
                    ZBELONGSTOPRESET2=NULL, ZDESC=?, ZLAUNCHPATH=NULL
                WHERE ZUNIQUEIDENTIFIER=?
                """,
                (TOUCHBAR_ENTITY, blob, 2, status, uuid),
            )
            changed = True
        else:
            conn.execute(
                "UPDATE ZBTTBASEENTITY SET ZDESC=?, ZBEZIERPATH=? WHERE ZUNIQUEIDENTIFIER=?",
                (status, blob, uuid),
            )
        widget_pk = conn.execute(
            "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=? AND Z_ENT=?",
            (uuid, TOUCHBAR_ENTITY),
        ).fetchone()
        if widget_pk:
            linked = conn.execute(
                "SELECT 1 FROM Z_2APPS_GESTURES WHERE Z_2GESTURES=? AND Z_9APPS_GESTURES=?",
                (GLOBAL_APP_PK, widget_pk[0]),
            ).fetchone()
            if not linked:
                conn.execute(
                    "INSERT INTO Z_2APPS_GESTURES (Z_2GESTURES, Z_9APPS_GESTURES) VALUES (?, ?)",
                    (GLOBAL_APP_PK, widget_pk[0]),
                )
                changed = True
    else:
        # Row missing entirely; we cannot create it safely without PK collision risk.
        # Rely on the live add_new_trigger having created it.
        pass
    return changed


def repair_offline(
    db: Path,
    widgets: Dict[str, Dict[str, Any]],
    status: str,
    tap_script: Path,
    per_widget_state: Optional[Dict[str, Dict[str, str]]] = None,
) -> bool:
    changed = False
    per_widget_state = per_widget_state or {}
    with sqlite3.connect(db) as conn:
        for key, cfg in widgets.items():
            script = ROOT / cfg["script"]
            slot_index = _slot_index_from_key(key)
            state = per_widget_state.get(key) or {}
            color = state.get("color") or COLOR_IDLE
            icon = state.get("icon") or "terminal"
            if repair_widget(
                conn,
                cfg["uuid"],
                cfg["width"],
                status,
                script,
                tap_script,
                color=color,
                icon=icon,
                slot_index=slot_index,
            ):
                changed = True
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"BTT datastore integrity check failed: {integrity}")
    return changed


def slot_status(slot: str) -> str:
    script = ROOT / f"scripts/btt_muxy_slot_{slot}.sh"
    proc = subprocess.run(["sh", str(script)], check=False, text=True, capture_output=True)
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "·"


def _slot_index_from_key(key: str) -> Optional[int]:
    """Map widget config key to slot index passed to btt_muxy_slot_focus.sh.

    'summary' -> None (no slot index; tap action uses legacy refresh path).
    'slot_N' -> N. Anything else -> None.
    """
    if key == "summary":
        return None
    if key.startswith("slot_"):
        suffix = key[len("slot_"):]
        if suffix.isdigit():
            return int(suffix)
    return None


def self_test() -> int:
    errors: List[str] = []
    try:
        cfg = load_widget_config()
    except Exception as exc:
        print(f"FAIL config load: {exc}")
        return 2

    widgets = cfg.get("widgets")
    if not isinstance(widgets, dict):
        print("FAIL widgets key missing or not dict")
        return 2

    expected_slots = {"summary", "slot_0", "slot_1", "slot_2", "slot_3"}
    missing_slots = expected_slots - set(widgets.keys())
    if missing_slots:
        print(f"FAIL missing widget slots: {missing_slots}")
        return 2

    for key, item in widgets.items():
        if not isinstance(item, dict):
            errors.append(f"widget {key} is not a dict")
            continue
        for field in ("uuid", "name", "script", "width", "update_interval"):
            if field not in item:
                errors.append(f"widget {key} missing field {field}")
        uuid = str(item.get("uuid", ""))
        if len(uuid) != 36:
            errors.append(f"widget {key} has invalid uuid length {len(uuid)}")
        script = ROOT / str(item.get("script", ""))
        if not script.exists():
            errors.append(f"widget {key} script missing: {script}")

    tap_script = ROOT / str(cfg.get("tap_script", ""))
    if not tap_script.exists():
        errors.append(f"tap script missing: {tap_script}")

    # Validate payload construction for each widget
    tap_script = ROOT / str(cfg.get("tap_script", "scripts/btt_muxy_slot_tap.sh"))
    icons = load_icons()
    for key, item in widgets.items():
        try:
            slot_index = _slot_index_from_key(key)
            _payload = widget_payload(
                item["uuid"],
                item["name"],
                ROOT / item["script"],
                item["width"],
                item["update_interval"],
                FOCUS_SCRIPT,
                "self-test",
                color=COLOR_IDLE,
                icon=icons.get("default", "terminal"),
                slot_index=slot_index,
            )
            if "BTTTouchBarButtonColor" not in _payload:
                errors.append(f"widget {key} payload missing BTTTouchBarButtonColor")
            if "BTTTouchBarButtonIcon" not in _payload:
                errors.append(f"widget {key} payload missing BTTTouchBarButtonIcon")
            if slot_index is not None:
                expected_tap = f'do shell script "{FOCUS_SCRIPT} {slot_index}"'
                if _payload.get("BTTTouchBarAppleScriptString") != expected_tap:
                    errors.append(
                        f"widget {key} tap action should be {expected_tap!r} "
                        f"got {_payload.get('BTTTouchBarAppleScriptString')!r}"
                    )
        except Exception as exc:
            errors.append(f"widget {key} payload construction failed: {exc}")

    # Ensure no Muxy UUID collides with Vibe Island
    muxy_uuids = {item["uuid"] for item in widgets.values()}
    if VI_WIDGET in muxy_uuids or VI_TAP in muxy_uuids:
        errors.append("Muxy widget UUIDs collide with Vibe Island UUIDs")

    if errors:
        for err in errors:
            print(f"FAIL {err}")
        return 2

    print(f"OK {len(widgets)} widgets verified")
    print(f"tap_script={tap_script}")
    for key, item in widgets.items():
        print(f"  {key}: uuid={item['uuid']} script={item['script']} width={item['width']}")
    return 0


def do_install(cleanup_legacy: bool = True) -> int:
    cfg = load_widget_config()
    widgets = cfg.get("widgets", {})
    if not widgets:
        raise SystemExit("No widgets configured")

    tap_script = ROOT / str(cfg.get("tap_script", "scripts/btt_muxy_slot_tap.sh"))
    statuses = {key: slot_status(key) if key != "summary" else slot_status("summary") for key in widgets}

    # Load daemon state + icon config to derive per-slot color / SF Symbol.
    snapshot = load_state_snapshot()
    icons = load_icons()
    per_widget_state: Dict[str, Dict[str, str]] = {}
    for key in widgets.keys():
        idx = _slot_index_from_key(key)
        if idx is None:
            per_widget_state[key] = {"color": COLOR_IDLE, "icon": icons.get("default", "terminal"), "label": statuses[key]}
        else:
            slot = slot_state(snapshot, idx, icons)
            per_widget_state[key] = slot

    db = find_db()
    if cleanup_legacy:
        do_cleanup_legacy()
    elif btt_running():
        stop_btt()

    muxy_uuids = {item["uuid"] for item in widgets.values()}
    purge_muxy_rows(db, muxy_uuids)
    set_visibility_defaults()
    start_btt()
    time.sleep(6)

    # Tell BTT to drop any in-memory triggers with our target UUIDs before
    # add_new_trigger fires. BTT 6.521's add_new_trigger shows a modal
    # "Keep / Merge / Replace / Create New" dialog when the target UUID is
    # already present in its runtime cache (even if the SQLite store was
    # already purged). Forcing a delete_trigger first makes the install
    # fully unattended.
    purged = delete_existing_triggers(muxy_uuids)
    if purged:
        print(f"Pre-cleared {purged} cached trigger(s) to avoid merge dialog.")

    for order, (key, item) in enumerate(widgets.items()):
        slot_index = _slot_index_from_key(key)
        state = per_widget_state.get(key) or {}
        payload = widget_payload(
            item["uuid"],
            item["name"],
            ROOT / item["script"],
            item["width"],
            item["update_interval"],
            FOCUS_SCRIPT,
            statuses[key],
            order=order,
            color=state.get("color") or COLOR_IDLE,
            icon=state.get("icon") or icons.get("default", "terminal"),
            slot_index=slot_index,
        )
        add_live_trigger(payload)
        time.sleep(0.3)

    time.sleep(2)
    set_visibility_defaults()
    show_touch_bar()

    for key, item in widgets.items():
        refresh_widget(item["uuid"])
        time.sleep(0.2)

    subprocess.run(["killall", "ControlStrip"], check=False)
    subprocess.run(["killall", "TouchBarServer"], check=False)
    time.sleep(2)
    set_visibility_defaults()

    uuids = global_touchbar_uuids()
    muxy_global = [uuid for uuid in muxy_uuids if uuid in uuids]
    print(f"Muxy widgets registered: {len(muxy_global)}/{len(muxy_uuids)}")
    print(f"Global Touch Bar triggers: {len(uuids)}")
    for key, item in widgets.items():
        registered = "yes" if item["uuid"] in uuids else "no"
        state = per_widget_state.get(key) or {}
        print(
            f"  {key} ({item['uuid']}): {registered} "
            f"color={state.get('color', '?')} icon={state.get('icon', '?')} slot_index={_slot_index_from_key(key)}"
        )

    # Install + launchd-load the Muxy daemon plist so the daemon starts now
    # and survives reboot. Failures here are non-fatal (the touch-bar widgets
    # already work; only the daemon's polling loses autostart).
    install_launchd_plist(verbose=True)
    return 0


def install_launchd_plist(verbose: bool = True) -> int:
    """Copy scripts/com.touchbar.muxy-daemon.plist to ~/Library/LaunchAgents
    and immediately `launchctl load` it so the daemon is live now AND on
    every subsequent login/reboot.

    Idempotent: running twice is a no-op (the destination plist is overwritten
    with the same content; the daemon is reloaded if it's already loaded).
    Returns 0 on success (including graceful degradation if launchd is
    unavailable in the current environment), non-zero on hard errors.
    """
    if not LAUNCHD_PLIST_SRC.exists():
        if verbose:
            print(f"launchd plist source missing: {LAUNCHD_PLIST_SRC}", file=sys.stderr)
        return 1

    # Validate the plist syntax before installing.
    rc = subprocess.run(
        ["plutil", "-lint", str(LAUNCHD_PLIST_SRC)],
        check=False, capture_output=True, text=True,
    ).returncode
    if rc != 0:
        if verbose:
            print(f"launchd plist failed plutil -lint: rc={rc}", file=sys.stderr)
        return rc or 1

    LAUNCHD_PLIST_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LAUNCHD_PLIST_SRC, LAUNCHD_PLIST_DST)
    if verbose:
        print(f"launchd plist copied to {LAUNCHD_PLIST_DST}")

    # `launchctl unload` is best-effort: it fails cleanly if the label is not
    # currently loaded. We then `launchctl load` to bring it up immediately
    # so the daemon starts now (not just on next login).
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHD_PLIST_DST)],
        check=False, capture_output=True, text=True,
    )
    rc = subprocess.run(
        ["launchctl", "load", str(LAUNCHD_PLIST_DST)],
        check=False, capture_output=True, text=True,
    ).returncode
    if rc != 0:
        if verbose:
            out = (subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST_DST)],
                                   check=False, capture_output=True, text=True).stderr
                   or subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST_DST)],
                                     check=False, capture_output=True, text=True).stdout)
            print(f"launchctl load failed: rc={rc} ({out.strip()})", file=sys.stderr)
        return rc or 1

    if verbose:
        print(f"launchd plist loaded: {LAUNCHD_LABEL}")
    return 0


def do_cleanup_legacy() -> int:
    """Delete legacy/orphan Touch Bar triggers so the bar only shows Muxy widgets.

    Stops BTT, snapshots the live trigger list, computes the orphan set
    (anything not in the configured Muxy UUIDs plus the known Vibe Island
    UUIDs), deletes them from the offline datastore, and restarts BTT.

    Idempotent: rerunning with no orphans is a no-op (reports 0 removed).
    """
    cfg = load_widget_config()
    widgets = cfg.get("widgets", {})
    muxy_uuids = {item["uuid"] for item in widgets.values()} if isinstance(widgets, dict) else set()

    db = find_db()
    btt_was_running = btt_running()
    if btt_was_running:
        stop_btt()

    # Snapshot live triggers before deletion so we can report what we removed.
    try:
        live = global_touchbar_uuids()
    except RuntimeError:
        live = []
    # Orphans: anything in the live trigger list that isn't a Muxy widget.
    orphans = {uuid for uuid in live if uuid not in muxy_uuids}
    # Force-delete the known legacy Vibe Island UUIDs as well so the bar
    # only shows the 5 Muxy widgets (these UUIDs are kept in
    # LEGACY_UUIDS purely to track their historical identity, not to
    # protect them from deletion).
    target = orphans | LEGACY_UUIDS
    # Defensive: never delete a Muxy widget, even if it was reported as
    # an orphan by BTT.
    target -= muxy_uuids

    removed = purge_rows(db, target) if target else 0

    if btt_was_running:
        start_btt()
        time.sleep(2)

    print(f"Cleaned legacy triggers: {removed}/{len(target)}")
    for uuid in sorted(target):
        print(f"  removed {uuid}")
    return 0


def do_list() -> int:
    cfg = load_widget_config()
    widgets = cfg.get("widgets", {})
    for key, item in widgets.items():
        print(f"{key}\t{item['uuid']}\t{item.get('name', '')}\t{item.get('script', '')}\t{item.get('width', '')}")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Muxy Touch Bar control panel widgets")
    parser.add_argument("--self-test", action="store_true", help="validate scripts, payloads, UUIDs, and config")
    parser.add_argument("--install", action="store_true", help="register widgets in live BetterTouchTool state")
    parser.add_argument("--list", action="store_true", help="list configured widget UUIDs")
    parser.add_argument(
        "--cleanup-legacy",
        dest="cleanup_legacy",
        action="store_true",
        default=None,
        help="delete the old Vibe Island widget, its tap trigger, and any orphan global Touch Bar triggers not in the Muxy widget set (default: on)",
    )
    parser.add_argument(
        "--no-cleanup-legacy",
        dest="cleanup_legacy",
        action="store_false",
        help="skip the legacy cleanup step",
    )
    parser.add_argument(
        "--cleanup-legacy-only",
        action="store_true",
        help="only run the legacy cleanup step, do not register widgets",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.list:
        return do_list()
    if args.cleanup_legacy_only:
        return do_cleanup_legacy()
    # Default: install. Legacy cleanup is on by default; pass --no-cleanup-legacy to skip.
    return do_install(cleanup_legacy=args.cleanup_legacy is not False)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
