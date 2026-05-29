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

- `LLM_API_KEY`

The workflow already defaults to:

- `LLM_BASE_URL`: `https://models.sjtu.edu.cn/api/v1`
- `LLM_MODEL`: `deepseek-chat`

So only `LLM_API_KEY` is required for the first AI summary setup.

The current adapter expects an OpenAI-compatible `/chat/completions` endpoint. If the school API has a different format, update `journal_tracker/tracker.py`.
