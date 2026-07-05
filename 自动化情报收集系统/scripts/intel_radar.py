#!/usr/bin/env python3
"""Lightweight AI/Agent intelligence radar.

Fetches RSS/Atom or monitored web pages, deduplicates items into SQLite, tags
them with product-manager oriented fields, and exports Notion-ready CSV plus
daily/weekly markdown reports.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DB_PATH = ROOT / "data" / "intelligence.sqlite3"
REPORTS_DIR = ROOT / "reports"
EXPORTS_DIR = ROOT / "exports"
ENV_PATH = ROOT / ".env"
DEFAULT_NOTION_VERSION = "2022-06-28"

TYPE_KEYWORDS = {
    "政策": ["监管", "办法", "条例", "合规", "政策", "regulation", "policy", "act", "framework", "rmf"],
    "产品": ["release", "launch", "发布", "上线", "功能", "agent", "workflow", "api", "model", "产品"],
    "融资": ["funding", "raises", "融资", "估值", "acquisition", "ipo", "收购"],
    "技术": ["benchmark", "research", "paper", "model", "inference", "training", "eval", "开源"],
    "风险": ["safety", "risk", "incident", "abuse", "漏洞", "安全", "风险", "kill switch"],
    "竞品": ["jobs", "hiring", "招聘", "岗位", "product manager", "jd"],
}

DIRECTION_KEYWORDS = {
    "Agent": ["agent", "智能体", "LangGraph", "workflow", "tool use", "computer use"],
    "算力": ["gpu", "算力", "data center", "nvidia", "coreweave", "inference"],
    "API": ["api", "gateway", "router", "openrouter", "模型网关", "pricing"],
    "知识库": ["rag", "knowledge", "知识库", "retrieval", "搜索"],
    "安全": ["safety", "security", "risk", "audit", "权限", "审计", "kill switch"],
    "监管": ["regulation", "policy", "监管", "合规", "条例", "办法", "act"],
}

IMPACT_OBJECT_KEYWORDS = {
    "C端": ["consumer", "chatgpt", "companion", "陪伴", "角色", "个人用户"],
    "B端": ["enterprise", "business", "workflow", "员工", "企业", "组织"],
    "大厂": ["openai", "google", "meta", "anthropic", "microsoft", "腾讯", "阿里", "字节"],
    "创业公司": ["startup", "funding", "融资", "seed", "series"],
    "产品经理": ["product manager", "产品经理", "roadmap", "需求", "体验"],
}


def now_cn() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def fetch_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 intelligence-radar/1.0 (+local product research)",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, text/html;q=0.8,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def resolve_source_fetch_url(source: dict[str, Any]) -> str:
    if source.get("rsshub_path"):
        base_url = os.getenv("RSSHUB_BASE_URL", "https://rsshub.app").rstrip("/")
        path = source["rsshub_path"]
        return f"{base_url}/{path.lstrip('/')}"
    return source.get("feed_url") or source["url"]


def github_releases_atom_url(source: dict[str, Any]) -> str | None:
    match = re.search(r"github\.com/([^/]+)/([^/]+)/releases", source.get("url", ""))
    if not match:
        return None
    owner, repo = match.groups()
    return f"https://github.com/{owner}/{repo}/releases.atom"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def json_request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def parse_feed(xml_text: str, source: dict[str, Any]) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    items: list[dict[str, str]] = []

    for node in root.findall(".//item"):
        title = clean_text(node.findtext("title"))
        link = clean_text(node.findtext("link"))
        summary = clean_text(node.findtext("description") or node.findtext("content:encoded", namespaces=ns))
        published = clean_text(node.findtext("pubDate") or node.findtext("dc:date", namespaces=ns))
        if title:
            items.append({"title": title, "link": link or source["url"], "summary": summary, "published": published})

    for node in root.findall(".//atom:entry", ns):
        title = clean_text(node.findtext("atom:title", namespaces=ns))
        link_node = node.find("atom:link[@rel='alternate']", ns)
        if link_node is None:
            link_node = node.find("atom:link", ns)
        link = link_node.attrib.get("href", "") if link_node is not None else ""
        summary = clean_text(node.findtext("atom:summary", namespaces=ns) or node.findtext("atom:content", namespaces=ns))
        published = clean_text(node.findtext("atom:published", namespaces=ns) or node.findtext("atom:updated", namespaces=ns))
        if title:
            items.append({"title": title, "link": link or source["url"], "summary": summary, "published": published})

    return items


def parse_web_page(page: str, source: dict[str, Any]) -> list[dict[str, str]]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    meta_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', page, re.I | re.S)
    title = clean_text(title_match.group(1) if title_match else source["name"])
    summary = clean_text(meta_match.group(1) if meta_match else "")
    haystack = clean_text(page[:15000])
    hits = [kw for kw in source.get("monitor_keywords", []) if kw.lower() in haystack.lower()]
    if hits:
        summary = (summary + " 监测命中关键词: " + ", ".join(hits[:8])).strip()
    return [{"title": title, "link": source["url"], "summary": summary, "published": now_cn().isoformat()}]


def choose_label(text: str, mapping: dict[str, list[str]], default: str) -> str:
    lowered = text.lower()
    scores: list[tuple[int, str]] = []
    for label, keywords in mapping.items():
        score = sum(1 for kw in keywords if kw.lower() in lowered)
        if score:
            scores.append((score, label))
    return sorted(scores, reverse=True)[0][1] if scores else default


def choose_directions(text: str) -> str:
    lowered = text.lower()
    labels = [label for label, kws in DIRECTION_KEYWORDS.items() if any(kw.lower() in lowered for kw in kws)]
    return " / ".join(labels[:3]) if labels else "待判断"


def choose_impact(text: str) -> str:
    lowered = text.lower()
    labels = [label for label, kws in IMPACT_OBJECT_KEYWORDS.items() if any(kw.lower() in lowered for kw in kws)]
    return " / ".join(labels[:3]) if labels else "待判断"


def impact_judgement(text: str) -> str:
    lowered = text.lower()
    positive = ["launch", "release", "open source", "funding", "降低成本", "enterprise", "上线", "开源"]
    negative = ["ban", "restrict", "risk", "incident", "下架", "处罚", "漏洞", "监管", "lawsuit"]
    pos = sum(1 for kw in positive if kw.lower() in lowered)
    neg = sum(1 for kw in negative if kw.lower() in lowered)
    if pos > neg:
        return "利好"
    if neg > pos:
        return "利空"
    return "不确定"


def build_pm_notes(item: dict[str, str], source: dict[str, Any]) -> tuple[str, str, str]:
    what = item["title"]
    why = "可能影响产品路线、合规边界、商业化节奏或竞品策略，需要进入周复盘验证。"
    action = "面试话术 / 竞品分析 / 产品机会 / 风险提示"
    if source["channel"] == "政策监管":
        why = "政策源头信号优先级最高，适合判断平台能力收缩、产品边界和合规必备能力。"
        action = "补充政策原文证据，转化为 B 端 Agent 权限、审计、人工接管需求"
    elif "招聘" in source["channel"]:
        why = "招聘 JD 往往提前暴露组织投入方向，可用于判断公司真实战略而非公关叙事。"
        action = "记录岗位能力要求，沉淀成目标公司面试问题和作品集切入点"
    elif "Infra" in source["channel"]:
        why = "Infra/API 变化会传导到模型成本、稳定性、网关能力和企业采购门槛。"
        action = "更新模型网关、成本监控、供应商切换、SLA 相关需求"
    elif "安全" in source["channel"]:
        why = "Agent 正从内容风险走向权限、数据流和外部系统调用风险。"
        action = "补充权限隔离、日志审计、熔断机制、人工接管需求"
    return what, why, action


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intelligence_items (
              id TEXT PRIMARY KEY,
              collected_at TEXT NOT NULL,
              title TEXT NOT NULL,
              source TEXT NOT NULL,
              source_url TEXT NOT NULL,
              link TEXT NOT NULL,
              channel TEXT NOT NULL,
              type TEXT NOT NULL,
              direction TEXT NOT NULL,
              impact_object TEXT NOT NULL,
              impact_judgement TEXT NOT NULL,
              evidence_level TEXT NOT NULL,
              summary TEXT NOT NULL,
              what_happened TEXT NOT NULL,
              why_it_matters TEXT NOT NULL,
              action TEXT NOT NULL,
              my_judgement TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT '未复盘',
              published TEXT
            )
            """
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(intelligence_items)").fetchall()}
        migrations = {
            "llm_status": "ALTER TABLE intelligence_items ADD COLUMN llm_status TEXT NOT NULL DEFAULT 'pending'",
            "notion_page_id": "ALTER TABLE intelligence_items ADD COLUMN notion_page_id TEXT",
            "notion_synced_at": "ALTER TABLE intelligence_items ADD COLUMN notion_synced_at TEXT",
            "notion_status": "ALTER TABLE intelligence_items ADD COLUMN notion_status TEXT NOT NULL DEFAULT 'pending'",
        }
        for column, statement in migrations.items():
            if column not in existing:
                conn.execute(statement)


