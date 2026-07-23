"""Run daily intelligence pipelines with structured operations logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_LOG_ROOT = PROJECT_ROOT / "data" / "run_logs"
TIMEZONE = ZoneInfo("Asia/Shanghai")
SECRET_NAMES = ("NOTION_TOKEN", "OPENAI_API_KEY", "LLM_API_KEY")


@dataclass
class StepResult:
    name: str
    command: List[str]
    status: str
    exit_code: int
    started_at: str
    finished_at: str
    duration_seconds: float
    log_path: str
    error_excerpt: str = ""


def now_iso() -> str:
    return datetime.now(TIMEZONE).replace(microsecond=0).isoformat()


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower() or "step"


def redact(text: str, env: Optional[Dict[str, str]] = None) -> str:
    values = env or os.environ
    for name in SECRET_NAMES:
        secret = values.get(name, "")
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


def run_step(index: int, name: str, command: Sequence[str], run_dir: Path) -> StepResult:
    started_at = now_iso()
    started = time.monotonic()
    completed = subprocess.run(
        list(command), cwd=PROJECT_ROOT, env=os.environ.copy(), text=True,
        capture_output=True, check=False,
    )
    stdout = redact(completed.stdout or "")
    stderr = redact(completed.stderr or "")
    finished_at = now_iso()
    duration = round(time.monotonic() - started, 3)
    log_file = run_dir / f"{index:02d}_{slug(name)}.log"
    log_file.write_text(
        "\n".join([
            f"step: {name}", f"started_at: {started_at}", f"finished_at: {finished_at}",
            f"duration_seconds: {duration}", f"exit_code: {completed.returncode}",
            f"command: {json.dumps(list(command), ensure_ascii=False)}", "",
            "===== STDOUT =====", stdout.rstrip(), "", "===== STDERR =====", stderr.rstrip(), "",
        ]),
        encoding="utf-8",
    )
    error_source = stderr.strip() or stdout.strip()
    return StepResult(
        name=name, command=list(command),
        status="success" if completed.returncode == 0 else "failed",
        exit_code=completed.returncode, started_at=started_at, finished_at=finished_at,
        duration_seconds=duration, log_path=str(log_file.relative_to(PROJECT_ROOT)),
        error_excerpt=error_source[-1000:] if completed.returncode else "",
    )


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def notion_state(summary: Optional[Dict[str, Any]]) -> str:
    if not summary:
        return "unknown"
    notion = summary.get("notion") or {}
    if not notion.get("enabled"):
        return "not_configured"
    return "success" if notion.get("success") else "failed"


def build_diagnostics(
    steps: Sequence[StepResult],
    general: Optional[Dict[str, Any]],
    competitive: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for step in steps:
        if step.status == "failed":
            items.append({
                "severity": "error", "code": f"STEP_FAILED_{slug(step.name).upper().replace('-', '_')}",
                "message": f"{step.name} 执行失败，退出码 {step.exit_code}。",
                "action": f"查看 {step.log_path} 的 STDERR 和错误摘要。",
            })
    if general is None:
        items.append({"severity": "error", "code": "GENERAL_SUMMARY_MISSING",
                      "message": "通用情报 Pipeline 未生成摘要。",
                      "action": "检查 Pipeline 日志、数据源和输出目录权限。"})
    else:
        counts = general.get("counts", {})
        if int(counts.get("raw_items", 0) or 0) == 0:
            items.append({"severity": "warning", "code": "GENERAL_ZERO_RAW_ITEMS",
                          "message": "通用情报采集为 0 条。",
                          "action": "检查 RSSHub、网络、数据源地址和关键词过滤。"})
        if notion_state(general) == "failed":
            items.append({"severity": "error", "code": "GENERAL_NOTION_FAILED",
                          "message": "通用情报同步 Notion 失败。",
                          "action": "检查 Token、数据库/页面 ID 和 Integration 授权。"})
    if competitive is None:
        items.append({"severity": "error", "code": "COMPETITIVE_SUMMARY_MISSING",
                      "message": "竞品采购雷达未生成摘要。",
                      "action": "检查竞品雷达日志、配置和输出目录。"})
    else:
        counts = competitive.get("counts", {})
        if int(counts.get("signals", 0) or 0) == 0:
            items.append({"severity": "warning", "code": "COMPETITIVE_ZERO_SIGNALS",
                          "message": "竞品采购雷达未识别到有效信号。",
                          "action": "检查竞品源、搜索 RSS、关键词和 RSSHub。"})
        coverage = float(counts.get("competitor_coverage_rate", 0) or 0)
        if coverage < 0.3:
            items.append({"severity": "warning", "code": "LOW_COMPETITOR_COVERAGE",
                          "message": f"重点竞品覆盖率仅为 {coverage:.0%}。",
                          "action": "检查零信号竞品的数据源健康度。"})
        if notion_state(competitive) == "failed":
            items.append({"severity": "error", "code": "COMPETITIVE_NOTION_FAILED",
                          "message": "竞品采购日报同步 Notion 失败。",
                          "action": "检查竞品父页面 ID 和页面授权。"})
    return items


def overall_status(steps: Sequence[StepResult], diagnostics: Sequence[Dict[str, str]]) -> str:
    if any(step.status == "failed" for step in steps) or any(d["severity"] == "error" for d in diagnostics):
        return "failed"
    return "warning" if any(d["severity"] == "warning" for d in diagnostics) else "success"


def build_commands(date_text: str, skip_tests: bool) -> List[tuple[str, List[str]]]:
    rsshub = os.getenv("RSSHUB_BASE_URL") or "https://rsshub.app"
    result: List[tuple[str, List[str]]] = []
    if not skip_tests:
        result.append(("unit-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]))
    general = [sys.executable, "src/pipeline.py", "--date", date_text, "--rsshub-base", rsshub,
               "--skip-low-stability", "--top-n", "10", "--notion-event-limit", "20"]
    if os.getenv("NOTION_DATABASE_ID"):
        general += ["--notion-database-id", os.environ["NOTION_DATABASE_ID"]]
    if os.getenv("NOTION_PARENT_PAGE_ID"):
        general += ["--notion-parent-page-id", os.environ["NOTION_PARENT_PAGE_ID"]]
    result.append(("general-pipeline", general))
    competitive = [sys.executable, "src/competitive_procurement.py", "--date", date_text,
                   "--rsshub-base", rsshub, "--limit-per-source", "5", "--top-n", "20"]
    parent = os.getenv("NOTION_COMPETITIVE_PARENT_PAGE_ID") or os.getenv("NOTION_PARENT_PAGE_ID")
    if parent:
        competitive += ["--notion-parent-page-id", parent]
    result.append(("competitive-radar", competitive))
    return result


def render_markdown(summary: Dict[str, Any]) -> str:
    metrics = summary["metrics"]
    lines = [
        f"# AI 情报系统运行摘要 - {summary['date']}", "",
        f"- 状态：**{summary['status']}**", f"- Run ID：`{summary['run_id']}`",
        f"- 触发方式：`{summary['trigger']}`", f"- Commit：`{summary.get('commit_sha') or 'local'}`",
        f"- 总耗时：{summary['duration_seconds']} 秒", "", "## 步骤状态", "",
        "| 步骤 | 状态 | 退出码 | 耗时 | 日志 |", "| --- | --- | ---: | ---: | --- |",
    ]
    for step in summary["steps"]:
        lines.append(f"| {step['name']} | {step['status']} | {step['exit_code']} | {step['duration_seconds']}s | `{step['log_path']}` |")
    lines += ["", "## 核心指标", "",
              f"- 通用原始资讯：{metrics.get('general_raw_items')}",
              f"- 通用日报事件：{metrics.get('general_report_events')}",
              f"- 竞品采购信号：{metrics.get('competitive_signals')}",
              f"- 高可信竞品信号：{metrics.get('competitive_high_confidence')}",
              f"- 竞品覆盖：{metrics.get('competitors_with_signals')}/{metrics.get('watchlist_competitors')}",
              f"- 通用 Notion：{metrics.get('general_notion')}",
              f"- 竞品 Notion：{metrics.get('competitive_notion')}", "", "## 诊断结果", ""]
    if summary["diagnostics"]:
        for item in summary["diagnostics"]:
            lines.append(f"- **{item['severity'].upper()} / {item['code']}**：{item['message']} 处理建议：{item['action']}")
    else:
        lines.append("- 未发现异常。")
    lines += ["", "## 产物位置", "", f"- 运行目录：`{summary['run_directory']}`",
              "- 通用日报：`data/reports/`", "- 竞品日报：`data/competitive_procurement/`",
              "- 完整日志和摘要会随 GitHub Actions Artifact 上传。", ""]
    return "\n".join(lines)


def write_github_summary(markdown: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(markdown + ("" if markdown.endswith("\n") else "\n"))


def run_daily(date_text: str, run_id: str, trigger: str, skip_tests: bool = False) -> Dict[str, Any]:
    started_at = now_iso()
    started = time.monotonic()
    run_dir = RUN_LOG_ROOT / date_text / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    steps = [run_step(i, name, command, run_dir) for i, (name, command) in enumerate(build_commands(date_text, skip_tests), 1)]
    general = read_json(PROJECT_ROOT / "data" / "reports" / f"pipeline_summary_{date_text}.json")
    competitive = read_json(PROJECT_ROOT / "data" / "competitive_procurement" / f"summary_{date_text}.json")
    diagnostics = build_diagnostics(steps, general, competitive)
    general_counts = (general or {}).get("counts", {})
    competitive_counts = (competitive or {}).get("counts", {})
    summary: Dict[str, Any] = {
        "schema_version": 1, "date": date_text, "run_id": run_id, "trigger": trigger,
        "repository": os.getenv("GITHUB_REPOSITORY", ""), "commit_sha": os.getenv("GITHUB_SHA", ""),
        "started_at": started_at, "finished_at": now_iso(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": overall_status(steps, diagnostics),
        "run_directory": str(run_dir.relative_to(PROJECT_ROOT)),
        "steps": [asdict(step) for step in steps],
        "metrics": {
            "general_raw_items": general_counts.get("raw_items"),
            "general_report_events": general_counts.get("report_events_after_filter"),
            "competitive_signals": competitive_counts.get("signals"),
            "competitive_high_confidence": competitive_counts.get("high_confidence"),
            "watchlist_competitors": competitive_counts.get("watchlist_competitors"),
            "competitors_with_signals": competitive_counts.get("competitors_with_signals"),
            "competitor_coverage_rate": competitive_counts.get("competitor_coverage_rate"),
            "general_notion": notion_state(general), "competitive_notion": notion_state(competitive),
        },
        "diagnostics": diagnostics,
        "source_summaries": {"general": general, "competitive": competitive},
    }
    markdown = render_markdown(summary)
    (run_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "run_summary.md").write_text(markdown, encoding="utf-8")
    RUN_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    (RUN_LOG_ROOT / "latest_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (RUN_LOG_ROOT / "latest_run.md").write_text(markdown, encoding="utf-8")
    write_github_summary(markdown)
    return summary


def resolve_date(value: str) -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d") if value == "today" else value


def main() -> None:
    parser = argparse.ArgumentParser(description="运行每日情报任务并生成运维日志")
    parser.add_argument("--date", default="today")
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID") or datetime.now(TIMEZONE).strftime("%H%M%S"))
    parser.add_argument("--trigger", default=os.getenv("GITHUB_EVENT_NAME") or "local")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args()
    summary = run_daily(resolve_date(args.date), args.run_id, args.trigger, args.skip_tests)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] == "failed" and not args.allow_failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
