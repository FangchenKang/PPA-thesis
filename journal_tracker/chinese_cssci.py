from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .site_index import repair_text
from .tracker import HISTORY_JOURNALS_DIR, ROOT, clean_text, load_json, save_json


TARGET_DIR = ROOT / "data" / "chinese_political_cssci"
LOG_DIR = ROOT / "logs"
ERROR_LOG = LOG_DIR / "crawl_errors.log"
METADATA_PATH = ROOT / "config" / "journal_metadata.json"
USER_AGENT = "Mozilla/5.0 (compatible; PPA-thesis-metadata-audit/0.1; public metadata only)"
CURRENT_YEAR = 2026


EXPLICIT_TARGET_JOURNALS = {
    "政治学研究",
    "世界经济与政治",
    "国际政治研究",
    "国际政治科学",
    "国际安全研究",
    "国际问题研究",
    "国际展望",
    "国际观察",
    "国际论坛",
    "国际关系研究",
    "现代国际关系",
    "外交评论",
    "欧洲研究",
    "美国研究",
    "日本学刊",
    "当代亚太",
    "东北亚论坛",
    "东南亚研究",
    "南洋问题研究",
    "西亚非洲",
    "亚太安全与海洋研究",
    "太平洋学报",
    "台湾研究",
    "港澳研究",
    "当代美国评论",
    "当代世界",
    "和平与发展",
    "中国评论",
    "人权",
    "北京行政学院学报",
    "上海行政学院学报",
    "江苏行政学院学报",
    "甘肃行政学院学报",
    "行政论坛",
    "公共行政评论",
    "公共行政学报",
    "理论探索",
    "理论探讨",
    "理论学刊",
    "理论与改革",
    "求实",
    "探索",
    "中共中央党校，国家行政学院，学报",
    "中共中央党校（国家行政学院）学报",
    "中国人民公安大学学报，社会科学版",
}


