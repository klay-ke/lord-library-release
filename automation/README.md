# 晨兴 EPUB 每周自动发布

工作流每周日 23:17（America/Los_Angeles）执行，也可在 GitHub Actions 手动运行。

顺序：

1. 从 Notion“七次特会”总页找到最新年份，并只检查该年份最底下的一卷。
2. 如果 `catalog.json` 没有该卷，进入新增流程。
3. 如果 catalog 已有该卷，但 `source` 是 `ios-html-export`、`stemofjesse-html`
   或 `generated-html`，重新检查是否出现了原生 EPUB；找到后覆盖 R2 原对象并更新 catalog。
4. 如果 catalog 已经是原生 EPUB，本周直接结束；历史来源标记继续兼容。
5. 只从 Paul 的 Notion 读取资源页中的 EPUB。
6. 没有 EPUB 时读取 Notion 中的 `epub&pdb` ZIP，并选择其中最大的 EPUB。
7. Notion 暂无原生文件时正常结束，保留现有书库，下次运行继续检查。不会访问 Stem of Jesse，也不会从 HTML 生成 EPUB。
8. 校验 EPUB ZIP、OPF、正文和 SHA-256。
9. 先上传 EPUB 并从公网回读校验，最后更新 R2 `catalog.json`。

## GitHub 配置

Repository secrets：

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`

Repository variables（可选，脚本已有当前默认值）：

- `R2_PUBLIC_BASE`
- `MORNING_CATALOG_URL`

本地只检查是否有新书：

```bash
python automation/sync_morning_epubs.py --dry-run
```
