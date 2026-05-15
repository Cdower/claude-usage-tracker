# Billing Periods Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Billing History section that shows per-billing-period token usage, API cost, plan cost, and savings, with Period 0 covering pre-paid (free plan) usage.

**Architecture:** One new GET + POST settings endpoint stores `billing_start_date` in the existing `settings` table. One new `/api/stats/billing-periods` endpoint computes period boundaries from that date and queries the `sessions` table. The frontend adds a new HTML section that shows a date-input prompt when unconfigured and a summary table when configured.

**Tech Stack:** Python/Flask, SQLite (existing `sessions` + `settings` tables), vanilla JS, CSS matching existing dark-theme patterns.

---

## File Map

| File | Change |
|------|--------|
| `app.py` | Add `GET /api/settings/billing-start-date`, `POST /api/settings/billing-start-date`, `GET /api/stats/billing-periods` |
| `templates/index.html` | Add Billing History `<section>` after the Plan Comparison section |
| `static/app.js` | Add `loadBillingPeriods()`, call from `loadAll()` |
| `static/style.css` | Add `.billing-setup` and `.billing-totals-row` styles |

No new files. No schema changes.

---

## Task 1: Backend — billing-start-date settings endpoints

**Files:**
- Modify: `app.py` (after the `set_plan` endpoint, around line 100)

- [ ] **Step 1: Add the two endpoints**

Open `app.py`. After the `set_plan` function (around line 100), insert:

```python
# ── Billing start date ────────────────────────────────────────────────────────

@app.get("/api/settings/billing-start-date")
def get_billing_start_date():
    return jsonify({"billing_start_date": _get_setting("billing_start_date")})


@app.post("/api/settings/billing-start-date")
def set_billing_start_date():
    body = request.get_json(force=True)
    raw = (body.get("date") or "").strip()
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "invalid date, expected YYYY-MM-DD"}), 400
    _set_setting("billing_start_date", raw)
    return jsonify({"ok": True, "billing_start_date": raw})
```

- [ ] **Step 2: Smoke-test the endpoints**

With the app running (`source venv/bin/activate && ./run.sh &`):

```bash
# GET with nothing set → null
curl -s http://127.0.0.1:5000/api/settings/billing-start-date
# Expected: {"billing_start_date":null}

# POST a valid date
curl -s -X POST http://127.0.0.1:5000/api/settings/billing-start-date \
  -H "Content-Type: application/json" \
  -d '{"date":"2025-04-21"}'
# Expected: {"billing_start_date":"2025-04-21","ok":true}

# GET confirms it persisted
curl -s http://127.0.0.1:5000/api/settings/billing-start-date
# Expected: {"billing_start_date":"2025-04-21"}

# POST invalid date → 400
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://127.0.0.1:5000/api/settings/billing-start-date \
  -H "Content-Type: application/json" \
  -d '{"date":"not-a-date"}'
# Expected: 400
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add billing-start-date settings endpoints"
```

---

## Task 2: Backend — billing-periods calculation endpoint

**Files:**
- Modify: `app.py` (add after `plan_comparison` endpoint, around line 337)

- [ ] **Step 1: Add the period-boundary helper and endpoint**

Insert after the `plan_comparison` function:

```python
# ── Billing periods ───────────────────────────────────────────────────────────

def _add_months(d, n):
    """Return date d advanced by n calendar months, clamping to last day of month."""
    month = d.month - 1 + n
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


@app.get("/api/stats/billing-periods")
def billing_periods():
    raw = _get_setting("billing_start_date")
    if not raw:
        return jsonify([])

    try:
        start = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify([])

    today = datetime.now().date()
    conn  = _db()

    # Detect active plan for plan_cost calculation
    snap = conn.execute(
        "SELECT plan FROM web_usage_snapshots ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    selected = _get_setting("selected_plan")
    active_plan = (snap["plan"] if snap else None) or selected
    plan_cost = PLAN_DETAILS.get(active_plan, {}).get("monthly_usd")

    def query_period(date_from, date_to):
        """Query sessions between date_from (inclusive) and date_to (exclusive).
        Pass None for date_from to mean 'beginning of time'.
        Pass None for date_to to mean 'up to now'."""
        if date_from is None and date_to is None:
            row = conn.execute("""
                SELECT SUM(total_input_tokens + total_output_tokens +
                           total_cache_read + total_cache_creation) AS tokens,
                       SUM(estimated_api_cost) AS api_cost,
                       COUNT(*) AS sessions
                FROM sessions
            """).fetchone()
        elif date_from is None:
            row = conn.execute("""
                SELECT SUM(total_input_tokens + total_output_tokens +
                           total_cache_read + total_cache_creation) AS tokens,
                       SUM(estimated_api_cost) AS api_cost,
                       COUNT(*) AS sessions
                FROM sessions WHERE first_timestamp < ?
            """, (date_to.isoformat(),)).fetchone()
        elif date_to is None:
            row = conn.execute("""
                SELECT SUM(total_input_tokens + total_output_tokens +
                           total_cache_read + total_cache_creation) AS tokens,
                       SUM(estimated_api_cost) AS api_cost,
                       COUNT(*) AS sessions
                FROM sessions WHERE first_timestamp >= ?
            """, (date_from.isoformat(),)).fetchone()
        else:
            row = conn.execute("""
                SELECT SUM(total_input_tokens + total_output_tokens +
                           total_cache_read + total_cache_creation) AS tokens,
                       SUM(estimated_api_cost) AS api_cost,
                       COUNT(*) AS sessions
                FROM sessions WHERE first_timestamp >= ? AND first_timestamp < ?
            """, (date_from.isoformat(), date_to.isoformat())).fetchone()
        return dict(row) if row else {"tokens": 0, "api_cost": 0, "sessions": 0}

    periods = []

    # Period 0 — free plan (before billing start)
    p0 = query_period(None, start)
    if (p0["sessions"] or 0) > 0:
        periods.append({
            "period":     0,
            "label":      "Free Plan",
            "start":      None,
            "end":        start.isoformat(),
            "tokens":     p0["tokens"] or 0,
            "api_cost":   round(p0["api_cost"] or 0, 2),
            "sessions":   p0["sessions"] or 0,
            "plan_cost":  None,
            "savings":    None,
        })

    # Paid periods
    period_num   = 1
    period_start = start
    while period_start <= today:
        period_end = _add_months(period_start, 1)
        is_current = period_end > today

        if is_current:
            data  = query_period(period_start, None)
            label = "Current Period"
            end   = None
            savings = None  # period not complete
        else:
            data  = query_period(period_start, period_end)
            label = f"Period {period_num}"
            end   = period_end.isoformat()
            savings = round((data["api_cost"] or 0) - plan_cost, 2) if plan_cost else None

        periods.append({
            "period":     period_num,
            "label":      label,
            "start":      period_start.isoformat(),
            "end":        end,
            "tokens":     data["tokens"] or 0,
            "api_cost":   round(data["api_cost"] or 0, 2),
            "sessions":   data["sessions"] or 0,
            "plan_cost":  plan_cost,
            "savings":    savings,
        })

        if is_current:
            break
        period_start = period_end
        period_num  += 1

    conn.close()
    return jsonify(periods)
```

- [ ] **Step 2: Smoke-test the endpoint**

```bash
curl -s http://127.0.0.1:5000/api/stats/billing-periods | python3 -m json.tool
```