ARTICLE_FIELDS = [
    "journal",
    "year",
    "issue",
    "month",
    "issue_label",
    "title",
    "original_title",
    "authors",
    "abstract",
    "keywords",
    "pages",
    "column",
    "source_url",
    "source_name",
    "source_file",
    "crawl_time",
    "data_status",
    "confidence",
    "notes",
    "sources",
]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_error(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def safe_segment(value: str) -> str:
    segment = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    segment = re.sub(r"\s+", "_", segment).strip(" ._")
    return segment[:120] or "untitled"


def normalize_title(value: str) -> str:
    value = clean_text(repair_text(value))
    value = value.replace("《", "").replace("》", "")
    value = re.sub(r"[：:][-—－]*", ":", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，,。；;！!？?、·.\-—_（）()【】\\[\\]“”\"']", "", value)
    return value.casefold()


def normalize_authors(value: Any) -> list[str]:
    value = repair_text(value)
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[;,，；、]\s*", str(value))
    return [clean_text(repair_text(item)) for item in items if clean_text(repair_text(item))]


def normalize_keywords(value: Any) -> list[str]:
    value = repair_text(value)
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[;,，；、]\s*", str(value))
    return [clean_text(repair_text(item)) for item in items if clean_text(repair_text(item))]


def source_name_for(url: str, fallback: str = "") -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "cqvip" in host:
        return "维普公开目录页"
    if "cnki" in host:
        return "知网公开目录页"
    if "ncpssd" in host or "nssd" in host:
        return "国家哲学社会科学文献中心"
    if "wanfang" in host:
        return "万方公开目录页"
    return fallback or host or "现有仓库数据"


def parse_year_issue_from_url(url: str) -> tuple[int | None, int | None, int | None, str]:
    match = re.search(r"/(?:QK|qk)/[^/]+/(\d{4})(\d{2})/", url)
    if match:
        return int(match.group(1)), int(match.group(2)), None, f"{match.group(1)}年第{int(match.group(2))}期"
    match = re.search(r"/journal/[^/]+/[^/]+/(\d{4})(?:[/?#]|$)", url)
    if match:
        return int(match.group(1)), None, None, f"{match.group(1)}年"
    return None, None, None, ""


def parse_year_issue_from_period(period: str) -> tuple[int | None, int | None, int | None, str]:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", period)
    if not match:
        return None, None, None, period
    year = int(match.group(1))
    issue = int(match.group(2))
    return year, issue, None, f"{year}年第{issue}期"


def load_target_metadata() -> list[dict[str, Any]]:
    rows = load_json(METADATA_PATH, [])
    candidates = []
    for row in rows:
        row = repair_text(row)
        journal_id = int(row["journal_id"])
        journal_name = clean_text(row.get("journal_name") or row.get("display_name"))
        if row.get("journal_type") != "CSSCI":
            continue
        is_political = row.get("discipline") == "政治学" or journal_name in EXPLICIT_TARGET_JOURNALS
        if not is_political:
            continue
        row["journal_name"] = journal_name
        row["target_source"] = "journal_metadata:CSSCI政治学" if row.get("discipline") == "政治学" else "user_explicit_target"
        candidates.append(row)

    by_name: dict[str, dict[str, Any]] = {}
    for row in candidates:
        key = clean_text(row["journal_name"]).replace("（", "(").replace("）", ")")
        existing = by_name.get(key)
        if not existing:
            by_name[key] = row
            continue
        existing_score = (existing.get("discipline") == "政治学", int(existing["journal_id"]))
        row_score = (row.get("discipline") == "政治学", int(row["journal_id"]))
        if row_score > existing_score:
            by_name[key] = row
    return sorted(by_name.values(), key=lambda item: int(item["journal_id"]))


def history_folder_for_id(journal_id: int) -> Path | None:
    matches = sorted(HISTORY_JOURNALS_DIR.glob(f"{journal_id:04d}-*"))
    return matches[0] if matches else None


def cqvip_code_from_rows(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        url = clean_text(row.get("url") or row.get("source_url"))
        match = re.search(r"/(?:QK|qk)/([^/]+)/", url)
        if match:
            return match.group(1).upper()
    return ""


def article_key(row: dict[str, Any]) -> str:
    title_key = normalize_title(row.get("title") or row.get("original_title") or "")
    author_key = "|".join(normalize_authors(row.get("authors")))[:120]
    source_url = clean_text(row.get("source_url"))
    if source_url:
        return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24]
    return hashlib.sha256(f"{title_key}|{author_key}|{row.get('year')}|{row.get('issue')}".encode("utf-8")).hexdigest()[:24]


def data_status(row: dict[str, Any]) -> str:
    missing = []
    if not row.get("title"):
        missing.append("missing_title")
    if not row.get("authors"):
        missing.append("missing_authors")
    if not row.get("abstract"):
        missing.append("missing_abstract")
    if not row.get("source_url"):
        missing.append("missing_source_url")
    if not row.get("year") or not row.get("issue"):
        missing.append("missing_year_or_issue")
    return "complete" if not missing else ";".join(missing)


def merge_article(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        incoming["sources"] = incoming.get("sources") or [
            {
                "source_name": incoming.get("source_name", ""),
                "source_url": incoming.get("source_url", ""),
                "crawl_time": incoming.get("crawl_time", ""),
                "confidence": incoming.get("confidence", ""),
            }
        ]
        incoming["data_status"] = data_status(incoming)
        return incoming
    merged = dict(existing)
    for field in ARTICLE_FIELDS:
        if field == "sources":
            continue
        current = merged.get(field)
        new = incoming.get(field)
        if (not current or current in ([], "")) and new not in (None, "", []):
            merged[field] = new
    sources = merged.get("sources") or []
    new_source = {
        "source_name": incoming.get("source_name", ""),
        "source_url": incoming.get("source_url", ""),
        "crawl_time": incoming.get("crawl_time", ""),
        "confidence": incoming.get("confidence", ""),
    }
    if new_source.get("source_url") and new_source not in sources:
        sources.append(new_source)
    merged["sources"] = sources
    merged["data_status"] = data_status(merged)
    return merged


def row_from_existing(
    journal: dict[str, Any],
    row: dict[str, Any],
    source_file: str,
    period: str,
    crawl_time: str,
) -> dict[str, Any]:
    row = repair_text(row)
    url = clean_text(row.get("url") or row.get("doi") or row.get("openalex_id"))
    url_year, url_issue, url_month, url_label = parse_year_issue_from_url(url)
    period_year, period_issue, period_month, period_label = parse_year_issue_from_period(period)
    year = url_year or int(row.get("publication_year") or 0) or period_year
    issue = url_issue or period_issue
    month = url_month or period_month
    title = clean_text(row.get("title"))
    authors = normalize_authors(row.get("authors"))
    abstract = clean_text(row.get("abstract") or row.get("summary") or row.get("description"))
    keywords = normalize_keywords(row.get("keywords"))
    issue_label = url_label or period_label or (f"{year}年第{issue}期" if year and issue else "")
    notes = []
    if not title:
        notes.append("标题缺失，保留现有来源链接待公开目录补齐")
    if not abstract:
        notes.append("暂无公开摘要")
    return {
        "journal": journal["journal_name"],
        "year": year,
        "issue": issue,
        "month": month,
        "issue_label": issue_label,
        "title": title,
        "original_title": clean_text(row.get("title")),
        "authors": authors,
        "abstract": abstract,
        "keywords": keywords,
        "pages": clean_text(row.get("pages")),
        "column": clean_text(row.get("column")),
        "source_url": url,
        "source_name": source_name_for(url, "OpenAlex/现有仓库历史数据"),
        "source_file": source_file,
        "crawl_time": crawl_time,
        "data_status": "",
        "confidence": 0.55 if title else 0.35,
        "notes": "；".join(notes),
    }


def fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_cqvip_nuxt(html_text: str) -> dict[str, Any]:
    script = r"""
const fs = require('fs');
const html = fs.readFileSync(0, 'utf8');
const match = html.match(/<script>window\.__NUXT__=(.*?);<\/script>/s);
if (!match) {
  console.log(JSON.stringify({ issues: [], articles: [] }));
  process.exit(0);
}
const window = {};
try {
  eval('window.__NUXT__=' + match[1]);
} catch (error) {
  console.log(JSON.stringify({ issues: [], articles: [], error: String(error) }));
  process.exit(0);
}
const issues = [];
const articles = [];
const seenIssues = new Set();
const seenArticles = new Set();
function plain(value) {
  if (value === undefined || value === null) return '';
  return String(value).replace(/\s+/g, ' ').trim();
}
function walk(value) {
  if (!value || typeof value !== 'object') return;
  if (Array.isArray(value)) {
    for (const item of value) walk(item);
    return;
  }
  if (Array.isArray(value.periodical) && value.year) {
    for (const item of value.periodical) {
      if (!item || !item.name) continue;
      const issue = { year: plain(value.year), issue: plain(item.name), id: plain(item.id) };
      const key = issue.year + '|' + issue.issue + '|' + issue.id;
      if (!seenIssues.has(key)) {
        seenIssues.add(key);
        issues.push(issue);
      }
    }
  }
  if (Array.isArray(value.children)) {
    for (const child of value.children) {
      if (!child || typeof child !== 'object') continue;
      if (!(child.title || child.name) || !(child.authorInfo || child.signInfo)) continue;
      const id = plain(child.id || (child.signInfo && child.signInfo.resourceId));
      const key = id || plain(child.title || child.name);
      if (seenArticles.has(key)) continue;
      seenArticles.add(key);
      articles.push({
        id,
        title: plain(child.title || child.name),
        authors: (child.authorInfo || []).map((author) => plain(author.name)).filter(Boolean),
        beginPage: plain(child.beginPage),
        endPage: plain(child.endPage),
        column: (child.journalColumnInfo || []).map((item) => plain(item.name)).filter(Boolean).join('; '),
        sourceUrl: id ? `https://www.cqvip.com/doc/journal/${id}` : ''
      });
    }
  }
  for (const item of Object.values(value)) walk(item);
}
walk(window.__NUXT__);
console.log(JSON.stringify({ issues, articles }));
"""
    try:
        result = subprocess.run(
            ["node", "-e", script],
            input=html_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"issues": [], "articles": [], "error": str(exc)}
    if result.returncode != 0:
        return {"issues": [], "articles": [], "error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"issues": [], "articles": [], "error": "failed to decode node parser output"}


def fetch_cqvip_year(
    journal: dict[str, Any],
    cqvip_code: str,
    year: int,
    sleep_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not cqvip_code:
        return [], []
    url = f"https://www.cqvip.com/journal/{cqvip_code}/{cqvip_code}/{year}"
    try:
        html_text = fetch_text(url)
    except (urllib.error.URLError, TimeoutError) as exc:
        append_error(f"{journal['journal_name']} {year} cqvip fetch failed: {exc}")
        return [], []
    if "captcha" in html_text.lower() or "验证码" in html_text:
        append_error(f"{journal['journal_name']} {year} cqvip captcha or verification page detected")
        return [], []
    parsed = parse_cqvip_nuxt(html_text)
    if parsed.get("error"):
        append_error(f"{journal['journal_name']} {year} cqvip parse failed: {parsed['error']}")
    issues = []
    for item in parsed.get("issues") or []:
        try:
            issue_year = int(item.get("year") or year)
            issue = int(str(item.get("issue")).strip())
        except (TypeError, ValueError):
            continue
        if issue_year == year:
            issues.append({"year": issue_year, "issue": issue, "id": item.get("id", ""), "source_url": url})

    active_issue = None
    if issues:
        # CQVIP SSR renders the newest public issue for that year first.
        active_issue = sorted(issues, key=lambda item: item["issue"], reverse=True)[0]["issue"]
    articles = []
    crawl_time = dt.datetime.now(dt.timezone.utc).isoformat()
    for item in parsed.get("articles") or []:
        title = clean_text(item.get("title"))
        if not title:
            continue
        begin = clean_text(item.get("beginPage"))
        end = clean_text(item.get("endPage"))
        pages = f"{begin}-{end}" if begin and end and begin != end else begin or end
        issue = active_issue or 1
        articles.append(
            {
                "journal": journal["journal_name"],
                "year": year,
                "issue": issue,
                "month": None,
                "issue_label": f"{year}年第{issue}期",
                "title": title,
                "original_title": title,
                "authors": normalize_authors(item.get("authors")),
                "abstract": "",
                "keywords": [],
                "pages": pages,
                "column": clean_text(item.get("column")),
                "source_url": clean_text(item.get("sourceUrl")) or url,
                "source_name": "维普公开目录页",
                "source_file": url,
                "crawl_time": crawl_time,
                "data_status": "",
                "confidence": 0.78,
                "notes": "从维普公开期刊目录页补齐；暂无公开摘要",
            }
        )
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return issues, articles


def expected_issue_count(issues_by_year: dict[int, set[int]]) -> int:
    counts = [len(values) for year, values in issues_by_year.items() if year < CURRENT_YEAR and values]
    if not counts:
        return 0
    observed = max(counts)
    if observed >= 10:
        return 12
    if observed >= 5:
        return 6
    if observed >= 3:
        return 4
    return observed


def audit_issue_record(
    journal: dict[str, Any],
    year: int,
    issue: int | None,
    rows: list[dict[str, Any]],
    expected: bool,
    issue_public_status: str,
) -> dict[str, Any]:
    return {
        "journal": journal["journal_name"],
        "journal_id": journal["journal_id"],
        "discipline": journal.get("discipline", ""),
        "journal_type": journal.get("journal_type", ""),
        "year": year,
        "issue": issue,
        "issue_label": f"{year}年第{issue}期" if issue else f"{year}年",
        "expected": expected,
        "exists": bool(rows),
        "article_count": len(rows),
        "missing_title_count": sum(1 for row in rows if not row.get("title")),
        "missing_authors_count": sum(1 for row in rows if not row.get("authors")),
        "missing_year_or_issue_count": sum(1 for row in rows if not row.get("year") or not row.get("issue")),
        "missing_abstract_count": sum(1 for row in rows if not row.get("abstract")),
        "missing_source_url_count": sum(1 for row in rows if not row.get("source_url")),
        "issue_public_status": issue_public_status,
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_chinese_political_cssci(
    fetch_cqvip: bool = False,
    start_year: int | None = None,
    end_year: int | None = None,
    force_update: bool = False,
    sleep_seconds: float = 2.5,
    max_journals: int | None = None,
) -> dict[str, Any]:
    crawl_time = dt.datetime.now(dt.timezone.utc).isoformat()
    journals = load_target_metadata()
    if max_journals:
        journals = journals[:max_journals]

    all_rows: list[dict[str, Any]] = []
    completeness_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    journal_status_rows: list[dict[str, Any]] = []
    total_new_external_articles = 0
    total_new_external_issues = 0
    total_2026_external_issues = 0

    for journal in journals:
        journal_id = int(journal["journal_id"])
        folder = history_folder_for_id(journal_id)
        article_map: dict[tuple[int, int], dict[str, dict[str, Any]]] = defaultdict(dict)
        public_found_issues: dict[int, set[int]] = defaultdict(set)
        cqvip_code = ""

        if folder:
            issue_dirs = sorted((folder / "issues").glob("*")) if (folder / "issues").exists() else []
            for issue_dir in issue_dirs:
                if not issue_dir.is_dir():
                    continue
                period = issue_dir.name
                source_file = str((issue_dir / "works.jsonl").relative_to(ROOT)).replace("\\", "/")
                rows = iter_jsonl(issue_dir / "works.jsonl")
                if not cqvip_code:
                    cqvip_code = cqvip_code_from_rows(rows)
                for source_row in rows:
                    article = row_from_existing(journal, source_row, source_file, period, crawl_time)
                    if not article.get("year") or not article.get("issue"):
                        missing_rows.append(
                            {
                                "journal": journal["journal_name"],
                                "year": article.get("year") or "",
                                "issue": article.get("issue") or "",
                                "category": "found_title_missing_metadata",
                                "detail": "记录缺少年份或期次，无法稳定归入期次目录",
                                "source_url": article.get("source_url", ""),
                            }
                        )
                        continue
                    key = article_key(article)
                    bucket = article_map[(int(article["year"]), int(article["issue"]))]
                    bucket[key] = merge_article(bucket.get(key), article)

        if fetch_cqvip and cqvip_code:
            years_to_fetch = range(start_year or CURRENT_YEAR, (end_year or CURRENT_YEAR) + 1)
            for year in years_to_fetch:
                issues, articles = fetch_cqvip_year(journal, cqvip_code, year, sleep_seconds)
                before_issues = {issue for existing_year, issue in article_map if existing_year == year}
                for issue_info in issues:
                    public_found_issues[year].add(issue_info["issue"])
                for article in articles:
                    key = article_key(article)
                    bucket = article_map[(int(article["year"]), int(article["issue"]))]
                    was_new = key not in bucket
                    bucket[key] = merge_article(bucket.get(key), article)
                    if was_new:
                        total_new_external_articles += 1
                after_issues = {issue for existing_year, issue in article_map if existing_year == year}
                new_issue_count = len(after_issues - before_issues)
                total_new_external_issues += new_issue_count
                if year == CURRENT_YEAR:
                    total_2026_external_issues += new_issue_count

        by_year_issues: dict[int, set[int]] = defaultdict(set)
        for year, issue in article_map:
            by_year_issues[int(year)].add(int(issue))
        for year, issues in public_found_issues.items():
            by_year_issues[int(year)].update(int(issue) for issue in issues)

        earliest_year = min(by_year_issues) if by_year_issues else None
        latest_year = max(by_year_issues) if by_year_issues else None
        freq = expected_issue_count(by_year_issues)
        journal_all_rows = []
        for (year, issue), rows_by_key in sorted(article_map.items()):
            rows = sorted(rows_by_key.values(), key=lambda item: (item.get("pages") or "", item.get("title") or "", item.get("source_url") or ""))
            target_file = TARGET_DIR / safe_segment(journal["journal_name"]) / str(year) / f"{issue:02d}" / "articles.jsonl"
            write_jsonl(target_file, rows)
            all_rows.extend(rows)
            journal_all_rows.extend(rows)

        if earliest_year:
            for year in range(earliest_year, CURRENT_YEAR + 1):
                if year == CURRENT_YEAR:
                    expected_issues = sorted(by_year_issues.get(year, set()))
                    if not expected_issues:
                        completeness_rows.append(audit_issue_record(journal, year, None, [], False, "2026_no_public_issue_found"))
                        missing_rows.append(
                            {
                                "journal": journal["journal_name"],
                                "year": year,
                                "issue": "",
                                "category": "no_public_source_found",
                                "detail": "2026 年尚未在当前公开来源中检索到期次",
                                "source_url": "",
                            }
                        )
                    continue
                expected_issues = list(range(1, freq + 1)) if freq else sorted(by_year_issues.get(year, set()))
                if not expected_issues:
                    expected_issues = [None]
                for issue in expected_issues:
                    rows = list(article_map.get((year, issue), {}).values()) if issue else []
                    public_status = "found" if rows else "not_found_in_public_sources"
                    completeness_rows.append(audit_issue_record(journal, year, issue, rows, True, public_status))
                    if not rows:
                        missing_rows.append(
                            {
                                "journal": journal["journal_name"],
                                "year": year,
                                "issue": issue or "",
                                "category": "no_public_source_found",
                                "detail": "未在当前已整理数据或本次低频公开检索中找到该期目录",
                                "source_url": "",
                            }
                        )

        for row in journal_all_rows:
            if row.get("title") and not row.get("abstract"):
                missing_rows.append(
                    {
                        "journal": row["journal"],
                        "year": row["year"],
                        "issue": row["issue"],
                        "category": "issue_found_missing_abstract",
                        "detail": "已找到题名或目录记录，但暂无公开摘要",
                        "source_url": row.get("source_url", ""),
                    }
                )
            if row.get("title") and (not row.get("authors") or not row.get("pages") or not row.get("source_url")):
                missing_rows.append(
                    {
                        "journal": row["journal"],
                        "year": row["year"],
                        "issue": row["issue"],
                        "category": "found_title_missing_metadata",
                        "detail": "已找到题名，但作者、页码或链接字段仍不完整",
                        "source_url": row.get("source_url", ""),
                    }
                )

        missing_issue_count = sum(
            1
            for row in completeness_rows
            if row["journal"] == journal["journal_name"] and row["expected"] and not row["exists"]
        )
        journal_status_rows.append(
            {
                "journal": journal["journal_name"],
                "journal_id": journal_id,
                "discipline": journal.get("discipline", ""),
                "journal_type": journal.get("journal_type", ""),
                "metadata_source": journal.get("target_source", ""),
                "earliest_year": earliest_year,
                "latest_year": latest_year,
                "included_years": sorted(str(year) for year in by_year_issues),
                "included_issue_count": len(article_map),
                "included_article_count": len(journal_all_rows),
                "missing_issue_count": missing_issue_count,
                "missing_abstract_article_count": sum(1 for row in journal_all_rows if not row.get("abstract")),
                "found_2026_issues": sorted(by_year_issues.get(CURRENT_YEAR, set())),
                "last_updated": crawl_time,
                "cqvip_code": cqvip_code,
            }
        )

    write_jsonl(TARGET_DIR / "all_articles.jsonl", all_rows)
    save_json(TARGET_DIR / "journals_status.json", journal_status_rows)
    save_json(TARGET_DIR / "completeness_report.json", completeness_rows)
    write_csv(
        TARGET_DIR / "completeness_report.csv",
        completeness_rows,
        [
            "journal",
            "journal_id",
            "discipline",
            "journal_type",
            "year",
            "issue",
            "issue_label",
            "expected",
            "exists",
            "article_count",
            "missing_title_count",
            "missing_authors_count",
            "missing_year_or_issue_count",
            "missing_abstract_count",
            "missing_source_url_count",
            "issue_public_status",
        ],
    )
    write_csv(
        TARGET_DIR / "missing_report.csv",
        missing_rows,
        ["journal", "year", "issue", "category", "detail", "source_url"],
    )
    write_missing_markdown(missing_rows, journal_status_rows)
    summary = {
        "generated_at": crawl_time,
        "scanned_journals": len(journals),
        "covered_years": len({row["year"] for row in all_rows if row.get("year")}),
        "found_issues": len({(row["journal"], row["year"], row["issue"]) for row in all_rows if row.get("year") and row.get("issue")}),
        "newly_completed_issues": total_new_external_issues,
        "new_articles": total_new_external_articles,
        "filled_titles": sum(1 for row in all_rows if row.get("title")),
        "filled_authors": sum(1 for row in all_rows if row.get("authors")),
        "filled_abstracts": sum(1 for row in all_rows if row.get("abstract")),
        "filled_2026_issues": total_2026_external_issues,
        "remaining_missing_issues": sum(1 for row in completeness_rows if row.get("expected") and not row.get("exists")),
        "largest_gap_journals": sorted(
            [
                {"journal": row["journal"], "missing_issue_count": row["missing_issue_count"]}
                for row in journal_status_rows
            ],
            key=lambda item: item["missing_issue_count"],
            reverse=True,
        )[:10],
        "output_files": [
            "data/chinese_political_cssci/all_articles.jsonl",
            "data/chinese_political_cssci/journals_status.json",
            "data/chinese_political_cssci/completeness_report.json",
            "data/chinese_political_cssci/completeness_report.csv",
            "data/chinese_political_cssci/missing_report.csv",
            "data/chinese_political_cssci/missing_report.md",
        ],
    }
    save_json(TARGET_DIR / "run_summary.json", summary)
    return summary


def write_missing_markdown(missing_rows: list[dict[str, Any]], journal_status_rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in missing_rows:
        grouped[row["journal"]][row["category"]].append(row)
    lines = [
        "# 中文政治学 CSSCI 期刊目录缺口报告",
        "",
        "本报告区分三类缺口：公开来源未找到年份或期次；找到期次目录但缺少摘要；找到题目但缺少作者、页码或链接。",
        "",
    ]
    status_by_journal = {row["journal"]: row for row in journal_status_rows}
    for journal in sorted(grouped):
        status = status_by_journal.get(journal, {})
        lines.append(f"## {journal}")
        lines.append("")
        lines.append(
            f"- 已收录期次：{status.get('included_issue_count', 0)}；已收录文章：{status.get('included_article_count', 0)}；缺失期次：{status.get('missing_issue_count', 0)}"
        )
        for category, title in [
            ("no_public_source_found", "公开来源没有找到该年份或该期"),
            ("issue_found_missing_abstract", "找到了期次目录但缺少摘要"),
            ("found_title_missing_metadata", "找到了题目但缺少作者、页码或链接"),
        ]:
            rows = grouped[journal].get(category, [])
            lines.append(f"- {title}：{len(rows)}")
            for row in rows[:20]:
                issue = row.get("issue") or ""
                lines.append(f"  - {row.get('year', '')} {issue}：{row.get('detail', '')}")
            if len(rows) > 20:
                lines.append(f"  - 其余 {len(rows) - 20} 条见 missing_report.csv")
        lines.append("")
    (TARGET_DIR / "missing_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
