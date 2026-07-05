# Notion 同步配置

## 1. 新建 Integration

1. 打开 Notion 的 Integrations 页面。
2. 创建一个 internal integration。
3. 复制 secret，填入 `.env`：

```bash
NOTION_TOKEN=secret_xxx
```

## 2. 建数据库

在 Notion 里新建一个数据库，字段名建议完全使用下面这些中文名：

| 字段名 | 类型 |
| --- | --- |
| 标题 | Title |
| 来源 | Select |
| 类型 | Select |
| 方向 | Multi-select |
| 影响对象 | Multi-select |
| 影响判断 | Select |
| 证据等级 | Select |
| 我的判断 | Text |
| 可行动作 | Text |
| 原文链接 | URL |
| 状态 | Select |
| 收集时间 | Date |

## 3. 授权数据库

在 Notion 数据库页面右上角 `...` 菜单里，把刚创建的 integration 加进连接。

## 4. 填数据源 ID

推荐使用新版数据源 ID：

```bash
NOTION_DATA_SOURCE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

如果你的 Notion 页面仍使用旧数据库 ID，也可以先试：

```bash
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## 5. 运行同步

```bash
python3 scripts/intel_radar.py notion-sync --limit 50
```

或者一键跑完全流程：

```bash
python3 scripts/intel_radar.py run --enhance --sync-notion --limit 30
```

## 6. 常见问题

- `missing NOTION_TOKEN`: `.env` 没填 token，或没有复制 `.env.example`。
- `missing NOTION_DATA_SOURCE_ID`: 没填数据源 ID；旧库可尝试 `NOTION_DATABASE_ID`。
- 字段类型错误：Notion 字段名或类型和上表不一致。
- 403/unauthorized：数据库没有授权给 integration。
- 同步重复：脚本会记录 `notion_page_id`，已同步记录不会重复创建。
