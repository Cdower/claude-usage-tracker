from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import sqlite3
import json
import os
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

from scanner import scan, get_db, init_db, DB_PATH
from scraper import is_authenticated, collect, fetch_usage

app = Flask(__name__)
CORS(app)

REMOTE_TOKEN = os.environ.get("REMOTE_TOKEN", "")

# Subscription plan pricing
PLANS = {
    "Pro": {"monthly_usd": 20},
    "Max5": {"monthly_usd": 100},
    "Max20": {"monthly_usd": 200},
}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.before_request
def ensure_db():
    conn = get_db()
    init_db(conn)
    conn.close()


# ── Auth helpers ─────────────────────────────────────────────────────────────

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not REMOTE_TOKEN:
            return jsonify({"error": "REMOTE_TOKEN not set on hub"}), 500
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {REMOTE_TOKEN}":
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/api/auth/status")
def auth_status():
    return jsonify({"authenticated": is_authenticated()})



# ── Sync ──────────────────────────────────────────────────────────────────────

@app.post("/api/sync")
def sync():
    local = scan()
    web = collect() if is_authenticated() else {"authenticated": False}
    return jsonify({"local": local, "web": web})


# ── Local JSONL stats ─────────────────────────────────────────────────────────

@app.get("/api/stats/summary")
def stats_summary():
    conn = _db()

    # All-time totals
    totals = conn.execute("""
        SELECT
            SUM(total_input_tokens)  AS input_tokens,
            SUM(total_output_tokens) AS output_tokens,
            SUM(total_cache_read)    AS cache_read,
            SUM(total_cache_creation) AS cache_creation,
            SUM(estimated_api_cost)  AS api_cost,
            COUNT(*)                 AS sessions
        FROM sessions
    """).fetchone()

    # This month
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat()
    monthly = conn.execute("""
        SELECT
            SUM(total_input_tokens + total_output_tokens + total_cache_read + total_cache_creation) AS tokens,
            SUM(estimated_api_cost) AS api_cost,
            COUNT(*) AS sessions
        FROM sessions WHERE first_timestamp >= ?
    """, (month_start,)).fetchone()

    conn.close()
    return jsonify({
        "allTime": dict(totals) if totals else {},
        "thisMonth": dict(monthly) if monthly else {},
    })


