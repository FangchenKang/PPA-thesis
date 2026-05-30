from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.message
import hashlib
import html
import json
import os
import re
import shutil
import smtplib
import ssl
import sys
import time
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JOURNALS_PATH = ROOT / "data" / "journals.json"
STATE_PATH = ROOT / "data" / "state.json"
ITEMS_DIR = ROOT / "data" / "items"
HISTORY_DIR = ROOT / "data" / "history"
HISTORY_JOURNALS_DIR = HISTORY_DIR / "journals"
REPORTS_DIR = ROOT / "reports"
OVERRIDES_PATH = ROOT / "config" / "feed_overrides.json"
USER_AGENT = "PPA-thesis-journal-tracker/0.1 (+https://github.com/FangchenKang/PPA-thesis)"
OPENALEX_BASE_URL = "https://api.openalex.org"


@dataclasses.dataclass
class Article:
    journal_id: int
    journal_title: str
    title: str
    url: str
    published: str
    summary: str = ""
    source: str = ""

    @property
    def fingerprint(self) -> str:
        base = f"{self.journal_id}|{normalize_text(self.title)}|{self.url}".encode("utf-8")
        return hashlib.sha256(base).hexdigest()[:24]


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.feed_links: list[str] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and attrs_map.get("href"):
            self._current_href = attrs_map["href"]
            self._current_text = []
        if tag.lower() == "link":
            rel = attrs_map.get("rel", "").lower()
            type_value = attrs_map.get("type", "").lower()
            href = attrs_map.get("href", "")
            if href and "alternate" in rel and ("rss" in type_value or "atom" in type_value or "xml" in type_value):
                self.feed_links.append(href)

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href:
            text = normalize_text(" ".join(self._current_text))
            self.links.append({"href": self._current_href, "text": text})
            self._current_href = None
            self._current_text = []


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def today_china() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def fetch_text(url: str, timeout: int = 25, attempts: int = 3) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("content-type", "")
                charset = "utf-8"
                match = re.search(r"charset=([\w-]+)", content_type)
                if match:
                    charset = match.group(1)
                data = response.read()
                return data.decode(charset, errors="replace")
        except (TimeoutError, ssl.SSLError, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}")


def fetch_json(url: str, timeout: int = 60) -> dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout))


def absolute_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        if strip_namespace(child.tag) in names:
            return normalize_text("".join(child.itertext()))
    return ""


def atom_link(entry: ET.Element) -> str:
    first = ""
    for child in list(entry):
        if strip_namespace(child.tag) != "link":
            continue
        href = child.attrib.get("href", "")
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return href
        if href and not first:
            first = href
    return first


def parse_feed(xml_text: str, journal: dict[str, Any], feed_url: str) -> list[Article]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    root_name = strip_namespace(root.tag)
    articles: list[Article] = []
    if root_name == "rss":
        entries = root.findall("./channel/item")
        for entry in entries:
            title = child_text(entry, {"title"})
            url = child_text(entry, {"link", "guid"})
            published = child_text(entry, {"pubdate", "published", "updated", "date"})
            summary = child_text(entry, {"description", "summary"})
            if title and url:
                articles.append(article_from_parts(journal, title, absolute_url(feed_url, url), published, summary, "feed"))
    else:
        entries = [node for node in root.iter() if strip_namespace(node.tag) == "entry"]
        for entry in entries:
            title = child_text(entry, {"title"})
            url = atom_link(entry)
            published = child_text(entry, {"published", "updated"})
            summary = child_text(entry, {"summary", "content"})
            if title and url:
                articles.append(article_from_parts(journal, title, absolute_url(feed_url, url), published, summary, "feed"))
    return articles


def article_from_parts(
    journal: dict[str, Any],
    title: str,
    url: str,
    published: str = "",
    summary: str = "",
    source: str = "",
) -> Article:
    return Article(
        journal_id=int(journal["id"]),
        journal_title=str(journal["title"]),
        title=normalize_text(title),
        url=url,
        published=normalize_text(published),
        summary=normalize_text(summary),
        source=source,
    )


def discover_feed_urls(homepage_url: str, html_text: str) -> list[str]:
    collector = LinkCollector()
    collector.feed(html_text)
    return [absolute_url(homepage_url, href) for href in collector.feed_links]


def looks_like_article(url: str, text: str) -> bool:
    lower_url = url.lower()
    lower_text = text.lower()
    excluded = [
        "alert",
        "author",
        "editorial",
        "instructions",
        "submission",
        "subscribe",
        "permissions",
        "metrics",
        "privacy",
        "terms",
        "about",
        "aims",
        "scope",
        "contact",
        "login",
        "register",
    ]
    if any(part in lower_url for part in excluded):
        return False
    if len(text) < 12 or len(text) > 260:
        return False
    if re.search(r"\b(vol|volume|issue|current issue|latest articles|view all)\b", lower_text):
        return False
    article_markers = [
        "/doi/",
        "/article/",
        "/articles/",
        "/content/",
        "/chapter/",
        "/core/journals/",
        "/view/journals/",
        "abstract",
        "fulltext",
    ]
    return any(marker in lower_url for marker in article_markers)


