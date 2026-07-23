from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_daily_ops.py"
spec = importlib.util.spec_from_file_location("run_daily_ops", MODULE_PATH)
ops = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = ops
spec.loader.exec_module(ops)


class TestRunDailyOps(unittest.TestCase):
    def test_redacts_secret(self) -> None:
        text = ops.redact("token=secret-123", {"NOTION_TOKEN": "secret-123"})
        self.assertEqual(text, "token=***REDACTED***")

    def test_notion_states(self) -> None:
        self.assertEqual(ops.notion_state(None), "unknown")
        self.assertEqual(ops.notion_state({"notion": {"enabled": False}}), "not_configured")
        self.assertEqual(ops.notion_state({"notion": {"enabled": True, "success": True}}), "success")
        self.assertEqual(ops.notion_state({"notion": {"enabled": True, "success": False}}), "failed")

    def test_diagnostics_detect_zero_counts_and_low_coverage(self) -> None:
        diagnostics = ops.build_diagnostics(
            [],
            {"counts": {"raw_items": 0}, "notion": {"enabled": False}},
            {"counts": {"signals": 0, "competitor_coverage_rate": 0.1}, "notion": {"enabled": False}},
        )
        codes = {item["code"] for item in diagnostics}
        self.assertIn("GENERAL_ZERO_RAW_ITEMS", codes)
        self.assertIn("COMPETITIVE_ZERO_SIGNALS", codes)
        self.assertIn("LOW_COMPETITOR_COVERAGE", codes)
        self.assertEqual(ops.overall_status([], diagnostics), "warning")

    def test_failed_step_sets_failed_status(self) -> None:
        step = ops.StepResult("x", ["false"], "failed", 1, "a", "b", 0.1, "x.log", "error")
        diagnostics = ops.build_diagnostics([step], {}, {})
        self.assertEqual(ops.overall_status([step], diagnostics), "failed")

    def test_build_commands_contains_three_steps(self) -> None:
        old = os.environ.pop("RSSHUB_BASE_URL", None)
        try:
            commands = ops.build_commands("2026-07-23", False)
        finally:
            if old is not None:
                os.environ["RSSHUB_BASE_URL"] = old
        self.assertEqual([name for name, _ in commands], ["unit-tests", "general-pipeline", "competitive-radar"])
        self.assertIn("2026-07-23", commands[1][1])


if __name__ == "__main__":
    unittest.main()
