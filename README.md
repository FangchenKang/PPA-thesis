# PPA Thesis Journal Tracker

This repository tracks new publications from the political science and public administration journal list used for the PPA thesis workflow. It runs every morning at 08:00 Beijing time through GitHub Actions, stores the detected items in the repository, and sends digest emails to `kangfangchen@sjtu.edu.cn`.

## What It Does

- Reads the journal source from `data/journals.json`.
- Tries each journal's configured RSS feed first.
- If no RSS feed is configured, discovers RSS/Atom feeds from the journal homepage.
- If no feed is available, falls back to scanning the public journal homepage for article links.
- Deduplicates items with `data/state.json`.
- Writes daily item snapshots to `data/items/YYYY-MM-DD.json`.
- Writes Markdown reports to:
  - `reports/daily/`
  - `reports/weekly/`
  - `reports/monthly/`
  - `reports/quarterly/`
- Sends email when SMTP secrets are configured.
- Uses an LLM summary only when `LLM_API_KEY` is configured. Otherwise it sends a rule-based summary.
- Can run a separate manual historical backfill that stores public bibliographic metadata in `data/history/`.

The first run initializes a baseline so old articles are not all treated as "new". Later runs only report newly discovered records.

## Schedule

The GitHub Actions workflow is in `.github/workflows/journal-digest.yml`.

```yaml
cron: "0 0 * * *"
```

GitHub cron uses UTC, so this is 08:00 in Asia/Shanghai.

The daily workflow also creates:

- a weekly report on Mondays, covering the previous Monday through Sunday;
- a monthly report on the first day of each month, covering the previous month;
- a quarterly report on January 1, April 1, July 1, and October 1, covering the previous quarter.

## GitHub Secrets

Open the repository in GitHub, then go to:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Required for email:

| Secret | Meaning |
| --- | --- |
| `SMTP_HOST` | SMTP server host, for example `smtp.example.edu.cn` |
| `SMTP_PORT` | Usually `465` for SSL or `587` for STARTTLS |
| `SMTP_USERNAME` | Sender account username |
| `SMTP_PASSWORD` | SMTP password or app password |
| `SMTP_FROM` | Sender email address |

Optional for AI summaries:

| Secret | Meaning |
| --- | --- |
| `LLM_API_KEY` | API key |

The workflow currently defaults to the SJTU API base URL and `deepseek-chat` model:

- `LLM_BASE_URL`: `https://models.sjtu.edu.cn/api/v1`
- `LLM_MODEL`: `deepseek-chat`

If the school API is not OpenAI-compatible, update `summarize_with_llm()` in `journal_tracker/tracker.py` after the API documentation is available.

## Historical Backfill

The historical backfill is intentionally separate from the daily digest because it may be large.

Run it manually from GitHub Actions:

`Actions -> Historical backfill -> Run workflow`

The results are stored in `data/history/`. See `docs/HISTORICAL_BACKFILL.md` for batching suggestions.

## Local Test

Run a no-network test that writes an empty daily report:

```bash
python -m journal_tracker run --date 2026-05-29 --no-fetch
```

Run the real tracker locally:

```bash
python -m journal_tracker run --all-due
```

Run and send email if SMTP environment variables are present:

```bash
python -m journal_tracker run --all-due --send-email
```

## Updating Journal Sources

The original workbook used to build the source is kept at:

`data/source/政治学与公共管理期刊目录_已补官网.xlsx`

The tracker reads:

`data/journals.json`

Each journal entry supports:

- `homepage_url`: public page used for feed discovery or homepage scraping;
- `feed_url`: optional direct RSS/Atom feed;
- `enabled`: set to `false` to skip a journal.

For more reliable tracking, add feed URLs to either `data/journals.json` or `config/feed_overrides.json`:

```json
{
  "1": {
    "feed_url": "https://example.com/journal/rss"
  }
}
```

The key is the journal ID from `data/journals.json`.

## Notes

Some Chinese journals do not expose stable RSS feeds or public article metadata. For those journals, the tracker may only detect updates from the public CNKI/CBPT navigation page. This is enough for a first monitoring workflow, but the highest-quality version will come from adding stable RSS feeds, publisher TOC feeds, or official API endpoints where available.
