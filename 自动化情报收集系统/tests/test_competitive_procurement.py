"""Tests for competitive_procurement.py.

Run:
    cd 自动化情报收集系统
    python -m unittest tests/test_competitive_procurement.py
"""

from __future__ import annotations

import json
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
    CompetitiveProcurementRadar,
    confidence_level,
    detect_stage,
    extract_budget,
    keyword_hits,
    match_competitor,
    score_signal,
    validate_watchlist,
)


class TestCompetitiveProcurement(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.watchlist = json.loads(
            (PROJECT_ROOT / "config" / "competitor_watchlist.json").read_text(encoding="utf-8")
        )

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

    def test_watchlist_has_exactly_ten_unique_competitors(self) -> None:
        competitors = validate_watchlist(self.watchlist)
        self.assertEqual(len(competitors), 10)
        self.assertEqual(len({item["id"] for item in competitors}), 10)
        self.assertIn("Microsoft Copilot Studio", {item["product"] for item in competitors})
        self.assertIn("腾讯云智能体开发平台 ADP", {item["product"] for item in competitors})

    def test_match_competitor_by_alias_and_forced_source(self) -> None:
        competitors = validate_watchlist(self.watchlist)
        matched = match_competitor("UiPath Maestro orchestrates agents and robots", competitors)
        self.assertEqual(matched["id"], "uipath_agentic_automation")
        forced = match_competitor("企业工作流发布新功能", competitors, "sap_joule_studio")
        self.assertEqual(forced["product"], "SAP Joule Studio")

    def test_radar_builds_ten_competitor_sources(self) -> None:
        radar = CompetitiveProcurementRadar()
        sources = radar.competitor_sources()
        self.assertEqual(len(sources), 10)
        self.assertTrue(all(source.get("competitor_id") for source in sources))

    def test_competitor_signal_contains_personal_relevance(self) -> None:
        radar = CompetitiveProcurementRadar()
        source = next(
            item for item in radar.competitor_sources()
            if item["competitor_id"] == "salesforce_agentforce"
        )
        signal = radar.analyze(
            source,
            source["feed_url"],
            {
                "title": "Salesforce updates Agentforce pricing and CRM actions",
                "summary": "New workflow actions and enterprise pricing are available.",
                "link": "https://example.com/agentforce",
                "published_at": "2026-07-23T00:00:00+00:00",
            },
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.competitor_product, "Salesforce Agentforce")
        self.assertIn("商业闭环", signal.competitor_relevance)


if __name__ == "__main__":
    unittest.main()
