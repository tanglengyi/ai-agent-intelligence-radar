# rule_engine.py 使用说明

## 1. 这个文件解决什么问题

`src/rule_engine.py` 是 AI/Agent 情报雷达的第一版规则引擎。

它解决的问题不是“抓新闻”，而是把一条新闻加工成可判断、可输出、可变现的情报。

输入：

```json
{
  "title": "豆包部分智能体能力疑似调整",
  "text": "部分拟人陪伴类智能体下架，开发者社区讨论与合规整改有关。",
  "source_name": "晚点 LatePost 公众号 RSSHub",
  "source_url": "https://example.com",
  "published_at": "2026-07-05T10:00:00+08:00"
}
```

输出：

1. 事件类型；
2. 多维评分；
3. 推荐动作；
4. 商业洞察；
5. 合规风险；
6. 竞品信号；
7. 面试表达；
8. 日报 Markdown 条目。

## 2. 它为什么叫“具备洞察”的规则引擎

普通 RSS 系统只做：

```text
抓取 → 标题 → 链接 → 摘要
```

这个规则引擎做：

```text
抓取 → 事件分类 → 来源可信度 → 趋势判断 → 商业价值 → 个人相关度 → 合规风险 → 行动建议 → 日报表达
```

它对应你的底层逻辑：

> RSS 是原材料，真正可售卖的是行业判断、合规预警、竞品信号、赛道机会。

## 3. 文件位置

```text
自动化情报收集系统/src/rule_engine.py
```

依赖配置：

```text
自动化情报收集系统/config/sources.json
自动化情报收集系统/config/scoring_rules.json
自动化情报收集系统/config/event_types.json
自动化情报收集系统/config/output_schema.json
自动化情报收集系统/config/personal_focus.json
```

## 4. 命令行使用

### 4.1 直接传参数

```bash
cd 自动化情报收集系统
python src/rule_engine.py \
  --title "OpenAI Agents SDK 发布新版本" \
  --text "新增 tracing、handoff、tool call 能力，提升 Agent 工作流可观测性。" \
  --source "OpenAI Agents SDK GitHub Releases" \
  --url "https://github.com/openai/openai-agents-python/releases"
```

### 4.2 从 stdin 读取 JSON

```bash
echo '{"title":"豆包部分智能体能力调整","text":"拟人陪伴类智能体下架，可能与内容安全和合规有关。","source_name":"晚点 LatePost 公众号 RSSHub"}' | python src/rule_engine.py --stdin
```

### 4.3 只输出 JSON

```bash
python src/rule_engine.py \
  --title "Cursor Changelog 更新" \
  --text "新增 coding agent 权限控制能力" \
  --source "Cursor Changelog" \
  --json-only
```

## 5. 输入字段

| 字段 | 必填 | 含义 |
|---|---|---|
| `title` | 是 | 新闻/公告/招聘/社媒信号标题 |
| `text` | 否 | 正文或摘要 |
| `source_name` | 否 | 来源名称，最好和 `sources.json` 中的 `name` 一致 |
| `source_url` | 否 | 原文链接 |
| `published_at` | 否 | 发布时间 |
| `channel` | 否 | 频道，不填则从 sources.json 推断 |
| `evidence_level` | 否 | 证据等级，不填则从 sources.json 推断 |
| `language` | 否 | zh/en/mixed |

## 6. 输出字段

| 字段 | 含义 |
|---|---|
| `event_types` | 多事件标签 |
| `primary_event_type` | 主事件标签 |
| `scores` | 多维评分 |
| `recommended_actions` | 推荐动作 |
| `insight.decision_value` | 为什么重要 |
| `insight.business_opportunity` | 商业机会 |
| `insight.compliance_warning` | 合规风险 |
| `insight.competitor_signal` | 竞品信号 |
| `insight.interview_angle` | 面试表达 |
| `markdown` | 可直接进入日报的 Markdown 条目 |

## 7. 评分逻辑

当前评分维度：

| 维度 | 说明 |
|---|---|
| `freshness_score` | 新鲜度 |
| `evidence_score` | 来源可信度 |
| `trend_score` | 趋势强度 |
| `business_score` | 商业价值 |
| `personal_relevance_score` | 和唐文怡个人目标的相关度 |
| `risk_score` | 风险/预警价值 |

总分公式来自 `config/scoring_rules.json`：

```text
final_score = freshness*0.15 + evidence*0.20 + trend*0.20 + business*0.20 + personal*0.20 + risk*0.05
```

## 8. 推荐动作

| 动作 | 含义 |
|---|---|
| `save_to_knowledge_base` | 进入知识库 |
| `track_follow_up` | 持续追踪 |
| `convert_to_interview_story` | 转成面试案例 |
| `create_product_insight` | 沉淀产品洞察 |
| `ignore` | 忽略或低频归档 |
| `archive_only` | 仅归档 |

## 9. 对变现逻辑的支持

这版规则引擎已经内置了四类可变现判断：

### 9.1 行业判断

通过 `trend_score` 和 `event_types` 判断一条信息是否代表行业方向变化。

适合输出：

```text
周度行业复盘
AI Agent 赛道趋势判断
专题报告
```

### 9.2 合规预警

通过 `risk_score`、`policy_regulation`、`shutdown_or_removal`、`security_safety_risk` 判断是否需要预警。

适合输出：

```text
AI 合规政策月报
智能体下架风险清单
企业 AI 产品整改建议
```

### 9.3 竞品信号

通过 `product_launch`、`feature_update`、`pricing_change`、`shutdown_or_removal` 判断平台动作。

适合输出：

```text
竞品动态追踪
Agent 平台能力地图
产品经理面试案例
```

### 9.4 赛道机会

通过 `business_score`、`personal_relevance_score`、`focus_topics` 判断是否能转成商业机会。

适合输出：

```text
专题报告
咨询服务
私域会员内容
企业情报雷达搭建
```

## 10. 后续扩展方向

### 10.1 接 RSS 采集器

新增：

```text
src/rss_collector.py
```

负责从 `sources.json` 读取 `feed_url` 和 `rsshub_path`，抓取原始文章。

### 10.2 接日报生成器

新增：

```text
src/report_generator.py
```

负责把多条 `EvaluationResult` 按 `final_score` 排序，生成每日 Markdown 报告。

### 10.3 接事件归并器

新增：

```text
src/event_merger.py
```

负责把同一事件的多来源报道合并，比如：

```text
官方公告 + 媒体解读 + 社媒讨论 + 招聘变化
```

合并成一个高价值事件。

### 10.4 接 LLM 洞察增强

当前版本是规则引擎，不依赖 LLM。

后续可以加：

```text
src/llm_insight_enhancer.py
```

用于把规则引擎输出再加工成更像报告的自然语言洞察。

## 11. 下一步建议

优先顺序：

```text
1. rule_engine.py：已完成
2. tests/test_rule_engine.py：补测试样例
3. rss_collector.py：接 RSS 数据
4. report_generator.py：生成日报
5. event_merger.py：多源交叉验证
```

不要急着直接做大而全自动化。先验证一件事：

> 输入一条新闻，系统是否能判断它的价值。
