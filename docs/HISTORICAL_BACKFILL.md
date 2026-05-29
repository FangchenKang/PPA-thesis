# Historical Backfill

The historical backfill workflow collects public bibliographic metadata for the configured journals and stores it under `data/history/`.

It does not use the school LLM API. It uses the OpenAlex public metadata API to find a journal source and then page through public article records for that source.

## Storage Layout

- `data/history/index.json`: run summary and diagnostics.
- `data/history/journals/0001.jsonl`: one JSON record per article for journal ID 1.
- `data/history/journals/0002.jsonl`: one JSON record per article for journal ID 2.

Each article record stores compact metadata such as title, DOI, publication date, authors, OpenAlex ID, article URL, citation count, and open access status. Abstracts are off by default to keep the repository smaller.

## How To Run

In GitHub:

1. Open `Actions`.
2. Select `Historical backfill`.
3. Click `Run workflow`.
4. Choose a journal ID range.

Recommended first run:

- `start_id`: `1`
- `end_id`: `20`
- `max_pages_per_journal`: `0`
- `include_abstracts`: `false`

After confirming the result size, continue with more ID ranges.

The backfill is resumable. If `data/history/journals/0002.jsonl` already exists, rerunning a range that includes journal ID 2 will skip that file by default. Use `--no-resume` locally only when you intentionally want to refetch an existing journal.

## Full Backfill

To try the full list in one run:

- `start_id`: `1`
- `end_id`: `192`
- `max_pages_per_journal`: `0`

This asks the script to fetch every available cursor page for every matched journal. Depending on metadata volume and GitHub Actions runtime limits, it may be better to split into batches:

- `1-33`
- `34-66`
- `67-108`
- `109-148`
- `149-192`

## Limits And Ethics

The backfill collects public metadata only. It does not log in to CNKI, school databases, or publisher portals, and it does not download paywalled full text.

Some Chinese journals may have incomplete coverage in OpenAlex. Those will appear in `data/history/index.json` with lower counts or source-match warnings.

For local controlled batches:

```bash
python -m journal_tracker backfill --start-id 31 --end-id 33
```
