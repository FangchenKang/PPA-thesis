# Operations

## Manual Run In GitHub

1. Open the repository on GitHub.
2. Go to `Actions`.
3. Select `Journal digest`.
4. Click `Run workflow`.

The first run creates a baseline. Later runs report only newly discovered records.

## Report Storage

Reports are committed back to the repository by GitHub Actions:

- `reports/daily/YYYY-MM-DD.md`
- `reports/weekly/YYYY-MM-DD.md`
- `reports/monthly/YYYY-MM-DD.md`
- `reports/quarterly/YYYY-MM-DD.md`

Raw daily item snapshots are stored in:

- `data/items/YYYY-MM-DD.json`

The deduplication state is stored in:

- `data/state.json`

## Common Adjustments

Change recipient:

Edit `MAIL_TO` in `.github/workflows/journal-digest.yml`.

Change send time:

Edit the cron line in `.github/workflows/journal-digest.yml`. GitHub cron uses UTC.

Change per-journal maximum:

Edit `PER_JOURNAL_LIMIT` in `.github/workflows/journal-digest.yml`.

Pause a journal:

Set `enabled` to `false` for that journal in `data/journals.json`.

Add a direct feed:

Add a `feed_url` in `data/journals.json` or `config/feed_overrides.json`.
