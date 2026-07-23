# 运行日志目录

该目录用于保存 `scripts/run_daily_ops.py` 生成的运行日志和结构化摘要。

运行时结构：

```text
latest_run.json
latest_run.md
YYYY-MM-DD/<run_id>/
  01_unit-tests.log
  02_general-pipeline.log
  03_competitive-radar.log
  run_summary.json
  run_summary.md
```

运行数据默认不提交到 Git，而是通过 GitHub Actions Artifact 保存 30 天。仓库仅保留本说明文件。

线上查看路径：

```text
GitHub -> Actions -> Daily AI Intelligence Radar -> 对应 Run -> Artifacts
```

长期故障结论请写入：

```text
docs/故障排查记录.md
```
