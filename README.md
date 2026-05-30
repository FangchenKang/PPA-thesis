# PPA Thesis 期刊文章资料库

这个仓库用于追踪和整理政治学、公共管理相关期刊文章。当前包括两个部分：

- 自动追踪：每天检查期刊更新，生成日报、周报、月报和季报。
- 静态网站：把已经爬取并整理好的历史文章数据生成本地 JSON 索引，并通过 GitHub Pages 展示。

本网站不存储 PDF，不下载 PDF，不展示文章全文，只展示题目、作者、摘要、关键词、原文链接和后续补充的 AI 总结。

## 目录结构

```text
.
├── .github/workflows/
│   ├── journal-digest.yml        # 每日追踪和邮件报告
│   ├── historical-backfill.yml   # 手动历史数据回填
│   └── pages.yml                 # GitHub Pages 静态网站部署
├── config/
│   ├── feed_overrides.json       # RSS 或期刊入口覆盖配置
│   └── journal_metadata.json     # 网站使用的期刊元数据配置
├── data/
│   ├── journals.json             # 原始期刊清单
│   ├── items/                    # 每日新增文章快照
│   └── history/                  # 历史文章书目信息
├── docs/
│   └── HISTORICAL_BACKFILL.md    # 历史数据回填说明
├── journal_tracker/
│   ├── tracker.py                # 追踪、回填、拆分、总结命令
│   └── site_index.py             # 静态网站索引生成脚本
├── reports/                      # 日报、周报、月报、季报
└── site/
    ├── index.html                # 静态网站入口
    ├── app.js                    # 前端路由、搜索和展示逻辑
    ├── styles.css                # 网站样式
    └── data/
        ├── journals.json         # 网站期刊索引
        ├── issues.json           # 网站年份、月份或期次索引
        ├── articles.json         # 网站文章索引
        └── stats.json            # 网站统计信息
```

## 静态网站功能

- 首页按学科和期刊类型展示所有期刊。
- 期刊卡片显示期刊名称、学科、期刊类型、分区、年份范围和文章数量。
- 期刊页面按年份展示月份或期次。
- 文章页面展示题目、作者、摘要、关键词、AI 总结区域和原文链接。
- 如果没有摘要，显示“暂无摘要”。
- 如果没有 AI 总结，显示“暂无 AI 总结”。
- 顶部搜索框可搜索期刊名称、文章题目、作者、关键词、摘要和 AI 总结。
- 搜索完全基于本地 JSON 文件，不联网，不需要后端。

`site/data/articles.json` 使用 `schema + dictionaries + articles` 的紧凑 JSON 结构保存文章级信息。这样可以保留全部文章索引，同时避免超过 GitHub 单文件大小限制。

## 期刊元数据

网站优先读取：

```bash
config/journal_metadata.json
```

每条记录包含：

- `journal_name`
- `display_name`
- `discipline`
- `journal_type`
- `language`
- `quartile`
- `notes`

如果后续需要调整学科、类型、语言、分区或显示名称，直接编辑这个文件，然后重新生成网站索引。

生成或补齐期刊元数据：

```bash
python -m journal_tracker seed-site-metadata
```

从 `data/journals.json` 重新生成期刊元数据：

```bash
python -m journal_tracker seed-site-metadata --overwrite
```

## 数据索引生成

生成网站所需的本地 JSON 索引：

```bash
python -m journal_tracker build-site-index
```

如果希望构建前自动补齐缺失的期刊元数据：

```bash
python -m journal_tracker build-site-index --seed-metadata
```

索引生成脚本只读取 `data/history/` 下已有数据，不移动、不删除、不覆盖原始爬虫数据。

## 本地运行

静态网站不需要安装前端依赖。先生成索引，然后启动本地静态服务器：

```bash
python -m journal_tracker build-site-index
python -m http.server 4173 --directory site
```

浏览器打开：

```text
http://localhost:4173
```

## 构建方式

当前网站是纯静态 HTML、CSS、JavaScript，不需要 Vite 或 React 构建。构建步骤就是生成站点索引：

```bash
python -m journal_tracker build-site-index
```

## GitHub Pages 部署

仓库已经包含 GitHub Pages workflow：

```text
.github/workflows/pages.yml
```

部署步骤：

1. 进入 GitHub 仓库的 `Settings -> Pages`。
2. 在 `Build and deployment` 中选择 `GitHub Actions`。
3. 推送到 `main` 分支，或手动运行 `Static journal website` workflow。
4. workflow 会重新生成 `site/data/*.json`，并把 `site/` 作为 GitHub Pages 页面发布。

## 每日追踪

每日追踪 workflow：

```text
.github/workflows/journal-digest.yml
```

GitHub Actions 使用 UTC 时间：

```yaml
cron: "0 0 * * *"
```

这对应北京时间每天 08:00。

邮件发送需要在 GitHub 仓库中配置 SMTP secrets：

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`

可选 AI 总结密钥：

- `LLM_API_KEY`

不要把 API key、SMTP 密码或其他密钥写进仓库文件。
