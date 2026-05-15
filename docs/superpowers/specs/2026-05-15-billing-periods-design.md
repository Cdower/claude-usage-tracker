# Billing Periods Design

**Date:** 2026-05-15
**Branch:** billing-periods
**Status:** Approved

## Problem

The dashboard's "This Month" section uses calendar months, so a user whose billing renews on the 21st sees a misleading split — e.g., May 1–14 in one "month" and May 15–20 in the next. Historical data predating a paid plan (free-tier usage) is mixed in with paid-plan usage, distorting savings calculations.

## Goal

Add a **Billing History** section showing per-billing-period token usage, API cost equivalent, plan cost, and savings — with Period 0 covering pre-paid (free plan) usage.

---

## Settings Storage

- Key: `billing_start_date` (string, `YYYY-MM-DD`)
- Stored in the existing `settings` table — no schema migration needed
- New endpoints:
  - `GET /api/settings/billing-start-date` → `{ "billing_start_date": "2025-04-21" | null }`
  - `POST /api/settings/billing-start-date` body `{ "date": "2025-04-21" }` → `{ "ok": true }`

---

## Period Calculation

Given `billing_start_date = D`:

| Period | Start (inclusive) | End (exclusive) | Label |
|--------|-------------------|-----------------|-------|
| 0 | beginning of logs | D | Free Plan |
| 1 | D | D + 1 month | Period 1 |
| 2 | D + 1 month | D + 2 months | Period 2 |
| N | D + (N-1) months | D + N months | Period N |
| current | D + (N-1) months | today | Current Period |

Month arithmetic: add months by incrementing the month field, clamping day to the last day of the resulting month (e.g., Jan 31 + 1 month → Feb 28/29).

Period 0 is omitted if there are no sessions before `billing_start_date`.

---

## New Endpoint: `/api/stats/billing-periods`

Returns an array of period objects, oldest first:

```json
[
  {
    "period": 0,
    "label": "Free Plan",
    "start": null,
    "end": "2025-04-21",
    "tokens": 1200000,
    "api_cost": 4.21,
    "sessions": 3,
    "plan_cost": null,
    "savings": null
  },
  {
    "period": 1,
    "label": "Period 1",
    "start": "2025-04-21",
    "end": "2025-05-21",
    "tokens": 342050000,
    "api_cost": 422.45,
    "sessions": 33,
    "plan_cost": 20.00,
    "savings": 402.45
  },
  {
    "period": 2,
    "label": "Current Period",
    "start": "2025-05-21",
    "end": null,
    "tokens": 8400000,
    "api_cost": 11.20,
    "sessions": 4,
    "plan_cost": 20.00,
    "savings": null
  }
]
```

`plan_cost` and `savings` use the detected/selected plan (same logic as existing plan comparison). `savings` is null for the current (incomplete) period — it can't be finalized until the period ends. Period 0 always has null `plan_cost` and `savings`.

---

## UI Changes

### "This Month" section
No changes.

### New "Billing History" section (added below "This Month")

**State A — not configured:**
```
Billing History
───────────────────────────────────────────────────────
Paid plan start date: [____________] [Save]
Check Claude → Settings → Billing for your subscription history.
```

**State B — configured:**

```
Billing History                                    [Edit date]
┌──────────────────┬──────────────┬──────────┬──────────┬──────────┬──────────┐
│ Period           │ Dates        │ Tokens   │ API Cost │ Plan Cost│ Savings  │
├──────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ Free Plan        │ — → Apr 21   │ 1.20M    │ $4.21    │ —        │ —        │
│ Period 1         │ Apr 21–May 21│ 342.05M  │ $422.45  │ $20.00   │ $402.45  │
│ Current Period   │ May 21–today │ 8.40M    │ $11.20   │ $20.00   │ (ongoing)│
├──────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ Total (paid)     │              │ 350.45M  │ $433.65  │ $40.00   │ $393.65  │
└──────────────────┴──────────────┴──────────┴──────────┴──────────┴──────────┘
```

- "Total (paid)" row sums only completed paid periods (excludes Period 0 and the current incomplete period)
- "Edit date" link replaces the table with State A for correction
- Dates column: uses short month names, e.g. "Apr 21 – May 21"
- Savings column for current period shows "(ongoing)" in muted text

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | Add `GET/POST /api/settings/billing-start-date`, add `GET /api/stats/billing-periods` |
| `static/app.js` | Add `loadBillingPeriods()`, called from `loadSummary()` or on page load |
| `templates/index.html` | Add Billing History section HTML below "This Month" |
| `static/style.css` | Add styles for billing history table and setup prompt |

No database schema changes required.

---

## Error Handling

- If `billing_start_date` is missing or unparseable, `/api/stats/billing-periods` returns `[]` — frontend shows State A
- If no sessions exist before `billing_start_date`, Period 0 is omitted from results
- Invalid date submitted via POST returns `400 { "error": "invalid date" }`
