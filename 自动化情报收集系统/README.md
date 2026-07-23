# AI/Agent 自动化情报收集系统

当前版本：**v1.2.0**

这是一个面向企业 AI 产品经理、AI Business Product、企业 AI 解决方案负责人、求职复盘和作品集沉淀的轻量情报系统。

目标不是收藏更多新闻，而是把外部信息变成稳定、可排查的决策输入：

```text
信息源
  -> 自动抓取
  -> 去重与分类
  -> 竞品归属 / 采购阶段 / 预算识别
  -> 可信度评分
  -> Notion / 日报 / 周报
  -> 运行摘要 / 故障诊断 / 日志留存
  -> 产品判断 / 机会池 / 需求池 / 面试表达
```

## 已落地能力

### 通用 AI/Agent 情报

- `config/sources.json`：覆盖政策监管、Agent 产品、AI Infra、安全合规、投融资、招聘竞品等信息源；
- `scripts/intel_radar.py`：抓取、去重、分类、LLM 摘要、Notion 同步、导出和报告；
- `src/pipeline.py`：采集、规则评分、事件归并、质量过滤、日报和 Notion 的端到端 Pipeline；
- `data/intelligence.sqlite3`：本地 SQLite 情报库；
- `exports/notion_import.csv`：可导入 Notion 的数据库 CSV；
- `reports/YYYY-MM-DD-日报.md`：每日复盘报告；
- `reports/YYYY-MM-DD-周报.md`：每周复盘报告。

### AI 竞品与采购情报雷达

- `config/competitive_procurement.json`：十类信号、采购阶段、评分规则和数据源；
- `config/competitor_watchlist.json`：固定追踪 10 个企业 AI 竞品；
- `src/competitive_procurement.py`：竞品归属、采购阶段识别、预算提取、评分、日报和 Notion 发布；
- `data/competitive_procurement/`：每日竞品采购结果；
- `.github/workflows/daily-ai-intelligence.yml`：每天北京时间 08:30 云端自动运行。

十类信号包括：政策监管、行业报告、竞品价格、招聘、招标采购、会议展会、客户年报、搜索社交、渠道伙伴和技术发展。

当前重点竞品：

1. Microsoft Copilot Studio；
2. Salesforce Agentforce；
3. ServiceNow AI Agents；
4. SAP Joule Studio；
5. UiPath Agentic Automation；
6. Palantir AIP；
7. Dify Enterprise；
8. 扣子企业版；
9. 阿里云百炼 / Model Studio；
10. 腾讯云智能体开发平台 ADP。

### 运行运维与故障排查

- `scripts/run_daily_ops.py`：统一运行测试、通用 Pipeline 和竞品雷达；
- 每一步记录退出码、耗时、stdout、stderr 和错误摘要；
- 自动生成 `success`、`warning`、`failed` 运行状态；
- 自动识别零采集、零竞品信号、低覆盖率、Summary 缺失和 Notion 失败；
- GitHub Actions 页面直接展示 Job Summary；
- `data/run_logs/` 保存逐步骤日志和结构化运行摘要；
- 即使任务失败，日报和日志仍会通过 Artifact 上传；
- 日志中的常用 Token 和 API Key 会自动脱敏。

## 产品、版本和运维文档

- [AI 竞品与采购情报雷达 PRD](docs/AI竞品与采购情报雷达_PRD.md)
- [竞品与采购情报雷达使用说明](docs/竞品与采购情报雷达.md)
- [运行与故障排查手册](docs/运行与故障排查手册.md)
- [故障排查记录](docs/故障排查记录.md)
- [版本更新记录](docs/版本更新记录.md)
- [产品规划](docs/产品规划.md)
- [解决方案设计](docs/解决方案设计.md)
- [执行节奏](docs/执行节奏.md)
- [Notion 同步配置](docs/Notion同步配置.md)
- [需求池](docs/需求池.csv)

## 快速开始

完整执行并生成运维日志：

```bash
cd 自动化情报收集系统
python3 scripts/run_daily_ops.py --date today
```

只运行通用情报：

```bash
python3 scripts/intel_radar.py run
```

只运行竞品与采购雷达：

```bash
python3 src/competitive_procurement.py \
  --date today \
  --limit-per-source 5 \
  --top-n 20
```

运行后查看：

```bash
open data/reports
open data/competitive_procurement
open data/run_logs/latest_run.md
```

## 常用命令

```bash
# 每日完整运行：测试 + 通用情报 + 竞品情报 + 运维摘要
python3 scripts/run_daily_ops.py --date today

# 指定日期
python3 scripts/run_daily_ops.py --date 2026-07-23

# 排查模式：失败时仍返回 0
python3 scripts/run_daily_ops.py --date today --allow-failure

# 跳过测试
python3 scripts/run_daily_ops.py --date today --skip-tests

# 只抓取新增信息
python3 scripts/intel_radar.py collect

# 导出 Notion CSV
python3 scripts/intel_radar.py export

# 生成通用日报
python3 scripts/intel_radar.py daily

# 生成周报
python3 scripts/intel_radar.py weekly

# 通用情报完整流程
python3 scripts/intel_radar.py run

# LLM 三句话摘要
python3 scripts/intel_radar.py enhance --limit 20

# 通用情报同步 Notion
python3 scripts/intel_radar.py notion-sync --limit 50

# 竞品与采购情报
python3 src/competitive_procurement.py --date today

# 运行全部测试
python3 -m unittest discover -s tests -p "test_*.py"
```

