"""AI/Agent Intelligence Radar Rule Engine.

输入一条新闻/公告/招聘/JD/社媒信号，输出：
1. 事件类型 event_types
2. 多维评分 scores
3. 推荐动作 recommended_actions
4. 洞察 insight
5. 日报 Markdown 条目

设计原则：
- 不依赖大模型，先用规则引擎跑通 MVP。
- 规则全部从 config/*.json 读取，方便后续扩展。
- 区分事实、判断、行动，避免把社媒传闻当成事实。
- 面向唐文怡当前目标：AI 产品经理求职、Agent 产品判断、商业化机会、合规预警。

Example:
    python src/rule_engine.py --title "豆包部分智能体下架" --text "..." --source "晚点 LatePost 公众号 RSSHub" --url "https://example.com"

    echo '{"title":"OpenAI Agents SDK 更新", "text":"新增 tracing 和 handoff 能力", "source_name":"OpenAI Agents SDK GitHub Releases"}' \
      | python src/rule_engine.py --stdin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# -----------------------------
# Path & Config Loading
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


def load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON config file with UTF-8 encoding."""
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class RadarConfigs:
    sources: Dict[str, Any]
    scoring_rules: Dict[str, Any]
    event_types: Dict[str, Any]
    output_schema: Dict[str, Any]
    personal_focus: Dict[str, Any]

    @classmethod
    def load(cls, config_dir: Path = CONFIG_DIR) -> "RadarConfigs":
        return cls(
            sources=load_json(config_dir / "sources.json"),
            scoring_rules=load_json(config_dir / "scoring_rules.json"),
            event_types=load_json(config_dir / "event_types.json"),
            output_schema=load_json(config_dir / "output_schema.json"),
            personal_focus=load_json(config_dir / "personal_focus.json"),
        )


# -----------------------------
# Data Models
# -----------------------------

@dataclass
class NewsInput:
    title: str
    text: str = ""
    source_name: str = "unknown"
    source_url: str = ""
    published_at: Optional[str] = None
    collected_at: Optional[str] = None
    channel: Optional[str] = None
    evidence_level: Optional[str] = None
    language: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoreResult:
    freshness_score: int
    evidence_score: int
    trend_score: int
    business_score: int
    personal_relevance_score: int
    risk_score: int
    final_score: float


@dataclass
class InsightResult:
    decision_value: str
    business_opportunity: str
    compliance_warning: str
    competitor_signal: str
    interview_angle: str
    follow_up_questions: List[str]
    evidence_note: str


@dataclass
class EvaluationResult:
    item_id: str
    title: str
    source_name: str
    source_url: str
    published_at: Optional[str]
    collected_at: str
    channel: str
    event_types: List[str]
    primary_event_type: str
    evidence_level: str
    language: str
    clean_summary: str
    why_it_matters: str
    product_insight: str
    career_insight: str
    risk_insight: str
    scores: ScoreResult
    recommended_actions: List[str]
    tags: List[str]
    insight: InsightResult
    markdown: str
    status: str = "new"


# -----------------------------
# Utilities
# -----------------------------

ZH_EN_SPLIT_RE = re.compile(r"[\s,，。；;：:\n\t\-_/|（）()\[\]【】]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    candidates = [
        value,
        value.replace("Z", "+00:00"),
    ]
    date_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    for fmt in date_formats:
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def clamp_score(score: float, min_score: int = 1, max_score: int = 5) -> int:
    return max(min_score, min(max_score, int(round(score))))


def normalize_text(text: str) -> str:
    return (text or "").lower()


def count_keyword_hits(text: str, keywords: Sequence[str]) -> Tuple[int, List[str]]:
    normalized = normalize_text(text)
    hits: List[str] = []
    for keyword in keywords:
        if not keyword:
            continue
        if normalize_text(keyword) in normalized:
            hits.append(keyword)
    return len(hits), sorted(set(hits), key=lambda x: normalize_text(x))


