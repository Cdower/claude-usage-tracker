const fmt = {
  tokens: n => n >= 1_000_000 ? (n/1_000_000).toFixed(2)+'M' : n >= 1_000 ? (n/1_000).toFixed(1)+'K' : String(n||0),
  usd: n => n == null ? '—' : '$'+Number(n).toFixed(2),
  pct: n => Number(n||0).toFixed(0)+'%',
};

const charts = {};

function chartDefaults(type) {
  return {
    type,
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { labels: { color: '#7a7f9a', font: { size: 11 } } } },
      scales: type === 'line' || type === 'bar' ? {
        x: { ticks: { color: '#7a7f9a', font: { size: 10 } }, grid: { color: '#2e3350' } },
        y: { ticks: { color: '#7a7f9a', font: { size: 10 } }, grid: { color: '#2e3350' }, beginAtZero: true }
      } : {}
    }
  };
}

function makeOrUpdate(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id).getContext('2d'), config);
}

// ── Auth ─────────────────────────────────────────────────────────────────────

async function checkAuth() {
  const res = await fetch('/api/auth/status').then(r => r.json());
  const el = document.getElementById('authStatus');
  if (res.authenticated) {
    const name = res.browser ? res.browser.charAt(0).toUpperCase() + res.browser.slice(1) : 'Browser';
    el.textContent = `● ${name} session active`;
    el.style.color = '#56cfa8';
  } else {
    el.textContent = '● Log into claude.ai in Firefox or Chrome to enable web sync';
    el.style.color = '#e0a050';
  }
}

// ── Sync ─────────────────────────────────────────────────────────────────────

document.getElementById('syncBtn').addEventListener('click', async () => {
  const btn = document.getElementById('syncBtn');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    await fetch('/api/sync', { method: 'POST' });
    await loadAll();
    document.getElementById('lastSync').textContent =
      'Last synced: ' + new Date().toLocaleTimeString();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sync Now';
  }
});

// ── Web Usage Bars ────────────────────────────────────────────────────────────

async function loadWebUsage() {
  const res = await fetch('/api/web-usage/latest');
  if (!res.ok) return;
  const data = await res.json();

  document.getElementById('webUsageSection').style.display = '';
  document.getElementById('planBadge').textContent = data.plan ? `${data.plan} Plan` : 'Plan';

  const container = document.getElementById('usageBars');
  container.innerHTML = '';
  (data.bars || []).forEach(bar => {
    const pct = Math.min(bar.percentage, 100);
    const fillClass = pct >= 90 ? 'danger' : pct >= 70 ? 'warning' : '';
    container.insertAdjacentHTML('beforeend', `
      <div class="usage-bar-row">
        <div class="usage-bar-header">
          <span class="usage-bar-label">${bar.label}</span>
          <span class="usage-bar-pct">${pct}% used</span>
        </div>
        <div class="usage-bar-track">
          <div class="usage-bar-fill ${fillClass}" style="width:${pct}%"></div>
        </div>
        ${bar.resetInfo ? `<div class="usage-bar-reset">${bar.resetInfo}</div>` : ''}
      </div>
    `);
  });

  if (data.reset_date) {
    document.getElementById('resetDate').textContent = data.reset_date;
  }
}

// ── Summary Cards ─────────────────────────────────────────────────────────────