## 每日输出

通用情报：

```text
data/raw/
data/processed/
data/reports/
reports/
exports/
```

竞品采购：

```text
data/competitive_procurement/signals_YYYY-MM-DD.jsonl
data/competitive_procurement/daily_YYYY-MM-DD.md
data/competitive_procurement/summary_YYYY-MM-DD.json
```

运行运维：

```text
data/run_logs/
  latest_run.json
  latest_run.md
  YYYY-MM-DD/
    <run_id>/
      01_unit-tests.log
      02_general-pipeline.log
      03_competitive-radar.log
      run_summary.json
      run_summary.md
```

运行日志不会提交进 Git 历史，由 GitHub Actions Artifact 保存 30 天。

## 线上查看日报和日志

进入：

```text
GitHub -> Actions -> Daily AI Intelligence Radar
```

每次 Run 可查看：

- 工作流整体成功或失败；
- Job Summary 中的步骤状态、数据量、竞品覆盖率和 Notion 状态；
- Artifact 中的通用日报、竞品日报、JSON Summary 和逐步骤日志。

Artifact 名称：

```text
ai-intelligence-<github_run_id>
```

任务失败时，Artifact 上传步骤仍会执行。

## Notion 使用方式

1. 在 Notion 新建情报数据库和竞品日报父页面；
2. 给 Notion Integration 授权目标数据库和页面；
3. 按 [Notion 同步配置](docs/Notion同步配置.md) 设置 Token 和 ID；
4. 运行同步命令，或通过 GitHub Actions 每日自动执行；
5. 未配置 API 时，系统仍会生成本地文件和 GitHub Artifact。

推荐视图：

- 今日新增：按收集时间倒序；
- 官方和公司公告：筛选高证据等级；
- 采购机会：筛选采购阶段不等于 `unknown`；
- 明确预算：筛选 `budget_cny` 非空；
- 重点竞品：按 `competitor_product` 分组；
- 面试素材：筛选可行动作或手工状态；
- 待复盘：筛选状态为未复盘。

## 环境变量

复制模板：

```bash
cp .env.example .env
```

关键配置：

- `RSSHUB_BASE_URL`：RSSHub 实例地址，未配置时使用公共实例；
- `OPENAI_API_KEY` 或 `LLM_API_KEY`：LLM 摘要；
- `LLM_BASE_URL`：OpenAI-compatible 服务地址；
- `LLM_MODEL`：摘要模型；
- `NOTION_TOKEN`：Notion Integration Secret；
- `NOTION_DATA_SOURCE_ID`：通用情报数据源 ID；
- `NOTION_DATABASE_ID`：旧版数据库 ID 或通用事件数据库；
- `NOTION_PARENT_PAGE_ID`：通用日报父页面；
- `NOTION_COMPETITIVE_PARENT_PAGE_ID`：竞品采购日报父页面；
- `ENABLE_LLM=1`：通用流程默认执行 LLM 摘要；
- `ENABLE_NOTION_SYNC=1`：通用流程默认同步 Notion。

密钥只能存放在本地 `.env` 或 GitHub Secrets，不应提交到仓库。

## 定时任务

### GitHub Actions

`.github/workflows/daily-ai-intelligence.yml`：

- 每天北京时间 08:30 执行；
- 支持手动触发；
- 统一运行单元测试、通用情报和竞品采购情报；
- 有 Notion Secret 时自动同步；
- 自动生成 Job Summary 和诊断结果；
- 日报和运维日志作为 Artifact 保留 30 天。

### macOS launchd

本地模板每天 09:30 调用 `scripts/run_daily.sh`。该脚本已统一转到 `run_daily_ops.py`。本地任务依赖电脑开机，GitHub Actions 是主要定时保障。

## 数据源方式

`config/sources.json` 和竞品配置支持：

- `feed_url`：直接 RSS / Atom；
- `rsshub_path`：通过 RSSHub 转换；
- 官方域名搜索 RSS：用于捕捉官网更新和产品变化；
- 普通网页：作为人工关注或后续网页解析入口。

公众号、招聘、社媒和部分网页路由稳定性较低，失败不会中断其他来源。

## 判断原则

一条信息不要立刻下结论。至少满足以下任意两项，再进入正式判断：

- 官方源头、公司公告、招标、中标、合同或年报出现明确原文；
- 平台动作和政策时间点接近；
- 招聘 JD、产品更新、客户案例和采购信息出现同向信号；
- 多个独立来源出现相同变化；
- 能明确传导到权限、日志、审计、人工接管、数据边界、成本、SLA、交付或 ROI。

Notion 是知识资产库，不是资讯垃圾桶。低价值资讯可以采集，但不应全部入库。
