from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .tracker import HISTORY_JOURNALS_DIR, JOURNALS_PATH, ROOT, load_json, normalize_text


UNIFIED_JOURNALS_DIR = ROOT / "data" / "journals"
CHINESE_CSSCI_ALL_ARTICLES = ROOT / "data" / "chinese_political_cssci" / "all_articles.jsonl"
CHINESE_CSSCI_STATUS = ROOT / "data" / "chinese_political_cssci" / "journals_status.json"
METADATA_PATH = ROOT / "config" / "journal_metadata.json"

CATEGORY_NAMES = {
    ("中文", "政治学"): "中文政治学期刊",
    ("英文", "政治学"): "英文政治学期刊",
    ("中文", "公共管理"): "中文公共管理期刊",
    ("英文", "公共管理"): "英文公共管理期刊",
}

STANDARD_FIELDS = [
    "journal",
    "journal_id",
    "category",
    "discipline",
    "journal_type",
    "language",
    "quartile",
    "year",
    "issue",
    "month",
    "issue_label",
    "period",
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
]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def list_value(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [text_value(item) for item in value if text_value(item)]
    if isinstance(value, str):
        parts = re.split(r"[;；,，、]\s*", value)
        return [text_value(part) for part in parts if text_value(part)]
    return [text_value(value)]


def safe_segment(value: Any) -> str:
    segment = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text_value(value))
    segment = re.sub(r"\s+", "_", segment).strip(" ._")
    return segment[:120] or "untitled"


def normalize_key(value: Any) -> str:
    text = text_value(value).casefold()
    return re.sub(r"[\s\u3000·•,，、:：;；.!！?？'\"“”‘’《》〈〉（）()【】\[\]_\-—]+", "", text)


def parse_history_journal_folder(path: Path) -> tuple[int | None, str]:
    match = re.match(r"^(\d{4})-(.+)$", path.name)
    if not match:
        return None, path.name.replace("_", " ")
    return int(match.group(1)), match.group(2).replace("_", " ")


def source_url(row: dict[str, Any]) -> str:
    for key in ("source_url", "url", "landing_page_url", "doi", "openalex_id"):
        value = text_value(row.get(key))
        if value:
            return value
    return ""


def article_quality(row: dict[str, Any]) -> int:
    score = 0
    for field in ("title", "authors", "abstract", "keywords", "pages", "source_url"):
        value = row.get(field)
        if isinstance(value, list):
            score += 1 if value else 0
        elif value:
            score += 1
    confidence = row.get("confidence")
    if isinstance(confidence, int | float):
        score += int(confidence * 10)
    return score


def normalize_issue_number(value: Any) -> int | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = text_value(value)
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())
    return text or None