Expected: JSON array. First object should have `"period": 0, "label": "Free Plan"` if there are sessions before 2025-04-21. Last object should have `"label": "Current Period"` and `"end": null`.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add billing-periods calculation endpoint"
```

---

## Task 3: HTML — add Billing History section

**Files:**
- Modify: `templates/index.html` (after the Plan Comparison `</section>`, around line 113)

- [ ] **Step 1: Insert the section**

After the closing `</section>` of the Plan Comparison section, insert:

```html
  <!-- Billing History -->
  <section id="billingHistorySection">
    <h2>Billing History <span class="section-note">from local logs</span></h2>

    <!-- Shown when billing_start_date not yet configured -->
    <div id="billingSetup" style="display:none">
      <div class="billing-setup">
        <label for="billingStartInput">Paid plan start date</label>
        <input type="date" id="billingStartInput" />
        <button class="btn btn-primary" id="billingStartSave">Save</button>
        <span class="billing-setup-hint">Find this in Claude → Settings → Billing</span>
      </div>
    </div>

    <!-- Shown once configured -->
    <div id="billingTable" style="display:none">
      <table class="plan-table">
        <thead>
          <tr>
            <th>Period</th>
            <th>Dates</th>
            <th>Tokens</th>
            <th>Est. API Cost</th>
            <th>Plan Cost</th>
            <th>Savings</th>
          </tr>
        </thead>
        <tbody id="billingTableBody"></tbody>
      </table>
      <div style="margin-top:8px;text-align:right">
        <a href="#" id="billingEditLink" style="font-size:12px;color:var(--muted)">Edit start date</a>
      </div>
    </div>
  </section>
```

- [ ] **Step 2: Verify HTML renders without errors**

Reload http://127.0.0.1:5000 — the page should load without JS errors. The Billing History heading should be visible. Both inner divs are hidden so nothing else is visible yet.

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add billing history section HTML"
```

---

## Task 4: CSS — billing section styles

**Files:**
- Modify: `static/style.css` (append at end)

- [ ] **Step 1: Add styles**

Append to `static/style.css`:

```css
/* Billing History */
.billing-setup {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  flex-wrap: wrap;
}
.billing-setup label {
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
}
.billing-setup input[type="date"] {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  padding: 6px 10px;
}
.billing-setup-hint {
  font-size: 11px;
  color: var(--muted);
}
.billing-totals-row td {
  border-top: 2px solid var(--border);
  font-weight: 600;
}
.billing-ongoing {
  color: var(--muted);
  font-style: italic;
}
```

- [ ] **Step 2: Verify no visual regressions**

Reload http://127.0.0.1:5000 — existing sections should look unchanged.

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "feat: add billing history CSS"
```

---

## Task 5: JavaScript — loadBillingPeriods()

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add the function**

Before the `// ── Boot ──` comment at the bottom of `static/app.js`, insert:

```javascript
// ── Billing History ───────────────────────────────────────────────────────────

function fmtBillingDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function renderBillingPeriods(periods) {
  const tbody = document.getElementById('billingTableBody');
  tbody.innerHTML = '';

  let totalTokens = 0, totalApiCost = 0, totalPlanCost = 0, totalSavings = 0;
  let hasPaidComplete = false;

  periods.forEach(p => {
    const isFreePlan  = p.period === 0;
    const isCurrent   = p.end === null && p.period > 0;
    const startStr    = p.start ? fmtBillingDate(p.start) : 'beginning';
    const endStr      = p.end   ? fmtBillingDate(p.end)   : 'today';
    const dateRange   = `${startStr} – ${endStr}`;

    const savingsCell = isFreePlan
      ? '—'
      : isCurrent
        ? '<span class="billing-ongoing">(ongoing)</span>'
        : p.savings != null
          ? `<span class="${p.savings >= 0 ? 'verdict-good' : 'verdict-bad'}">${fmt.usd(p.savings)}</span>`
          : '—';

    const planCostCell = p.plan_cost != null ? fmt.usd(p.plan_cost) : '—';

    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td>${p.label}</td>
        <td style="color:var(--muted);font-size:12px">${dateRange}</td>
        <td>${fmt.tokens(p.tokens)}</td>
        <td>${fmt.usd(p.api_cost)}</td>
        <td>${planCostCell}</td>
        <td>${savingsCell}</td>
      </tr>
    `);

    // Accumulate totals for completed paid periods only
    if (!isFreePlan && !isCurrent) {
      totalTokens   += p.tokens   || 0;
      totalApiCost  += p.api_cost || 0;
      totalPlanCost += p.plan_cost || 0;
      totalSavings  += p.savings  || 0;
      hasPaidComplete = true;
    }
  });

  // Totals row (only if there's at least one completed paid period)
  if (hasPaidComplete) {
    tbody.insertAdjacentHTML('beforeend', `
      <tr class="billing-totals-row">
        <td>Total (paid)</td>
        <td></td>
        <td>${fmt.tokens(totalTokens)}</td>
        <td>${fmt.usd(totalApiCost)}</td>
        <td>${fmt.usd(totalPlanCost)}</td>
        <td><span class="${totalSavings >= 0 ? 'verdict-good' : 'verdict-bad'}">${fmt.usd(totalSavings)}</span></td>
      </tr>
    `);
  }
}

