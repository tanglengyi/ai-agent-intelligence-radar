"""End-to-end pipeline for AI Agent Intelligence Radar.

一键完成：
1. RSS/RSSHub 采集原始信息；
2. rule_engine.py 逐条评分和生成洞察；
3. event_merger.py 多源事件归并；
4. quality_filter.py 过滤低价值噪音；
5. report_generator.py 生成日报 Markdown；
6. 可选同步到 Notion；
7. 输出本次运行摘要。

MVP 设计原则：
- 先跑通闭环，不依赖数据库、不依赖大语言模型、不依赖第三方包。
- 每一步都有中间文件，方便调试和复盘。
- 失败源不会中断整个 pipeline。
- Notion 同步是可选项，不影响本地文件生成。
- RSS 可以多抓，但 Notion 必须少进；Notion 是资产库，不是资讯垃圾桶。

Run:
    cd 自动化情报收集系统
    python src/pipeline.py --date 2026-07-05 --max-sources 10 --limit-per-source 3

Production-like:
    python src/pipeline.py --date today --skip-low-stability

With Notion:
    export NOTION_TOKEN="secret_xxx"
    python src/pipeline.py \
      --date today \
      --skip-low-stability \
      --notion-database-id YOUR_DATABASE_ID \
      --notion-parent-page-id YOUR_PAGE_ID
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from event_merger import EventMerger
from notion_exporter import NotionClient, export_events_to_database, export_report_to_page
from quality_filter import QualityFilter
from report_generator import generate_report
from rss_collector import CollectedItem, RSSCollector, write_jsonl as write_raw_jsonl
from rule_engine import IntelligenceRuleEngine, result_to_dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IntelligencePipeline:
    """One-command pipeline for collection, evaluation, merge, quality filtering, report and optional Notion export."""

    def __init__(
        self,
        date_text: Optional[str] = None,
        rsshub_base: str = "https://rsshub.app",
        limit_per_source: int = 5,
        max_sources: Optional[int] = None,
        include_low_stability: bool = True,
        top_n: int = 5,
        notion_database_id: Optional[str] = None,
        notion_parent_page_id: Optional[str] = None,
        notion_token_env: str = "NOTION_TOKEN",
        notion_event_limit: Optional[int] = None,
        disable_quality_filter: bool = False,
    ) -> None:
        self.date_text = self._resolve_date(date_text)
        self.rsshub_base = rsshub_base
        self.limit_per_source = limit_per_source
        self.max_sources = max_sources
        self.include_low_stability = include_low_stability
        self.top_n = top_n
        self.notion_database_id = notion_database_id
        self.notion_parent_page_id = notion_parent_page_id
        self.notion_token_env = notion_token_env
        self.notion_event_limit = notion_event_limit
        self.disable_quality_filter = disable_quality_filter
        self.quality_filter = QualityFilter()

        self.raw_path = PROJECT_ROOT / f"data/raw/rss_items_{self.date_text}.jsonl"
        self.evaluated_path = PROJECT_ROOT / f"data/processed/evaluated_items_{self.date_text}.jsonl"
        self.merged_all_path = PROJECT_ROOT / f"data/processed/merged_events_all_{self.date_text}.jsonl"
        self.merged_path = PROJECT_ROOT / f"data/processed/merged_events_filtered_{self.date_text}.jsonl"
        self.notion_events_path = PROJECT_ROOT / f"data/processed/notion_events_{self.date_text}.jsonl"
        self.dropped_path = PROJECT_ROOT / f"data/processed/dropped_events_{self.date_text}.jsonl"
        self.report_path = PROJECT_ROOT / f"data/reports/daily_{self.date_text}.md"
        self.summary_path = PROJECT_ROOT / f"data/reports/pipeline_summary_{self.date_text}.json"

    def _resolve_date(self, value: Optional[str]) -> str:
        if not value or value == "today":
            return datetime.now().strftime("%Y-%m-%d")
        return value

    def run(self) -> Dict[str, Any]:
        raw_items = self.collect()
        evaluated_items = self.evaluate(raw_items)
        merged_all = self.merge(evaluated_items)
        report_events, notion_events, dropped_events = self.apply_quality_filters(merged_all)
        self.generate_daily_report(report_events)
        notion_result = self.export_to_notion_if_enabled()
        summary = self.build_summary(raw_items, evaluated_items, merged_all, report_events, notion_events, dropped_events, notion_result)
        self.write_summary(summary)
        return summary

    def collect(self) -> List[CollectedItem]:
        collector = RSSCollector(rsshub_base=self.rsshub_base)
        items = collector.collect_all(
            limit_per_source=self.limit_per_source,
            max_sources=self.max_sources,
            include_low_stability=self.include_low_stability,
        )
        write_raw_jsonl(items, self.raw_path)
        return items

    def evaluate(self, raw_items: List[CollectedItem]) -> List[Dict[str, Any]]:
        engine = IntelligenceRuleEngine()
        evaluated: List[Dict[str, Any]] = []
        for raw in raw_items:
            payload = asdict(raw)
            result = engine.evaluate(payload)
            evaluated.append(result_to_dict(result))
        self._write_jsonl(evaluated, self.evaluated_path)
        return evaluated

    def merge(self, evaluated_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merger = EventMerger()
        merged = [asdict(event) for event in merger.merge(evaluated_items)]
        self._write_jsonl(merged, self.merged_all_path)
        return merged

    def apply_quality_filters(self, merged_all: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self.disable_quality_filter:
            self._write_jsonl(merged_all, self.merged_path)
            self._write_jsonl(merged_all, self.notion_events_path)
            self._write_jsonl([], self.dropped_path)
            return merged_all, merged_all, []

        report_events = self.quality_filter.filter_for_report(merged_all)
        notion_events, dropped_events = self.quality_filter.filter_for_notion(
            report_events,
            limit=self.notion_event_limit,
        )
        self._write_jsonl(report_events, self.merged_path)
        self._write_jsonl(notion_events, self.notion_events_path)
        self._write_jsonl(dropped_events, self.dropped_path)
        return report_events, notion_events, dropped_events

    def generate_daily_report(self, report_events: List[Dict[str, Any]]) -> str:
        report = generate_report(
            report_events,
            merged=True,
            date_text=self.date_text,
            top_n=self.top_n,
        )
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report, encoding="utf-8")
        return report

    def export_to_notion_if_enabled(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"enabled": False}
        if not self.notion_database_id and not self.notion_parent_page_id:
            return result

        token = os.getenv(self.notion_token_env)
        if not token:
            return {
                "enabled": True,
                "success": False,
                "error": f"Missing Notion token env: {self.notion_token_env}",
            }

        client = NotionClient(token=token)
        result = {"enabled": True, "success": True}

        if self.notion_database_id:
            event_page_ids = export_events_to_database(
                client,
                self.notion_database_id,
                self.notion_events_path,
                limit=self.notion_event_limit,
            )
            result["event_pages"] = event_page_ids

        if self.notion_parent_page_id:
            report_page_id = export_report_to_page(
                client,
                self.notion_parent_page_id,
                self.report_path,
                f"AI Agent Intelligence Radar Daily - {self.date_text}",
            )
            result["report_page"] = report_page_id

        return result

    def build_summary(
        self,
        raw_items: List[CollectedItem],
        evaluated_items: List[Dict[str, Any]],
        merged_all: List[Dict[str, Any]],
        report_events: List[Dict[str, Any]],
        notion_events: List[Dict[str, Any]],
        dropped_events: List[Dict[str, Any]],
        notion_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        top_events = sorted(
            report_events,
            key=lambda x: float(x.get("max_final_score", x.get("average_final_score", 0)) or 0),
            reverse=True,
        )[: self.top_n]
        return {
            "date": self.date_text,
            "paths": {
                "raw": str(self.raw_path.relative_to(PROJECT_ROOT)),
                "evaluated": str(self.evaluated_path.relative_to(PROJECT_ROOT)),
                "merged_all": str(self.merged_all_path.relative_to(PROJECT_ROOT)),
                "merged_filtered_for_report": str(self.merged_path.relative_to(PROJECT_ROOT)),
                "notion_events": str(self.notion_events_path.relative_to(PROJECT_ROOT)),
                "dropped_events": str(self.dropped_path.relative_to(PROJECT_ROOT)),
                "report": str(self.report_path.relative_to(PROJECT_ROOT)),
                "summary": str(self.summary_path.relative_to(PROJECT_ROOT)),
            },
            "counts": {
                "raw_items": len(raw_items),
                "evaluated_items": len(evaluated_items),
                "merged_events_all": len(merged_all),
                "report_events_after_filter": len(report_events),
                "notion_events_after_filter": len(notion_events),
                "dropped_events": len(dropped_events),
            },
            "top_events": [
                {
                    "title": event.get("canonical_title"),
                    "score": event.get("max_final_score"),
                    "confidence": event.get("confidence_level"),
                    "type": event.get("primary_event_type"),
                    "sources": event.get("sources", [])[:5],
                    "actions": event.get("recommended_actions", []),
                    "quality_filter": event.get("quality_filter", {}),
                }
                for event in top_events
            ],
            "notion": notion_result or {"enabled": False},
            "note": "RSS 可以多抓；Notion 只保留能形成判断、预警、竞品信号、机会清单或面试案例的内容。",
        }

    def write_summary(self, summary: Dict[str, Any]) -> None:
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_jsonl(self, items: List[Dict[str, Any]], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI/Agent 情报雷达一键 Pipeline")
    parser.add_argument("--date", default="today", help="日报日期，如 2026-07-05；默认 today")
    parser.add_argument("--rsshub-base", default="https://rsshub.app", help="RSSHub 实例地址")
    parser.add_argument("--limit-per-source", type=int, default=5, help="每个源最多采集条数")
    parser.add_argument("--max-sources", type=int, default=None, help="最多采集多少个源，调试用")
    parser.add_argument("--skip-low-stability", action="store_true", help="跳过低稳定 RSSHub/社媒/招聘源")
    parser.add_argument("--top-n", type=int, default=5, help="日报 Top 信号数量")
    parser.add_argument("--disable-quality-filter", action="store_true", help="调试用：关闭质量过滤，保留全量事件")
    parser.add_argument("--notion-database-id", default=None, help="可选：把高质量合并事件同步到 Notion 数据库")
    parser.add_argument("--notion-parent-page-id", default=None, help="可选：把日报 Markdown 同步为该 Notion 页面下的子页面")
    parser.add_argument("--notion-token-env", default="NOTION_TOKEN", help="Notion token 环境变量名")
    parser.add_argument("--notion-event-limit", type=int, default=None, help="可选：最多同步多少条合并事件到 Notion")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    pipeline = IntelligencePipeline(
        date_text=args.date,
        rsshub_base=args.rsshub_base,
        limit_per_source=args.limit_per_source,
        max_sources=args.max_sources,
        include_low_stability=not args.skip_low_stability,
        top_n=args.top_n,
        notion_database_id=args.notion_database_id,
        notion_parent_page_id=args.notion_parent_page_id,
        notion_token_env=args.notion_token_env,
        notion_event_limit=args.notion_event_limit,
        disable_quality_filter=args.disable_quality_filter,
    )
    summary = pipeline.run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
