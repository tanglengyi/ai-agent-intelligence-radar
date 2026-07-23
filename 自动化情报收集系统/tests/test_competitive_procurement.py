"""Tests for competitive_procurement.py.

Run:
    cd 自动化情报收集系统
    python -m unittest tests/test_competitive_procurement.py
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

notion_stub = types.ModuleType("notion_exporter")
notion_stub.NotionClient = object
notion_stub.export_report_to_page = lambda *args, **kwargs: "page-id"
sys.modules.setdefault("notion_exporter", notion_stub)

rss_stub = types.ModuleType("rss_collector")
rss_stub.fetch_text = lambda url: ""
rss_stub.parse_rss_or_atom = lambda text: []
rss_stub.source_feed_url = lambda source, base="": source.get("feed_url")
sys.modules.setdefault("rss_collector", rss_stub)

from competitive_procurement import (  # noqa: E402
    confidence_level,
    detect_stage,
    extract_budget,
    keyword_hits,
    score_signal,
)


class TestCompetitiveProcurement(unittest.TestCase):
    def test_extract_chinese_budget(self) -> None:
        text, amount = extract_budget("本项目采购预算为 1280 万元，采购企业知识库和智能体平台。")
        self.assertIn("1280", text)
        self.assertEqual(amount, 12_800_000)

    def test_extract_usd_budget(self) -> None:
        text, amount = extract_budget("The contract award is USD 2.5 million for an AI agent platform.")
        self.assertEqual(text, "USD 2.5 million")
        self.assertEqual(amount, 17_500_000)

    def test_detect_procurement_stage(self) -> None:
        stages = {
            "award": ["中标公告", "contract award"],
            "tender": ["招标公告", "RFP"],
            "intent": ["采购意向"],
        }
        self.assertEqual(detect_stage("某市发布人工智能项目采购意向", stages), "intent")
        self.assertEqual(detect_stage("AI platform RFP is now open", stages), "tender")

    def test_score_high_value_procurement(self) -> None:
        score = score_signal(
            category_weight=5,
            evidence_level="官方采购公告",
            stage="tender",
            budget_cny=20_000_000,
            companies=["Microsoft"],
            keyword_count=5,
        )
        self.assertGreaterEqual(score, 9)
        self.assertEqual(confidence_level(score, "官方采购公告", "tender", 20_000_000), "high")

    def test_keyword_hits_are_case_insensitive_and_unique(self) -> None:
        hits = keyword_hits("OpenAI agent pricing update", ["OpenAI", "agent", "AGENT", "pricing"])
        self.assertEqual(hits, ["OpenAI", "agent", "pricing"])


if __name__ == "__main__":
    unittest.main()