async function loadBillingPeriods() {
  const settingRes = await fetch('/api/settings/billing-start-date').then(r => r.json());
  const setup  = document.getElementById('billingSetup');
  const table  = document.getElementById('billingTable');
  const editLk = document.getElementById('billingEditLink');

  if (!settingRes.billing_start_date) {
    setup.style.display = '';
    table.style.display = 'none';
    return;
  }

  // Date is configured — load and render periods
  setup.style.display = 'none';
  table.style.display = '';

  // Pre-fill input in case user clicks Edit
  document.getElementById('billingStartInput').value = settingRes.billing_start_date;

  const periods = await fetch('/api/stats/billing-periods').then(r => r.json());
  renderBillingPeriods(periods);
}

// Save handler
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('billingStartSave').addEventListener('click', async () => {
    const val = document.getElementById('billingStartInput').value;
    if (!val) return;
    const res = await fetch('/api/settings/billing-start-date', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: val }),
    });
    if (res.ok) loadBillingPeriods();
  });

  document.getElementById('billingEditLink').addEventListener('click', e => {
    e.preventDefault();
    document.getElementById('billingSetup').style.display = '';
    document.getElementById('billingTable').style.display = 'none';
  });
});
```

- [ ] **Step 2: Wire loadBillingPeriods into loadAll()**

Find the `loadAll` function (near the bottom of `app.js`):

```javascript
async function loadAll() {
  await Promise.all([
    loadWebUsage(),
    loadSummary(),
    loadPlan(),
    loadLimitsTable(),
    loadPlanComparison(),
    loadDailyCharts(),
    loadModelChart(),
    loadProjectChart(),
  ]);
}
```

Replace with:

```javascript
async function loadAll() {
  await Promise.all([
    loadWebUsage(),
    loadSummary(),
    loadPlan(),
    loadLimitsTable(),
    loadPlanComparison(),
    loadDailyCharts(),
    loadModelChart(),
    loadProjectChart(),
    loadBillingPeriods(),
  ]);
}
```

- [ ] **Step 3: End-to-end test**

1. Reload http://127.0.0.1:5000
2. Scroll to Billing History — should show the date input and "Find this in Claude → Settings → Billing" hint
3. Enter `2025-04-21` and click Save
4. Table should appear with Period 0 (Free Plan, if sessions exist before that date), completed periods, and Current Period
5. Verify "Total (paid)" row appears if there is at least one completed paid period
6. Click "Edit start date" — input reappears; change date and save again — table updates

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: add billing periods JS — loadBillingPeriods, save/edit handlers"
```

---

## Task 6: Push branch and open PR

- [ ] **Step 1: Push branch**

```bash
git push origin billing-periods
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --base main \
  --head billing-periods \
  --title "Add billing history section with per-period savings breakdown" \
  --body "$(cat <<'EOF'
## Summary
- Adds a Billing History section below Plan Comparison
- User enters their paid plan start date (with a hint to check Claude → Settings → Billing)
- Period 0 covers pre-paid (free plan) usage; subsequent periods are derived from the billing date
- Each row shows tokens, estimated API cost, plan cost, and savings; a totals row sums completed paid periods
- Date is stored in the existing settings table — no schema migration

## Test plan
- [ ] No billing date set → setup prompt renders, table hidden
- [ ] Enter valid date → table renders with correct period boundaries
- [ ] Period 0 only appears if sessions exist before billing start date
- [ ] Current period shows "(ongoing)" in Savings column
- [ ] Totals row only appears when at least one completed paid period exists
- [ ] Edit start date link shows prompt again; saving updates the table
- [ ] Invalid date POST returns 400

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Confirm PR URL is returned, share with team**