@app.get("/api/stats/daily")
def stats_daily():
    """Daily token + cost totals for the past 30 days."""
    conn = _db()
    rows = conn.execute("""
        SELECT
            substr(first_timestamp, 1, 10) AS date,
            SUM(total_input_tokens + total_output_tokens + total_cache_read + total_cache_creation) AS tokens,
            SUM(estimated_api_cost) AS api_cost,
            COUNT(*) AS sessions
        FROM sessions
        WHERE first_timestamp >= date('now', '-30 days')
        GROUP BY date
        ORDER BY date
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/stats/models")
def stats_models():
    conn = _db()
    rows = conn.execute("""
        SELECT
            model,
            SUM(total_input_tokens + total_output_tokens) AS tokens,
            SUM(estimated_api_cost) AS api_cost,
            COUNT(*) AS sessions
        FROM sessions
        WHERE model IS NOT NULL
        GROUP BY model
        ORDER BY tokens DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/stats/projects")
def stats_projects():
    conn = _db()
    rows = conn.execute("""
        SELECT
            project_name,
            SUM(total_input_tokens + total_output_tokens) AS tokens,
            SUM(estimated_api_cost) AS api_cost,
            COUNT(*) AS sessions
        FROM sessions
        GROUP BY project_name
        ORDER BY tokens DESC
        LIMIT 20
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Web usage (scraped from claude.ai) ───────────────────────────────────────

@app.get("/api/web-usage/latest")
def web_usage_latest():
    conn = _db()
    row = conn.execute("""
        SELECT * FROM web_usage_snapshots ORDER BY timestamp DESC LIMIT 1
    """).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "No web usage data. Login and sync first."}), 404
    data = dict(row)
    data["bars"] = json.loads(data["bars_json"] or "[]")
    del data["bars_json"]
    return jsonify(data)


@app.get("/api/web-usage/history")
def web_usage_history():
    conn = _db()
    rows = conn.execute("""
        SELECT timestamp, plan, bars_json, reset_date, credit_balance
        FROM web_usage_snapshots
        ORDER BY timestamp DESC LIMIT 60
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["bars"] = json.loads(d["bars_json"] or "[]")
        del d["bars_json"]
        result.append(d)
    return jsonify(result)


# ── Plan comparison ───────────────────────────────────────────────────────────

@app.get("/api/plan-comparison")
def plan_comparison():
    """Compare what user actually paid vs. what API would cost at their usage."""
    conn = _db()
    monthly = conn.execute("""
        SELECT SUM(estimated_api_cost) AS api_cost
        FROM sessions
        WHERE first_timestamp >= date('now', 'start of month')
    """).fetchone()
    conn.close()

    api_cost = monthly["api_cost"] or 0

    return jsonify({
        "this_month_api_equivalent": round(api_cost, 4),
        "plans": {
            name: {
                "monthly_usd": plan["monthly_usd"],
                "savings_vs_api": round(api_cost - plan["monthly_usd"], 2),
                "api_is_cheaper": api_cost < plan["monthly_usd"],
            }
            for name, plan in PLANS.items()
        }
    })


# ── Remote push (from other machines) ────────────────────────────────────────

@app.post("/api/remote/push")
@require_token
def remote_push():
    """
    Accept sessions + turns from a remote agent.
    Body: { "machine": "laptop", "sessions": [...], "turns": [...] }
    """
    body = request.get_json(force=True)
    machine = body.get("machine", "unknown")
    sessions = body.get("sessions", [])
    turns = body.get("turns", [])

    conn = _db()
    inserted_sessions = 0
    inserted_turns = 0

    for s in sessions:
        existing = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            (s["session_id"],)
        ).fetchone()
        if existing is None:
            conn.execute("""
                INSERT INTO sessions
                    (session_id, project_name, first_timestamp, last_timestamp,
                     git_branch, total_input_tokens, total_output_tokens,
                     total_cache_read, total_cache_creation, model,
                     turn_count, estimated_api_cost, machine)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["session_id"], s.get("project_name"), s.get("first_timestamp"),
                s.get("last_timestamp"), s.get("git_branch"),
                s.get("total_input_tokens", 0), s.get("total_output_tokens", 0),
                s.get("total_cache_read", 0), s.get("total_cache_creation", 0),
                s.get("model"), s.get("turn_count", 0),
                s.get("estimated_api_cost", 0), machine
            ))
            inserted_sessions += 1
        else:
            conn.execute("""
                UPDATE sessions SET
                    last_timestamp = MAX(last_timestamp, ?),
                    total_input_tokens = ?,
                    total_output_tokens = ?,
                    total_cache_read = ?,
                    total_cache_creation = ?,
                    turn_count = ?,
                    estimated_api_cost = ?,
                    machine = ?
                WHERE session_id = ?
            """, (
                s.get("last_timestamp"),
                s.get("total_input_tokens", 0), s.get("total_output_tokens", 0),
                s.get("total_cache_read", 0), s.get("total_cache_creation", 0),
                s.get("turn_count", 0), s.get("estimated_api_cost", 0),
                machine, s["session_id"]
            ))

    for t in turns:
        cur = conn.execute("""
            INSERT OR IGNORE INTO turns
                (session_id, timestamp, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_creation_tokens, tool_name, cwd,
                 message_id, estimated_api_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["session_id"], t.get("timestamp"), t.get("model"),
            t.get("input_tokens", 0), t.get("output_tokens", 0),
            t.get("cache_read_tokens", 0), t.get("cache_creation_tokens", 0),
            t.get("tool_name"), t.get("cwd"), t.get("message_id", ""),
            t.get("estimated_api_cost", 0)
        ))
        if cur.rowcount:
            inserted_turns += 1

    conn.commit()
    conn.close()

    return jsonify({
        "machine": machine,
        "sessions_received": len(sessions),
        "sessions_inserted": inserted_sessions,
        "turns_received": len(turns),
        "turns_inserted": inserted_turns,
    })


@app.get("/api/machines")
def machines():
    """List all machines that have pushed data."""
    conn = _db()
    rows = conn.execute("""
        SELECT machine,
               COUNT(*) AS sessions,
               SUM(estimated_api_cost) AS api_cost,
               MAX(last_timestamp) AS last_seen
        FROM sessions
        GROUP BY machine
        ORDER BY last_seen DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host=host, port=port)
