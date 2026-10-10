# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is a **public repository**. Never commit `.env`, `agent_config.json`, `usage.db`, `.agent_state.json`, real tokens, cookie values, or real org UUIDs/IPs.

## Commands

```bash
# Setup (hub) — Python 3.11
python3.11 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

./run.sh                      # start hub: activates venv, exports .env, runs app.py (http://localhost:5000)
python scanner.py             # one-off scan of ~/.claude/projects JSONL into usage.db, prints stats
python scraper.py             # fetch claude.ai usage via browser cookies, save snapshot, print JSON
python3 sync_agent.py         # remote agent push (needs agent_config.json next to it)
```

There is no test suite, linter config, or build step. Frontend is plain JS/CSS served by Flask (no bundler).

Env vars read by the hub: `REMOTE_TOKEN`, `HOST` (default `127.0.0.1`), `PORT` (default 5000), `FLASK_DEBUG`. Note `.env.example` contains stale unused keys (`ANTHROPIC_ADMIN_API_KEY`, `CLAUDE_PLAN`, `MONTHLY_TOKEN_LIMIT`), and the README still says `HOST` defaults to `0.0.0.0` — the code default is `127.0.0.1`.

## Architecture

Hub-and-agent design around a single SQLite file, `usage.db` (repo root, git-ignored).

- **`scanner.py`** — owns the DB schema (`init_db`) and local ingestion. Parses `~/.claude/projects/**/*.jsonl`, dedupes streaming events by `message.id`, aggregates turns into sessions, and tracks per-file mtime/line count in `processed_files` to skip unchanged files. Also owns `MODEL_PRICING` and `_estimate_cost` (cache read = 0.1× input rate, cache write = 1.25× input rate).
- **`scraper.py`** — pulls live plan utilization from claude.ai's internal `/api/organizations/{uuid}/usage` using session cookies copied from a local browser (Firefox directly via a temp copy of `cookies.sqlite`; Chromium-family via `browser_cookie3`) and `curl_cffi` with browser TLS impersonation to get past Cloudflare. `detect_plan` maps `extra_usage.monthly_limit` (cents) to Pro / Max 5× / Max 20×. Snapshots go to `web_usage_snapshots`.
- **`app.py`** — Flask API + dashboard. `@app.before_request` runs `init_db` on every request, so schema migrations happen lazily. `POST /api/sync` runs `scan()` then `collect()`. `POST /api/remote/push` (Bearer `REMOTE_TOKEN`, constant-time compare, 10 MB / record-count caps) ingests agent data tagged with a `machine` name. Settings (plan override, billing start date, `pricing_version`) live in a key/value `settings` table. Billing-period logic is in `/api/stats/billing-periods` (design in `docs/superpowers/specs/`).
- **`sync_agent.py`** — **stdlib-only by design** (copied alone to remote machines; do not add third-party imports). Re-implements JSONL parsing/aggregation/pricing independently of `scanner.py`, keeps incremental state in `.agent_state.json`, and only advances that state after a successful push.
- **Frontend** — `templates/index.html` + `static/app.js` fetch the `/api/*` endpoints and render with charts; `static/style.css`.

### Things that must stay in sync

- **Pricing** is duplicated in `scanner.py` and `sync_agent.py`. `MODEL_PRICING` uses substring matching in dict order, so more-specific keys (e.g. `claude-opus-4-7`) must come before generic ones (`claude-opus-4`). The agent's table is currently stale (old Opus/Haiku rates); remote-pushed costs are trusted as sent until the next repricing.
- **Repricing**: after changing `MODEL_PRICING`, bump `PRICING_VERSION` inside `recalculate_all_costs` (`scanner.py`); on next startup all turn/session costs are recomputed from stored token counts.
- **Schema changes**: add columns via the try-`SELECT`/`except OperationalError`/`ALTER TABLE` pattern in `init_db`; remote push SQL in `app.py` must match.
- The README's pricing table, API endpoint table, and `CHANGELOG.md` (Keep a Changelog format) are user-facing docs updated alongside code changes.

### Known issue

On a brand-new `usage.db`, `init_db` → `recalculate_all_costs` queries the `settings` table before anything creates it (`app.py` creates it only after `init_db` returns), raising `sqlite3.OperationalError: no such table: settings`.