async function loadSummary() {
  const data = await fetch('/api/stats/summary').then(r => r.json());
  const m = data.thisMonth || {};
  document.getElementById('monthTokens').textContent = fmt.tokens(m.tokens);
  document.getElementById('monthCost').textContent = fmt.usd(m.api_cost);
  document.getElementById('monthSessions').textContent = m.sessions ?? '—';

  // Rolling 30-day projection card
  const p = data.projected || {};
  if (p.projected_30d_api_cost != null) {
    const proj = p.projected_30d_api_cost;
    document.getElementById('projectedCost').textContent = fmt.usd(proj);

    // Pick the active plan comparison for the sub-label
    const cmp = p.plan_comparisons?.[p.active_plan];
    if (cmp) {
      const savings = cmp.savings_30d;
      const sign    = savings >= 0 ? '+' : '';
      const color   = savings >= 0 ? '#56cfa8' : '#e05c6b';
      const verb    = savings >= 0 ? 'saved' : 'over budget';
      document.getElementById('projectedSavingsSub').innerHTML =
        `30-day API equivalent &nbsp;<span style="color:${color};font-weight:600">${sign}${fmt.usd(Math.abs(savings))} ${verb} vs ${cmp.label}</span>`;
    } else {
      document.getElementById('projectedSavingsSub').textContent = '30-day API equivalent at current rate';
    }

    // Bar = daily rate expressed as % of the cheapest plan's daily budget
    const cheapestDaily = 20 / 30;  // Pro daily budget
    const ratePct = Math.min((p.daily_rate / cheapestDaily) * 100, 100);
    document.getElementById('projectedBar').style.width = ratePct + '%';
    document.getElementById('projectedDays').textContent =
      p.days_with_data
        ? `avg over ${p.window_days}-day window (${p.days_with_data} active days)`
        : 'no data yet';
  }
}

// ── Plan Comparison ───────────────────────────────────────────────────────────

async function loadPlanComparison() {
  const data = await fetch('/api/plan-comparison').then(r => r.json());
  const apiEq = data.this_month_api_equivalent || 0;

  // Find cheapest plan for user
  let bestPlan = null, bestSavings = -Infinity;
  Object.entries(data.plans || {}).forEach(([name, plan]) => {
    if (!plan.api_is_cheaper && plan.savings_vs_api > bestSavings) {
      bestSavings = plan.savings_vs_api;
      bestPlan = name;
    }
  });

  // Savings card
  if (bestPlan && bestSavings > 0) {
    document.getElementById('planSavings').textContent = fmt.usd(bestSavings);
    document.getElementById('planSavingsSub').textContent = `saved with ${bestPlan} vs. API`;
  } else {
    document.getElementById('planSavings').textContent = fmt.usd(Math.abs(apiEq));
    document.getElementById('planSavingsSub').textContent = 'est. API cost this month';
  }

  // Plan table
  const tbody = document.getElementById('planTableBody');
  tbody.innerHTML = '';
  Object.entries(data.plans || {}).forEach(([name, plan]) => {
    const cheaper = plan.api_is_cheaper;
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td>${name}</td>
        <td>${fmt.usd(plan.monthly_usd)}/mo</td>
        <td>${fmt.usd(apiEq)}</td>
        <td class="${cheaper ? 'verdict-bad' : 'verdict-good'}">
          ${cheaper
            ? `API cheaper by ${fmt.usd(plan.monthly_usd - apiEq)}`
            : `Saving ${fmt.usd(plan.savings_vs_api)}/mo`}
        </td>
      </tr>
    `);
  });
}

// ── Charts ────────────────────────────────────────────────────────────────────

async function loadDailyCharts() {
  const rows = await fetch('/api/stats/daily').then(r => r.json());
  const labels = rows.map(r => r.date.slice(5));  // MM-DD
  const tokens = rows.map(r => r.tokens || 0);
  const costs = rows.map(r => +(r.api_cost || 0).toFixed(4));

  makeOrUpdate('dailyTokenChart', {
    ...chartDefaults('bar'),
    data: {
      labels,
      datasets: [{ label: 'Tokens', data: tokens, backgroundColor: '#7c6ee680', borderColor: '#7c6ee6', borderWidth: 1 }]
    }
  });

  makeOrUpdate('dailyCostChart', {
    ...chartDefaults('line'),
    data: {
      labels,
      datasets: [{ label: 'API Cost ($)', data: costs, borderColor: '#56cfa8', backgroundColor: '#56cfa820', tension: 0.4, fill: true, pointRadius: 2 }]
    }
  });
}

async function loadModelChart() {
  const rows = await fetch('/api/stats/models').then(r => r.json());
  if (!rows.length) return;
  makeOrUpdate('modelChart', {
    ...chartDefaults('doughnut'),
    data: {
      labels: rows.map(r => (r.model || 'unknown').replace('claude-', '').replace(/-\d{8}$/, '')),
      datasets: [{ data: rows.map(r => r.tokens), backgroundColor: ['#7c6ee6','#56cfa8','#e0a050','#e05c6b','#6eb4e6'] }]
    }
  });
}

async function loadProjectChart() {
  const rows = await fetch('/api/stats/projects').then(r => r.json());
  if (!rows.length) return;
  const top = rows.slice(0, 8);
  makeOrUpdate('projectChart', {
    ...chartDefaults('bar'),
    data: {
      labels: top.map(r => r.project_name || 'unknown'),
      datasets: [{ label: 'Tokens', data: top.map(r => r.tokens), backgroundColor: '#7c6ee680', borderColor: '#7c6ee6', borderWidth: 1 }]
    },
    options: {
      ...chartDefaults('bar').options,
      indexAxis: 'y',
    }
  });
}

// ── Usage Limits table ────────────────────────────────────────────────────────

async function loadLimitsTable() {
  const res = await fetch('/api/web-usage/latest');
  if (!res.ok) return;
  const data = await res.json();
  const bars = data.bars || [];
  if (!bars.length) return;

  document.getElementById('limitsSection').style.display = '';
  const tbody = document.getElementById('limitsTableBody');
  tbody.innerHTML = '';

  bars.forEach(bar => {
    const pct = Math.min(bar.percentage || 0, 100);
    const cls = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : '';
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td>${bar.label}</td>
        <td><span class="limit-pct ${cls}">${pct}%</span></td>
        <td>
          <div class="limit-bar-track">
            <div class="limit-bar-fill ${cls}" style="width:${pct}%"></div>
          </div>
        </td>
        <td class="limit-reset">${bar.resetInfo || '—'}</td>
      </tr>
    `);
  });
}

