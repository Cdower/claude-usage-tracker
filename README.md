# Claude Usage Tracker

A local dashboard for tracking your Claude Code and Claude.ai usage across multiple machines — with plan vs. API cost comparison, real-time usage bars, historical analytics, and multi-device sync.

Built for individual **Pro** and **Max** plan subscribers who want visibility into what they're actually consuming and whether their subscription is paying off compared to pay-per-token API pricing.

![Dashboard](docs/dashboard.png)

---

## Features

- **Real-time plan usage bars** — current session %, 7-day %, and extra usage pulled directly from claude.ai's API (no scraping, no login prompts)
- **Local JSONL analytics** — token counts, model breakdown, project breakdown, and estimated API cost equivalent parsed from Claude Code's local logs
- **Plan vs. API cost comparison** — see whether your $20 Pro or $100/$200 Max plan is saving you money vs. paying per token
- **Historical charts** — 30-day daily token and cost trends, model mix over time, top projects by token spend
- **Multi-machine sync** — a lightweight push agent (zero dependencies, stdlib only) syncs Claude Code logs from other machines to the hub
- **No cloud account required** — runs entirely on your local network; web usage auth is borrowed from your existing Firefox session

---

## How it works

### Data sources

| Source | What it provides | How accessed |
|--------|-----------------|--------------|
| `~/.claude/projects/**/*.jsonl` | Per-turn token counts, models, projects, timestamps | Read locally on each machine |
| `claude.ai/api/organizations/{uuid}/usage` | Plan utilization % for current session, 7-day window, extra usage credits | HTTP request using Firefox session cookies |
| Remote agents | JSONL data from other machines (laptop, etc.) | Authenticated HTTP push to the hub |

### Architecture

```
┌─────────────────────────────────┐      ┌──────────────────────┐
│  Desktop (hub)                  │      │  Laptop (agent)      │
│                                 │      │                      │
│  Flask app  ←── sync_agent.py  │◄─────│  sync_agent.py       │
│       │                         │      │  (reads local JSONL) │
│  SQLite DB                      │      └──────────────────────┘
│       │                         │
│  Scanner (local JSONL)          │      ┌──────────────────────┐
│  Scraper (claude.ai API via     │      │  claude.ai API       │
│           Firefox cookies)      │─────►│  /api/organizations/ │
└─────────────────────────────────┘      │  {uuid}/usage        │
                                         └──────────────────────┘
```

---

## Requirements

- macOS (Firefox cookie reading is macOS-specific; Linux support is straightforward to add)
- Python 3.11
- Firefox with an active claude.ai session (for web usage bars)
- Claude Code installed and used at least once (for JSONL logs)

---

## Installation

### Hub machine (desktop)

```bash
git clone https://github.com/jimdawdy-hub/claude-usage-tracker.git
cd claude-usage-tracker

# Create virtual environment with Python 3.11
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install Firefox for Playwright (used only if you add browser-based features)
python -m playwright install firefox

# Generate a shared secret for remote agents
python3 -c "import secrets; print('REMOTE_TOKEN=' + secrets.token_hex(32))" >> .env

# Start the hub
./run.sh
```

Open **http://localhost:5000** in your browser.

Click **Sync Now** to pull your local JSONL data and fetch live usage from claude.ai (requires Firefox with an active claude.ai session).

### Remote machine (laptop, etc.)

No virtual environment or pip installs needed — `sync_agent.py` uses only the Python standard library.

```bash
# Copy just the agent script to the remote machine
scp sync_agent.py user@laptop:~/claude-usage-tracker/

# Create the agent config on the remote machine
mkdir -p ~/claude-usage-tracker
cat > ~/claude-usage-tracker/agent_config.json << 'EOF'
{
  "hub_url": "http://YOUR_DESKTOP_IP:5000",
  "token": "paste REMOTE_TOKEN from hub .env here",
  "machine_name": "laptop"
}
EOF

# Test it
python3 ~/claude-usage-tracker/sync_agent.py

# Add to crontab to run every 15 minutes
(crontab -l 2>/dev/null; echo "*/15 * * * * /usr/bin/python3 ~/claude-usage-tracker/sync_agent.py >> ~/claude-usage-tracker/agent.log 2>&1") | crontab -
```

---

## Configuration

### Hub `.env`

```env
REMOTE_TOKEN=<hex secret shared with all agents>
HOST=0.0.0.0      # bind address (default: 0.0.0.0)
PORT=5000          # port (default: 5000)
```

### Agent `agent_config.json`