def period_sort_value(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return 0


def period_folder(year: int | None, issue: Any, month: Any, label: str) -> str:
    number = issue if issue not in (None, "") else month
    if isinstance(number, int):
        return f"{year}-{number:02d}" if year else f"{number:02d}"
    if number:
        return f"{year}-{safe_segment(number)}" if year else safe_segment(number)
    return f"{year}-{safe_segment(label)}" if year and label else str(year or "unknown")


def period_from_history(period: str, frequency: str) -> dict[str, Any]:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", period)
    if not match:
        return {"year": None, "issue": period, "month": None, "issue_label": period, "period": period}
    year = int(match.group(1))
    number = int(match.group(2))
    if frequency == "quarterly":
        return {
            "year": year,
            "issue": number,
            "month": None,
            "issue_label": f"{year}年第{number}期",
            "period": f"{year}-{number:02d}",
        }
    return {
        "year": year,
        "issue": "",
        "month": number,
        "issue_label": f"{year}-{number:02d}",
        "period": f"{year}-{number:02d}",
    }


def period_from_article(row: dict[str, Any]) -> dict[str, Any]:
    year = int(row["year"]) if str(row.get("year") or "").isdigit() else None
    issue = normalize_issue_number(row.get("issue"))
    month = normalize_issue_number(row.get("month"))
    label = text_value(row.get("issue_label"))
    if isinstance(issue, int):
        period = f"{year}-{issue:02d}" if year else f"{issue:02d}"
        label = label or (f"{year}年第{issue}期" if year else str(issue))
    elif isinstance(month, int):
        period = f"{year}-{month:02d}" if year else f"{month:02d}"
        label = label or (f"{year}-{month:02d}" if year else str(month))
        issue = ""
    else:
        period = period_folder(year, issue, month, label)
        issue = issue or ""
        label = label or period
    return {"year": year, "issue": issue, "month": month if isinstance(month, int) else None, "issue_label": label, "period": period}


def history_frequency(journal_dir: Path) -> str:
    index_path = journal_dir / "issue_index.json"
    if not index_path.exists():
        return "monthly"
    payload = load_json(index_path, {})
    return text_value(payload.get("frequency")) or "monthly"


def load_metadata() -> tuple[dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = load_json(METADATA_PATH, []) if METADATA_PATH.exists() else []
    by_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        if str(item.get("journal_id") or "").isdigit():
            by_id[int(item["journal_id"])] = item
        for key in (item.get("journal_name"), item.get("display_name")):
            if key:
                name_key = normalize_key(key)
                if item not in by_name[name_key]:
                    by_name[name_key].append(item)
    return by_id, by_name


def load_source_journals() -> tuple[dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = load_json(JOURNALS_PATH, []) if JOURNALS_PATH.exists() else []
    by_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").isdigit():
            by_id[int(row["id"])] = row
        if row.get("title"):
            by_name[normalize_key(row["title"])].append(row)
    return by_id, by_name


def load_curated_status() -> tuple[dict[str, dict[str, Any]], set[str]]:
    rows = load_json(CHINESE_CSSCI_STATUS, []) if CHINESE_CSSCI_STATUS.exists() else []
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("journal"):
            by_name[normalize_key(row["journal"])] = row
    return by_name, set(by_name)


def choose_metadata(
    journal_name: str,
    journal_id: int | None,
    metadata_by_id: dict[int, dict[str, Any]],
    metadata_by_name: dict[str, list[dict[str, Any]]],
    source_by_id: dict[int, dict[str, Any]],
    curated_status_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    name_key = normalize_key(journal_name)
    curated = curated_status_by_name.get(name_key)
    if curated and str(curated.get("journal_id") or "").isdigit():
        journal_id = int(curated["journal_id"])
    metadata = metadata_by_id.get(journal_id or -1)
    if not metadata:
        candidates = metadata_by_name.get(name_key, [])
        metadata = candidates[0] if candidates else None
    source = source_by_id.get(journal_id or -1, {})
    display_name = text_value((metadata or {}).get("display_name")) or text_value((metadata or {}).get("journal_name")) or journal_name
    discipline = text_value((metadata or {}).get("discipline")) or text_value(source.get("field")) or text_value(curated.get("discipline") if curated else "") or "未分类"
    language = text_value((metadata or {}).get("language")) or text_value(source.get("language")) or "未分类"
    journal_type = text_value((metadata or {}).get("journal_type")) or text_value(source.get("database")) or "未分类"
    return {
        "journal_id": journal_id,
        "journal_name": text_value((metadata or {}).get("journal_name")) or journal_name,
        "display_name": display_name,
        "discipline": discipline,
        "journal_type": journal_type,
        "language": language,
        "quartile": text_value((metadata or {}).get("quartile")) or text_value(source.get("category")),
        "notes": text_value((metadata or {}).get("notes")) or text_value(source.get("source_note")),
    }


def category_for(metadata: dict[str, Any]) -> str:
    language = "中文" if "中文" in text_value(metadata.get("language")) else "英文"
    discipline = "政治学" if "政治" in text_value(metadata.get("discipline")) else "公共管理"
    return CATEGORY_NAMES[(language, discipline)]


def data_status_for(row: dict[str, Any]) -> str:
    if not text_value(row.get("title")):
        return "missing_title"
    if not list_value(row.get("authors")):
        return "missing_authors"
    if not text_value(row.get("abstract")):
        return "missing_abstract"
    return "complete"


def standardize_article(
    row: dict[str, Any],
    metadata: dict[str, Any],
    period: dict[str, Any],
    source_file: str,
    source_name: str,
    crawl_time: str,
    confidence: float,
    notes: str,
) -> dict[str, Any]:
    title = text_value(row.get("title") or row.get("original_title"))
    article = {
        "journal": metadata["display_name"],
        "journal_id": metadata.get("journal_id"),
        "category": category_for(metadata),
        "discipline": metadata["discipline"],
        "journal_type": metadata["journal_type"],
        "language": metadata["language"],
        "quartile": metadata["quartile"],
        "year": period["year"],
        "issue": period["issue"],
        "month": period["month"],
        "issue_label": period["issue_label"],
        "period": period["period"],
        "title": title,
        "original_title": text_value(row.get("original_title")) or title,
        "authors": list_value(row.get("authors")),
        "abstract": text_value(row.get("abstract") or row.get("summary") or row.get("description")),
        "keywords": list_value(row.get("keywords") or row.get("keyword")),
        "pages": text_value(row.get("pages")),
        "column": text_value(row.get("column")),
        "source_url": source_url(row),
        "source_name": text_value(row.get("source_name")) or source_name,
        "source_file": source_file,
        "crawl_time": text_value(row.get("crawl_time")) or crawl_time,
        "data_status": text_value(row.get("data_status")),
        "confidence": row.get("confidence", confidence),
        "notes": text_value(row.get("notes")) or notes,
    }
    if not article["keywords"] and isinstance(row.get("concepts"), list):
        article["keywords"] = [text_value(item.get("display_name")) for item in row["concepts"] if isinstance(item, dict) and item.get("display_name")]
    if not article["data_status"]:
        article["data_status"] = data_status_for(article)
    return {field: article.get(field, "") for field in STANDARD_FIELDS}


def merge_key(article: dict[str, Any]) -> tuple[str, str, str]:
    title_key = normalize_key(article.get("title"))
    fallback = normalize_key(article.get("source_url"))
    return (normalize_key(article.get("journal")), text_value(article.get("period")), title_key or fallback)


def sort_articles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (period_sort_value(row.get("issue") or row.get("month")), text_value(row.get("title"))))


def build_unified_journals(output_dir: Path = UNIFIED_JOURNALS_DIR, clear: bool = True) -> dict[str, Any]:
    if not HISTORY_JOURNALS_DIR.exists() and not CHINESE_CSSCI_ALL_ARTICLES.exists():
        existing_index = output_dir / "index.json"
        if existing_index.exists():
            payload = load_json(existing_index, {})
            return {
                "data_dir": str(output_dir.relative_to(ROOT)),
                "journal_count": int(payload.get("journal_count") or 0),
                "issue_count": int(payload.get("issue_count") or 0),
                "article_count": int(payload.get("article_count") or 0),
                "imported_history_records": 0,
                "imported_curated_records": 0,
                "skipped_curated_history_journals": 0,
            }
        raise FileNotFoundError("No legacy import sources found and data/journals does not exist.")

    metadata_by_id, metadata_by_name = load_metadata()
    source_by_id, _source_by_name = load_source_journals()
    curated_status_by_name, curated_names = load_curated_status()
    crawl_time = dt.datetime.now(dt.timezone.utc).isoformat()

    articles_by_issue: dict[tuple[str, str, str, str], dict[tuple[str, str, str], dict[str, Any]]] = defaultdict(dict)
    journal_meta_by_folder: dict[tuple[str, str], dict[str, Any]] = {}
    imported_history = 0
    skipped_curated_history = 0
    imported_curated = 0

    if HISTORY_JOURNALS_DIR.exists():
        for journal_dir in sorted(path for path in HISTORY_JOURNALS_DIR.iterdir() if path.is_dir()):
            journal_id, folder_title = parse_history_journal_folder(journal_dir)
            source = source_by_id.get(journal_id or -1, {})
            journal_name = text_value(source.get("title")) or text_value(folder_title)
            if normalize_key(journal_name) in curated_names:
                skipped_curated_history += 1
                continue
            metadata = choose_metadata(journal_name, journal_id, metadata_by_id, metadata_by_name, source_by_id, curated_status_by_name)
            category = category_for(metadata)
            folder = f"{int(metadata['journal_id']):04d}-{safe_segment(metadata['display_name'])}" if metadata.get("journal_id") else safe_segment(metadata["display_name"])
            journal_meta_by_folder[(category, folder)] = {**metadata, "category": category, "folder": folder}
            issues_dir = journal_dir / "issues"
            if not issues_dir.exists():
                continue
            frequency = history_frequency(journal_dir)
            for issue_dir in sorted(path for path in issues_dir.iterdir() if path.is_dir()):
                period = period_from_history(issue_dir.name, frequency)
                source_file = str((issue_dir / "works.jsonl").relative_to(ROOT)).replace("\\", "/")
                issue_folder = period_folder(period["year"], period["issue"], period["month"], period["issue_label"])
                issue_key = (category, folder, str(period["year"] or "unknown"), issue_folder)
                for row in iter_jsonl(issue_dir / "works.jsonl"):
                    article = standardize_article(
                        row,
                        metadata,
                        period,
                        source_file,
                        "OpenAlex public metadata",
                        crawl_time,
                        0.65,
                        "从历史元数据统一整理；未下载 PDF",
                    )
                    key = merge_key(article)
                    existing = articles_by_issue[issue_key].get(key)
                    if not existing or article_quality(article) > article_quality(existing):
                        articles_by_issue[issue_key][key] = article
                    imported_history += 1

    if CHINESE_CSSCI_ALL_ARTICLES.exists():
        for row in iter_jsonl(CHINESE_CSSCI_ALL_ARTICLES):
            journal_name = text_value(row.get("journal"))
            if not journal_name:
                continue
            curated = curated_status_by_name.get(normalize_key(journal_name), {})
            journal_id = int(curated["journal_id"]) if str(curated.get("journal_id") or "").isdigit() else None
            metadata = choose_metadata(journal_name, journal_id, metadata_by_id, metadata_by_name, source_by_id, curated_status_by_name)
            category = category_for(metadata)
            folder = f"{int(metadata['journal_id']):04d}-{safe_segment(metadata['display_name'])}" if metadata.get("journal_id") else safe_segment(metadata["display_name"])
            journal_meta_by_folder[(category, folder)] = {**metadata, "category": category, "folder": folder}
            period = period_from_article(row)
            issue_folder = period_folder(period["year"], period["issue"], period["month"], period["issue_label"])
            source_file = text_value(row.get("source_file"))
            issue_key = (category, folder, str(period["year"] or "unknown"), issue_folder)
            article = standardize_article(
                row,
                metadata,
                period,
                source_file,
                text_value(row.get("source_name")) or "公开目录页",
                crawl_time,
                0.9,
                "从中文 CSSCI 补齐库统一整理；未下载 PDF",
            )
            key = merge_key(article)
            existing = articles_by_issue[issue_key].get(key)
            if not existing or article_quality(article) > article_quality(existing):
                articles_by_issue[issue_key][key] = article
            imported_curated += 1

    if clear and output_dir.exists():
        if output_dir.resolve() != UNIFIED_JOURNALS_DIR.resolve():
            raise ValueError(f"Refusing to clear unexpected directory: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    issue_count = 0
    article_count = 0
    journal_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"years": set(), "issues": 0, "articles": 0})
    for (category, folder, year, issue_folder), bucket in sorted(articles_by_issue.items()):
        rows = sort_articles(list(bucket.values()))
        issue_dir = output_dir / category / folder / year / issue_folder
        write_jsonl(issue_dir / "articles.jsonl", rows)
        first = rows[0]
        write_json(
            issue_dir / "issue.json",
            {
                "journal": first["journal"],
                "category": category,
                "year": first["year"],
                "issue": first["issue"],
                "month": first["month"],
                "issue_label": first["issue_label"],
                "period": first["period"],
                "article_count": len(rows),
            },
        )
        issue_count += 1
        article_count += len(rows)
        stat = journal_stats[(category, folder)]
        if first["year"]:
            stat["years"].add(int(first["year"]))
        stat["issues"] += 1
        stat["articles"] += len(rows)

    for (category, folder), metadata in sorted(journal_meta_by_folder.items()):
        stat = journal_stats[(category, folder)]
        years = sorted(stat["years"])
        journal_dir = output_dir / category / folder
        write_json(
            journal_dir / "journal.json",
            {
                **metadata,
                "year_start": years[0] if years else None,
                "year_end": years[-1] if years else None,
                "year_range": f"{years[0]}-{years[-1]}" if years else "暂无",
                "issue_count": stat["issues"],
                "article_count": stat["articles"],
            },
        )

    write_json(
        output_dir / "index.json",
        {
            "generated_at": crawl_time,
            "category_count": len(CATEGORY_NAMES),
            "journal_count": len(journal_meta_by_folder),
            "issue_count": issue_count,
            "article_count": article_count,
            "imported_history_records": imported_history,
            "imported_curated_records": imported_curated,
            "skipped_curated_history_journals": skipped_curated_history,
            "layout": "data/journals/{category}/{journal}/{year}/{period}/articles.jsonl",
        },
    )
    return {
        "data_dir": str(output_dir.relative_to(ROOT)),
        "journal_count": len(journal_meta_by_folder),
        "issue_count": issue_count,
        "article_count": article_count,
        "imported_history_records": imported_history,
        "imported_curated_records": imported_curated,
        "skipped_curated_history_journals": skipped_curated_history,
    }
