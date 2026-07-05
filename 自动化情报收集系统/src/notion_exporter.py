"""Notion exporter for AI Agent Intelligence Radar.

作用：
1. 把 event_merger.py 输出的合并事件写入 Notion 数据库；
2. 把 report_generator.py 生成的 Markdown 日报写入 Notion 页面；
3. 只依赖 Python 标准库，通过 Notion REST API 调用。

使用前准备：
1. 在 Notion 创建 Internal Integration，拿到 NOTION_TOKEN；
2. 新建一个情报数据库，复制 database_id；
3. 把 Integration 授权给该数据库；
4. 设置环境变量：export NOTION_TOKEN="secret_xxx"。

Example:
    python src/notion_exporter.py \
      --events data/processed/merged_events_2026-07-05.jsonl \
      --database-id YOUR_DATABASE_ID

    python src/notion_exporter.py \
      --report data/reports/daily_2026-07-05.md \
      --parent-page-id YOUR_PAGE_ID \
      --report-title "AI Agent Intelligence Radar Daily - 2026-07-05"
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionClient:
    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token or os.getenv("NOTION_TOKEN")
        if not self.token:
            raise RuntimeError("Missing Notion token. Set NOTION_TOKEN or pass --token-env with an env var name.")

    def request(self, method: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = NOTION_API_BASE + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion API error {exc.code}: {body}") from exc

    def create_page(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/pages", payload)


def rich_text(text: str, max_len: int = 1900) -> List[Dict[str, Any]]:
    text = (text or "")[:max_len]
    return [{"type": "text", "text": {"content": text}}]


def title_prop(text: str) -> Dict[str, Any]:
    return {"title": rich_text(text, max_len=200)}


def rich_text_prop(text: str) -> Dict[str, Any]:
    return {"rich_text": rich_text(text, max_len=1900)}


def number_prop(value: Any) -> Dict[str, Any]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = 0.0
    return {"number": num}


def select_prop(value: str) -> Dict[str, Any]:
    value = value or "unknown"
    return {"select": {"name": value[:100]}}


def multi_select_prop(values: Sequence[str]) -> Dict[str, Any]:
    unique = []
    seen = set()
    for value in values or []:
        value = str(value)[:100]
        if value and value not in seen:
            unique.append({"name": value})
            seen.add(value)
    return {"multi_select": unique[:20]}


def url_prop(value: str) -> Dict[str, Any]:
    return {"url": value or None}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def event_to_notion_page(database_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    urls = event.get("source_urls") or []
    return {
        "parent": {"database_id": database_id},
        "properties": {
            "Name": title_prop(event.get("canonical_title", "未命名事件")),
            "Score": number_prop(event.get("max_final_score", 0)),
            "Confidence": select_prop(event.get("confidence_level", "unknown")),
            "Type": select_prop(event.get("primary_event_type", "other")),
            "Sources": multi_select_prop(event.get("sources", [])),
            "Actions": multi_select_prop(event.get("recommended_actions", [])),
            "Tags": multi_select_prop(event.get("tags", [])),
            "Summary": rich_text_prop(event.get("merged_summary", "")),
            "Strategic Signal": rich_text_prop(event.get("strategic_signal", "")),
            "URL": url_prop(urls[0] if urls else ""),
        },
        "children": [
            paragraph("事件摘要", bold=True),
            paragraph(event.get("merged_summary", "暂无摘要")),
            paragraph("战略信号", bold=True),
            paragraph(event.get("strategic_signal", "暂无战略信号")),
            paragraph("来源", bold=True),
            bulleted_list([str(s) for s in event.get("sources", [])[:10]]),
        ],
    }


def paragraph(text: str, bold: bool = False) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{
                "type": "text",
                "text": {"content": (text or "")[:1900]},
                "annotations": {"bold": bold},
            }]
        },
    }


def bulleted_list(items: Sequence[str]) -> Dict[str, Any]:
    text = "\n".join(f"- {item}" for item in items) if items else "暂无"
    return paragraph(text)


def markdown_to_blocks(markdown: str, max_blocks: int = 80) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": rich_text(line[2:], 1900)},
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": rich_text(line[3:], 1900)},
            })
        elif line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rich_text(line[4:], 1900)},
            })
        elif line.startswith("- "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_text(line[2:], 1900)},
            })
        else:
            blocks.append(paragraph(line))
        if len(blocks) >= max_blocks:
            blocks.append(paragraph("内容过长，已截断。完整报告请查看本地 Markdown 文件。"))
            break
    return blocks


def export_events_to_database(client: NotionClient, database_id: str, events_path: Path, limit: Optional[int] = None) -> List[str]:
    events = load_jsonl(events_path)
    if limit is not None:
        events = events[:limit]
    page_ids: List[str] = []
    for event in events:
        page = client.create_page(event_to_notion_page(database_id, event))
        page_ids.append(page.get("id", ""))
    return page_ids


def export_report_to_page(client: NotionClient, parent_page_id: str, report_path: Path, report_title: str) -> str:
    markdown = report_path.read_text(encoding="utf-8")
    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": title_prop(report_title),
        },
        "children": markdown_to_blocks(markdown),
    }
    page = client.create_page(payload)
    return page.get("id", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 AI/Agent 情报到 Notion")
    parser.add_argument("--token-env", default="NOTION_TOKEN", help="Notion token 环境变量名")
    parser.add_argument("--events", default=None, help="merged_events JSONL 路径")
    parser.add_argument("--database-id", default=None, help="Notion 情报数据库 ID")
    parser.add_argument("--limit", type=int, default=None, help="最多导出多少条事件")
    parser.add_argument("--report", default=None, help="日报 Markdown 路径")
    parser.add_argument("--parent-page-id", default=None, help="日报父页面 ID")
    parser.add_argument("--report-title", default="AI Agent Intelligence Radar Daily", help="日报页面标题")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    token = os.getenv(args.token_env)
    client = NotionClient(token=token)

    exported: Dict[str, Any] = {}
    if args.events:
        if not args.database_id:
            raise SystemExit("--events requires --database-id")
        page_ids = export_events_to_database(client, args.database_id, PROJECT_ROOT / args.events, limit=args.limit)
        exported["event_pages"] = page_ids

    if args.report:
        if not args.parent_page_id:
            raise SystemExit("--report requires --parent-page-id")
        page_id = export_report_to_page(client, args.parent_page_id, PROJECT_ROOT / args.report, args.report_title)
        exported["report_page"] = page_id

    print(json.dumps(exported, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
