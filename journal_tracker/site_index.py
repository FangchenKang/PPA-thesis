from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .tracker import (
    HISTORY_JOURNALS_DIR,
    JOURNALS_PATH,
    ROOT,
    clean_text,
    load_json,
    normalize_text,
)


METADATA_PATH = ROOT / "config" / "journal_metadata.json"
SITE_DIR = ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"
ARTICLE_SCHEMA = [
    "id",
    "journal",
    "journal_key",
    "discipline",
    "journal_type",
    "quartile",
    "year",
    "month",
    "issue",
    "period",
    "period_label",
    "publication_date",
    "title",
    "authors",
    "abstract",
    "keywords",
    "ai_summary",
    "basis",
    "source_url",
    "source_file",
]
DICTIONARY_FIELDS = {
    "journal",
    "journal_key",
    "discipline",
    "journal_type",
    "quartile",
    "period",
    "period_label",
    "publication_date",
    "authors",
    "abstract",
    "keywords",
    "ai_summary",
    "basis",
    "source_file",
}


MOJIBAKE_MARKERS = (
    "鍏",
    "鑻",
    "绠",
    "鐞",
    "鏂",
    "鐢",
    "鎻",
    "涓",
    "鈥",
    "銆",
    "酶",
    "€",
    "�",
)


def compact_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


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


def mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in MOJIBAKE_MARKERS)


def repair_text(value: Any) -> Any:
    if isinstance(value, list):
        return [repair_text(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_text(item) for key, item in value.items()}
    if value is None or not isinstance(value, str):
        return value
    original = value
    best = original
    best_score = mojibake_score(original)
    for encoding in ("gb18030", "gbk", "cp936"):
        try:
            candidate = original.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        candidate_score = mojibake_score(candidate)
        if candidate_score < best_score:
            best = candidate
            best_score = candidate_score
    return best


def text_value(value: Any) -> str:
    return normalize_text(str(repair_text(value) or ""))


def list_value(value: Any) -> list[str]:
    value = repair_text(value)
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [normalize_text(str(item)) for item in value if normalize_text(str(item))]
    if isinstance(value, str):
        parts = re.split(r"[;；,，]\s*", value)
        return [normalize_text(part) for part in parts if normalize_text(part)]
    return [normalize_text(str(value))]


def parse_journal_folder(path: Path) -> tuple[int | None, str]:
    match = re.match(r"^(\d{4})-(.+)$", path.name)
    if not match:
        return None, path.name
    return int(match.group(1)), match.group(2).replace("_", " ")


def parse_period(period: str, frequency: str) -> dict[str, Any]:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", period)
    if not match:
        return {
            "year": None,
            "month": None,
            "issue": period,
            "period_label": period,
        }
    year = int(match.group(1))
    number = int(match.group(2))
    if frequency == "quarterly":
        return {
            "year": year,
            "month": None,
            "issue": number,
            "period_label": f"{year}年第{number}期",
        }
    return {
        "year": year,
        "month": number,
        "issue": "",
        "period_label": f"{year}-{number:02d}",
    }


def period_sort_key(issue: dict[str, Any]) -> tuple[int, int]:
    year = issue.get("year")
    number = issue.get("month") or issue.get("issue") or 0
    return (int(year or 0), int(number or 0))


def journal_key_for(journal_id: int | None, journal_name: str, folder_name: str) -> str:
    if journal_id is not None:
        return f"{journal_id:04d}-{safe_slug(journal_name or folder_name)}"
    return safe_slug(folder_name)


def safe_slug(value: str) -> str:
    value = repair_text(value)
    segment = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value))
    segment = re.sub(r"\s+", "_", segment).strip(" ._")
    return segment[:120] or "untitled"


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", text_value(value)).casefold()


def read_source_journals() -> dict[int, dict[str, Any]]:
    journals = load_json(JOURNALS_PATH, [])
    return {int(item["id"]): repair_text(item) for item in journals if item.get("id") is not None}


