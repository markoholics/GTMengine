# Secrets

Secrets live only in environment variables or a secrets manager. `.env` is git ignored; `.env.example` lists every name.

| Where | What | How |
|---|---|---|
| Local | `.env` | `cp .env.example .env`, fill in what the current phase needs |
| GitHub Actions | repository secrets | Settings, Secrets and variables, Actions. Names match `.env.example` |
| Make | connection credentials and data store | Never paste keys into scenario modules; use Make connections. Webhook secrets go in scenario variables |
| Supabase | `SUPABASE_SERVICE_ROLE_KEY` | Server side only. It bypasses RLS. Never in a browser page, scenario blueprint or log |

Rules:
- The approval page uses the anon key plus a user JWT carrying `client_id`. It never sees the service role key.
- Rotate any key that has appeared in a log, a screenshot or a chat.
- Blueprints exported from Make are scrubbed of connection ids before commit (Phase 6 adds a check).
- `DATABASE_URL` for production migrations is used from a trusted machine, not from CI, until you decide otherwise.
