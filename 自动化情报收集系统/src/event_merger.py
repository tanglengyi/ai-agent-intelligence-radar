"""Event merger for AI Agent Intelligence Radar.

职责：
1. 把多条新闻归并成同一事件。
2. 计算多源交叉验证信号。
3. 识别官方源 + 媒体源 + 社媒/招聘弱信号叠加后的情报强度。

为什么需要它：
单条新闻只是信息，多源同向出现才是情报。
例如：政策征求意见 + 大厂智能体下架 + AI 合规岗位增加 = 合规赛道机会。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]

STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "by",
    "ai", "人工智能", "大模型", "发布", "更新", "公告", "新闻", "快讯", "产品",
}

VENDOR_KEYWORDS = [
    "OpenAI", "Anthropic", "Google", "DeepMind", "Dify", "LangChain", "LangGraph", "LlamaIndex",
    "Coze", "扣子", "豆包", "字节", "通义", "阿里", "百度", "腾讯", "智谱", "Kimi", "Cursor",
    "NVIDIA", "OpenRouter", "LiteLLM", "vLLM", "Qwen", "ModelScope",
]

RISK_KEYWORDS = ["下架", "整改", "监管", "合规", "处罚", "关闭", "停服", "禁用", "限制", "隐私", "内容安全", "未成年人", "拟人化", "shutdown", "remove", "suspend", "risk", "safety"]
BUSINESS_KEYWORDS = ["商业化", "定价", "价格", "融资", "客户", "收入", "岗位", "招聘", "付费", "pricing", "funding", "revenue", "customer", "hiring"]


@dataclass
class MergedEvent:
    event_id: str
    canonical_title: str
    primary_event_type: str
    event_types: List[str]
    sources: List[str]
    source_urls: List[str]
    item_ids: List[str]
    evidence_levels: List[str]
    channels: List[str]
    tags: List[str]
    max_final_score: float
    average_final_score: float
    cross_source_score: int
    confidence_level: str
    merged_summary: str
    strategic_signal: str
    recommended_actions: List[str]


def normalize_text(text: str) -> str:
    return (text or "").lower()


def tokenize(text: str) -> Set[str]:
    text = normalize_text(text)
    raw_tokens = re.findall(r"[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]{2,}", text)
    return {t for t in raw_tokens if t not in STOP_WORDS and len(t) >= 2}


def keyword_hits(text: str, keywords: Sequence[str]) -> List[str]:
    n = normalize_text(text)
    return [k for k in keywords if normalize_text(k) in n]


def item_text(item: Dict[str, Any]) -> str:
    parts = [
        item.get("title", ""),
        item.get("clean_summary", ""),
        item.get("why_it_matters", ""),
        item.get("product_insight", ""),
        item.get("risk_insight", ""),
        " ".join(item.get("event_types", []) or []),
        " ".join(item.get("tags", []) or []),
    ]
    return "\n".join([p for p in parts if p])


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ta = tokenize(item_text(a))
    tb = tokenize(item_text(b))
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union = len(ta | tb)
    jaccard = overlap / union if union else 0.0

    vendors_a = set(keyword_hits(item_text(a), VENDOR_KEYWORDS))
    vendors_b = set(keyword_hits(item_text(b), VENDOR_KEYWORDS))
    vendor_bonus = 0.25 if vendors_a and vendors_a & vendors_b else 0.0

    event_overlap = set(a.get("event_types", []) or []) & set(b.get("event_types", []) or [])
    event_bonus = 0.15 if event_overlap else 0.0
    return min(1.0, jaccard + vendor_bonus + event_bonus)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def write_jsonl(items: Sequence[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


class EventMerger:
    def __init__(self, threshold: float = 0.32) -> None:
        self.threshold = threshold

    def merge(self, items: List[Dict[str, Any]]) -> List[MergedEvent]:
        clusters: List[List[Dict[str, Any]]] = []
        for item in sorted(items, key=lambda x: x.get("scores", {}).get("final_score", 0), reverse=True):
            placed = False
            for cluster in clusters:
                if max(similarity(item, member) for member in cluster) >= self.threshold:
                    cluster.append(item)
                    placed = True
                    break
            if not placed:
                clusters.append([item])
        return [self._build_event(cluster) for cluster in clusters]

    def _build_event(self, cluster: List[Dict[str, Any]]) -> MergedEvent:
        cluster = sorted(cluster, key=lambda x: x.get("scores", {}).get("final_score", 0), reverse=True)
        canonical = cluster[0]
        event_types = self._collect_unique(cluster, "event_types")
        sources = self._collect_unique(cluster, "source_name")
        urls = self._collect_unique(cluster, "source_url")
        item_ids = self._collect_unique(cluster, "item_id")
        evidence_levels = self._collect_unique(cluster, "evidence_level")
        channels = self._collect_unique(cluster, "channel")
        tags = self._collect_unique(cluster, "tags")
        actions = self._collect_unique(cluster, "recommended_actions")
        scores = [float(item.get("scores", {}).get("final_score", 0)) for item in cluster]
        cross_source_score = self._cross_source_score(cluster, evidence_levels)
        confidence = self._confidence_level(cross_source_score, evidence_levels)
        merged_text = "\n".join(item_text(i) for i in cluster)
        strategic_signal = self._strategic_signal(merged_text, event_types, sources, cross_source_score)
        event_id = self._event_id(canonical.get("title", ""), event_types, sources)

        return MergedEvent(
            event_id=event_id,
            canonical_title=canonical.get("title", "未命名事件"),
            primary_event_type=canonical.get("primary_event_type", event_types[0] if event_types else "other"),
            event_types=event_types,
            sources=sources,
            source_urls=urls,
            item_ids=item_ids,
            evidence_levels=evidence_levels,
            channels=channels,
            tags=tags,
            max_final_score=round(max(scores) if scores else 0, 2),
            average_final_score=round(sum(scores) / len(scores), 2) if scores else 0,
            cross_source_score=cross_source_score,
            confidence_level=confidence,
            merged_summary=self._merged_summary(cluster),
            strategic_signal=strategic_signal,
            recommended_actions=actions,
        )

    def _collect_unique(self, cluster: List[Dict[str, Any]], key: str) -> List[str]:
        values: List[str] = []
        for item in cluster:
            value = item.get(key)
            if isinstance(value, list):
                values.extend([str(v) for v in value if v])
            elif value:
                values.append(str(value))
        seen = set()
        out = []
        for value in values:
            if value not in seen:
                out.append(value)
                seen.add(value)
        return out

    def _cross_source_score(self, cluster: List[Dict[str, Any]], evidence_levels: List[str]) -> int:
        source_count = len(set(i.get("source_name") for i in cluster if i.get("source_name")))
        evidence_text = " ".join(evidence_levels)
        score = min(3, source_count)
        if any(k in evidence_text for k in ["官方原文", "公司公告", "平台公告"]):
            score += 1
        if source_count >= 3 and any(k in evidence_text for k in ["媒体", "招聘信号", "社媒弱信号"]):
            score += 1
        return max(1, min(5, score))

    def _confidence_level(self, cross_source_score: int, evidence_levels: List[str]) -> str:
        evidence_text = " ".join(evidence_levels)
        if cross_source_score >= 4 and any(k in evidence_text for k in ["官方原文", "公司公告", "平台公告"]):
            return "high"
        if cross_source_score >= 3:
            return "medium"
        return "low"

    def _strategic_signal(self, text: str, event_types: List[str], sources: List[str], cross_source_score: int) -> str:
        risks = keyword_hits(text, RISK_KEYWORDS)
        businesses = keyword_hits(text, BUSINESS_KEYWORDS)
        vendors = keyword_hits(text, VENDOR_KEYWORDS)
        source_text = "、".join(sources[:3])
        vendor_text = "、".join(vendors[:5]) if vendors else "相关平台/公司"
        if risks and businesses:
            return f"多源信号显示：{vendor_text} 同时出现风险/合规与商业化变化，可能意味着赛道进入规则重塑期。建议关注企业合规、内容安全、审核流程、竞品应对。来源：{source_text}。"
        if risks:
            return f"这是风险/合规类交叉信号。若后续出现官方确认或更多平台跟进，可能影响 Agent 产品上架、运营和商业化边界。来源：{source_text}。"
        if businesses:
            return f"这是商业化/岗位/融资类信号。可用于判断市场是否正在验证某类 AI/Agent 能力的付费价值。来源：{source_text}。"
        if cross_source_score >= 4:
            return f"这是多源确认度较高的趋势信号，建议纳入周报重点观察。来源：{source_text}。"
        return "当前更像单点信息，建议继续观察是否出现官方公告、媒体跟进或招聘/产品动作同步。"

    def _merged_summary(self, cluster: List[Dict[str, Any]]) -> str:
        titles = [item.get("title", "") for item in cluster[:3] if item.get("title")]
        sources = [item.get("source_name", "") for item in cluster[:3] if item.get("source_name")]
        return f"该事件由 {len(cluster)} 条情报合并而成。代表标题：{' / '.join(titles)}。主要来源：{'、'.join(sources)}。"

    def _event_id(self, title: str, event_types: List[str], sources: List[str]) -> str:
        raw = "|".join([title, ",".join(event_types), ",".join(sources)])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多源事件归并器")
    parser.add_argument("--input", required=True, help="rule_engine 输出的 JSONL 文件")
    parser.add_argument("--output", default="data/processed/merged_events.jsonl", help="归并后的事件 JSONL")
    parser.add_argument("--threshold", type=float, default=0.32, help="相似度阈值")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = PROJECT_ROOT / args.input
    output_path = PROJECT_ROOT / args.output
    items = load_jsonl(input_path)
    events = EventMerger(threshold=args.threshold).merge(items)
    write_jsonl([asdict(e) for e in events], output_path)
    print(f"Merged {len(items)} items into {len(events)} events -> {output_path}")


if __name__ == "__main__":
    main()
