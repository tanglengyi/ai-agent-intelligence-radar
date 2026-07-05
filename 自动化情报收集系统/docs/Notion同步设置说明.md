# Notion 同步设置说明

## 1. 现在是否能直接在 Notion 里看到？

默认还不能。

当前 pipeline 默认输出到本地文件：

```text
RSS/RSSHub 采集
→ rule_engine.py 逐条评分和生成洞察
→ event_merger.py 多源事件归并
→ report_generator.py 生成日报 Markdown
→ 输出 pipeline_summary
```

如果要在 Notion 里直接看到，需要开启 Notion 同步参数。

现在项目已经新增：

```text
src/notion_exporter.py
```

并且 `src/pipeline.py` 已支持可选同步到 Notion。

## 2. Notion 里建议建两个地方

### 2.1 情报事件数据库

用于存放每条合并后的事件。

建议数据库字段：

| 字段名 | Notion 类型 | 说明 |
|---|---|---|
| `Name` | Title | 事件标题 |
| `Score` | Number | 最高评分 |
| `Confidence` | Select | high / medium / low |
| `Type` | Select | primary_event_type |
| `Sources` | Multi-select | 来源 |
| `Actions` | Multi-select | 建议动作 |
| `Tags` | Multi-select | 标签 |
| `Summary` | Text | 事件摘要 |
| `Strategic Signal` | Text | 战略信号 |
| `URL` | URL | 原文链接 |

注意：字段名要尽量和上表一致，否则 Notion API 会报错。

### 2.2 日报父页面

用于每天生成一篇日报子页面。

比如你可以在 Notion 新建页面：

```text
AI Agent Intelligence Radar Daily
```

之后每天的日报会作为它下面的子页面。

## 3. 获取 Notion Token

1. 打开 Notion Integrations；
2. 创建 Internal Integration；
3. 复制 token；
4. 在 Notion 数据库和日报父页面里，点击 Share，把这个 Integration 加进去。

本地设置环境变量：

```bash
export NOTION_TOKEN="secret_xxx"
```

## 4. 运行方式

### 4.1 只本地生成，不同步 Notion

```bash
cd 自动化情报收集系统

python src/pipeline.py \
  --date today \
  --skip-low-stability \
  --limit-per-source 5
```

### 4.2 同步合并事件到 Notion 数据库

```bash
python src/pipeline.py \
  --date today \
  --skip-low-stability \
  --limit-per-source 5 \
  --notion-database-id YOUR_DATABASE_ID
```

### 4.3 同步日报到 Notion 页面

```bash
python src/pipeline.py \
  --date today \
  --skip-low-stability \
  --limit-per-source 5 \
  --notion-parent-page-id YOUR_PAGE_ID
```

### 4.4 同时同步事件数据库和日报页面

```bash
python src/pipeline.py \
  --date today \
  --skip-low-stability \
  --limit-per-source 5 \
  --notion-database-id YOUR_DATABASE_ID \
  --notion-parent-page-id YOUR_PAGE_ID \
  --notion-event-limit 20
```

## 5. 同步后 Notion 里能看到什么

### 数据库里能看到

每条合并事件：

```text
标题
评分
置信度
事件类型
来源
建议动作
标签
事件摘要
战略信号
原文链接
```

### 日报页面里能看到

每天一篇 Markdown 转换后的日报：

```text
今日结论
今日最高优先级信号
政策 / 合规 / 风险
Agent 产品 / 平台更新
Infra / API / 算力
投融资 / 商业化
招聘 / 岗位 / 能力信号
使用提醒
```

## 6. 重要提醒

1. Notion 同步不是默认开启，因为需要你自己的 token 和 database_id；
2. 社媒、招聘、RSSHub 低稳定源只作为弱信号，不能直接当事实结论；
3. Notion 只是展示和知识库沉淀层，不负责判断；
4. 真正的判断逻辑仍然在：

```text
config/scoring_rules.json
config/event_types.json
config/personal_focus.json
src/rule_engine.py
src/event_merger.py
```

## 7. 后续可扩展

下一步可以增加：

```text
src/notion_schema_creator.py
```

用于自动创建 Notion 数据库字段。

目前版本先要求你手动建数据库字段，因为这样更安全，也避免误改已有 Notion 页面。