// ── Plan badge + selector ─────────────────────────────────────────────────────

const PLAN_LABELS = { Pro: 'Pro Plan', Max5: 'Max Plan (5×)', Max20: 'Max Plan (20×)' };

async function loadPlan() {
  const data = await fetch('/api/plan').then(r => r.json());
  const badge = document.getElementById('planBadge');

  if (data.plan) {
    badge.textContent = PLAN_LABELS[data.plan] || data.plan;
    // Dim badge if user-selected rather than API-detected (less confident)
    badge.style.opacity = data.detected ? '1' : '0.75';
    badge.title = data.detected
      ? `Auto-detected from claude.ai API. Click to override.`
      : `Manually set. Click to change.`;
  } else {
    badge.textContent = 'Select Plan';
    badge.style.opacity = '1';
    showPlanModal();
  }
  return data;
}

function showPlanModal() {
  document.getElementById('planModal').style.display = 'flex';
}

function hidePlanModal() {
  document.getElementById('planModal').style.display = 'none';
}

document.getElementById('planBadge').addEventListener('click', showPlanModal);

document.getElementById('planModal').addEventListener('click', e => {
  if (e.target === document.getElementById('planModal')) hidePlanModal();
});

document.querySelectorAll('.plan-option').forEach(btn => {
  btn.addEventListener('click', async () => {
    const plan = btn.dataset.plan;
    await fetch('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan }),
    });
    hidePlanModal();
    loadPlan();
    loadPlanComparison();
  });
});

// ── Boot ─────────────────────────────────────────────────────────────────────

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

checkAuth();
loadAll();
// Auto-refresh every 5 min
setInterval(loadAll, 5 * 60 * 1000);