def scrape_homepage_articles(journal: dict[str, Any], homepage_url: str, html_text: str, limit: int) -> list[Article]:
    collector = LinkCollector()
    collector.feed(html_text)
    seen_urls: set[str] = set()
    articles: list[Article] = []
    for link in collector.links:
        url = absolute_url(homepage_url, link["href"])
        text = normalize_text(link["text"])
        if url in seen_urls or not looks_like_article(url, text):
            continue
        seen_urls.add(url)
        articles.append(article_from_parts(journal, text, url, source="homepage"))
        if len(articles) >= limit:
            break
    return articles


def fetch_articles_for_journal(
    journal: dict[str, Any],
    overrides: dict[str, Any],
    per_journal_limit: int,
) -> tuple[list[Article], str]:
    if not journal.get("enabled", True):
        return [], "disabled"

    homepage_url = str(journal.get("homepage_url", "")).strip()
    feed_url = str(overrides.get(str(journal["id"]), {}).get("feed_url") or journal.get("feed_url") or "").strip()

    try:
        if feed_url:
            return parse_feed(fetch_text(feed_url), journal, feed_url)[:per_journal_limit], "feed"
        if not homepage_url:
            return [], "missing homepage_url"

        page = fetch_text(homepage_url)
        for discovered in discover_feed_urls(homepage_url, page)[:3]:
            articles = parse_feed(fetch_text(discovered), journal, discovered)
            if articles:
                return articles[:per_journal_limit], "discovered feed"
        return scrape_homepage_articles(journal, homepage_url, page, per_journal_limit), "homepage scrape"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return [], f"fetch error: {exc}"


def article_to_dict(article: Article) -> dict[str, Any]:
    return {
        "id": article.fingerprint,
        "journal_id": article.journal_id,
        "journal_title": article.journal_title,
        "title": article.title,
        "url": article.url,
        "published": article.published,
        "summary": article.summary,
        "source": article.source,
    }


def normalize_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def openalex_url(path: str, params: dict[str, Any]) -> str:
    clean_params = {key: value for key, value in params.items() if value not in (None, "")}
    mailto = os.environ.get("OPENALEX_MAILTO") or os.environ.get("MAIL_TO")
    if mailto:
        clean_params["mailto"] = mailto
    return f"{OPENALEX_BASE_URL}{path}?{urllib.parse.urlencode(clean_params)}"


