# AI/Agent 自动化情报收集系统

这是一个面向 AI 产品经理求职、复盘和作品集沉淀的轻量情报系统。

目标不是收藏更多新闻，而是把信息源变成稳定管道：

信息源 -> 自动抓取 -> 去重入库 -> 分类打标签 -> Notion/报告导出 -> 周复盘 -> 产品判断/需求池/面试表达

## 已落地能力

- `config/sources.json`: 六类信息源配置，覆盖政策监管、Agent 产品、AI Infra、安全合规、投融资、招聘竞品。
- `scripts/intel_radar.py`: 无第三方依赖的抓取、去重、分类、LLM 摘要、Notion 同步、导出、报告脚本。
- `data/intelligence.sqlite3`: 本地 SQLite 情报库，首次运行后生成。
- `exports/notion_import.csv`: 可导入 Notion 的数据库 CSV。
- `reports/YYYY-MM-DD-日报.md`: 每日复盘报告。
- `reports/YYYY-MM-DD-周报.md`: 每周复盘报告。
- `docs/需求池.csv`: 从产品经理视角拆好的需求池和状态。
- `docs/产品规划.md`: MVP 到 V2 的迭代路线。
- `docs/解决方案设计.md`: 从需求分析到技术落地的系统方案。
- `docs/执行节奏.md`: 每天、每周、每月应该做什么。
- `docs/Notion同步配置.md`: Notion API 同步字段和授权配置。

## 快速开始

```bash
chmod +x scripts/run_daily.sh scripts/run_weekly.sh
python3 scripts/intel_radar.py run
```

运行后查看：

```bash
open exports/notion_import.csv
open reports
```

## 常用命令

```bash
# 只抓取新增信息
python3 scripts/intel_radar.py collect

# 导出 Notion CSV
python3 scripts/intel_radar.py export

# 生成日报
python3 scripts/intel_radar.py daily

# 生成周报
python3 scripts/intel_radar.py weekly

# 一次性执行：抓取 + 导出 + 日报
python3 scripts/intel_radar.py run

# LLM 三句话摘要，需要先配置 OPENAI_API_KEY 或 LLM_API_KEY
python3 scripts/intel_radar.py enhance --limit 20

# 同步到 Notion，需要先配置 NOTION_TOKEN 和 NOTION_DATA_SOURCE_ID
python3 scripts/intel_radar.py notion-sync --limit 50

# 一次性执行：抓取 + LLM 摘要 + 导出 + Notion 同步 + 日报
python3 scripts/intel_radar.py run --enhance --sync-notion --limit 30
```

## Notion 使用方式

1. 在 Notion 新建数据库，字段名保持和 `templates/notion_database_template.csv` 一致。
2. 给 Notion Integration 授权这个数据库。
3. 按 [Notion同步配置.md](/Users/tanglengyi/Documents/自动化情报收集系统/docs/Notion同步配置.md) 配置 `NOTION_TOKEN` 和 `NOTION_DATA_SOURCE_ID`。
4. 运行 `python3 scripts/intel_radar.py notion-sync --limit 50`。
5. 如果暂时不配置 API，也可以继续导入 `exports/notion_import.csv`。
6. 推荐视图：
   - 今日新增：按 `收集时间` 倒序。
   - 官方/公司公告：筛选 `证据等级 = 官方原文` 或 `公司公告`。
   - 面试素材：筛选 `可行动作` 包含 `面试话术`。
   - B端机会：筛选 `影响对象` 包含 `B端`。
   - 待复盘：筛选 `状态 = 未复盘`。

## 环境变量

复制模板：

```bash
cp .env.example .env
```

关键配置：

- `RSSHUB_BASE_URL`: RSSHub 实例地址。不填时默认 `https://rsshub.app`，长期使用建议换成自建实例。
- `OPENAI_API_KEY` 或 `LLM_API_KEY`: 用于生成“三句话摘要”。
- `LLM_BASE_URL`: OpenAI-compatible 服务地址。
- `LLM_MODEL`: 摘要模型，默认 `gpt-4.1-mini`，可改成你账号可用的模型。
- `NOTION_TOKEN`: Notion integration secret。
- `NOTION_DATA_SOURCE_ID`: Notion 数据源 ID。旧数据库可尝试 `NOTION_DATABASE_ID`。
- `ENABLE_LLM=1`: 让 `run` 默认执行 LLM 摘要。
- `ENABLE_NOTION_SYNC=1`: 让 `run` 默认执行 Notion 同步。

## RSSHub 使用方式

`config/sources.json` 支持三种来源：

- `feed_url`: 直接 RSS/Atom。
- `rsshub_path`: 自动拼接 `RSSHUB_BASE_URL`，适合公众号、GitHub Release、微博等。
- `url`: 普通网页监控兜底。

例子：

```json
{
  "name": "Dify GitHub Releases",
  "rsshub_path": "/github/releases/langgenius/dify",
  "url": "https://github.com/langgenius/dify/releases"
}
```

公众号路由依赖 RSSHub 实例能力，公开实例可能失败或限流；失败会记录 warning，不会影响其它来源。

## 判断原则

一条信息不要立刻下结论。至少满足下面任意两项，再进入“我的判断”：

- 官方源头或公司公告出现明确原文。
- 平台动作和政策时间点接近。
- 招聘 JD、产品更新、媒体报道出现同向信号。
- 海外和国内同时出现类似监管或产品变化。
- 对 B 端能力有明确传导：权限、日志、审计、人工接管、数据边界、成本、SLA。
