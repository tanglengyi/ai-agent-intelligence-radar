"""RSS/RSSHub collector for AI Agent Intelligence Radar.

职责：
1. 读取 config/sources.json。
2. 抓取 feed_url 和 rsshub_path 对应的信息。
3. 输出标准化 NewsInput 字段，供 rule_engine.py 评分。

设计原则：
- MVP 优先：只依赖 Python 标准库，不强依赖 feedparser。
- RSSHub 公共实例可配置，默认 https://rsshub.app。
- 对 low stability 源不做复杂反爬，只记录失败，避免系统卡死。
- 采集层只负责“拿原材料”，不负责深度洞察。

Example:
    cd 自动化情报收集系统
    python src/rss_collector.py --limit-per-source 5 --output data/raw/rss_items.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_RSSHUB_BASE = "https://rsshub.app"


@dataclass
class CollectedItem:
    title: str
    text: str
    source_name: str
    source_url: str
    published_at: Optional[str]
    collected_at: str
    channel: str
    evidence_level: str
    language: str
    raw_feed_url: str
    source_type: str


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def strip_html(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return value


def build_rsshub_url(rsshub_path: str, rsshub_base: str = DEFAULT_RSSHUB_BASE) -> str:
    if not rsshub_path:
        return ""
    if rsshub_path.startswith("http://") or rsshub_path.startswith("https://"):
        return rsshub_path
    return rsshub_base.rstrip("/") + "/" + rsshub_path.lstrip("/")


def source_feed_url(source: Dict[str, Any], rsshub_base: str = DEFAULT_RSSHUB_BASE) -> Optional[str]:
    if source.get("feed_url"):
        return source["feed_url"]
    if source.get("rsshub_path"):
        return build_rsshub_url(source["rsshub_path"], rsshub_base)
    return None


def fetch_text(url: str, timeout: int = 20, user_agent: str = "AI-Agent-Intelligence-Radar/0.1") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def find_child_text(element: ET.Element, names: Iterable[str]) -> str:
    # RSS often has namespaces. Match by localname suffix.
    wanted = set(names)
    for child in list(element):
        local = child.tag.split("}")[-1]
        if local in wanted:
            return "".join(child.itertext()).strip()
    return ""


def parse_rss_or_atom(xml_text: str) -> List[Dict[str, Optional[str]]]:
    root = ET.fromstring(xml_text)
    root_local = root.tag.split("}")[-1].lower()
    items: List[Dict[str, Optional[str]]] = []

    if root_local == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            channel = root
        for item in channel.findall("item"):
            title = find_child_text(item, ["title"])
            link = find_child_text(item, ["link"])
            description = find_child_text(item, ["description", "summary", "encoded"])
            pub_date = find_child_text(item, ["pubDate", "published", "updated", "date"])
            guid = find_child_text(item, ["guid", "id"])
            items.append({
                "title": strip_html(title),
                "link": link.strip(),
                "summary": strip_html(description),
                "published_at": normalize_date(pub_date),
                "guid": guid,
            })
        return items

    # Atom
    entries = [e for e in root.iter() if e.tag.split("}")[-1] == "entry"]
    for entry in entries:
        title = find_child_text(entry, ["title"])
        summary = find_child_text(entry, ["summary", "content"])
        published = find_child_text(entry, ["published", "updated"])
        entry_id = find_child_text(entry, ["id"])
        link = ""
        for child in list(entry):
            if child.tag.split("}")[-1] == "link":
                link = child.attrib.get("href", "")
                if link:
                    break
        items.append({
            "title": strip_html(title),
            "link": link.strip(),
            "summary": strip_html(summary),
            "published_at": normalize_date(published),
            "guid": entry_id,
        })
    return items


class RSSCollector:
    def __init__(self, config_dir: Path = CONFIG_DIR, rsshub_base: str = DEFAULT_RSSHUB_BASE) -> None:
        self.config_dir = config_dir
        self.rsshub_base = rsshub_base
        self.sources_config = load_json(config_dir / "sources.json")

    def iter_feed_sources(self, include_low_stability: bool = True) -> Iterable[Dict[str, Any]]:
        for source in self.sources_config.get("sources", []):
            feed = source_feed_url(source, self.rsshub_base)
            if not feed:
                continue
            if not include_low_stability and source.get("stability") == "low":
                continue
            yield source

    def collect_source(self, source: Dict[str, Any], limit: int = 10) -> List[CollectedItem]:
        feed_url = source_feed_url(source, self.rsshub_base)
        if not feed_url:
            return []
        try:
            xml_text = fetch_text(feed_url)
            raw_items = parse_rss_or_atom(xml_text)
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            print(f"[WARN] collect failed: {source.get('name')} {feed_url} -> {exc}", file=sys.stderr)
            return []

        collected_at = now_iso()
        items: List[CollectedItem] = []
        for raw in raw_items[:limit]:
            title = raw.get("title") or "未命名情报"
            link = raw.get("link") or source.get("url", "")
            summary = raw.get("summary") or ""
            items.append(CollectedItem(
                title=title,
                text=summary,
                source_name=source.get("name", "unknown"),
                source_url=link,
                published_at=raw.get("published_at"),
                collected_at=collected_at,
                channel=source.get("channel", "未分类"),
                evidence_level=source.get("evidence_level", "未知"),
                language=source.get("language", "unknown"),
                raw_feed_url=feed_url,
                source_type=source.get("source_type", "unknown"),
            ))
        return items

    def collect_all(
        self,
        limit_per_source: int = 10,
        max_sources: Optional[int] = None,
        include_low_stability: bool = True,
        sleep_seconds: float = 0.2,
    ) -> List[CollectedItem]:
        results: List[CollectedItem] = []
        sources = list(self.iter_feed_sources(include_low_stability=include_low_stability))
        sources.sort(key=lambda s: int(s.get("priority", 1) or 1), reverse=True)
        if max_sources is not None:
            sources = sources[:max_sources]

        for source in sources:
            results.extend(self.collect_source(source, limit=limit_per_source))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        return results


def write_jsonl(items: List[CollectedItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS/RSSHub 情报采集器")
    parser.add_argument("--rsshub-base", default=DEFAULT_RSSHUB_BASE, help="RSSHub 实例地址")
    parser.add_argument("--limit-per-source", type=int, default=10, help="每个源最多采集多少条")
    parser.add_argument("--max-sources", type=int, default=None, help="最多采集多少个源，调试用")
    parser.add_argument("--skip-low-stability", action="store_true", help="跳过 RSSHub/社媒/招聘等低稳定源")
    parser.add_argument("--output", default="data/raw/rss_items.jsonl", help="输出 JSONL 路径")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    collector = RSSCollector(rsshub_base=args.rsshub_base)
    items = collector.collect_all(
        limit_per_source=args.limit_per_source,
        max_sources=args.max_sources,
        include_low_stability=not args.skip_low_stability,
    )
    output_path = PROJECT_ROOT / args.output
    write_jsonl(items, output_path)
    print(f"Collected {len(items)} items -> {output_path}")


if __name__ == "__main__":
    main()