def find_openalex_source(journal: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    title = str(journal["title"])
    url = openalex_url(
        "/sources",
        {"search": title, "filter": "type:journal", "per-page": 10},
    )
    payload = fetch_json(url)
    results = payload.get("results", [])
    if not results:
        return None, "no source match"

    title_key = normalize_match_key(title)
    for source in results:
        if normalize_match_key(str(source.get("display_name", ""))) == title_key:
            return source, "exact source match"
    return results[0], "best source match"


def openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted_index.items():
        for index in indexes:
            positions[index] = word
    return " ".join(positions[index] for index in sorted(positions))


def compact_openalex_work(
    work: dict[str, Any],
    journal: dict[str, Any],
    source: dict[str, Any],
    include_abstracts: bool,
) -> dict[str, Any]:
    doi = str(work.get("doi") or "")
    title = normalize_text(str(work.get("display_name") or ""))
    authorships = work.get("authorships") or []
    authors = [
        normalize_text(str((authorship.get("author") or {}).get("display_name") or ""))
        for authorship in authorships[:20]
    ]
    authors = [author for author in authors if author]
    primary_location = work.get("primary_location") or {}
    landing_page_url = primary_location.get("landing_page_url") or doi or work.get("id") or ""
    item = {
        "openalex_id": work.get("id"),
        "doi": doi,
        "title": title,
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "authors": authors,
        "journal_id": journal["id"],
        "journal_title": journal["title"],
        "source_openalex_id": source.get("id"),
        "source_display_name": source.get("display_name"),
        "url": landing_page_url,
        "cited_by_count": work.get("cited_by_count"),
        "is_oa": (work.get("open_access") or {}).get("is_oa"),
        "oa_status": (work.get("open_access") or {}).get("oa_status"),
        "type": work.get("type"),
    }
    if include_abstracts:
        item["abstract"] = openalex_abstract(work.get("abstract_inverted_index"))
    return item


def safe_path_segment(value: str) -> str:
    segment = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    segment = re.sub(r"\s+", "_", segment).strip(" ._")
    return segment[:120] or "untitled"


def history_folder_for_journal(journal: dict[str, Any]) -> Path:
    journal_id = int(journal["id"])
    title = safe_path_segment(str(journal.get("title") or f"journal-{journal_id:04d}"))
    return HISTORY_JOURNALS_DIR / f"{journal_id:04d}-{title}"


def history_file_for_journal(journal: dict[str, Any]) -> Path:
    return history_folder_for_journal(journal) / "works.jsonl"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def read_jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def parse_publication_date(row: dict[str, Any]) -> dt.date | None:
    value = row.get("publication_date")
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    year = row.get("publication_year")
    if isinstance(year, int):
        return dt.date(year, 1, 1)
    return None


def infer_issue_frequency(journal: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    title = str(journal.get("title") or "").lower()
    if "quarterly" in title:
        return "quarterly"
    months_by_year: dict[int, set[int]] = {}
    current_year = today_china().year
    for row in rows:
        published = parse_publication_date(row)
        if not published or published.year >= current_year:
            continue
        months_by_year.setdefault(published.year, set()).add(published.month)
    counts = sorted(len(months) for months in months_by_year.values() if months)
    if counts and counts[len(counts) // 2] <= 4:
        return "quarterly"
    return "monthly"


def issue_key_for_date(published: dt.date | None, frequency: str) -> str:
    if not published:
        return "unknown-date"
    if frequency == "quarterly":
        period = ((published.month - 1) // 3) + 1
    else:
        period = published.month
    return f"{published.year}-{period}"


def issue_sort_key(issue_key: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", issue_key)
    if not match:
        return (-1, -1)
    return (int(match.group(1)), int(match.group(2)))


def run_issue_split(
    start_id: int | None,
    end_id: int | None,
    frequency_mode: str,
    clear_existing: bool,
) -> dict[str, Any]:
    journals = [journal for journal in load_json(JOURNALS_PATH, []) if journal.get("enabled", True)]
    selected: list[dict[str, Any]] = []
    for journal in journals:
        journal_id = int(journal["id"])
        if start_id is not None and journal_id < start_id:
            continue
        if end_id is not None and journal_id > end_id:
            continue
        selected.append(journal)

    diagnostics: list[dict[str, Any]] = []
    total_items = 0
    total_periods = 0
    for journal in selected:
        folder = history_folder_for_journal(journal)
        history_path = history_file_for_journal(journal)
        rows = read_jsonl_rows(history_path)
        issues_dir = folder / "issues"
        if clear_existing and issues_dir.exists():
            shutil.rmtree(issues_dir)

        if frequency_mode == "auto":
            frequency = infer_issue_frequency(journal, rows)
        else:
            frequency = frequency_mode

        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = issue_key_for_date(parse_publication_date(row), frequency)
            buckets.setdefault(key, []).append(row)

        periods: list[dict[str, Any]] = []
        for key in sorted(buckets, key=issue_sort_key, reverse=True):
            path = issues_dir / key / "works.jsonl"
            write_jsonl(path, buckets[key])
            periods.append(
                {
                    "period": key,
                    "items": len(buckets[key]),
                    "path": str(path.relative_to(folder)).replace("\\", "/"),
                }
            )

        index = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "journal_id": int(journal["id"]),
            "journal_title": journal.get("title"),
            "frequency": frequency,
            "frequency_mode": frequency_mode,
            "source": "publication_date",
            "total_items": len(rows),
            "period_count": len(periods),
            "periods": periods,
        }
        folder.mkdir(parents=True, exist_ok=True)
        save_json(folder / "issue_index.json", index)
        total_items += len(rows)
        total_periods += len(periods)
        diagnostics.append(
            {
                "journal_id": int(journal["id"]),
                "journal_title": journal.get("title"),
                "frequency": frequency,
                "items": len(rows),
                "periods": len(periods),
            }
        )

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "frequency_mode": frequency_mode,
        "source": "publication_date",
        "selected_journals": len(selected),
        "total_items": total_items,
        "period_files": total_periods,
        "diagnostics": diagnostics,
    }
    save_json(HISTORY_DIR / "issues_index.json", summary)
    return summary


def run_historical_backfill(
    start_id: int | None,
    end_id: int | None,
    max_journals: int | None,
    max_pages_per_journal: int,
    per_page: int,
    sleep_seconds: float,
    include_abstracts: bool,
    dry_run: bool,
    resume: bool,
) -> dict[str, Any]:
    journals = [journal for journal in load_json(JOURNALS_PATH, []) if journal.get("enabled", True)]
    selected: list[dict[str, Any]] = []
    for journal in journals:
        journal_id = int(journal["id"])
        if start_id is not None and journal_id < start_id:
            continue
        if end_id is not None and journal_id > end_id:
            continue
        selected.append(journal)
    if max_journals is not None:
        selected = selected[:max_journals]

    HISTORY_JOURNALS_DIR.mkdir(parents=True, exist_ok=True)
    diagnostics: list[dict[str, Any]] = []
    total_items = 0
    existing_items = 0

    def checkpoint() -> None:
        if dry_run:
            return
        index = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "provider": "OpenAlex public API",
            "scope": "publicly available bibliographic metadata; no paywall or login bypass",
            "selected_journals": len(selected),
            "total_items": total_items + existing_items,
            "newly_fetched_items": total_items,
            "existing_items": existing_items,
            "include_abstracts": include_abstracts,
            "dry_run": dry_run,
            "resume": resume,
            "diagnostics": diagnostics,
        }
        save_json(HISTORY_DIR / "index.json", index)

    for journal in selected:
        journal_id = int(journal["id"])
        title = str(journal["title"])
        history_path = history_file_for_journal(journal)
        if resume and history_path.exists() and history_path.stat().st_size > 0:
            count = read_jsonl_count(history_path)
            existing_items += count
            diagnostics.append({"journal_id": journal_id, "journal_title": title, "status": "skipped existing file", "items": count})
            checkpoint()
            continue
        try:
            source, source_status = find_openalex_source(journal)
            if not source:
                diagnostics.append({"journal_id": journal_id, "journal_title": title, "status": source_status, "items": 0})
                checkpoint()
                continue

            source_id = str(source.get("id", ""))
            source_id_filter = source_id.rsplit("/", 1)[-1]
            cursor = "*"
            pages = 0
            tmp_path = history_path.with_suffix(".jsonl.tmp")
            row_count = 0
            select_fields = [
                "id",
                "doi",
                "title",
                "publication_year",
                "publication_date",
                "primary_location",
                "cited_by_count",
                "authorships",
            ]
            if include_abstracts:
                select_fields.append("abstract_inverted_index")
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            handle = None if dry_run else tmp_path.open("w", encoding="utf-8", errors="replace", newline="\n")
            while cursor:
                try:
                    if max_pages_per_journal > 0 and pages >= max_pages_per_journal:
                        break
                    works_url = openalex_url(
                        "/works",
                        {
                            "filter": f"primary_location.source.id:{source_id_filter},type:article",
                            "per-page": per_page,
                            "cursor": cursor,
                            "select": ",".join(select_fields),
                        },
                    )
                    if pages == 0 or pages % 25 == 0:
                        print(f"backfill {journal_id} {title}: fetching page {pages + 1}", file=sys.stderr, flush=True)
                    payload = fetch_json(works_url)
                    results = payload.get("results", [])
                    for work in results:
                        row = compact_openalex_work(work, journal, source, include_abstracts)
                        row_count += 1
                        if handle is not None:
                            append_jsonl(handle, row)
                    pages += 1
                    if pages == 1 or pages % 25 == 0:
                        print(f"backfill {journal_id} {title}: wrote page {pages}, rows {row_count}", file=sys.stderr, flush=True)
                    next_cursor = (payload.get("meta") or {}).get("next_cursor")
                    if not results or not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
                finally:
                    if handle is not None:
                        handle.flush()

            if handle is not None:
                handle.close()
                tmp_path.replace(history_path)
            total_items += row_count
            diagnostics.append(
                {
                    "journal_id": journal_id,
                    "journal_title": title,
                    "status": source_status,
                    "source_display_name": source.get("display_name"),
                    "source_openalex_id": source_id,
                    "pages": pages,
                    "items": row_count,
                }
            )
            checkpoint()
        except Exception as exc:
            try:
                if handle is not None and not handle.closed:
                    handle.close()
            except NameError:
                pass
            diagnostics.append({"journal_id": journal_id, "journal_title": title, "status": f"error: {exc}", "items": 0})
            checkpoint()
    index = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "provider": "OpenAlex public API",
        "scope": "publicly available bibliographic metadata; no paywall or login bypass",
        "selected_journals": len(selected),
        "total_items": total_items + existing_items,
        "newly_fetched_items": total_items,
        "existing_items": existing_items,
        "include_abstracts": include_abstracts,
        "dry_run": dry_run,
        "resume": resume,
        "diagnostics": diagnostics,
    }
    checkpoint()
    return index


ARTICLE_CONTEXT_FIELDS = [
    "abstract",
    "summary",
    "description",
    "body",
    "content",
    "intro",
    "text",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(clean_text(item) for item in value if clean_text(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip()


def article_keywords(row: dict[str, Any]) -> str:
    for key in ("keywords", "keyword", "concepts"):
        value = row.get(key)
        if not value:
            continue
        if key == "concepts" and isinstance(value, list):
            names = []
            for item in value:
                if isinstance(item, dict) and item.get("display_name"):
                    names.append(str(item["display_name"]))
            return clean_text(names)
        return clean_text(value)
    return ""


def article_context(row: dict[str, Any]) -> str:
    parts = []
    for key in ARTICLE_CONTEXT_FIELDS:
        value = clean_text(row.get(key))
        if value:
            parts.append(value)
    return " ".join(parts)


def title_has_enough_information(title: str) -> bool:
    normalized = normalize_text(title)
    if len(normalized) < 4:
        return False
    weak_titles = {
        "abstract",
        "article",
        "back matter",
        "book review",
        "book reviews",
        "books received",
        "contents",
        "correction",
        "editorial",
        "errata",
        "erratum",
        "front matter",
        "introduction",
        "preface",
        "review",
        "reviews",
    }
    return normalized not in weak_titles


def summary_basis(row: dict[str, Any]) -> str:
    title = clean_text(row.get("title"))
    if not title or not title_has_enough_information(title):
        return "信息不足，暂不展开"
    has_context = bool(article_context(row))
    has_keywords = bool(article_keywords(row))
    if has_context and has_keywords:
        return "根据题目、摘要和关键词判断"
    if has_context:
        return "根据题目和摘要判断"
    if has_keywords:
        return "根据题目和关键词判断"
    return "仅根据题目判断"


def append_title_only_marker(summary: str, basis: str) -> str:
    summary = summary.strip()
    if basis == "仅根据题目判断" and not summary.endswith("仅根据题目判断"):
        return f"{summary} 仅根据题目判断"
    return summary


def extract_json_array(text: str) -> list[Any]:
    text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("LLM response is not a JSON array")
    return payload


def generate_article_summaries_with_llm(records: list[dict[str, Any]]) -> dict[str, str]:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set")
    base_url = os.environ.get("LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    payload_records = [
        {
            "id": record["id"],
            "title": record["title"],
            "authors": record["authors"],
            "abstract_or_intro": record["context"],
            "keywords": record["keywords"],
            "basis": record["basis"],
        }
        for record in records
    ]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你为政治学与公共管理期刊文章写中文简明总结。"
                    "不要复制摘要，不要使用“本文研究了、作者认为、文章指出”等外部评述式表达。"
                    "直接说明文章关注的问题、对象、关系、机制或判断。"
                    "不能编造输入中没有的信息。只返回 JSON 数组。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请为下列文章逐篇生成 ai_summary。"
                    "每个总结用一到两句话，简洁、准确、平实，不做意义评价。"
                    "如果 basis 是“仅根据题目判断”，总结末尾必须加“仅根据题目判断”。"
                    "返回格式为 [{\"id\":\"...\",\"ai_summary\":\"...\"}]。\n\n"
                    + json.dumps(payload_records, ensure_ascii=False)
                ),
            },
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    parsed = extract_json_array(content)
    summaries: dict[str, str] = {}
    for item in parsed:
        if isinstance(item, dict) and item.get("id") and item.get("ai_summary"):
            summaries[str(item["id"])] = clean_text(item["ai_summary"])
    return summaries


def iter_issue_files(start_id: int | None, end_id: int | None) -> list[Path]:
    paths: list[Path] = []
    for journal_dir in sorted(HISTORY_JOURNALS_DIR.iterdir()):
        if not journal_dir.is_dir():
            continue
        match = re.match(r"(\d{4})-", journal_dir.name)
        if not match:
            continue
        journal_id = int(match.group(1))
        if start_id is not None and journal_id < start_id:
            continue
        if end_id is not None and journal_id > end_id:
            continue
        paths.extend(sorted((journal_dir / "issues").glob("*/works.jsonl")))
    return paths


def count_journal_dirs(start_id: int | None, end_id: int | None) -> int:
    count = 0
    for journal_dir in HISTORY_JOURNALS_DIR.iterdir():
        if not journal_dir.is_dir():
            continue
        match = re.match(r"(\d{4})-", journal_dir.name)
        if not match:
            continue
        journal_id = int(match.group(1))
        if start_id is not None and journal_id < start_id:
            continue
        if end_id is not None and journal_id > end_id:
            continue
        count += 1
    return count


def read_existing_summary_keys(path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys.add((clean_text(row.get("title")), clean_text(row.get("source_file"))))
    return keys


def article_summary_output(row: dict[str, Any], source_file: str, summary: str, basis: str) -> dict[str, Any]:
    published = parse_publication_date(row)
    return {
        "journal": row.get("journal_title") or row.get("source_display_name") or "",
        "year": published.year if published else None,
        "month": published.month if published else None,
        "title": clean_text(row.get("title")),
        "authors": row.get("authors") or [],
        "source_file": source_file,
        "ai_summary": append_title_only_marker(summary, basis),
        "basis": basis,
    }


def run_article_summary_generation(
    start_id: int | None,
    end_id: int | None,
    batch_size: int,
    max_articles: int | None,
    sleep_seconds: float,
    dry_run: bool,
) -> dict[str, Any]:
    issue_files = iter_issue_files(start_id, end_id)
    scanned_journals = count_journal_dirs(start_id, end_id)
    scanned_periods = len(issue_files)
    found_articles = 0
    generated = 0
    skipped = 0
    insufficient = 0
    missing_api = 0
    pending: list[tuple[Path, dict[str, Any], str, str]] = []

    def flush_batch() -> None:
        nonlocal generated, missing_api
        if not pending:
            return
        batch = list(pending)
        pending.clear()
        if dry_run:
            return
        records = [
            {
                "id": str(index),
                "title": clean_text(row.get("title")),
                "authors": row.get("authors") or [],
                "context": article_context(row)[:4000],
                "keywords": article_keywords(row)[:1000],
                "basis": basis,
            }
            for index, (_, row, _, basis) in enumerate(batch)
        ]
        try:
            summaries = generate_article_summaries_with_llm(records)
        except RuntimeError as exc:
            if "LLM_API_KEY" in str(exc):
                missing_api += len(batch)
                return
            raise
        by_output: dict[Path, list[dict[str, Any]]] = {}
        for index, (output_path, row, source_file, basis) in enumerate(batch):
            summary = summaries.get(str(index), "")
            if not summary:
                summary = "信息不足，暂不展开"
                basis = "信息不足，暂不展开"
            by_output.setdefault(output_path, []).append(article_summary_output(row, source_file, summary, basis))
        for output_path, rows in by_output.items():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    append_jsonl(handle, row)
                    generated += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    for source_path in issue_files:
        output_path = source_path.parent / "ai_summaries.jsonl"
        existing = read_existing_summary_keys(output_path)
        source_file = str(source_path.relative_to(ROOT)).replace("\\", "/")
        for row in read_jsonl_rows(source_path):
            if max_articles is not None and found_articles >= max_articles:
                flush_batch()
                return {
                    "scanned_journals": scanned_journals,
                    "scanned_periods": scanned_periods,
                    "found_articles": found_articles,
                    "generated": generated,
                    "skipped": skipped,
                    "insufficient": insufficient,
                    "missing_api": missing_api,
                    "dry_run": dry_run,
                }
            found_articles += 1
            key = (clean_text(row.get("title")), source_file)
            if key in existing:
                skipped += 1
                continue
            basis = summary_basis(row)
            if basis == "信息不足，暂不展开":
                insufficient += 1
                if not dry_run:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
                        append_jsonl(handle, article_summary_output(row, source_file, "信息不足，暂不展开", basis))
                        generated += 1
                continue
            pending.append((output_path, row, source_file, basis))
            if len(pending) >= batch_size:
                flush_batch()
    flush_batch()
    return {
        "scanned_journals": scanned_journals,
        "scanned_periods": scanned_periods,
        "found_articles": found_articles,
        "generated": generated,
        "skipped": skipped,
        "insufficient": insufficient,
        "missing_api": missing_api,
        "dry_run": dry_run,
    }


def markdown_link(title: str, url: str) -> str:
    escaped_title = title.replace("[", "\\[").replace("]", "\\]")
    return f"[{escaped_title}]({url})"


def summarize_without_llm(items: list[dict[str, Any]], period_label: str) -> str:
    if not items:
        return f"{period_label}没有发现新的论文记录。"
    journal_counts: dict[str, int] = {}
    for item in items:
        journal_counts[item["journal_title"]] = journal_counts.get(item["journal_title"], 0) + 1
    top = sorted(journal_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
    top_text = "；".join(f"{journal} {count} 篇" for journal, count in top)
    return f"{period_label}共发现 {len(items)} 条新增论文记录。新增较多的期刊包括：{top_text}。"


def summarize_with_llm(items: list[dict[str, Any]], period_label: str) -> str | None:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key or not items:
        return None
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    titles = "\n".join(
        f"- {item['journal_title']}: {item['title']}" for item in items[:80]
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是政治学与公共管理研究助手。请用中文为学术论文更新写简洁日报/周报摘要。",
            },
            {
                "role": "user",
                "content": f"请总结{period_label}的期刊新论文，突出主题、方法和可能值得关注的方向。\n\n{titles}",
            },
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"LLM 摘要暂不可用，已回退到规则摘要。错误：{exc}"


def build_report(
    report_type: str,
    date_label: str,
    period_label: str,
    items: list[dict[str, Any]],
    diagnostics: list[dict[str, str]] | None = None,
    baseline_initialized: bool = False,
) -> str:
    title = f"{period_label}期刊论文追踪{report_type}"
    summary = summarize_with_llm(items, period_label) or summarize_without_llm(items, period_label)
    lines = [
        f"# {title}",
        "",
        f"- 生成日期：{date_label}",
        f"- 新增记录：{len(items)}",
        f"- 摘要模式：{'LLM' if os.environ.get('LLM_API_KEY') and items else '规则版'}",
        "",
    ]
    if baseline_initialized:
        lines.extend(
            [
                "> 首次运行已建立基线。为避免把历史论文全部当成新增，本次不发送大量历史条目；之后只追踪新增。",
                "",
            ]
        )
    lines.extend(["## 自动摘要", "", summary, ""])
    if items:
        lines.extend(["## 新增论文", ""])
        by_journal: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_journal.setdefault(item["journal_title"], []).append(item)
        for journal_title in sorted(by_journal):
            lines.extend([f"### {journal_title}", ""])
            for item in by_journal[journal_title]:
                published = f"（{item['published']}）" if item.get("published") else ""
                lines.append(f"- {markdown_link(item['title'], item['url'])}{published}")
            lines.append("")
    if diagnostics:
        failed = [entry for entry in diagnostics if entry["status"].startswith("fetch error")]
        if failed:
            lines.extend(["## 抓取提示", ""])
            for entry in failed[:20]:
                lines.append(f"- {entry['journal_title']}: {entry['status']}")
            if len(failed) > 20:
                lines.append(f"- 另有 {len(failed) - 20} 条抓取提示未列出。")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or username
    recipients = [addr.strip() for addr in os.environ.get("MAIL_TO", "").split(",") if addr.strip()]
    if not host or not username or not password or not sender or not recipients:
        print("Email skipped: SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM/SMTP_USERNAME, or MAIL_TO is missing.")
        return False

    port = int(os.environ.get("SMTP_PORT", "465"))
    message = email.message.EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(message)
    return True


def save_report(report_type: str, run_date: dt.date, body: str) -> Path:
    report_dir = REPORTS_DIR / report_type
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{run_date.isoformat()}.md"
    path.write_text(body, encoding="utf-8")
    return path


def date_range_for_report(report_type: str, run_date: dt.date) -> tuple[dt.date, dt.date, str]:
    if report_type == "weekly":
        end = run_date - dt.timedelta(days=1)
        start = end - dt.timedelta(days=6)
        return start, end, f"{start.isoformat()} 至 {end.isoformat()} 周报"
    if report_type == "monthly":
        first = run_date.replace(day=1)
        end = first - dt.timedelta(days=1)
        start = end.replace(day=1)
        return start, end, f"{start.strftime('%Y-%m')} 月报"
    if report_type == "quarterly":
        current_quarter_start_month = ((run_date.month - 1) // 3) * 3 + 1
        current_quarter_start = dt.date(run_date.year, current_quarter_start_month, 1)
        end = current_quarter_start - dt.timedelta(days=1)
        start_month = ((end.month - 1) // 3) * 3 + 1
        start = dt.date(end.year, start_month, 1)
        quarter = ((start.month - 1) // 3) + 1
        return start, end, f"{start.year} Q{quarter} 季报"
    return run_date, run_date, f"{run_date.isoformat()} 日报"


def load_items_between(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current = start
    while current <= end:
        path = ITEMS_DIR / f"{current.isoformat()}.json"
        if path.exists():
            payload = load_json(path, {})
            items.extend(payload.get("new_items", []))
        current += dt.timedelta(days=1)
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        deduped[item["id"]] = item
    return list(deduped.values())


def run_fetch(run_date: dt.date, per_journal_limit: int, include_baseline: bool, no_fetch: bool) -> dict[str, Any]:
    journals = [journal for journal in load_json(JOURNALS_PATH, []) if journal.get("enabled", True)]
    state = load_json(STATE_PATH, {"seen": {}, "initialized_at": None})
    overrides = load_json(OVERRIDES_PATH, {})
    seen: dict[str, Any] = state.setdefault("seen", {})
    baseline_initialized = not bool(state.get("initialized_at"))
    diagnostics: list[dict[str, str]] = []
    discovered: list[Article] = []

    if not no_fetch:
        for journal in journals:
            articles, status = fetch_articles_for_journal(journal, overrides, per_journal_limit)
            diagnostics.append({"journal_title": journal["title"], "status": status})
            discovered.extend(articles)

    new_items: list[dict[str, Any]] = []
    for article in discovered:
        item = article_to_dict(article)
        if item["id"] not in seen:
            seen[item["id"]] = {"first_seen": run_date.isoformat(), "journal_title": article.journal_title, "title": article.title}
            if include_baseline or not baseline_initialized:
                new_items.append(item)

    if baseline_initialized:
        state["initialized_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_json(STATE_PATH, state)

    payload = {
        "date": run_date.isoformat(),
        "baseline_initialized": baseline_initialized,
        "new_items": new_items,
        "diagnostics": diagnostics,
        "discovered_count": len(discovered),
        "journal_count": len(journals),
    }
    save_json(ITEMS_DIR / f"{run_date.isoformat()}.json", payload)
    return payload


def run_reports(run_date: dt.date, daily_payload: dict[str, Any], all_due: bool, send_mail: bool) -> list[Path]:
    generated: list[Path] = []
    daily_body = build_report(
        "日报",
        run_date.isoformat(),
        f"{run_date.isoformat()}",
        daily_payload.get("new_items", []),
        daily_payload.get("diagnostics", []),
        daily_payload.get("baseline_initialized", False),
    )
    generated.append(save_report("daily", run_date, daily_body))
    if send_mail:
        send_email(f"期刊论文追踪日报 {run_date.isoformat()}", daily_body)

    due_reports: list[str] = []
    if all_due and run_date.weekday() == 0:
        due_reports.append("weekly")
    if all_due and run_date.day == 1:
        due_reports.append("monthly")
    if all_due and run_date.day == 1 and run_date.month in {1, 4, 7, 10}:
        due_reports.append("quarterly")

    for report_type in due_reports:
        start, end, period_label = date_range_for_report(report_type, run_date)
        items = load_items_between(start, end)
        report_name = {"weekly": "周报", "monthly": "月报", "quarterly": "季报"}[report_type]
        body = build_report(report_name, run_date.isoformat(), period_label, items)
        generated.append(save_report(report_type, run_date, body))
        if send_mail:
            send_email(f"期刊论文追踪{report_name} {period_label}", body)
    return generated


def parse_date(value: str | None) -> dt.date:
    if not value:
        return today_china()
    return dt.date.fromisoformat(value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Track latest journal publications and send digests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Fetch articles, generate reports, and optionally send email.")
    run_parser.add_argument("--date", help="Run date in YYYY-MM-DD. Defaults to Asia/Shanghai today.")
    run_parser.add_argument("--all-due", action="store_true", help="Generate weekly/monthly/quarterly reports when due.")
    run_parser.add_argument("--send-email", action="store_true", help="Send generated report emails when SMTP is configured.")
    run_parser.add_argument("--per-journal-limit", type=int, default=int(os.environ.get("PER_JOURNAL_LIMIT", "5")))
    run_parser.add_argument("--include-baseline", action="store_true", help="Treat first discovered records as new on initial run.")
    run_parser.add_argument("--no-fetch", action="store_true", help="Skip network fetching and only generate an empty daily payload.")

    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical public metadata through OpenAlex.")
    backfill_parser.add_argument("--start-id", type=int, help="First journal id to include.")
    backfill_parser.add_argument("--end-id", type=int, help="Last journal id to include.")
    backfill_parser.add_argument("--max-journals", type=int, help="Maximum number of journals to process.")
    backfill_parser.add_argument(
        "--max-pages-per-journal",
        type=int,
        default=int(os.environ.get("MAX_PAGES_PER_JOURNAL", "0")),
        help="OpenAlex cursor pages per journal. 0 means no explicit page limit.",
    )
    backfill_parser.add_argument("--per-page", type=int, default=int(os.environ.get("OPENALEX_PER_PAGE", "200")))
    backfill_parser.add_argument("--sleep-seconds", type=float, default=float(os.environ.get("OPENALEX_SLEEP_SECONDS", "0.15")))
    backfill_parser.add_argument("--include-abstracts", action="store_true", help="Store abstracts when OpenAlex provides them.")
    backfill_parser.add_argument("--no-resume", action="store_true", help="Refetch journals even if a history file already exists.")
    backfill_parser.add_argument("--dry-run", action="store_true", help="Fetch and count without writing history files.")

    split_parser = subparsers.add_parser("split-issues", help="Split journal history files into issue-like date buckets.")
    split_parser.add_argument("--start-id", type=int, help="First journal id to include.")
    split_parser.add_argument("--end-id", type=int, help="Last journal id to include.")
    split_parser.add_argument(
        "--frequency",
        choices=["auto", "monthly", "quarterly"],
        default="auto",
        help="Issue bucket frequency. Auto infers monthly or quarterly per journal.",
    )
    split_parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not clear existing issues directories before regenerating buckets.",
    )

    summarize_parser = subparsers.add_parser("summarize-articles", help="Generate per-article AI summaries in each issue folder.")
    summarize_parser.add_argument("--start-id", type=int, help="First journal id to include.")
    summarize_parser.add_argument("--end-id", type=int, help="Last journal id to include.")
    summarize_parser.add_argument("--batch-size", type=int, default=int(os.environ.get("LLM_ARTICLE_BATCH_SIZE", "20")))
    summarize_parser.add_argument("--max-articles", type=int, help="Maximum number of unskipped source articles to scan.")
    summarize_parser.add_argument("--sleep-seconds", type=float, default=float(os.environ.get("LLM_SLEEP_SECONDS", "6")))
    summarize_parser.add_argument("--dry-run", action="store_true", help="Scan and report counts without writing summaries.")

    args = parser.parse_args(argv)
    if args.command == "run":
        run_date = parse_date(args.date)
        payload = run_fetch(run_date, args.per_journal_limit, args.include_baseline, args.no_fetch)
        paths = run_reports(run_date, payload, args.all_due, args.send_email)
        print(
            textwrap.dedent(
                f"""
                Journal tracker completed.
                Date: {run_date.isoformat()}
                Journals: {payload['journal_count']}
                Discovered records: {payload['discovered_count']}
                New records: {len(payload['new_items'])}
                Reports: {', '.join(str(path.relative_to(ROOT)) for path in paths)}
                """
            ).strip()
        )
    elif args.command == "backfill":
        result = run_historical_backfill(
            args.start_id,
            args.end_id,
            args.max_journals,
            args.max_pages_per_journal,
            args.per_page,
            args.sleep_seconds,
            args.include_abstracts,
            args.dry_run,
            not args.no_resume,
        )
        print(
            textwrap.dedent(
                f"""
                Historical backfill completed.
                Selected journals: {result['selected_journals']}
                Total metadata records: {result['total_items']}
                Include abstracts: {result['include_abstracts']}
                Dry run: {result['dry_run']}
                """
            ).strip()
        )
    elif args.command == "split-issues":
        result = run_issue_split(args.start_id, args.end_id, args.frequency, not args.keep_existing)
        print(
            textwrap.dedent(
                f"""
                Issue split completed.
                Selected journals: {result['selected_journals']}
                Total metadata records: {result['total_items']}
                Period files: {result['period_files']}
                Frequency mode: {result['frequency_mode']}
                """
            ).strip()
        )
    elif args.command == "summarize-articles":
        result = run_article_summary_generation(
            args.start_id,
            args.end_id,
            args.batch_size,
            args.max_articles,
            args.sleep_seconds,
            args.dry_run,
        )
        print(
            textwrap.dedent(
                f"""
                Article summary generation completed.
                Scanned journal directories: {result['scanned_journals']}
                Scanned period directories: {result['scanned_periods']}
                Found articles: {result['found_articles']}
                Newly generated summaries: {result['generated']}
                Skipped existing summaries: {result['skipped']}
                Insufficient information: {result['insufficient']}
                Missing API key: {result['missing_api']}
                Dry run: {result['dry_run']}
                """
            ).strip()
        )


if __name__ == "__main__":
    main(sys.argv[1:])
