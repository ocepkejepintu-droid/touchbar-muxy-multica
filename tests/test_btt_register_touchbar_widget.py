#!/usr/bin/env python3
"""Tests for btt_register_touchbar_widget.py --list flag."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "btt_register_touchbar_widget.py"

spec = importlib.util.spec_from_file_location("btt_register_touchbar_widget", SCRIPT_PATH)
btt_register = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(btt_register)


class BttRegisterTouchbarWidgetTests(unittest.TestCase):
    def test_do_list_prints_uuid_and_name(self):
        triggers = [
            {"BTTUUID": "UUID-1", "BTTTouchBarButtonName": "Widget A"},
            {"BTTUUID": "UUID-2", "BTTWidgetName": "Widget B"},
            {"BTTUUID": "UUID-3"},
        ]
        with mock.patch.object(
            btt_register, "global_touchbar_triggers", return_value=triggers
        ), mock.patch("builtins.print") as mock_print:
            code = btt_register.do_list()
        self.assertEqual(code, 0)
        calls = [call.args for call in mock_print.call_args_list]
        self.assertEqual(calls, [
            ("UUID-1\tWidget A",),
            ("UUID-2\tWidget B",),
            ("UUID-3\t",),
        ])

    def test_do_list_empty(self):
        with mock.patch.object(
            btt_register, "global_touchbar_triggers", return_value=[]
        ), mock.patch("builtins.print") as mock_print:
            code = btt_register.do_list()
        self.assertEqual(code, 0)
        mock_print.assert_not_called()

    def test_main_with_list_flag_calls_do_list(self):
        with mock.patch.object(
            btt_register, "do_list", return_value=42
        ) as mock_do_list:
            code = btt_register.main(["--list"])
        self.assertEqual(code, 42)
        mock_do_list.assert_called_once()


if __name__ == "__main__":
    unittest.main()