def unique_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def short_summary(text: str, max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return "暂无正文，仅根据标题和来源进行规则判断。"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def hash_id(*parts: str) -> str:
    raw = "|".join([p or "" for p in parts])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# -----------------------------
# Rule Engine
# -----------------------------

class IntelligenceRuleEngine:
    """Insight-oriented rule engine for AI/Agent intelligence."""

    def __init__(self, configs: Optional[RadarConfigs] = None) -> None:
        self.configs = configs or RadarConfigs.load()
        self.sources_by_name = {
            item.get("name", ""): item for item in self.configs.sources.get("sources", [])
        }
        self.event_type_items = self.configs.event_types.get("event_types", [])
        self.event_type_by_id = {
            item.get("id", ""): item for item in self.event_type_items
        }
        self.event_priority_order = self.configs.event_types.get("classification_rules", {}).get(
            "priority_order", []
        )
        self.focus_topics = self.configs.personal_focus.get("focus_topics", [])

    def evaluate(self, item: NewsInput | Dict[str, Any]) -> EvaluationResult:
        news = self._coerce_input(item)
        source = self._resolve_source(news)
        text_bundle = self._build_text_bundle(news, source)

        event_types, event_hits = self.classify_event_types(text_bundle)
        primary_event_type = self.choose_primary_event_type(event_types)
        tags, focus_hits = self.extract_tags(text_bundle, event_types)
        scores = self.score(news, source, text_bundle, event_types, focus_hits)
        recommended_actions = self.recommend_actions(scores, event_types, focus_hits)
        insight = self.generate_insight(news, source, text_bundle, event_types, scores, focus_hits)

        clean = self.clean_summary(news, source, event_types, event_hits, focus_hits)
        why = insight.decision_value
        product = insight.business_opportunity
        career = insight.interview_angle
        risk = insight.compliance_warning
        collected_at = news.collected_at or now_iso()
        item_id = hash_id(news.title, news.source_url, news.published_at or collected_at)
        markdown = self.render_markdown(
            title=news.title,
            source_name=source.get("name", news.source_name),
            source_url=news.source_url or source.get("url", ""),
            primary_event_type=primary_event_type,
            scores=scores,
            clean_summary=clean,
            why_it_matters=why,
            product_insight=product,
            career_insight=career,
            risk_insight=risk,
            recommended_actions=recommended_actions,
        )

        return EvaluationResult(
            item_id=item_id,
            title=news.title,
            source_name=source.get("name", news.source_name),
            source_url=news.source_url or source.get("url", ""),
            published_at=news.published_at,
            collected_at=collected_at,
            channel=news.channel or source.get("channel", "未分类"),
            event_types=event_types,
            primary_event_type=primary_event_type,
            evidence_level=news.evidence_level or source.get("evidence_level", "未知"),
            language=news.language or source.get("language", "unknown"),
            clean_summary=clean,
            why_it_matters=why,
            product_insight=product,
            career_insight=career,
            risk_insight=risk,
            scores=scores,
            recommended_actions=recommended_actions,
            tags=tags,
            insight=insight,
            markdown=markdown,
            status=self._status_from_score(scores.final_score),
        )

    def _coerce_input(self, item: NewsInput | Dict[str, Any]) -> NewsInput:
        if isinstance(item, NewsInput):
            return item
        return NewsInput(
            title=item.get("title") or item.get("headline") or "未命名情报",
            text=item.get("text") or item.get("content") or item.get("summary") or "",
            source_name=item.get("source_name") or item.get("source") or "unknown",
            source_url=item.get("source_url") or item.get("url") or "",
            published_at=item.get("published_at") or item.get("published") or item.get("date"),
            collected_at=item.get("collected_at"),
            channel=item.get("channel"),
            evidence_level=item.get("evidence_level"),
            language=item.get("language"),
            extra={k: v for k, v in item.items() if k not in {
                "title", "headline", "text", "content", "summary", "source_name", "source",
                "source_url", "url", "published_at", "published", "date", "collected_at",
                "channel", "evidence_level", "language",
            }},
        )

    def _resolve_source(self, news: NewsInput) -> Dict[str, Any]:
        if news.source_name in self.sources_by_name:
            return self.sources_by_name[news.source_name]
        # Fuzzy resolve by source URL domain/name fragment.
        source_url = normalize_text(news.source_url)
        source_name = normalize_text(news.source_name)
        for source in self.configs.sources.get("sources", []):
            if source_name and source_name in normalize_text(source.get("name", "")):
                return source
            if source_url and normalize_text(source.get("url", "")) and normalize_text(source.get("url", "")) in source_url:
                return source
        return {
            "name": news.source_name or "unknown",
            "channel": news.channel or "未分类",
            "evidence_level": news.evidence_level or "未知",
            "priority": 1,
            "language": news.language or "unknown",
            "stability": "unknown",
            "monitor_keywords": [],
            "url": news.source_url,
        }

    def _build_text_bundle(self, news: NewsInput, source: Dict[str, Any]) -> str:
        parts = [
            news.title,
            news.text,
            news.source_name,
            source.get("name", ""),
            source.get("channel", ""),
            source.get("evidence_level", ""),
            " ".join(source.get("monitor_keywords", [])),
        ]
        return "\n".join([p for p in parts if p])

    def classify_event_types(self, text_bundle: str) -> Tuple[List[str], Dict[str, List[str]]]:
        scored: List[Tuple[str, int, int]] = []
        hits_by_type: Dict[str, List[str]] = {}
        priority_index = {eid: idx for idx, eid in enumerate(self.event_priority_order)}

        for event in self.event_type_items:
            eid = event.get("id", "other")
            keywords = event.get("keywords", [])
            hit_count, hits = count_keyword_hits(text_bundle, keywords)
            if hit_count > 0:
                default_priority = int(event.get("default_priority", 1))
                scored.append((eid, hit_count, default_priority))
                hits_by_type[eid] = hits

        if not scored:
            return [self.configs.event_types.get("default_event_type", "other")], {}

        scored.sort(
            key=lambda x: (
                -x[2],
                -x[1],
                priority_index.get(x[0], 999),
            )
        )
        max_labels = self.configs.event_types.get("classification_rules", {}).get("max_labels_per_item", 3)
        event_types = [eid for eid, _, _ in scored[:max_labels]]
        return event_types, hits_by_type

    def choose_primary_event_type(self, event_types: Sequence[str]) -> str:
        if not event_types:
            return "other"
        for eid in self.event_priority_order:
            if eid in event_types:
                return eid
        return event_types[0]

    def extract_tags(self, text_bundle: str, event_types: Sequence[str]) -> Tuple[List[str], Dict[str, List[str]]]:
        tags: List[str] = list(event_types)
        focus_hits: Dict[str, List[str]] = {}
        for topic in self.focus_topics:
            topic_id = topic.get("id", "")
            keywords = topic.get("keywords", [])
            hit_count, hits = count_keyword_hits(text_bundle, keywords)
            if hit_count:
                tags.append(topic.get("name", topic_id))
                focus_hits[topic_id] = hits
        return unique_keep_order(tags), focus_hits

    # -----------------------------
    # Scoring
    # -----------------------------

    def score(
        self,
        news: NewsInput,
        source: Dict[str, Any],
        text_bundle: str,
        event_types: Sequence[str],
        focus_hits: Dict[str, List[str]],
    ) -> ScoreResult:
        freshness = self._score_freshness(news)
        evidence = self._score_evidence(news, source)
        trend = self._score_trend(source, event_types, text_bundle)
        business = self._score_business(event_types, text_bundle, focus_hits)
        personal = self._score_personal_relevance(event_types, focus_hits, text_bundle)
        risk = self._score_risk(event_types, text_bundle)

        weights = self.configs.scoring_rules.get("final_score_formula", {})
        final_score = (
            freshness * float(weights.get("freshness_score", 0.15))
            + evidence * float(weights.get("evidence_score", 0.20))
            + trend * float(weights.get("trend_score", 0.20))
            + business * float(weights.get("business_score", 0.20))
            + personal * float(weights.get("personal_relevance_score", 0.20))
            + risk * float(weights.get("risk_score", 0.05))
        )
        return ScoreResult(
            freshness_score=freshness,
            evidence_score=evidence,
            trend_score=trend,
            business_score=business,
            personal_relevance_score=personal,
            risk_score=risk,
            final_score=round(final_score, 2),
        )

    def _score_freshness(self, news: NewsInput) -> int:
        published = parse_datetime(news.published_at) or parse_datetime(news.collected_at)
        if not published:
            return 3
        delta_days = max(0, (datetime.now(timezone.utc) - published).total_seconds() / 86400)
        if delta_days <= 1:
            return 5
        if delta_days <= 3:
            return 4
        if delta_days <= 7:
            return 3
        if delta_days <= 30:
            return 2
        return 1

    def _score_evidence(self, news: NewsInput, source: Dict[str, Any]) -> int:
        evidence = news.evidence_level or source.get("evidence_level", "")
        source_type = source.get("source_type", "")
        stability = source.get("stability", "")
        text = normalize_text(" ".join([evidence, source_type, stability, source.get("name", "")]))
        if any(k in text for k in ["官方原文", "policy", "standard", "gov", "监管机构"]):
            return 5
        if any(k in text for k in ["公司公告", "平台公告", "github", "release", "changelog", "产品更新日志"]):
            return 4
        if any(k in text for k in ["媒体", "reuters", "techcrunch", "mit", "晚点", "机器之心", "量子位"]):
            return 3
        if any(k in text for k in ["招聘", "jd", "社区", "wechat"]):
            return 2
        if any(k in text for k in ["社媒", "x", "twitter", "即刻", "小红书", "弱信号"]):
            return 1
        return 2

    def _score_trend(self, source: Dict[str, Any], event_types: Sequence[str], text_bundle: str) -> int:
        high_trend_events = {"policy_regulation", "shutdown_or_removal", "security_safety_risk", "pricing_change"}
        medium_trend_events = {"product_launch", "feature_update", "open_source_release", "standard_update"}
        source_priority = int(source.get("priority", 1) or 1)
        if any(e in high_trend_events for e in event_types):
            return 5
        if any(e in medium_trend_events for e in event_types):
            return clamp_score(max(3, source_priority))
        hit_count, _ = count_keyword_hits(
            text_bundle,
            ["platform", "生态", "监管", "商业化", "enterprise", "workflow", "agent", "智能体", "趋势"],
        )
        return clamp_score(1 + hit_count + source_priority / 2)

    def _score_business(self, event_types: Sequence[str], text_bundle: str, focus_hits: Dict[str, List[str]]) -> int:
        business_events = {"pricing_change", "funding_commercialization", "hiring_signal", "case_study", "shutdown_or_removal"}
        if any(e in business_events for e in event_types):
            base = 4
        elif any(e in {"product_launch", "feature_update", "policy_regulation"} for e in event_types):
            base = 3
        else:
            base = 2
        business_terms = [
            "商业化", "定价", "价格", "套餐", "客户", "付费", "收入", "融资", "岗位", "招聘",
            "pricing", "revenue", "customer", "enterprise", "subscription", "monetization", "ARR", "rate limit",
        ]
        hit_count, _ = count_keyword_hits(text_bundle, business_terms)
        focus_bonus = 1 if any(k in focus_hits for k in ["commercial_pm", "api_gateway", "workflow_automation"]) else 0
        return clamp_score(base + min(hit_count, 2) * 0.5 + focus_bonus)

    def _score_personal_relevance(
        self,
        event_types: Sequence[str],
        focus_hits: Dict[str, List[str]],
        text_bundle: str,
    ) -> int:
        if not focus_hits:
            return 2 if "AI" in text_bundle or "ai" in normalize_text(text_bundle) else 1
        weights = []
        topic_by_id = {t.get("id"): t for t in self.focus_topics}
        for topic_id in focus_hits:
            weights.append(int(topic_by_id.get(topic_id, {}).get("weight", 1)))
        max_weight = max(weights) if weights else 1
        strong_count = sum(1 for w in weights if w >= 4)
        if max_weight >= 5 and strong_count >= 2:
            return 5
        if max_weight >= 5 or strong_count >= 2:
            return 4
        if focus_hits:
            return 3
        return 2

    def _score_risk(self, event_types: Sequence[str], text_bundle: str) -> int:
        if "shutdown_or_removal" in event_types:
            return 5
        if "security_safety_risk" in event_types or "policy_regulation" in event_types:
            return 4
        risk_terms = [
            "下架", "整改", "处罚", "关闭", "停服", "限制", "禁用", "备案", "合规", "隐私",
            "未成年人", "拟人化", "内容安全", "jailbreak", "prompt injection", "privacy", "safety",
            "remove", "shutdown", "suspend", "deprecate", "risk",
        ]
        hit_count, _ = count_keyword_hits(text_bundle, risk_terms)
        if hit_count >= 3:
            return 5
        if hit_count >= 2:
            return 4
        if hit_count == 1:
            return 3
        return 1

    # -----------------------------
    # Actions & Insights
    # -----------------------------

    def recommend_actions(
        self,
        scores: ScoreResult,
        event_types: Sequence[str],
        focus_hits: Dict[str, List[str]],
    ) -> List[str]:
        actions: List[str] = []
        for eid in event_types:
            event = self.event_type_by_id.get(eid, {})
            actions.extend(event.get("recommended_actions", []))

        if scores.final_score >= 3.5:
            actions.append("save_to_knowledge_base")
        if scores.personal_relevance_score >= 4 and scores.business_score >= 4:
            actions.append("convert_to_interview_story")
        if scores.risk_score >= 4 or scores.trend_score >= 4:
            actions.append("track_follow_up")
        if scores.trend_score >= 4 and scores.business_score >= 3:
            actions.append("create_product_insight")
        if scores.final_score < 2:
            actions.append("ignore")
        return unique_keep_order(actions)

    def generate_insight(
        self,
        news: NewsInput,
        source: Dict[str, Any],
        text_bundle: str,
        event_types: Sequence[str],
        scores: ScoreResult,
        focus_hits: Dict[str, List[str]],
    ) -> InsightResult:
        primary = self.choose_primary_event_type(event_types)
        evidence = news.evidence_level or source.get("evidence_level", "未知")
        source_name = source.get("name", news.source_name)

        decision_value = self._decision_value(primary, scores, source_name)
        business_opportunity = self._business_opportunity(primary, scores, focus_hits)
        compliance_warning = self._compliance_warning(primary, scores, evidence)
        competitor_signal = self._competitor_signal(primary, text_bundle, source_name)
        interview_angle = self._interview_angle(primary, scores, focus_hits)
        follow_up_questions = self._follow_up_questions(primary, scores)
        evidence_note = self._evidence_note(evidence, source)

        return InsightResult(
            decision_value=decision_value,
            business_opportunity=business_opportunity,
            compliance_warning=compliance_warning,
            competitor_signal=competitor_signal,
            interview_angle=interview_angle,
            follow_up_questions=follow_up_questions,
            evidence_note=evidence_note,
        )

    def _decision_value(self, primary: str, scores: ScoreResult, source_name: str) -> str:
        event_name = self.event_type_by_id.get(primary, {}).get("name", primary)
        if scores.final_score >= 4.2:
            return f"这是高优先级情报，属于「{event_name}」。它不只是资讯，而是可能影响产品方向、合规边界或商业化判断的强信号。来源为 {source_name}，建议进入日报 Top 5。"
        if scores.final_score >= 3.5:
            return f"这是值得进入日报的情报，属于「{event_name}」。它对趋势判断或产品复盘有参考价值，建议归档并观察后续变化。"
        if scores.final_score >= 2.8:
            return f"这是周报级情报，属于「{event_name}」。适合作为背景材料，不需要当天重点处理。"
        return f"这是低优先级情报，属于「{event_name}」。除非后续出现官方确认或多源交叉信号，否则不建议占用太多注意力。"

    def _business_opportunity(self, primary: str, scores: ScoreResult, focus_hits: Dict[str, List[str]]) -> str:
        if primary in {"pricing_change", "funding_commercialization"}:
            return "这类信息直接影响商业化判断：可观察客户是否愿意付费、厂商如何定价、市场是否在验证某类 Agent/AI 工具。"
        if primary == "shutdown_or_removal":
            return "下架/整改不是单纯负面，它可能暴露出新的合规产品机会：审核、风控、内容安全、企业准入、Agent 发布治理。"
        if primary == "policy_regulation":
            return "政策变化会重塑商业边界。适合转化为 B 端合规预警、企业 AI 使用规范、Agent 上架审核流程等服务机会。"
        if primary in {"product_launch", "feature_update", "open_source_release"}:
            return "产品/开源更新可用于判断能力地图变化：哪些能力正在标准化，哪些能力正在从技术特性变成可售卖功能。"
        if scores.business_score >= 4:
            return "该信息有较强商业参考价值，建议进一步拆解客户、痛点、付费理由和最小验证方式。"
        return "商业价值暂时偏间接，建议作为趋势背景沉淀，不急于转成具体机会。"

    def _compliance_warning(self, primary: str, scores: ScoreResult, evidence: str) -> str:
        if scores.risk_score >= 5:
            return f"强风险信号。涉及下架、整改、监管或安全边界变化，不能只看热闹，要追踪官方原文和后续平台动作。当前证据等级：{evidence}。"
        if scores.risk_score >= 4:
            return f"中高风险信号。适合纳入合规观察清单，尤其关注内容安全、拟人化、未成年人、隐私、API 权限等边界。当前证据等级：{evidence}。"
        if primary in {"market_opinion", "hiring_signal"}:
            return f"这类信息更多是弱信号，不能直接当事实结论，需要等待官方公告、可信媒体或多源交叉验证。当前证据等级：{evidence}。"
        return "暂未发现明显合规风险，但仍建议保留原文链接，便于后续交叉验证。"

    def _competitor_signal(self, primary: str, text_bundle: str, source_name: str) -> str:
        vendors = ["OpenAI", "Anthropic", "Google", "Dify", "Coze", "扣子", "豆包", "通义", "百度", "腾讯", "智谱", "Kimi", "Cursor", "LangChain"]
        _, vendor_hits = count_keyword_hits(text_bundle, vendors)
        vendor_text = "、".join(vendor_hits) if vendor_hits else source_name
        if primary in {"product_launch", "feature_update", "pricing_change", "shutdown_or_removal"}:
            return f"这是竞品/平台动作信号，涉及 {vendor_text}。建议拆解其目标用户、功能边界、定价/权限变化，以及对普通 Agent 公司的影响。"
        if primary == "hiring_signal":
            return "招聘信息是战略弱信号：如果同类岗位持续增加，说明企业正在投入对应方向，可以反推赛道热度和能力要求。"
        return f"当前竞品信号不强，但可作为 {vendor_text} 的长期观察材料。"

    def _interview_angle(self, primary: str, scores: ScoreResult, focus_hits: Dict[str, List[str]]) -> str:
        if scores.personal_relevance_score >= 4 and primary in {"policy_regulation", "shutdown_or_removal", "security_safety_risk"}:
            return "可转成面试表达：我不仅关注功能上线，还会关注政策、平台规则和安全边界，能提前识别 AI 产品的合规风险和商业化约束。"
        if scores.personal_relevance_score >= 4 and primary in {"product_launch", "feature_update", "open_source_release"}:
            return "可转成面试表达：我会把竞品更新拆成能力地图、用户价值、交付成本和可复用产品方案，而不是只停留在资讯层。"
        if primary == "hiring_signal":
            return "可转成求职判断：岗位 JD 反映市场真实需求，可用于调整简历关键词、面试案例和能力补齐路线。"
        return "暂时更适合作为行业背景材料，不一定要放进核心面试案例。"

    def _follow_up_questions(self, primary: str, scores: ScoreResult) -> List[str]:
        base = [
            "是否有官方原文或一手公告可以确认？",
            "是否有第二个独立来源验证同一趋势？",
        ]
        if primary in {"policy_regulation", "shutdown_or_removal", "security_safety_risk"}:
            base.extend([
                "这个变化会影响哪些 Agent/AI 产品上架或运营流程？",
                "普通 AI 公司需要补哪些合规模块或流程？",
            ])
        if primary in {"product_launch", "feature_update", "open_source_release"}:
            base.extend([
                "这个能力是否会变成平台标配？",
                "它能否转化为企业客户愿意付费的功能？",
            ])
        if scores.business_score >= 4:
            base.append("这条信息能否沉淀成收费报告、竞品拆解或企业咨询服务？")
        return unique_keep_order(base)

    def _evidence_note(self, evidence: str, source: Dict[str, Any]) -> str:
        stability = source.get("stability", "unknown")
        if "社媒" in evidence or "弱信号" in evidence or stability == "low":
            return "该来源稳定性或证据等级偏弱，必须交叉验证后再输出结论。"
        if "官方" in evidence or "公告" in evidence:
            return "该来源可信度较高，可作为事实依据，但仍建议保留原文链接。"
        return "该来源可作为参考，需要结合官方源或多个媒体源验证。"

    # -----------------------------
    # Summary & Rendering
    # -----------------------------

    def clean_summary(
        self,
        news: NewsInput,
        source: Dict[str, Any],
        event_types: Sequence[str],
        event_hits: Dict[str, List[str]],
        focus_hits: Dict[str, List[str]],
    ) -> str:
        event_names = [self.event_type_by_id.get(eid, {}).get("name", eid) for eid in event_types]
        hit_words = unique_keep_order([
            word for words in list(event_hits.values()) + list(focus_hits.values()) for word in words
        ])[:8]
        hit_text = "、".join(hit_words) if hit_words else "暂无明显关键词命中"
        body = short_summary(news.text, 160)
        return (
            f"{news.title}。来源：{source.get('name', news.source_name)}；"
            f"类型：{' / '.join(event_names)}；关键词：{hit_text}。"
            f"内容摘要：{body}"
        )

    def render_markdown(
        self,
        title: str,
        source_name: str,
        source_url: str,
        primary_event_type: str,
        scores: ScoreResult,
        clean_summary: str,
        why_it_matters: str,
        product_insight: str,
        career_insight: str,
        risk_insight: str,
        recommended_actions: Sequence[str],
    ) -> str:
        event_name = self.event_type_by_id.get(primary_event_type, {}).get("name", primary_event_type)
        actions = "、".join(recommended_actions) if recommended_actions else "暂无"
        url_line = f"\n- 原文：{source_url}" if source_url else ""
        return (
            f"### {title}\n"
            f"**评分：{scores.final_score}｜来源：{source_name}｜类型：{event_name}**\n\n"
            f"- 事实摘要：{clean_summary}\n"
            f"- 为什么重要：{why_it_matters}\n"
            f"- 产品/商业洞察：{product_insight}\n"
            f"- 合规/风险提示：{risk_insight}\n"
            f"- 面试/职业表达：{career_insight}\n"
            f"- 建议动作：{actions}"
            f"{url_line}\n"
        )

    def _status_from_score(self, final_score: float) -> str:
        if final_score >= 4.2:
            return "follow_up"
        if final_score >= 3.5:
            return "reviewed"
        if final_score >= 2.0:
            return "archived"
        return "ignored"


def result_to_dict(result: EvaluationResult) -> Dict[str, Any]:
    data = asdict(result)
    return data


# -----------------------------
# CLI
# -----------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI/Agent 情报雷达规则引擎")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取 JSON 输入")
    parser.add_argument("--title", default="", help="新闻/公告标题")
    parser.add_argument("--text", default="", help="新闻/公告正文或摘要")
    parser.add_argument("--source", dest="source_name", default="unknown", help="来源名称，对应 sources.json 的 name")
    parser.add_argument("--url", dest="source_url", default="", help="原文链接")
    parser.add_argument("--published-at", default=None, help="发布时间，如 2026-07-05T10:00:00+08:00")
    parser.add_argument("--json-only", action="store_true", help="只输出 JSON，不输出 Markdown")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.stdin:
        raw = input()
        payload = json.loads(raw)
    else:
        payload = {
            "title": args.title or "未命名情报",
            "text": args.text,
            "source_name": args.source_name,
            "source_url": args.source_url,
            "published_at": args.published_at,
        }

    engine = IntelligenceRuleEngine()
    result = engine.evaluate(payload)
    data = result_to_dict(result)

    if args.json_only:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(result.markdown)
        print("\n--- JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
