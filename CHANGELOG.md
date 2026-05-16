# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.6.0] — 2026-05-15

### Added
- **Billing History section** — per-billing-period breakdown of tokens, estimated API cost, plan cost, and savings; Period 0 covers pre-paid (free plan) usage; empty completed periods suppressed
- **Billing start date banner** — prominent date input below the header prompts users to enter their first billing cycle date, with a hint to check Claude → Settings → Billing; collapses to a compact display once set
- **Methodology & Sources section** — explains cache token accounting, plan vs. API comparison caveats, and links to the Anthropic pricing page and the May 2026 usage limits announcement

### Fixed
- `claude-opus-4-7` was priced at $15/MTok (old Opus 4.1 rate) instead of the correct $5/MTok — a 3x overcharge on all Opus 4.5/4.6/4.7 sessions
- `claude-haiku-4-5` was priced at $0.80/MTok (Haiku 3.5 rate) instead of $1.00/MTok
- Model pricing lookup used substring matching with shorter keys first, causing more-specific entries (e.g. `claude-opus-4-7`) to be shadowed by the generic `claude-opus-4` entry
- `HOST` defaulted to `0.0.0.0` (LAN-visible); changed to `127.0.0.1` for safer out-of-box installs; `HOST=0.0.0.0` documents the opt-in for multi-machine use

### Changed
- All historical session costs automatically recalculated on first startup after the pricing fix (versioned via a `pricing_version` settings key — no manual migration needed)
- Fine-print text (accuracy notice, limits note, methodology, footer disclaimer, section subtitles) increased by 1.5pt and set to full white for legibility
- README pricing table corrected and expanded to include cache write/read columns; multi-machine sync section clarified to state that agent setup is required on each remote machine

---

## [0.5.0] — 2026-05-15

### Security
- CORS restricted to localhost origins only (was previously open to any origin)
- Remote push token comparison made constant-time to resist timing-based inference
- Debug server now off by default; controlled via `FLASK_DEBUG` environment variable
- Temporary browser cookie database copies now created with a secure API (no race condition)
- Incoming push payloads bounded by size (10 MB) and record count

### Fixed
- Extra usage credits and monthly limit were displayed in cents (e.g. $105) rather than dollars ($1.05) — divided by 100 throughout
- `stats/summary` was making a live HTTP call to claude.ai on every page load; now reads plan from the most recent saved snapshot instead
- Month-end projection used a fragile manual day-count calculation; replaced with `calendar.monthrange()`

### Changed
- Removed dead `collector.py` (superseded by `scraper.py`)
- Removed duplicate and unused imports across `sync_agent.py` and `app.py`

---

## [0.4.0] — 2026-05-15

### Added
- Accuracy notice banner explaining that cost estimates improve as more Claude Code sessions accumulate
- Author contact card for AI/healthcare/privacy legal enquiries
- Projected month-end API cost card, with savings vs. plan price highlighted
- Usage Limits table showing current utilization % and reset countdowns for each quota window, with link to Anthropic's May 2026 rate-limit announcement

---

## [0.3.0] — 2026-05-15

### Added
- Automatic plan detection (Pro / Max 5× / Max 20×) from the claude.ai usage API — inferred from the extra-usage monthly credit limit
- Manual plan selector modal shown when auto-detection is unavailable or ambiguous
- Plan badge in the header reflects the active plan; clickable to override
- Footer with MIT license link, credits to prior-art projects, and legal disclaimer
- `/api/plan` GET and POST endpoints; plan selection persisted to a `settings` table in SQLite

---

## [0.2.0] — 2026-05-15

### Added
- Chrome, Brave, Chromium, and Edge cookie support via `browser-cookie3` (handles OS-level cookie encryption on macOS, Linux, and Windows)
- Auth status endpoint now returns the browser name that provided the session
- Firefox profile paths updated for Ubuntu snap and Flatpak installations
- Claude Code JSONL log paths updated for Windows (`%APPDATA%\Claude\projects`) and macOS Xcode assistant path

### Changed
- Priority order for cookie sources: Firefox → Chrome → Brave → Chromium → Edge

---

## [0.1.0] — 2026-05-15

### Added
- Initial release
- Flask dashboard with SQLite backend
- Local JSONL log scanner adapted from [phuryn/claude-usage](https://github.com/phuryn/claude-usage): incremental scanning, streaming deduplication, session aggregation, estimated API cost per turn
- claude.ai usage API integration using Firefox session cookies via `curl_cffi` with Firefox TLS fingerprint impersonation — bypasses Cloudflare without a login window
- Real-time plan usage bars (current session %, 7-day %, extra usage) with reset countdowns
- 30-day historical charts: daily tokens, daily API cost equivalent, model breakdown, top projects
- Plan vs. API cost comparison table (Pro / Max 5× / Max 20×)
- Multi-machine sync: hub exposes a token-authenticated push endpoint; `sync_agent.py` (stdlib only, zero pip installs) runs on remote machines via cron
- Sessions tagged by originating machine; `/api/machines` endpoint lists all sources
- Cross-platform support: macOS, Linux, Windows for both Firefox cookie reading and JSONL log discovery