def collect(config_path: Path = CONFIG_PATH) -> tuple[int, list[str]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    init_db()
    inserted = 0
    errors: list[str] = []
    collected_at = now_cn().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        for source in config["sources"]:
            url = resolve_source_fetch_url(source)
            try:
                body = fetch_url(url)
                entries = parse_feed(body, source)
                if not entries:
                    entries = parse_web_page(body, source)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                fallback_url = github_releases_atom_url(source)
                if fallback_url and fallback_url != url:
                    try:
                        body = fetch_url(fallback_url)
                        entries = parse_feed(body, source)
                        if not entries:
                            entries = parse_web_page(body, source)
                    except (urllib.error.URLError, TimeoutError, OSError) as fallback_exc:
                        errors.append(f"{source['name']}: {exc}; fallback={fallback_exc}")
                        continue
                else:
                    errors.append(f"{source['name']}: {exc}")
                    continue

            for entry in entries[:15]:
                full_text = f"{entry['title']} {entry.get('summary', '')} {source['name']} {source['channel']}"
                item_id = digest(entry.get("link") or entry["title"] + source["name"])
                item_type = choose_label(full_text, TYPE_KEYWORDS, source["channel"].split(" / ")[0])
                direction = choose_directions(full_text)
                impact_object = choose_impact(full_text)
                judgement = impact_judgement(full_text)
                what, why, action = build_pm_notes(entry, source)
                my_judgement = "待周复盘：确认是否出现第二个以上同向信号，再进入需求池或面试素材。"
                try:
                    conn.execute(
                        """
                        INSERT INTO intelligence_items (
                          id, collected_at, title, source, source_url, link, channel, type,
                          direction, impact_object, impact_judgement, evidence_level, summary,
                          what_happened, why_it_matters, action, my_judgement, published
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            collected_at,
                            entry["title"],
                            source["name"],
                            source["url"],
                            entry.get("link") or source["url"],
                            source["channel"],
                            item_type,
                            direction,
                            impact_object,
                            judgement,
                            source.get("evidence_level", "待判断"),
                            entry.get("summary", "")[:1200],
                            what,
                            why,
                            action,
                            my_judgement,
                            entry.get("published", ""),
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
    return inserted, errors


def parse_llm_json(raw: str) -> dict[str, str]:
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return {
        "what_happened": clean_text(data.get("what_happened", "")),
        "why_it_matters": clean_text(data.get("why_it_matters", "")),
        "my_judgement": clean_text(data.get("my_judgement", "")),
        "action": clean_text(data.get("action", "")),
    }


def call_llm_summary(row: sqlite3.Row) -> dict[str, str]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("missing LLM_API_KEY or OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 AI/Agent 产品经理情报分析助手。只输出 JSON，不要 Markdown。"
                    "字段必须是 what_happened, why_it_matters, my_judgement, action。"
                    "每个字段用中文，一句话，偏产品判断，不夸大，不把媒体报道当事实。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"标题：{row['title']}\n"
                    f"来源：{row['source']} / {row['evidence_level']}\n"
                    f"频道：{row['channel']} / 类型：{row['type']} / 方向：{row['direction']}\n"
                    f"影响对象：{row['impact_object']} / 初步影响：{row['impact_judgement']}\n"
                    f"摘要：{row['summary']}\n"
                    f"链接：{row['link']}\n\n"
                    "请生成三句话摘要和一个可行动作："
                    "1. 发生了什么；2. 为什么重要；3. 对我做产品/面试/判断公司有什么影响；4. 下一步动作。"
                ),
            },
        ],
    }
    response = json_request(
        f"{base_url}/chat/completions",
        payload,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    content = response["choices"][0]["message"]["content"]
    return parse_llm_json(content)


def enhance_with_llm(limit: int = 20) -> tuple[int, list[str]]:
    load_env()
    if not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return 0, ["missing LLM_API_KEY or OPENAI_API_KEY"]
    init_db()
    enhanced = 0
    errors: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM intelligence_items
        WHERE llm_status != 'done'
        ORDER BY collected_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in rows:
        try:
            notes = call_llm_summary(row)
            conn.execute(
                """
                UPDATE intelligence_items
                SET what_happened = ?, why_it_matters = ?, my_judgement = ?, action = ?, llm_status = 'done'
                WHERE id = ?
                """,
                (
                    notes["what_happened"] or row["what_happened"],
                    notes["why_it_matters"] or row["why_it_matters"],
                    notes["my_judgement"] or row["my_judgement"],
                    notes["action"] or row["action"],
                    row["id"],
                ),
            )
            enhanced += 1
        except Exception as exc:
            conn.execute("UPDATE intelligence_items SET llm_status = 'error' WHERE id = ?", (row["id"],))
            errors.append(f"{row['title'][:60]}: {exc}")
    conn.commit()
    conn.close()
    return enhanced, errors


def query_items(limit: int = 80, days: int | None = None) -> list[sqlite3.Row]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    params: list[Any] = []
    where = ""
    if days is not None:
        since = now_cn() - dt.timedelta(days=days)
        where = "WHERE collected_at >= ?"
        params.append(since.isoformat(timespec="seconds"))
    rows = conn.execute(
        f"SELECT * FROM intelligence_items {where} ORDER BY collected_at DESC, source ASC LIMIT ?",
        [*params, limit],
    ).fetchall()
    conn.close()
    return rows


def notion_rich_text(value: str, limit: int = 1900) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value[:limit]}}]} if value else {"rich_text": []}


def notion_select(value: str) -> dict[str, Any]:
    return {"select": {"name": value[:100]}} if value else {"select": None}


def notion_multi_select(value: str) -> dict[str, Any]:
    labels = [part.strip() for part in value.split("/") if part.strip()]
    return {"multi_select": [{"name": label[:100]} for label in labels[:6]]}


def notion_title(value: str) -> dict[str, Any]:
    return {"title": [{"text": {"content": value[:1900]}}]}


def notion_url(value: str) -> dict[str, Any]:
    return {"url": value[:2000] if value else None}


def notion_date(value: str) -> dict[str, Any]:
    return {"date": {"start": value[:10]}} if value else {"date": None}


def get_notion_schema(headers: dict[str, str]) -> dict[str, str]:
    database_id = os.getenv("NOTION_DATABASE_ID")
    if not database_id:
        return {}
    req = urllib.request.Request(f"https://api.notion.com/v1/databases/{database_id}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    return {name: prop.get("type", "") for name, prop in data.get("properties", {}).items()}


def first_property(schema: dict[str, str], property_type: str) -> str | None:
    for name, type_name in schema.items():
        if type_name == property_type:
            return name
    return None


def add_notion_property(properties: dict[str, Any], schema: dict[str, str], preferred_names: list[str], expected_type: str, value: dict[str, Any]) -> None:
    if not schema:
        properties[preferred_names[0]] = value
        return
    target = next((name for name in preferred_names if schema.get(name) == expected_type), None)
    if target is None and expected_type == "title":
        target = first_property(schema, "title")
    if target is None:
        return
    properties[target] = value


def notion_payload(row: sqlite3.Row, schema: dict[str, str] | None = None) -> dict[str, Any]:
    schema = schema or {}
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID")
    database_id = os.getenv("NOTION_DATABASE_ID")
    if data_source_id:
        parent = {"data_source_id": data_source_id}
    elif database_id:
        parent = {"database_id": database_id}
    else:
        raise RuntimeError("missing NOTION_DATA_SOURCE_ID or NOTION_DATABASE_ID")

    properties: dict[str, Any] = {}
    add_notion_property(properties, schema, ["标题", "序号", "Name"], "title", notion_title(row["title"]))
    add_notion_property(properties, schema, ["来源"], "select", notion_select(row["source"]))
    add_notion_property(properties, schema, ["类型"], "select", notion_select(row["type"]))
    add_notion_property(properties, schema, ["方向"], "multi_select", notion_multi_select(row["direction"]))
    add_notion_property(properties, schema, ["影响对象"], "multi_select", notion_multi_select(row["impact_object"]))
    add_notion_property(properties, schema, ["影响判断"], "select", notion_select(row["impact_judgement"]))
    add_notion_property(properties, schema, ["证据等级", "证据登记"], "select", notion_select(row["evidence_level"]))
    add_notion_property(properties, schema, ["我的判断"], "rich_text", notion_rich_text(row["my_judgement"]))
    add_notion_property(properties, schema, ["可行动作"], "rich_text", notion_rich_text(row["action"]))
    add_notion_property(properties, schema, ["原文链接"], "url", notion_url(row["link"]))
    add_notion_property(properties, schema, ["状态"], "select", notion_select(row["status"]))
    add_notion_property(properties, schema, ["收集时间"], "date", notion_date(row["collected_at"]))

    return {
        "parent": parent,
        "properties": properties,
        "children": [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": row["what_happened"][:1800]}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": row["why_it_matters"][:1800]}}]}},
        ],
    }


def sync_notion(limit: int = 50) -> tuple[int, list[str]]:
    load_env()
    token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    if not token:
        return 0, ["missing NOTION_TOKEN or NOTION_API_KEY"]
    init_db()
    synced = 0
    errors: list[str] = []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT * FROM intelligence_items WHERE notion_page_id IS NULL ORDER BY collected_at DESC LIMIT ?""", (limit,)).fetchall()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Notion-Version": os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION)}
    schema: dict[str, str] = {}
    try:
        schema = get_notion_schema(headers)
    except Exception as exc:
        errors.append(f"schema: {exc}")
    for row in rows:
        try:
            response = json_request("https://api.notion.com/v1/pages", notion_payload(row, schema), headers)
            conn.execute("""UPDATE intelligence_items SET notion_page_id = ?, notion_synced_at = ?, notion_status = 'done' WHERE id = ?""", (response.get("id", ""), now_cn().isoformat(timespec="seconds"), row["id"]))
            synced += 1
        except Exception as exc:
            conn.execute("UPDATE intelligence_items SET notion_status = 'error' WHERE id = ?", (row["id"],))
            errors.append(f"{row['title'][:60]}: {exc}")
    conn.commit()
    conn.close()
    return synced, errors


def export_notion_csv() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / "notion_import.csv"
    rows = query_items(limit=1000)
    columns = ["标题", "来源", "类型", "方向", "影响对象", "影响判断", "证据等级", "我的判断", "可行动作", "原文链接", "状态", "收集时间"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({"标题": row["title"], "来源": row["source"], "类型": row["type"], "方向": row["direction"], "影响对象": row["impact_object"], "影响判断": row["impact_judgement"], "证据等级": row["evidence_level"], "我的判断": row["my_judgement"], "可行动作": row["action"], "原文链接": row["link"], "状态": row["status"], "收集时间": row["collected_at"]})
    return path


def render_markdown_report(kind: str, days: int, limit: int) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = query_items(limit=limit, days=days)
    today = now_cn().date().isoformat()
    path = REPORTS_DIR / f"{today}-{kind}.md"
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["channel"], []).append(row)

    lines = [
        f"# AI/Agent 情报{kind}", "",
        f"- 生成时间: {now_cn().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 覆盖范围: 最近 {days} 天，最多 {limit} 条",
        "- 使用方法: 先看官方原文和公司公告，再看媒体/KOL；同一方向至少出现 2 个信号再下判断。", "",
        "## 本次复盘要回答的 4 个问题", "",
        "1. 这段时间 AI/Agent 最大变化是什么？",
        "2. 哪些方向被政策或平台规则利空？",
        "3. 哪些方向对 B 端产品经理有机会？",
        "4. 哪些话可以放进面试表达、作品集或小红书/公众号内容？", "",
    ]

    if not rows:
        lines.append("> 暂无新增信息。先检查网络或信息源配置。")
    for channel, items in grouped.items():
        lines.extend([f"## {channel}", ""])
        for row in items[:12]:
            summary = textwrap.shorten(row["summary"] or row["why_it_matters"], width=180, placeholder="...")
            lines.extend([
                f"### {row['title']}", "",
                f"- 来源: {row['source']} | 类型: {row['type']} | 方向: {row['direction']} | 证据等级: {row['evidence_level']}",
                f"- 影响对象: {row['impact_object']} | 影响判断: {row['impact_judgement']}",
                f"- 为什么重要: {row['why_it_matters']}",
                f"- 可行动作: {row['action']}",
                f"- 摘要: {summary}",
                f"- 原文: {row['link']}", "",
            ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="AI/Agent intelligence radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch sources and store new items")
    sub.add_parser("export", help="export Notion CSV")
    enhance = sub.add_parser("enhance", help="use an OpenAI-compatible LLM to write three-sentence PM notes")
    enhance.add_argument("--limit", type=int, default=20)
    notion = sub.add_parser("notion-sync", help="sync unsynced items to Notion")
    notion.add_argument("--limit", type=int, default=50)
    daily = sub.add_parser("daily", help="create daily report")
    daily.add_argument("--days", type=int, default=1)
    daily.add_argument("--limit", type=int, default=60)
    weekly = sub.add_parser("weekly", help="create weekly report")
    weekly.add_argument("--days", type=int, default=7)
    weekly.add_argument("--limit", type=int, default=120)
    run = sub.add_parser("run", help="collect, export, and create daily report")
    run.add_argument("--enhance", action="store_true", help="also run LLM summary for new/pending items")
    run.add_argument("--sync-notion", action="store_true", help="also sync unsynced items to Notion")
    run.add_argument("--limit", type=int, default=30, help="limit for optional enhance/sync steps")
    args = parser.parse_args()

    if args.command == "collect":
        inserted, errors = collect()
        print(f"inserted={inserted}")
        for error in errors:
            print(f"warn={error}", file=sys.stderr)
    elif args.command == "export":
        print(export_notion_csv())
    elif args.command == "enhance":
        enhanced, errors = enhance_with_llm(args.limit)
        print(f"enhanced={enhanced}")
        for error in errors:
            print(f"warn={error}", file=sys.stderr)
    elif args.command == "notion-sync":
        synced, errors = sync_notion(args.limit)
        print(f"synced={synced}")
        for error in errors:
            print(f"warn={error}", file=sys.stderr)
    elif args.command == "daily":
        print(render_markdown_report("日报", args.days, args.limit))
    elif args.command == "weekly":
        print(render_markdown_report("周报", args.days, args.limit))
    elif args.command == "run":
        inserted, errors = collect()
        if args.enhance or os.getenv("ENABLE_LLM") == "1":
            enhanced, llm_errors = enhance_with_llm(args.limit)
            print(f"enhanced={enhanced}")
            errors.extend(llm_errors)
        csv_path = export_notion_csv()
        if args.sync_notion or os.getenv("ENABLE_NOTION_SYNC") == "1":
            synced, notion_errors = sync_notion(args.limit)
            print(f"synced={synced}")
            errors.extend(notion_errors)
        report_path = render_markdown_report("日报", 1, 80)
        print(f"inserted={inserted}")
        print(f"csv={csv_path}")
        print(f"report={report_path}")
        for error in errors:
            print(f"warn={error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