```json
{
  "hub_url": "http://192.168.1.10:5000",
  "token": "<same REMOTE_TOKEN from hub>",
  "machine_name": "laptop"
}
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/auth/status` | Whether Firefox has a valid claude.ai session |
| `POST` | `/api/sync` | Scan local JSONL + fetch web usage |
| `GET` | `/api/stats/summary` | All-time and this-month token/cost totals |
| `GET` | `/api/stats/daily` | Daily breakdown (last 30 days) |
| `GET` | `/api/stats/models` | Token and cost breakdown by model |
| `GET` | `/api/stats/projects` | Top 20 projects by token spend |
| `GET` | `/api/web-usage/latest` | Most recent plan usage snapshot |
| `GET` | `/api/web-usage/history` | Last 60 usage snapshots |
| `GET` | `/api/plan-comparison` | Subscription vs. API cost comparison |
| `GET` | `/api/machines` | All machines that have pushed data |
| `POST` | `/api/remote/push` | Receive data from a remote agent (requires Bearer token) |

---

## Plan comparison

The dashboard compares your estimated API cost (computed from local token counts and published per-token pricing) against the flat subscription cost:

| Plan | Monthly cost |
|------|-------------|
| Pro | $20 |
| Max 5× | $100 |
| Max 20× | $200 |

If your estimated API-equivalent cost this month exceeds the plan price, your subscription is saving you money. If it's below, you might be better off on a pay-per-token API key.

> **Note:** The API cost estimate is computed from local JSONL logs using published Anthropic pricing as of May 2026. Cache-read tokens are priced at 10% of input rate; cache-creation tokens at 125% of input rate. Actual subscription usage limits are quota-based (not token-based), so this is a directional comparison, not an exact billing figure.

---

## Multi-machine sync

The hub exposes a `/api/remote/push` endpoint protected by a Bearer token. The `sync_agent.py` script on each remote machine:

1. Scans `~/.claude/projects/**/*.jsonl` for new or updated files
2. Parses sessions and turns incrementally (only new lines since last run)
3. POSTs the data to the hub
4. Saves local state so it never re-sends the same data

If the push fails (hub unreachable), the agent does not advance its state pointer — data will be retried on the next cron run.

Sessions in the database are tagged with the originating machine name, visible in the `/api/machines` endpoint.

---

## Security

- The push endpoint requires a `Bearer` token set via `REMOTE_TOKEN` in the hub's `.env`
- Web usage auth uses read-only access to Firefox's local cookie database — no credentials are stored or transmitted by this app
- The hub binds to `0.0.0.0` by default so remote agents can reach it on the local network; set `HOST=127.0.0.1` in `.env` to restrict to localhost if you only use it locally

---

## Project structure

```
claude-usage-tracker/
├── app.py              # Flask hub — API endpoints
├── scanner.py          # JSONL log parser (incremental, deduped)
├── scraper.py          # claude.ai usage API client (Firefox cookie auth)
├── sync_agent.py       # Standalone push agent for remote machines
├── requirements.txt    # Hub dependencies (Flask, curl_cffi)
├── run.sh              # Start the hub
├── agent_config.example.json
├── .env.example
├── templates/
│   └── index.html      # Dashboard HTML
└── static/
    ├── style.css
    └── app.js
```

---

## Credits and prior art

This project builds on ideas and code from two excellent open-source projects:

### [phuryn/claude-usage](https://github.com/phuryn/claude-usage)

The JSONL parsing logic in `scanner.py` is heavily adapted from this project. phuryn's work was the first to identify that Claude Code writes detailed per-turn usage logs to `~/.claude/projects/` and to build a clean, dependency-free scanner on top of them. Key contributions borrowed:

- Incremental file scanning with mtime tracking
- Streaming event deduplication by `message.id`
- Session aggregation and model priority logic
- The `processed_files` table design

### [IgniteStudiosLtd/claude-usage-tool](https://github.com/IgniteStudiosLtd/claude-usage-tool)

An Electron/React macOS menu bar app that scrapes `claude.ai/settings/usage` for real plan utilization data. This project showed that the claude.ai settings page exposes usable plan usage data and informed the approach of using authenticated browser sessions to access it. The JavaScript extraction patterns in this project's scraper are inspired by their work.

This project diverges from both by:
- Discovering and using claude.ai's internal `/api/organizations/{uuid}/usage` JSON API instead of HTML scraping
- Using `curl_cffi` with Firefox TLS fingerprint impersonation + direct Firefox cookie extraction to bypass Cloudflare without any login UI
- Adding multi-machine sync via a stdlib-only push agent
- Combining both local JSONL analytics and web usage data in a single persistent dashboard

---

## License

MIT
