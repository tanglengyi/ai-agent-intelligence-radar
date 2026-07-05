"""Tests for rule_engine.py.

Run:
    cd 自动化情报收集系统
    python -m unittest tests/test_rule_engine.py
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rule_engine import IntelligenceRuleEngine  # noqa: E402


class TestRuleEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = IntelligenceRuleEngine()

    def test_shutdown_or_removal_should_be_high_risk(self) -> None:
        result = self.engine.evaluate({
            "title": "豆包部分拟人陪伴智能体下架",
            "text": "部分拟人陪伴类智能体下架，开发者社区讨论可能与内容安全、未成年人保护和合规整改有关。",
            "source_name": "晚点 LatePost 公众号 RSSHub",
            "source_url": "https://example.com/doubao-agent-removal",
        })
        self.assertIn("shutdown_or_removal", result.event_types)
        self.assertGreaterEqual(result.scores.risk_score, 4)
        self.assertIn("track_follow_up", result.recommended_actions)
        self.assertIn("convert_to_interview_story", result.recommended_actions)
        self.assertIn("合规", result.risk_insight)

    def test_official_agent_release_should_create_product_insight(self) -> None:
        result = self.engine.evaluate({
            "title": "OpenAI Agents SDK 发布新版本",
            "text": "新增 tracing、handoff、tool call 能力，提升 Agent 工作流可观测性和企业调试效率。",
            "source_name": "OpenAI Agents SDK GitHub Releases",
            "source_url": "https://github.com/openai/openai-agents-python/releases",
        })
        self.assertTrue({"feature_update", "open_source_release"} & set(result.event_types))
        self.assertGreaterEqual(result.scores.personal_relevance_score, 4)
        self.assertIn("save_to_knowledge_base", result.recommended_actions)
        self.assertIn("create_product_insight", result.recommended_actions)
        self.assertIn("日报", result.why_it_matters)

    def test_social_rumor_should_have_low_evidence(self) -> None:
        result = self.engine.evaluate({
            "title": "社媒爆料某智能体产品即将关闭",
            "text": "X 用户爆料某 AI Agent 产品即将关闭，但目前没有官方公告。",
            "source_name": "X Search - agentic AI regulation",
            "source_url": "https://x.com/search?q=agentic%20AI%20regulation",
        })
        self.assertLessEqual(result.scores.evidence_score, 2)
        self.assertIn("track_follow_up", result.recommended_actions)
        self.assertIn("交叉验证", result.insight.evidence_note)

    def test_markdown_should_be_generated(self) -> None:
        result = self.engine.evaluate({
            "title": "Cursor Changelog 更新 coding agent 权限控制",
            "text": "新增 coding agent 的 workspace 权限与隐私控制能力。",
            "source_name": "Cursor Changelog",
        })
        self.assertIn("### Cursor Changelog", result.markdown)
        self.assertIn("建议动作", result.markdown)
        self.assertGreater(result.scores.final_score, 0)


if __name__ == "__main__":
    unittest.main()