def seed_journal_metadata(output_path: Path = METADATA_PATH, overwrite: bool = False) -> dict[str, Any]:
    source_journals = read_source_journals()
    existing: list[dict[str, Any]] = []
    if output_path.exists() and not overwrite:
        existing = load_json(output_path, [])

    existing_by_id = {
        int(entry["journal_id"]): entry
        for entry in existing
        if isinstance(entry, dict) and str(entry.get("journal_id") or "").isdigit()
    }
    merged: list[dict[str, Any]] = []
    added = 0
    for journal_id in sorted(source_journals):
        source = source_journals[journal_id]
        if journal_id in existing_by_id:
            merged.append(existing_by_id[journal_id])
            continue
        added += 1
        merged.append(
            {
                "journal_id": journal_id,
                "journal_name": text_value(source.get("title")),
                "display_name": text_value(source.get("title")),
                "discipline": text_value(source.get("field")) or "未分类",
                "journal_type": text_value(source.get("database")) or "未分类",
                "language": text_value(source.get("language")) or "未分类",
                "quartile": text_value(source.get("category")),
                "notes": text_value(source.get("source_note")),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"metadata_path": str(output_path.relative_to(ROOT)), "total": len(merged), "added": added}


def load_metadata() -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = load_json(METADATA_PATH, []) if METADATA_PATH.exists() else []
    by_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = repair_text(row)
        if str(row.get("journal_id") or "").isdigit():
            by_id[int(row["journal_id"])] = row
        for key in (row.get("journal_name"), row.get("display_name")):
            if key:
                by_name[normalize_key(str(key))] = row
    return by_id, by_name


def metadata_for(
    journal_id: int | None,
    journal_name: str,
    source_journal: dict[str, Any] | None,
    metadata_by_id: dict[int, dict[str, Any]],
    metadata_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metadata = None
    if journal_id is not None:
        metadata = metadata_by_id.get(journal_id)
    if not metadata:
        metadata = metadata_by_name.get(normalize_key(journal_name))
    if metadata:
        return {
            "journal_name": text_value(metadata.get("journal_name")) or journal_name,
            "display_name": text_value(metadata.get("display_name")) or journal_name,
            "discipline": text_value(metadata.get("discipline")) or "未分类",
            "journal_type": text_value(metadata.get("journal_type")) or "未分类",
            "language": text_value(metadata.get("language")) or "未分类",
            "quartile": text_value(metadata.get("quartile")),
            "notes": text_value(metadata.get("notes")),
        }

    return {
        "journal_name": journal_name,
        "display_name": journal_name,
        "discipline": "未分类",
        "journal_type": "未分类",
        "language": text_value((source_journal or {}).get("language")) or "未分类",
        "quartile": "",
        "notes": "请在 config/journal_metadata.json 中补充元数据",
    }


def summary_key(title: Any) -> str:
    return normalize_key(clean_text(repair_text(title)))


def load_summary_map(issue_dir: Path) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(issue_dir / "ai_summaries.jsonl"):
        key = summary_key(row.get("title"))
        if key:
            summaries[key] = repair_text(row)
    return summaries


def article_keywords(row: dict[str, Any]) -> list[str]:
    for key in ("keywords", "keyword"):
        if row.get(key):
            return list_value(row[key])
    concepts = row.get("concepts")
    if isinstance(concepts, list):
        names = []
        for item in concepts:
            if isinstance(item, dict) and item.get("display_name"):
                names.append(text_value(item["display_name"]))
        return [name for name in names if name]
    return []


def source_url(row: dict[str, Any]) -> str:
    for key in ("url", "landing_page_url", "doi", "openalex_id"):
        value = text_value(row.get(key))
        if value:
            return value
    return ""


def publication_date(row: dict[str, Any], year: int | None) -> str:
    value = text_value(row.get("publication_date"))
    if value:
        return value[:10]
    if year:
        return str(year)
    return ""


def article_id_for(parts: list[Any]) -> str:
    raw = "|".join(text_value(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def issue_frequency(journal_dir: Path) -> str:
    index_path = journal_dir / "issue_index.json"
    if not index_path.exists():
        return "monthly"
    payload = load_json(index_path, {})
    return text_value(payload.get("frequency")) or "monthly"


def build_site_index(output_dir: Path = SITE_DATA_DIR) -> dict[str, Any]:
    source_journals = read_source_journals()
    metadata_by_id, metadata_by_name = load_metadata()
    journal_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    article_rows: list[list[Any]] = []

    journal_dirs = sorted(path for path in HISTORY_JOURNALS_DIR.iterdir() if path.is_dir())
    for journal_dir in journal_dirs:
        journal_id, folder_title = parse_journal_folder(journal_dir)
        source_journal = source_journals.get(journal_id or -1)
        journal_name = text_value((source_journal or {}).get("title")) or text_value(folder_title)
        metadata = metadata_for(journal_id, journal_name, source_journal, metadata_by_id, metadata_by_name)
        journal_key = journal_key_for(journal_id, metadata["display_name"], journal_dir.name)
        frequency = issue_frequency(journal_dir)

        years: set[int] = set()
        article_count = 0
        issue_count = 0
        issues_dir = journal_dir / "issues"
        issue_dirs = sorted(path for path in issues_dir.iterdir() if path.is_dir()) if issues_dir.exists() else []

        for issue_dir in issue_dirs:
            period = issue_dir.name
            period_info = parse_period(period, frequency)
            summaries = load_summary_map(issue_dir)
            source_file = f"{journal_dir.name}/issues/{period}/works.jsonl"
            works_path = issue_dir / "works.jsonl"
            rows = iter_jsonl(works_path)
            if not rows:
                continue

            issue_count += 1
            year = period_info["year"]
            if year:
                years.add(int(year))
            issue_article_count = 0
            for row in rows:
                row = repair_text(row)
                title = text_value(row.get("title"))
                authors = list_value(row.get("authors"))
                abstract = text_value(row.get("abstract") or row.get("summary") or row.get("description"))
                keywords = article_keywords(row)
                summary = summaries.get(summary_key(title), {})
                ai_summary = text_value(summary.get("ai_summary"))
                basis = text_value(summary.get("basis"))
                item_id = article_id_for([journal_key, period, title, authors, source_url(row)])
                article_rows.append(
                    [
                        item_id,
                        metadata["display_name"],
                        journal_key,
                        metadata["discipline"],
                        metadata["journal_type"],
                        metadata["quartile"],
                        year,
                        period_info["month"],
                        period_info["issue"],
                        period,
                        period_info["period_label"],
                        publication_date(row, year),
                        title,
                        authors,
                        abstract,
                        keywords,
                        ai_summary,
                        basis,
                        source_url(row),
                        source_file,
                    ]
                )
                article_count += 1
                issue_article_count += 1

            issue_rows.append(
                {
                    "journal": metadata["display_name"],
                    "journal_key": journal_key,
                    "discipline": metadata["discipline"],
                    "journal_type": metadata["journal_type"],
                    "quartile": metadata["quartile"],
                    "year": year,
                    "month": period_info["month"],
                    "issue": period_info["issue"],
                    "period": period,
                    "period_label": period_info["period_label"],
                    "frequency": frequency,
                    "article_count": issue_article_count,
                }
            )

        year_start = min(years) if years else None
        year_end = max(years) if years else None
        journal_rows.append(
            {
                "journal_id": journal_id,
                "journal_key": journal_key,
                "journal_name": metadata["journal_name"],
                "display_name": metadata["display_name"],
                "discipline": metadata["discipline"],
                "journal_type": metadata["journal_type"],
                "language": metadata["language"],
                "quartile": metadata["quartile"],
                "notes": metadata["notes"],
                "frequency": frequency,
                "year_start": year_start,
                "year_end": year_end,
                "year_range": f"{year_start}-{year_end}" if year_start and year_end else "暂无",
                "article_count": article_count,
                "issue_count": issue_count,
            }
        )

    journal_rows.sort(key=lambda item: (item["discipline"], item["journal_type"], item["display_name"]))
    issue_rows.sort(
        key=lambda item: (
            item["journal_key"],
            -(item["year"] or 0),
            -(item["month"] or item["issue"] or 0),
        )
    )
    article_rows.sort(
        key=lambda row: (
            str(row[2]),
            -(int(row[6] or 0)),
            -(int(row[7] or row[8] or 0)),
            str(row[11]),
            str(row[12]),
        ),
        reverse=False,
    )

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    article_dictionaries, encoded_article_rows = dictionary_encode_rows(article_rows)
    compact_json(
        output_dir / "journals.json",
        {
            "generated_at": generated_at,
            "journals": journal_rows,
        },
    )
    compact_json(
        output_dir / "issues.json",
        {
            "generated_at": generated_at,
            "issues": issue_rows,
        },
    )
    compact_json(
        output_dir / "articles.json",
        {
            "generated_at": generated_at,
            "schema": ARTICLE_SCHEMA,
            "dictionaries": article_dictionaries,
            "articles": encoded_article_rows,
        },
    )
    compact_json(
        output_dir / "stats.json",
        {
            "generated_at": generated_at,
            "journal_count": len(journal_rows),
            "issue_count": len(issue_rows),
            "article_count": len(article_rows),
        },
    )
    return {
        "site_data_dir": str(output_dir.relative_to(ROOT)),
        "journal_count": len(journal_rows),
        "issue_count": len(issue_rows),
        "article_count": len(article_rows),
    }


def dictionary_encode_rows(rows: list[list[Any]]) -> tuple[dict[str, list[Any]], list[list[Any]]]:
    dictionaries: dict[str, list[Any]] = {field: [] for field in DICTIONARY_FIELDS}
    lookups: dict[str, dict[str, int]] = {field: {} for field in DICTIONARY_FIELDS}
    encoded_rows: list[list[Any]] = []
    for row in rows:
        encoded_row: list[Any] = []
        for field, value in zip(ARTICLE_SCHEMA, row):
            if field not in DICTIONARY_FIELDS:
                encoded_row.append(value)
                continue
            key = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            lookup = lookups[field]
            if key not in lookup:
                lookup[key] = len(dictionaries[field])
                dictionaries[field].append(value)
            encoded_row.append(lookup[key])
        encoded_rows.append(encoded_row)
    return dictionaries, encoded_rows
