"""Daily report generator for AI Agent Intelligence Radar.

职责：
1. 读取 rule_engine.py 输出的单条情报 JSONL，或 event_merger.py 输出的合并事件 JSONL。
2. 按 final_score / max_final_score 排序。
3. 生成可直接发给自己、私域会员、企业客户的 Markdown 日报。

Example:
    python src/report_generator.py --input data/processed/evaluated_items.jsonl --output data/reports/daily.md
    python src/report_generator.py --input data/processed/merged_events.jsonl --merged --output data/reports/daily_merged.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SECTION_ORDER = [
    ("top_signals", "今日最高优先级信号"),
    ("policy_and_risk", "政策 / 合规 / 风险"),
    ("agent_product_updates", "Agent 产品 / 平台更新"),
    ("infra_and_api", "Infra / API / 算力"),
    ("commercialization", "投融资 / 商业化"),
    ("career_and_hiring", "招聘 / 岗位 / 能力信号"),
]

EVENT_TO_SECTION = {
    "policy_regulation": "policy_and_risk",
    "standard_update": "policy_and_risk",
    "security_safety_risk": "policy_and_risk",
    "shutdown_or_removal": "policy_and_risk",
    "product_launch": "agent_product_updates",
    "feature_update": "agent_product_updates",
    "open_source_release": "agent_product_updates",
    "pricing_change": "infra_and_api",
    "funding_commercialization": "commercialization",
    "case_study": "commercialization",
    "hiring_signal": "career_and_hiring",
    "market_opinion": "career_and_hiring",
}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def score_of(item: Dict[str, Any], merged: bool = False) -> float:
    if merged:
        return float(item.get("max_final_score", item.get("average_final_score", 0)) or 0)
    return float(item.get("scores", {}).get("final_score", 0) or 0)


def primary_event_type(item: Dict[str, Any]) -> str:
    return item.get("primary_event_type") or (item.get("event_types") or ["other"])[0]


def section_for(item: Dict[str, Any]) -> str:
    return EVENT_TO_SECTION.get(primary_event_type(item), "agent_product_updates")


def action_text(actions: Sequence[str]) -> str:
    mapping = {
        "save_to_knowledge_base": "进知识库",
        "track_follow_up": "持续追踪",
        "convert_to_interview_story": "转面试案例",
        "create_product_insight": "沉淀产品洞察",
        "ignore": "忽略",
        "archive_only": "仅归档",
    }
    return "、".join(mapping.get(a, a) for a in actions) if actions else "暂无"


def item_title(item: Dict[str, Any], merged: bool) -> str:
    return item.get("canonical_title") if merged else item.get("title", "未命名情报")


def item_sources(item: Dict[str, Any], merged: bool) -> str:
    if merged:
        return "、".join(item.get("sources", [])[:5]) or "未知来源"
    return item.get("source_name", "未知来源")


def item_summary(item: Dict[str, Any], merged: bool) -> str:
    if merged:
        return item.get("merged_summary") or item.get("strategic_signal") or "暂无摘要"
    return item.get("clean_summary") or item.get("raw_summary") or item.get("text") or "暂无摘要"


def item_insight(item: Dict[str, Any], merged: bool) -> str:
    if merged:
        return item.get("strategic_signal", "暂无战略信号")
    insight = item.get("insight", {}) or {}
    return item.get("why_it_matters") or insight.get("decision_value") or "暂无判断"


def item_actions(item: Dict[str, Any], merged: bool) -> List[str]:
    return item.get("recommended_actions", []) or []


def render_item(item: Dict[str, Any], rank: int | None = None, merged: bool = False) -> str:
    title_prefix = f"{rank}. " if rank is not None else ""
    title = item_title(item, merged)
    score = score_of(item, merged)
    sources = item_sources(item, merged)
    event = primary_event_type(item)
    summary = item_summary(item, merged)
    insight = item_insight(item, merged)
    actions = action_text(item_actions(item, merged))
    confidence = f"｜置信度：{item.get('confidence_level')}" if merged and item.get("confidence_level") else ""
    urls = item.get("source_urls", []) if merged else [item.get("source_url", "")]
    url_line = ""
    if urls and urls[0]:
        url_line = f"\n- 原文/来源：{urls[0]}"
    return (
        f"### {title_prefix}{title}\n"
        f"**评分：{score}｜来源：{sources}｜类型：{event}{confidence}**\n\n"
        f"- 事实/事件：{summary}\n"
        f"- 判断：{insight}\n"
        f"- 建议动作：{actions}"
        f"{url_line}\n"
    )


def generate_takeaways(items: List[Dict[str, Any]], merged: bool = False) -> List[str]:
    top = sorted(items, key=lambda x: score_of(x, merged), reverse=True)[:10]
    event_counts = defaultdict(int)
    action_counts = defaultdict(int)
    for item in top:
        event_counts[primary_event_type(item)] += 1
        for action in item_actions(item, merged):
            action_counts[action] += 1

    takeaways: List[str] = []
    if top:
        best = top[0]
        takeaways.append(f"今天最高优先级信号是「{item_title(best, merged)}」，建议动作是：{action_text(item_actions(best, merged))}。")
    if event_counts:
        main_event = max(event_counts.items(), key=lambda x: x[1])[0]
        takeaways.append(f"今天出现最多的事件类型是 `{main_event}`，说明该方向值得持续跟踪。")
    if action_counts.get("track_follow_up"):
        takeaways.append("今天存在需要持续追踪的信号，后续应优先查找官方原文、第二信源和平台动作。")
    if action_counts.get("convert_to_interview_story"):
        takeaways.append("今天有信息可以转成面试案例，建议沉淀成“外部变化—产品判断—我会怎么做”的表达。")
    if not takeaways:
        takeaways.append("今天没有明显强信号，建议低频归档，不要被信息噪音牵着走。")
    return takeaways[:5]


def generate_report(items: List[Dict[str, Any]], merged: bool = False, date_text: str | None = None, top_n: int = 5) -> str:
    date_text = date_text or datetime.now().strftime("%Y-%m-%d")
    sorted_items = sorted(items, key=lambda x: score_of(x, merged), reverse=True)
    sections: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in sorted_items:
        sections[section_for(item)].append(item)

    lines: List[str] = []
    lines.append(f"# AI Agent Intelligence Radar Daily - {date_text}")
    lines.append("")
    lines.append("## 今日结论")
    for takeaway in generate_takeaways(sorted_items, merged=merged):
        lines.append(f"- {takeaway}")
    lines.append("")

    lines.append("## 今日最高优先级信号")
    top_items = sorted_items[:top_n]
    if not top_items:
        lines.append("暂无高优先级信号。")
    else:
        for idx, item in enumerate(top_items, start=1):
            lines.append(render_item(item, rank=idx, merged=merged))
    lines.append("")

    for section_id, title in SECTION_ORDER[1:]:
        lines.append(f"## {title}")
        section_items = sections.get(section_id, [])[:10]
        if not section_items:
            lines.append("暂无。")
        else:
            for item in section_items:
                lines.append(render_item(item, merged=merged))
        lines.append("")

    lines.append("## 使用提醒")
    lines.append("- 社媒、招聘、RSSHub 低稳定源只能作为弱信号，不应直接当事实结论。")
    lines.append("- 高价值情报需要至少保留原文链接，并优先交叉验证官方公告、可信媒体和产品动作。")
    lines.append("- 真正可变现的不是信息本身，而是行业判断、合规预警、竞品信号和赛道机会。")
    return "\n".join(lines).strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 AI/Agent 情报日报")
    parser.add_argument("--input", required=True, help="输入 JSONL，来自 rule_engine 或 event_merger")
    parser.add_argument("--output", default="data/reports/daily.md", help="输出 Markdown 路径")
    parser.add_argument("--merged", action="store_true", help="输入是否为 event_merger 归并后的事件")
    parser.add_argument("--date", default=None, help="日报日期，如 2026-07-05")
    parser.add_argument("--top-n", type=int, default=5, help="Top 信号数量")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = PROJECT_ROOT / args.input
    output_path = PROJECT_ROOT / args.output
    items = load_jsonl(input_path)
    report = generate_report(items, merged=args.merged, date_text=args.date, top_n=args.top_n)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Generated report with {len(items)} items -> {output_path}")


if __name__ == "__main__":
    main()
