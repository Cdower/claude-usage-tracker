# Claude Usage Tracker

A local dashboard for tracking your Claude Code and Claude.ai usage across multiple machines — with plan vs. API cost comparison, real-time usage bars, historical analytics, and multi-device sync.

Built for individual **Pro** and **Max** plan subscribers who want visibility into what they're actually consuming and whether their subscription is paying off compared to pay-per-token API pricing.

> **Accuracy improves over time.** Cost and token estimates are built from your local Claude Code logs. On first run only your current session is visible; the picture fills in as you keep using Claude Code. Plan usage bars are always live from claude.ai.

---

## Features

- **Real-time plan usage bars** — current session %, 7-day %, and extra usage credits pulled directly from claude.ai's internal API; no scraping, no login prompts
- **Auto plan detection** — Pro, Max (5×), and Max (20×) are inferred from your account data; a selector is shown if detection isn't possible
- **Local JSONL analytics** — token counts, model breakdown, project breakdown, and estimated API cost equivalent parsed from Claude Code's local logs
- **30-day rolling cost projection** — answers "at my current pace, what would I pay on the API over the next 30 days?" rather than extrapolating a partial calendar month; idle days are included so the rate is realistic
- **Plan vs. API cost comparison** — shows projected savings (or deficit) against Pro, Max 5×, and Max 20× simultaneously
- **Usage limits table** — all quota windows (current session, all-models 7-day, extra usage) with % used, colour-coded bars, and reset countdowns
- **Historical charts** — 30-day daily token and cost trends, model mix over time, top projects by token spend
- **Multi-machine sync** — a lightweight push agent (zero external dependencies, stdlib only) can sync Claude Code logs from other machines to the hub; requires manual setup on each remote machine (see [Multi-machine sync](#multi-machine-sync))
- **Multi-browser support** — reads session cookies from Firefox, Chrome, Brave, Chromium, or Edge; no login window required
- **Cross-platform** — macOS, Linux (including snap/Flatpak Firefox), and Windows

---

## How it works

### Data sources

| Source | What it provides | How accessed |
|--------|-----------------|--------------|
| `~/.claude/projects/**/*.jsonl` | Per-turn token counts, models, projects, timestamps | Read locally on each machine |
| `claude.ai/api/organizations/{uuid}/usage` | Plan utilization % for all quota windows and extra usage credits | Authenticated HTTP using your browser's session cookies |
| Remote agents | JSONL data from other machines (laptop, etc.) | Token-authenticated HTTP push to the hub |

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
│           browser cookies)      │─────►│  /api/organizations/ │
└─────────────────────────────────┘      │  {uuid}/usage        │
                                         └──────────────────────┘
```

### Plan detection

The app infers your plan from the `extra_usage.monthly_limit` field returned by the claude.ai usage API (denominated in cents):

| Limit value | Dollars | Detected plan |
|-------------|---------|--------------|
| 2000 | $20 | Pro |
| 10000 | $100 | Max (5×) |
| 20000 | $200 | Max (20×) |

If the value doesn't match a known plan, a selector is shown in the dashboard header. Your choice is persisted to the local database.

### 30-day rolling projection

Rather than extrapolating a partial calendar month (which produces misleading numbers for anyone who doesn't use Claude daily from the 1st), the projection:

1. Sums API cost equivalent across the last 14 days (idle days count as $0)
2. Divides by 14 to get a realistic average daily rate
3. Multiplies by 30 to produce a 30-day forward estimate
4. Compares that figure against each plan's monthly price

This answers: *"At my current pace, would the API be cheaper than my subscription over the next 30 days?"*

---

## Requirements

- macOS, Linux, or Windows
- Python 3.11
- Firefox, Chrome, Brave, Chromium, or Edge with an active claude.ai session (for web usage bars)
- Claude Code installed and used at least once (for JSONL logs)

---

## Installation

### Hub machine (desktop)

```bash
git clone https://github.com/Cdower/claude-usage-tracker.git
cd claude-usage-tracker

# Dependencies are pinned in uv.lock with hashes. Install uv first:
#   https://docs.astral.sh/uv/getting-started/installation/
cp .env.example .env

# Generate a shared secret for remote agents and paste it into REMOTE_TOKEN in .env
python3 -c "import secrets; print(secrets.token_hex(32))"

# Start the hub (uv creates .venv from uv.lock on first run)
./run.sh
```

Open **http://localhost:5000** in your browser (**https://** if TLS is enabled — see [TLS](#tls)).

Without uv, `pip install --require-hashes -r requirements.txt` installs the same pinned, hash-verified set. `requirements.txt` is generated with `uv export --format requirements-txt --no-emit-project`; regenerate it whenever `uv.lock` changes.

Click **Sync Now** to pull your local JSONL data and fetch live usage from claude.ai. The app automatically finds your browser session — no login step required as long as you're already signed into claude.ai in any supported browser.

### Remote machine (laptop, etc.)

No virtual environment or pip installs needed — `sync_agent.py` uses only the Python standard library.

```bash
# Copy the agent script (and, for a self-signed hub, its CA certificate)
ssh user@laptop mkdir -p ~/claude-usage-tracker
scp sync_agent.py certs/ca.pem user@laptop:~/claude-usage-tracker/

# Create the agent config on the remote machine
cat > ~/claude-usage-tracker/agent_config.json << 'EOF'
{
  "hub_url": "https://YOUR_HUB_HOST:5000",
  "token": "paste REMOTE_TOKEN from hub .env here",
  "machine_name": "laptop",
  "ca_cert": "ca.pem"
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
HOST=0.0.0.0       # bind address (default: 127.0.0.1 — use 0.0.0.0 for multi-machine sync)
PORT=5000          # port (default: 5000)
FLASK_DEBUG=0      # set to 1 during development only

TLS_MODE=off       # off | self-signed | custom (see TLS below)
TLS_HOSTNAMES=     # self-signed: extra names/IPs agents use to reach the hub
TLS_CERT_DIR=      # self-signed: where certificates are kept (default ./certs)
TLS_CERT_FILE=     # custom: PEM certificate chain
TLS_KEY_FILE=      # custom: PEM private key
```

### TLS

With `TLS_MODE` set to `self-signed` or `custom` the hub serves **HTTPS only** on `PORT`; plain HTTP is not served. The default, `TLS_MODE=off`, keeps plain HTTP. A TLS misconfiguration stops the hub at startup; it never silently falls back to HTTP.

**Self-signed (`TLS_MODE=self-signed`)** — on first start the hub creates a private CA and a server certificate signed by it in `certs/`:

| File | Purpose |
|------|---------|
| `certs/ca.pem` | CA certificate — copy to each sync agent (`ca_cert`) and optionally trust it in your browser |
| `certs/ca-key.pem` | CA private key (mode 0600) — never copy this off the hub |
| `certs/server.pem`, `certs/server-key.pem` | Server certificate and key (valid 397 days) |

The server certificate covers `localhost`, `127.0.0.1`, `::1`, the hub's hostname, and everything in `TLS_HOSTNAMES` — set that to the name or IP your agents put in `hub_url`, e.g. `TLS_HOSTNAMES=hub.lan,192.168.1.10`. The CA is kept across restarts; the server certificate is re-issued automatically when it is within 30 days of expiry or `TLS_HOSTNAMES` changes, so agents never need a new `ca.pem`. The hub prints the CA's SHA-256 fingerprint at startup so you can check the copy on each agent (`openssl x509 -in ca.pem -noout -fingerprint -sha256`). Run `uv run python hub_tls.py` to create or refresh the certificates without starting the hub.

Anyone holding `ca-key.pem` can issue certificates your agents (and your browser, if you trusted the CA) will accept, so keep it on the hub only. Delete `certs/` to start over with a new CA.

**Externally managed certificate (`TLS_MODE=custom`)** — point `TLS_CERT_FILE` (full chain, PEM) and `TLS_KEY_FILE` (PEM) at a certificate you manage yourself, e.g. from Let's Encrypt or an internal CA. Leave `ca_cert` out of the agent config when the certificate chains to a CA the agent machine already trusts; otherwise set `ca_cert` to your CA certificate. Restart the hub after renewing the certificate.

Agents always verify the hub's certificate and hostname. The agent prints a warning when `hub_url` uses plain `http://` to anything other than localhost.

### Agent `agent_config.json`

```json
{
  "hub_url": "https://192.168.1.10:5000",
  "token": "<same REMOTE_TOKEN from hub>",
  "machine_name": "laptop",
  "ca_cert": "ca.pem"
}
```

`ca_cert` is optional — the path (absolute, or relative to `sync_agent.py`) to the hub's CA certificate for a self-signed hub. Omit it for a publicly trusted certificate or a plain-HTTP hub.

---

## Dashboard overview

| Section | What it shows |
|---------|--------------|
| **Plan badge** (header) | Detected or selected plan; click to change |
| **Browser status** (header) | Which browser provided the session cookie |
| **Plan usage bars** | Live utilization % per quota window with reset countdowns |
| **This Month** cards | Tokens used, estimated API cost, session count |
| **Next 30 Days** card | Projected API cost at current rate vs. your plan |
| **Usage Limits table** | All quota windows with colour-coded progress bars |
| **Plan Comparison table** | Pro / Max 5× / Max 20× savings vs. projected 30-day API cost |
| **Daily charts** | Token and API cost equivalent trends (last 30 days) |
| **Model breakdown** | Donut chart of token use by model |
| **Top projects** | Horizontal bar chart of token use by project directory |

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/auth/status` | Auth status and which browser provided the session |
| `POST` | `/api/sync` | Scan local JSONL + fetch live usage from claude.ai |
| `GET` | `/api/plan` | Detected or selected plan with per-plan comparison data |
| `POST` | `/api/plan` | Set plan manually (persisted to DB) |
| `GET` | `/api/stats/summary` | All-time totals, this-month totals, 30-day projection |
| `GET` | `/api/stats/daily` | Daily token + cost breakdown (last 30 days) |
| `GET` | `/api/stats/models` | Token and cost breakdown by model |
| `GET` | `/api/stats/projects` | Top 20 projects by token spend |
| `GET` | `/api/web-usage/latest` | Most recent plan usage snapshot (bars, reset times) |
| `GET` | `/api/web-usage/history` | Last 60 usage snapshots |
| `GET` | `/api/plan-comparison` | This-month API equivalent vs. each plan |
| `GET` | `/api/machines` | All machines that have pushed data, with last-seen time |
| `POST` | `/api/remote/push` | Receive JSONL data from a remote agent (Bearer token required) |

---

## Plan comparison and cost methodology

Token pricing used for API cost estimates (as of May 2026, source: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Model | Input (per M tokens) | Cache write (per M tokens) | Cache read (per M tokens) | Output (per M tokens) |
|-------|---------------------|---------------------------|--------------------------|----------------------|
| Claude Opus 4.5 / 4.6 / 4.7 | $5.00 | $6.25 | $0.50 | $25.00 |
| Claude Opus 4.1 / 4 (deprecated) | $15.00 | $18.75 | $1.50 | $75.00 |
| Claude Sonnet 4.x | $3.00 | $3.75 | $0.30 | $15.00 |
| Claude Haiku 4.5 | $1.00 | $1.25 | $0.10 | $5.00 |
| Claude Haiku 3.5 (retired) | $0.80 | $1.00 | $0.08 | $4.00 |

### About cache tokens

Heavy Claude Code usage produces very large raw token counts because **prompt caching re-reads the entire conversation context on every turn**. A session with a 200K-token context and 500 turns accumulates 100M cache-read tokens — these are real API charges, but at 10% of the standard input rate. The dashboard's cost estimate correctly applies the 10% multiplier; the raw token count is accurate but can look alarming without context.

### Caveat: plan vs. API comparison

The "Est. API Cost" figure answers the hypothetical: *"What would this usage have cost at pay-per-token API rates?"* It is a directional comparison, not a billing statement. Two important caveats:

1. **Subscription plans are quota-based, not token-based.** Anthropic controls your actual limits and billing. Usage limits for Claude Code subscribers were doubled on May 6, 2026 — see [this announcement](https://www.anthropic.com/news/higher-limits-spacex).
2. **Caching behavior may differ between plan and API use.** Claude Code on a subscription plan may cache more aggressively than direct API calls would. The API cost estimate assumes you'd replicate the exact same usage pattern on the API, which may not reflect what you'd actually do or pay.

---

## Multi-machine sync

> **This does not happen automatically.** The hub only sees data from machines where you have manually installed and run `sync_agent.py`. Until you do that setup, the dashboard shows only the machine running the hub.

The hub exposes `/api/remote/push` protected by a Bearer token. The `sync_agent.py` on each remote machine:

1. Scans `~/.claude/projects/**/*.jsonl` for new or updated files
2. Parses sessions and turns incrementally (only new lines since last run)
3. POSTs the data to the hub
4. Saves local state so it never re-sends the same data

If the push fails (hub unreachable), the agent does not advance its state pointer — data will be retried on the next cron run.

Sessions are tagged with the originating machine name, visible in the `/api/machines` endpoint and the dashboard's machine list.

**Supported log paths by platform:**

| Platform | Path |
|----------|------|
| macOS | `~/.claude/projects/` and Xcode assistant path |
| Linux | `~/.claude/projects/` |
| Windows | `%APPDATA%\Claude\projects\` |

---

## Security

This project is designed for personal, local-network use. The following hardening measures are in place:

**Authentication & transport**
- The remote push endpoint requires a cryptographically random Bearer token (`REMOTE_TOKEN`). Generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"` and keep it out of version control.
- Token verification uses a constant-time comparison to resist timing-based inference.
- The hub binds to `127.0.0.1` by default. Set `HOST=0.0.0.0` in `.env` so remote agents can reach it.
- Traffic between agents and the hub can be encrypted with TLS (`TLS_MODE=self-signed` or `custom`; see [TLS](#tls)). Agents verify the hub's certificate and hostname. Enable TLS whenever agents reach the hub over a network, otherwise the token and usage data travel in clear text.

**Browser cookie access**
- Cookie reading is read-only and entirely local — no credentials are stored by this app or transmitted anywhere beyond claude.ai itself.
- Temporary copies of the browser cookie database are made in a private (0700) temporary directory that is always removed, including when reading fails.

**Supply chain**
- Python dependencies are pinned to exact versions in `pyproject.toml` and locked with hashes in `uv.lock`; `run.sh` uses `uv run --locked`, which refuses to run if the lock is out of date. `requirements.txt` carries the same hashes for `pip install --require-hashes`.
- Chart.js is loaded from jsDelivr with a Subresource Integrity (SRI) hash, so the browser refuses to run it if the file served differs from the published `chart.js@4.4.0` release.

**Input handling**
- Incoming push payloads are bounded by size and record count to prevent resource exhaustion.
- The debug server is off by default; set `FLASK_DEBUG=1` to enable it during development only.
- API responses are restricted to same-origin requests.

**What this tool is not**
This is a personal dashboard, not a hardened public web service. Do not expose port 5000 to the internet. TLS encrypts traffic but the dashboard itself has no login: anyone who can reach the port can view it. If you need access from outside your local network, put it behind a reverse proxy with authentication (e.g. nginx + HTTP basic auth) or a VPN.

---

## Project structure

```
claude-usage-tracker/
├── app.py                     # Flask hub — API endpoints
├── scanner.py                 # JSONL log parser (incremental, deduped)
├── scraper.py                 # claude.ai usage API client (browser cookie auth)
├── hub_tls.py                 # TLS setup: self-signed CA / external certificate
├── sync_agent.py              # Standalone push agent for remote machines
├── pyproject.toml             # Hub dependencies (exact pins)
├── uv.lock                    # Locked dependency tree with hashes
├── requirements.txt           # Hash-pinned export of uv.lock for pip
├── run.sh                     # Start the hub
├── agent_config.example.json
├── .env.example
├── CHANGELOG.md
├── CONTRIBUTORS.md
├── templates/
│   └── index.html             # Dashboard HTML
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
- Using `curl_cffi` with Firefox TLS fingerprint impersonation + direct browser cookie extraction to bypass Cloudflare without any login UI
- Supporting Firefox, Chrome, Brave, Chromium, and Edge on macOS, Linux, and Windows
- Adding multi-machine sync via a stdlib-only push agent
- Combining local JSONL analytics, web usage data, plan detection, and rolling 30-day cost projection in a single persistent dashboard

---

## License

MIT
