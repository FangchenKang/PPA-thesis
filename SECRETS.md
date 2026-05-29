# Secrets Checklist

Do not commit real passwords or API keys to this repository. Add them in GitHub:

`Settings -> Secrets and variables -> Actions`

## Minimum Setup For Email

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`

The recipient is already set in the workflow:

`kangfangchen@sjtu.edu.cn`

## Optional Setup For AI Summaries

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

The current adapter expects an OpenAI-compatible `/chat/completions` endpoint. If the school API has a different format, update `journal_tracker/tracker.py`.
