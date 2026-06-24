#!/usr/bin/env python3
"""Register the MUXY/Vibe Island Touch Bar widget in Global BTT state.

BTT only renders Touch Bar widgets that exist in its live trigger registry for
the Global preset. Orphan SQLite rows (entity 15 without a live registration)
produce a blank Touch Bar. AppleScript add_new_trigger creates the live row;
an offline repair pass then upgrades it to a real Touch Bar widget (entity 15).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/yoseph/TouchBar")
VI_WIDGET = "4EA2B0F6-983C-4DD9-8F30-5F7161DCB601"
VI_SCRIPT = ROOT / "scripts/btt_agentmax_widget.sh"
REPAIR = ROOT / "scripts/btt_repair_vibe_island_widget.py"
MUXY_CONFIG = ROOT / "config" / "btt-muxy-widgets.json"
BTT_SUPPORT = Path.home() / "Library/Application Support/BetterTouchTool"


def run_osascript(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"osascript exit {proc.returncode}")
    return proc.stdout.strip()


def widget_status() -> str:
    proc = subprocess.run(["sh", str(VI_SCRIPT)], check=False, text=True, capture_output=True)
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "VI !err"


def find_db() -> Path:
    matches = sorted(BTT_SUPPORT.glob("btt_data_store.version_*"))
    stores = [
        p
        for p in matches
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


def stop_btt() -> None:
    subprocess.run(["osascript", "-e", 'tell application "BetterTouchTool" to quit'], check=False)
    time.sleep(2)
    subprocess.run(["pkill", "-9", "-x", "BetterTouchTool"], check=False)
    subprocess.run(["pkill", "-9", "-f", "BTTRelaunch"], check=False)
    time.sleep(1)


def start_btt() -> None:
    subprocess.run(["open", "-a", "BetterTouchTool"], check=False)


def purge_widget_rows(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?",
            (VI_WIDGET,),
        ).fetchall()
        pks = [row[0] for row in rows]
        for pk in pks:
            conn.execute("DELETE FROM Z_2APPS_GESTURES WHERE Z_9APPS_GESTURES=?", (pk,))
        conn.execute("DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?", (VI_WIDGET,))
        conn.commit()


def set_visibility_defaults() -> None:
    for args in (
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTTouchBarVisible", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTTBWasVisibleBeforeSleep", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTAlwaysShowBTTTouchBarOnStartup", "-bool", "true"],
        ["defaults", "write", "com.hegenberg.BetterTouchTool", "BTTForcedHidden", "-bool", "false"],
    ):
        subprocess.run(args, check=False)


def add_live_trigger(status: str) -> None:
    payload = {
        "BTTUUID": VI_WIDGET,
        "BTTTriggerClass": "BTTTriggerTypeTouchBar",
        "BTTTriggerType": 642,
        "BTTEnabled": 1,
        "BTTBelongsToApp": "Global",
        "BTTTriggerBelongsToPreset": "Default",
        "BTTWidgetName": "MUXY Touch Bar",
        "BTTTriggerTypeDescription": status,
        "BTTTouchBarAlwaysShowButton": 1,
        "BTTTouchBarShellScriptString": str(VI_SCRIPT),
        "BTTTouchBarScriptUpdateInterval": 2,
        "BTTTouchBarButtonWidth": 260,
        "BTTTouchBarButtonUseFixedWidth": 1,
        "BTTTouchBarButtonMonoSpace": 1,
        "BTTTouchBarButtonFontSize": 12,
        "BTTTouchBarItemPadding": 3,
        "BTTTouchBarButtonColor": "25.000000, 30.000000, 38.000000, 255.000000",
        "BTTTouchBarFontColor": "180.000000, 220.000000, 255.000000, 255.000000",
        "BTTTouchBarButtonName": status,
    }
    run_osascript(
        "tell application \"BetterTouchTool\" to add_new_trigger "
        f"{json.dumps(json.dumps(payload))}"
    )


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


def refresh_widget(status: str) -> None:
    proc = subprocess.run(
        ["osascript", "-", VI_WIDGET, status],
        input="""on run argv
  tell application "BetterTouchTool"
    update_touch_bar_widget (item 1 of argv) text (item 2 of argv)
    refresh_widget (item 1 of argv)
  end tell
end run""",
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "refresh failed")


def global_touchbar_triggers() -> list[dict[str, Any]]:
    raw = run_osascript(
        "tell application \"BetterTouchTool\" to get_triggers "
        "\"{\\\"trigger_type\\\":\\\"BTTTriggerTypeTouchBar\\\",\\\"trigger_app_bundle_identifier\\\":\\\"BT.G\\\"}\""
    )
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("BTTUUID")]


def global_touchbar_uuids() -> list[str]:
    return [str(item.get("BTTUUID")) for item in global_touchbar_triggers()]


def muxy_widget_uuids() -> set[str]:
    try:
        with MUXY_CONFIG.open("r", encoding="utf-8") as handle:
            cfg = json.load(handle)
    except Exception:
        return set()
    widgets = cfg.get("widgets") if isinstance(cfg.get("widgets"), dict) else {}
    return {str(item.get("uuid", "")) for item in widgets.values() if isinstance(item, dict)}


def do_list() -> int:
    muxy_uuids = muxy_widget_uuids()
    for item in global_touchbar_triggers():
        uuid = str(item.get("BTTUUID", ""))
        button = str(item.get("BTTTouchBarButtonName") or "")
        widget = str(item.get("BTTWidgetName") or "")
        labels = " ".join(part for part in (button, widget) if part)
        marker = "\t[muxy]" if uuid in muxy_uuids else ""
        print(f"{uuid}\t{labels}{marker}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Register/list BTT Touch Bar widgets")
    parser.add_argument("--list", action="store_true", help="list global Touch Bar trigger UUIDs and names")
    args = parser.parse_args(argv)

    if args.list:
        return do_list()

    status = widget_status()
    db = find_db()
    stop_btt()
    purge_widget_rows(db)
    set_visibility_defaults()
    start_btt()
    time.sleep(4)
    add_live_trigger(status)
    time.sleep(1)
    stop_btt()
    proc = subprocess.run([sys.executable, str(REPAIR)], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or "repair failed")
    start_btt()
    time.sleep(4)
    set_visibility_defaults()
    show_touch_bar()
    refresh_widget(status)
    subprocess.run(["killall", "ControlStrip"], check=False)
    subprocess.run(["killall", "TouchBarServer"], check=False)
    time.sleep(2)
    set_visibility_defaults()
    uuids = global_touchbar_uuids()
    print(f"Registered Touch Bar widget: {VI_WIDGET in uuids}")
    print(f"Global Touch Bar triggers: {len(uuids)}")
    print(f"Status line: {status}")
    print(f"BTTTouchBarVisible: {subprocess.run(['defaults','read','com.hegenberg.BetterTouchTool','BTTTouchBarVisible'], capture_output=True, text=True).stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))