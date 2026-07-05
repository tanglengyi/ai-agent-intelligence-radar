"""Quality filter for AI Agent Intelligence Radar.

作用：
1. 把 RSS 噪音挡在 Notion 外面；
2. 只保留能形成判断、预警、竞品信号、机会清单或面试案例的内容；
3. 给每条被过滤的信息一个 reason，方便复盘规则。

注意：
- RSS 抓取可以多，Notion 入库必须少。
- Notion 是资产库，不是原始资讯池。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class FilterDecision:
    keep: bool
    reason: str
    quality_score: float


def load_noise_filters(config_dir: Path = CONFIG_DIR) -> Dict[str, Any]:
    path = config_dir / "noise_filters.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def score_of_item(item: Dict[str, Any]) -> float:
    if "max_final_score" in item:
        return float(item.get("max_final_score") or 0)
    return float((item.get("scores") or {}).get("final_score") or 0)


def confidence_of_item(item: Dict[str, Any]) -> str:
    return item.get("confidence_level") or "medium"


def event_types_of_item(item: Dict[str, Any]) -> List[str]:
    values = item.get("event_types") or []
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values]


def actions_of_item(item: Dict[str, Any]) -> List[str]:
    values = item.get("recommended_actions") or []
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values]


def text_blob(item: Dict[str, Any]) -> str:
    parts = [
        item.get("title", ""),
        item.get("canonical_title", ""),
        item.get("clean_summary", ""),
        item.get("merged_summary", ""),
        item.get("strategic_signal", ""),
        item.get("why_it_matters", ""),
        item.get("product_insight", ""),
        item.get("career_insight", ""),
        item.get("risk_insight", ""),
    ]
    insight = item.get("insight") or {}
    if isinstance(insight, dict):
        parts.extend(str(v) for v in insight.values() if isinstance(v, str))
    return "\n".join(p for p in parts if p)


class QualityFilter:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config if config is not None else load_noise_filters()
        self.thresholds = self.config.get("quality_thresholds", {})
        self.actionable_actions = set(self.config.get("actionable_actions", []))
        self.strong_event_types = set(self.config.get("strong_event_types", []))
        self.weak_event_types = set(self.config.get("weak_event_types", []))
        self.title_noise_keywords = self.config.get("title_noise_keywords", [])
        self.generic_sentence_blacklist = self.config.get("generic_sentence_blacklist", [])

    def filter_for_notion(self, items: Sequence[Dict[str, Any]], limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        kept: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        for item in items:
            decision = self.should_keep_for_notion(item)
            item_with_quality = dict(item)
            item_with_quality["quality_filter"] = {
                "keep": decision.keep,
                "reason": decision.reason,
                "quality_score": decision.quality_score,
            }
            if decision.keep:
                kept.append(item_with_quality)
            else:
                dropped.append(item_with_quality)

        kept.sort(key=lambda x: x.get("quality_filter", {}).get("quality_score", 0), reverse=True)
        max_items = limit or self.thresholds.get("max_items_per_day_to_notion")
        if max_items:
            overflow = kept[int(max_items):]
            for item in overflow:
                item["quality_filter"]["keep"] = False
                item["quality_filter"]["reason"] = "超过每日 Notion 入库上限，被保留在本地文件中。"
            dropped.extend(overflow)
            kept = kept[: int(max_items)]
        return kept, dropped

    def filter_for_report(self, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        min_score = float(self.thresholds.get("report_min_final_score", 3.2))
        out = []
        for item in items:
            score = score_of_item(item)
            events = set(event_types_of_item(item))
            if score >= min_score or events & self.strong_event_types:
                out.append(item)
        return sorted(out, key=score_of_item, reverse=True)

    def should_keep_for_notion(self, item: Dict[str, Any]) -> FilterDecision:
        score = score_of_item(item)
        min_score = float(self.thresholds.get("notion_min_final_score", 3.8))
        events = set(event_types_of_item(item))
        actions = set(actions_of_item(item))
        confidence = confidence_of_item(item)
        text = text_blob(item)

        quality_score = self._quality_score(item)

        # 强信号兜底：政策、下架、风险、价格变化可低分保留，但要标记待验证。
        strong_signal = bool(events & self.strong_event_types)
        actionable = bool(actions & self.actionable_actions)
        weak_only = bool(events) and events <= self.weak_event_types

        if score < min_score and not strong_signal:
            return FilterDecision(False, f"总分 {score} 低于 Notion 阈值 {min_score}，且不是强事件类型。", quality_score)

        min_conf = self.thresholds.get("min_confidence_for_notion", "medium")
        if CONFIDENCE_RANK.get(confidence, 2) < CONFIDENCE_RANK.get(min_conf, 2) and not strong_signal:
            return FilterDecision(False, f"置信度 {confidence} 低于 {min_conf}，且不是强信号。", quality_score)

        if weak_only:
            return FilterDecision(False, "仅为市场观点/弱信号，没有官方或多源确认，不进入 Notion。", quality_score)

        if self.thresholds.get("require_actionable_for_notion", True) and not actionable and not strong_signal:
            return FilterDecision(False, "没有可行动动作，不进入 Notion。", quality_score)

        if self._is_generic_template_text(text) and not strong_signal:
            return FilterDecision(False, "内容主要是模板化表述，没有具体事实增量。", quality_score)

        if self._has_title_noise(item) and score < 4.2:
            return FilterDecision(False, "标题疑似资讯噪音/标题党，且分数不够高。", quality_score)

        if strong_signal:
            return FilterDecision(True, "强事件类型，保留为可跟踪情报。", quality_score)

        return FilterDecision(True, "达到 Notion 入库质量门槛。", quality_score)

    def _quality_score(self, item: Dict[str, Any]) -> float:
        score = score_of_item(item)
        events = set(event_types_of_item(item))
        actions = set(actions_of_item(item))
        confidence = confidence_of_item(item)
        text = text_blob(item)
        quality = score
        if events & self.strong_event_types:
            quality += 0.5
        if actions & self.actionable_actions:
            quality += 0.3
        quality += 0.2 * CONFIDENCE_RANK.get(confidence, 2)
        if self._is_generic_template_text(text):
            quality -= 0.6
        if self._has_title_noise(item):
            quality -= 0.3
        return round(max(0.0, quality), 2)

    def _is_generic_template_text(self, text: str) -> bool:
        if not text:
            return True
        hit_count = sum(1 for phrase in self.generic_sentence_blacklist if phrase in text)
        concrete_markers = [
            "OpenAI", "Anthropic", "Google", "Dify", "Coze", "豆包", "扣子", "通义", "百度", "腾讯",
            "价格", "定价", "API", "下架", "整改", "发布", "更新", "招聘", "融资", "政策", "监管", "标准",
            "Agents SDK", "LangGraph", "Cursor", "NIST", "AI Act"
        ]
        concrete_count = sum(1 for marker in concrete_markers if marker.lower() in text.lower())
        return hit_count >= 2 and concrete_count <= 1

    def _has_title_noise(self, item: Dict[str, Any]) -> bool:
        title = item.get("title") or item.get("canonical_title") or ""
        return any(keyword in title for keyword in self.title_noise_keywords)
