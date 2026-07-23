"""AI competitive and procurement intelligence radar.

Collect ten signal families plus a focused competitor watchlist, extract
procurement intent/budget, score signals, write JSONL + Markdown, and optionally
publish the daily report to Notion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from notion_exporter import NotionClient, export_report_to_page
from rss_collector import fetch_text, parse_rss_or_atom, source_feed_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "competitive_procurement.json"
DEFAULT_WATCHLIST_PATH = CONFIG_DIR / "competitor_watchlist.json"
BASE_SOURCES_PATH = CONFIG_DIR / "sources.json"
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class ProcurementSignal:
    title: str
    summary: str
    source_name: str
    source_url: str
    published_at: Optional[str]
    collected_at: str
    signal_category: str
    category_label: str
    discoverable_insight: str
    evidence_level: str
    procurement_stage: str
    budget_text: str
    budget_cny: Optional[float]
    tracked_companies: List[str]
    matched_keywords: List[str]
    score: float
    confidence: str
    recommended_action: str
    competitor_id: str = ""
    competitor_vendor: str = ""
    competitor_product: str = ""
    competitor_relevance: str = ""
    competitor_dimensions: List[str] | None = None


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def keyword_hits(text: str, keywords: Sequence[str]) -> List[str]:
    lowered = text.lower()
    hits: List[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = str(keyword).lower()
        if normalized and normalized in lowered and normalized not in seen:
            hits.append(str(keyword))
            seen.add(normalized)
    return hits


def unique_strings(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        normalized = text.lower()
        if text and normalized not in seen:
            result.append(text)
            seen.add(normalized)
    return result


def validate_watchlist(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    competitors = payload.get("competitors", [])
    expected = int(payload.get("expected_competitor_count", 10))
    if len(competitors) != expected:
        raise ValueError(f"competitor watchlist requires {expected} entries, got {len(competitors)}")
    ids = [str(item.get("id", "")).strip() for item in competitors]
    if any(not item_id for item_id in ids):
        raise ValueError("every competitor requires a non-empty id")
    if len(set(ids)) != len(ids):
        raise ValueError("competitor ids must be unique")
    for item in competitors:
        for field in ("vendor", "product", "official_url", "news_feed_url"):
            if not str(item.get(field, "")).strip():
                raise ValueError(f"competitor {item.get('id')} missing required field: {field}")
    return competitors


def match_competitor(
    text: str,
    competitors: Sequence[Dict[str, Any]],
    forced_id: str = "",
) -> Optional[Dict[str, Any]]:
    if forced_id:
        for competitor in competitors:
            if competitor.get("id") == forced_id:
                return competitor
    lowered = text.lower()
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for competitor in competitors:
        aliases = unique_strings(
            [
                competitor.get("vendor", ""),
                competitor.get("product", ""),
                *competitor.get("aliases", []),
            ]
        )
        matched = [alias for alias in aliases if alias.lower() in lowered]
        if matched:
            candidates.append((max(len(alias) for alias in matched), competitor))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def detect_stage(text: str, rules: Dict[str, Sequence[str]]) -> str:
    lowered = text.lower()
    for stage, keywords in rules.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            return stage
    return "unknown"


def extract_budget(text: str) -> Tuple[str, Optional[float]]:
    cny = re.search(
        r"(?:预算|金额|中标价|成交价|合同金额|采购金额|项目金额|报价)?"
        r"[^\d]{0,10}(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|人民币元|元)",
        text,
    )
    if cny:
        value = float(cny.group(1))
        multiplier = {
            "亿元": 100_000_000,
            "亿": 100_000_000,
            "万元": 10_000,
            "万": 10_000,
            "人民币元": 1,
            "元": 1,
        }[cny.group(2)]
        return cny.group(0).strip(), value * multiplier

    usd = re.search(r"(?:US\$|USD|\$)\s*(\d+(?:\.\d+)?)\s*(billion|million|bn|m)?", text, re.I)
    if usd:
        value = float(usd.group(1))
        unit = (usd.group(2) or "").lower()
        multiplier = {
            "billion": 1_000_000_000,
            "bn": 1_000_000_000,
            "million": 1_000_000,
            "m": 1_000_000,
        }.get(unit, 1)
        return usd.group(0).strip(), value * multiplier * 7.0
    return "", None


def evidence_score(level: str) -> int:
    text = (level or "").lower()
    if any(key in text for key in ["官方", "采购公告", "招标", "年报", "official"]):
        return 5
    if any(key in text for key in ["公司公告", "研究报告", "行业报告", "财报"]):
        return 4
    if any(key in text for key in ["媒体", "搜索索引", "招聘"]):
        return 3
    if any(key in text for key in ["社媒", "弱信号"]):
        return 2
    return 1


def score_signal(
    category_weight: int,
    evidence_level: str,
    stage: str,
    budget_cny: Optional[float],
    companies: Sequence[str],
    keyword_count: int,
) -> float:
    score = float(category_weight) + max(0, evidence_score(evidence_level) - 2) * 0.8
    score += 1.5 if stage != "unknown" else 0
    score += 1.5 if budget_cny is not None else 0
    score += 0.5 if budget_cny and budget_cny >= 10_000_000 else 0
    score += 0.5 if companies else 0
    score += min(keyword_count, 4) * 0.25
    return round(min(score, 10.0), 2)


def confidence_level(score: float, evidence_level: str, stage: str, budget_cny: Optional[float]) -> str:
    evidence = evidence_score(evidence_level)
    if evidence >= 4 and score >= 7 and (stage != "unknown" or budget_cny is not None):
        return "high"
    if evidence >= 3 and score >= 5.5:
        return "medium"
    return "low"


class CompetitiveProcurementRadar:
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        watchlist_path: Path = DEFAULT_WATCHLIST_PATH,
        date_text: str = "today",
        rsshub_base: str = "https://rsshub.app",
        limit_per_source: int = 5,
        max_sources: Optional[int] = None,
        top_n: int = 20,
    ) -> None:
        self.config = load_json(config_path)
        self.watchlist = load_json(watchlist_path)
        self.competitors = validate_watchlist(self.watchlist)
        self.base_sources = load_json(BASE_SOURCES_PATH)
        self.timezone = self.config.get("timezone", "Asia/Shanghai")
        self.date_text = (
            date_text
            if date_text and date_text != "today"
            else datetime.now(ZoneInfo(self.timezone)).strftime("%Y-%m-%d")
        )
        self.rsshub_base = rsshub_base
        self.limit_per_source = limit_per_source
        self.max_sources = max_sources
        self.top_n = top_n
        self.output_dir = PROJECT_ROOT / "data" / "competitive_procurement"
        self.signals_path = self.output_dir / f"signals_{self.date_text}.jsonl"
        self.report_path = self.output_dir / f"daily_{self.date_text}.md"
        self.summary_path = self.output_dir / f"summary_{self.date_text}.json"

    def competitor_sources(self) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        for competitor in self.competitors:
            sources.append(
                {
                    "name": f"{competitor['product']} 官方动态",
                    "signal_category": "competitor_pricing",
                    "evidence_level": "公司公告 / 官方站点索引",
                    "source_type": "google_news_rss",
                    "priority": 6,
                    "language": competitor.get("language", "mixed"),
                    "stability": "high",
                    "url": competitor["official_url"],
                    "feed_url": competitor["news_feed_url"],
                    "monitor_keywords": unique_strings(
                        [
                            competitor["vendor"],
                            competitor["product"],
                            *competitor.get("aliases", []),
                            *competitor.get("monitor_keywords", []),
                        ]
                    ),
                    "require_keyword_match": True,
                    "competitor_id": competitor["id"],
                }
            )
        return sources

    def resolve_sources(self) -> List[Dict[str, Any]]:
        source_map = {item.get("name"): item for item in self.base_sources.get("sources", [])}
        resolved: List[Dict[str, Any]] = []
        for ref in self.config.get("source_refs", []):
            base = source_map.get(ref.get("name"))
            if not base:
                print(f"[WARN] source_ref not found: {ref.get('name')}", file=sys.stderr)
                continue
            merged = dict(base)
            merged.update(ref)
            resolved.append(merged)
        resolved.extend(dict(item) for item in self.config.get("sources", []))
        resolved.extend(self.competitor_sources())
        deduplicated: Dict[str, Dict[str, Any]] = {}
        for source in resolved:
            key = source_feed_url(source, self.rsshub_base) or source.get("url") or source.get("name")
            deduplicated[str(key)] = source
        sources = list(deduplicated.values())
        sources.sort(key=lambda item: int(item.get("priority", 1)), reverse=True)
        return sources[: self.max_sources] if self.max_sources else sources

    def tracked_company_keywords(self) -> List[str]:
        values: List[str] = list(self.config.get("tracked_companies", []))
        for competitor in self.competitors:
            values.extend(
                [
                    competitor.get("vendor", ""),
                    competitor.get("product", ""),
                    *competitor.get("aliases", []),
                ]
            )
        return unique_strings(values)

    def analyze(self, source: Dict[str, Any], feed_url: str, raw: Dict[str, Optional[str]]) -> Optional[ProcurementSignal]:
        category_key = source.get("signal_category", "technology_development")
        category = self.config.get("categories", {}).get(category_key)
        if not category:
            return None
        title = raw.get("title") or "未命名竞品采购信号"
        summary = raw.get("summary") or ""
        text = re.sub(r"\s+", " ", f"{title} {summary}").strip()
        competitor = match_competitor(text, self.competitors, source.get("competitor_id", ""))
        competitor_keywords: List[str] = []
        if competitor:
            competitor_keywords = unique_strings(
                [
                    competitor.get("vendor", ""),
                    competitor.get("product", ""),
                    *competitor.get("aliases", []),
                    *competitor.get("monitor_keywords", []),
                ]
            )
        hits = keyword_hits(
            text,
            [
                *self.config.get("global_ai_keywords", []),
                *category.get("keywords", []),
                *source.get("monitor_keywords", []),
                *competitor_keywords,
            ],
        )
        if not hits and source.get("require_keyword_match", True):
            return None
        stage = detect_stage(text, self.config.get("procurement_stage_keywords", {}))
        budget_text, budget_cny = extract_budget(text)
        companies = keyword_hits(text, self.tracked_company_keywords())
        if competitor:
            companies = unique_strings([*companies, competitor["vendor"], competitor["product"]])
        score = score_signal(
            int(category.get("weight", 3)),
            source.get("evidence_level", "未知"),
            stage,
            budget_cny,
            companies,
            len(hits),
        )
        return ProcurementSignal(
            title=title,
            summary=summary,
            source_name=source.get("name", "unknown"),
            source_url=raw.get("link") or source.get("url", ""),
            published_at=raw.get("published_at"),
            collected_at=datetime.now(ZoneInfo(self.timezone)).replace(microsecond=0).isoformat(),
            signal_category=category_key,
            category_label=category.get("label", category_key),
            discoverable_insight=category.get("discoverable", "待判断"),
            evidence_level=source.get("evidence_level", "未知"),
            procurement_stage=stage,
            budget_text=budget_text,
            budget_cny=budget_cny,
            tracked_companies=companies,
            matched_keywords=hits[:20],
            score=score,
            confidence=confidence_level(score, source.get("evidence_level", "未知"), stage, budget_cny),
            recommended_action=category.get("recommended_action", "进入周复盘并交叉验证"),
            competitor_id=competitor.get("id", "") if competitor else "",
            competitor_vendor=competitor.get("vendor", "") if competitor else "",
            competitor_product=competitor.get("product", "") if competitor else "",
            competitor_relevance=competitor.get("why_relevant", "") if competitor else "",
            competitor_dimensions=competitor.get("strategic_focus", []) if competitor else [],
        )

    def collect(self) -> List[ProcurementSignal]:
        signals: List[ProcurementSignal] = []
        for source in self.resolve_sources():
            feed_url = source_feed_url(source, self.rsshub_base)
            if not feed_url:
                print(f"[WARN] no feed available: {source.get('name')}", file=sys.stderr)
                continue
            try:
                items = parse_rss_or_atom(fetch_text(feed_url))
            except Exception as exc:
                print(f"[WARN] collect failed: {source.get('name')} -> {exc}", file=sys.stderr)
                continue
            for raw in items[: self.limit_per_source]:
                signal = self.analyze(source, feed_url, raw)
                if signal:
                    signals.append(signal)
            time.sleep(float(self.config.get("sleep_seconds", 0.2)))
        signals.sort(key=lambda item: (item.score, item.published_at or ""), reverse=True)
        return self.deduplicate(signals)

    @staticmethod
    def deduplicate(signals: Iterable[ProcurementSignal]) -> List[ProcurementSignal]:
        seen: set[Tuple[str, str, str]] = set()
        result: List[ProcurementSignal] = []
        for signal in signals:
            key = (
                re.sub(r"\W+", "", signal.title.lower())[:120],
                signal.signal_category,
                signal.competitor_id,
            )
            if key not in seen:
                seen.add(key)
                result.append(signal)
        return result

    def competitor_coverage(self, signals: Sequence[ProcurementSignal]) -> Dict[str, int]:
        return {
            competitor["id"]: sum(signal.competitor_id == competitor["id"] for signal in signals)
            for competitor in self.competitors
        }

    def generate_report(self, signals: List[ProcurementSignal]) -> str:
        coverage = self.competitor_coverage(signals)
        covered = sum(count > 0 for count in coverage.values())
        lines = [
            f"# AI 竞品与采购情报日报 - {self.date_text}",
            "",
            "## 今日概览",
            f"- 有效信号：{len(signals)}",
            f"- 高可信信号：{sum(item.confidence == 'high' for item in signals)}",
            f"- 明确预算：{sum(item.budget_cny is not None for item in signals)}",
            f"- 采购阶段信号：{sum(item.procurement_stage != 'unknown' for item in signals)}",
            f"- 重点竞品覆盖：{covered}/{len(self.competitors)}",
            "",
            "## 10 个重点竞品覆盖",
        ]
        for index, competitor in enumerate(self.competitors, 1):
            lines.extend(
                [
                    f"### {index}. {competitor['product']}（{competitor['vendor']}）",
                    f"- 今日信号：{coverage[competitor['id']]} 条",
                    f"- 定位：{competitor.get('positioning', '')}",
                    f"- 与你的关系：{competitor.get('why_relevant', '')}",
                    f"- 重点观察：{' / '.join(competitor.get('strategic_focus', []))}",
                ]
            )
        lines.extend(["", "## 十类信号覆盖"])
        for key, category in self.config.get("categories", {}).items():
            count = sum(item.signal_category == key for item in signals)
            lines.append(f"- {category.get('label')}：{count} 条；可发现：{category.get('discoverable')}")
        lines.extend(["", "## Top 竞品与采购信号"])
        for index, item in enumerate(signals[: self.top_n], 1):
            lines.extend(
                [
                    "",
                    f"### {index}. {item.title}",
                    f"- 竞品：{item.competitor_product or '未归属'}",
                    f"- 类别：{item.category_label}",
                    f"- 分数/可信度：{item.score} / {item.confidence}",
                    f"- 来源：{item.source_name}（{item.evidence_level}）",
                    f"- 采购阶段：{item.procurement_stage}",
                    f"- 预算：{item.budget_text or '未识别'}",
                    f"- 可发现：{item.discoverable_insight}",
                    f"- 与你的关系：{item.competitor_relevance or '作为行业或采购信号进入复盘'}",
                    f"- 建议动作：{item.recommended_action}",
                    f"- 链接：{item.source_url}",
                    f"- 摘要：{item.summary or '暂无摘要'}",
                ]
            )
        lines.extend(
            [
                "",
                "## 使用原则",
                "- 这 10 个竞品用于研究企业 AI 如何进入真实业务流程，不按模型榜单排序。",
                "- 招聘、社媒和搜索趋势只能作为弱信号。",
                "- 预算、采购阶段、官方公告和年报原文优先进入机会清单。",
                "- 同一方向至少由两个独立来源验证后再形成结论。",
            ]
        )
        return "\n".join(lines) + "\n"

    def run(self, notion_parent_page_id: Optional[str], notion_token_env: str = "NOTION_TOKEN") -> Dict[str, Any]:
        signals = self.collect()
        coverage = self.competitor_coverage(signals)
        covered = sum(count > 0 for count in coverage.values())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.signals_path.open("w", encoding="utf-8") as f:
            for item in signals:
                f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        self.report_path.write_text(self.generate_report(signals), encoding="utf-8")
        summary: Dict[str, Any] = {
            "date": self.date_text,
            "counts": {
                "signals": len(signals),
                "high_confidence": sum(item.confidence == "high" for item in signals),
                "with_budget": sum(item.budget_cny is not None for item in signals),
                "procurement_leads": sum(item.procurement_stage != "unknown" for item in signals),
                "watchlist_competitors": len(self.competitors),
                "competitors_with_signals": covered,
                "competitor_coverage_rate": round(covered / len(self.competitors), 4),
            },
            "competitor_coverage": [
                {
                    "id": competitor["id"],
                    "vendor": competitor["vendor"],
                    "product": competitor["product"],
                    "signal_count": coverage[competitor["id"]],
                    "why_relevant": competitor.get("why_relevant", ""),
                    "strategic_focus": competitor.get("strategic_focus", []),
                }
                for competitor in self.competitors
            ],
            "paths": {
                "signals": str(self.signals_path.relative_to(PROJECT_ROOT)),
                "report": str(self.report_path.relative_to(PROJECT_ROOT)),
                "summary": str(self.summary_path.relative_to(PROJECT_ROOT)),
            },
            "top_signals": [asdict(item) for item in signals[: self.top_n],
            "notion": {"enabled": False},
        }
        if notion_parent_page_id:
            token = os.getenv(notion_token_env)
            if token:
                page_id = export_report_to_page(
                    NotionClient(token=token),
                    notion_parent_page_id,
                    self.report_path,
                    f"AI 竞品与采购情报日报 - {self.date_text}",
                )
                summary["notion"] = {"enabled": True, "success": True, "report_page": page_id}
            else:
                summary["notion"] = {
                    "enabled": True,
                    "success": False,
                    "error": f"Missing Notion token env: {notion_token_env}",
                }
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 竞品与采购情报雷达")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST_PATH))
    parser.add_argument("--date", default="today")
    parser.add_argument("--rsshub-base", default=os.getenv("RSSHUB_BASE_URL", "https://rsshub.app"))
    parser.add_argument("--limit-per-source", type=int, default=5)
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--notion-parent-page-id",
        default=os.getenv("NOTION_COMPETITIVE_PARENT_PAGE_ID") or os.getenv("NOTION_PARENT_PAGE_ID"),
    )
    parser.add_argument("--notion-token-env", default="NOTION_TOKEN")
    return parser


def main() -> None:
    load_env()
    args = build_parser().parse_args()
    radar = CompetitiveProcurementRadar(
        config_path=Path(args.config),
        watchlist_path=Path(args.watchlist),
        date_text=args.date,
        rsshub_base=args.rsshub_base,
        limit_per_source=args.limit_per_source,
        max_sources=args.max_sources,
        top_n=args.top_n,
    )
    print(
        json.dumps(
            radar.run(args.notion_parent_page_id, args.notion_token_env),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
