"""
Reads Claude Code JSONL transcript logs from ~/.claude/projects/.
Adapted from phuryn/claude-usage (MIT License).
"""

import json
import os
import glob
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, Counter

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DB_PATH = Path(__file__).parent / "usage.db"

MODEL_PRIORITY = {"opus": 3, "sonnet": 2, "haiku": 1}

# Pricing per million tokens (input / output) as of May 2026
MODEL_PRICING = {
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4": (0.80, 4.00),
    "claude-haiku-4-5": (0.80, 4.00),
}


def _model_priority(model):
    if not model:
        return 0
    m = model.lower()
    for keyword, priority in MODEL_PRIORITY.items():
        if keyword in m:
            return priority
    return 0


def _model_cost_per_mtok(model):
    """Return (input_$/Mtok, output_$/Mtok) for a model."""
    if not model:
        return (3.00, 15.00)
    m = model.lower()
    for prefix, pricing in MODEL_PRICING.items():
        if prefix in m:
            return pricing
    return (3.00, 15.00)


def get_db(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id          TEXT PRIMARY KEY,
            project_name        TEXT,
            first_timestamp     TEXT,
            last_timestamp      TEXT,
            git_branch          TEXT,
            total_input_tokens  INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_cache_read    INTEGER DEFAULT 0,
            total_cache_creation INTEGER DEFAULT 0,
            model               TEXT,
            turn_count          INTEGER DEFAULT 0,
            estimated_api_cost  REAL DEFAULT 0,
            machine             TEXT DEFAULT 'local'
        );

        CREATE TABLE IF NOT EXISTS turns (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              TEXT,
            timestamp               TEXT,
            model                   TEXT,
            input_tokens            INTEGER DEFAULT 0,
            output_tokens           INTEGER DEFAULT 0,
            cache_read_tokens       INTEGER DEFAULT 0,
            cache_creation_tokens   INTEGER DEFAULT 0,
            tool_name               TEXT,
            cwd                     TEXT,
            message_id              TEXT,
            estimated_api_cost      REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS processed_files (
            path    TEXT PRIMARY KEY,
            mtime   REAL,
            lines   INTEGER
        );

        CREATE TABLE IF NOT EXISTS web_usage_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT,
            plan            TEXT,
            email           TEXT,
            bars_json       TEXT,
            reset_date      TEXT,
            credit_balance  REAL
        );

        CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
        CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sessions_first ON sessions(first_timestamp);
    """)
    try:
        conn.execute("SELECT message_id FROM turns LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE turns ADD COLUMN message_id TEXT")
    try:
        conn.execute("SELECT estimated_api_cost FROM turns LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE turns ADD COLUMN estimated_api_cost REAL DEFAULT 0")
    try:
        conn.execute("SELECT estimated_api_cost FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN estimated_api_cost REAL DEFAULT 0")
    try:
        conn.execute("SELECT machine FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN machine TEXT DEFAULT 'local'")
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_message_id
        ON turns(message_id) WHERE message_id IS NOT NULL AND message_id != ''
    """)
    conn.commit()


def project_name_from_cwd(cwd):
    if not cwd:
        return "unknown"
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1] or "unknown"


def _estimate_cost(model, input_tokens, output_tokens, cache_read, cache_creation):
    input_rate, output_rate = _model_cost_per_mtok(model)
    return (
        (input_tokens / 1_000_000) * input_rate +
        (output_tokens / 1_000_000) * output_rate +
        (cache_read / 1_000_000) * (input_rate * 0.1) +
        (cache_creation / 1_000_000) * (input_rate * 1.25)
    )


def parse_jsonl_file(filepath):
    seen_messages = {}
    turns_no_id = []
    session_meta = {}
    line_count = 0

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for line_count, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rtype = record.get("type")
                if rtype not in ("assistant", "user"):
                    continue

                session_id = record.get("sessionId")
                if not session_id:
                    continue

                timestamp = record.get("timestamp", "")
                cwd = record.get("cwd", "")

                if session_id not in session_meta:
                    session_meta[session_id] = {
                        "session_id": session_id,
                        "project_name": project_name_from_cwd(cwd),
                        "first_timestamp": timestamp,
                        "last_timestamp": timestamp,
                        "git_branch": record.get("gitBranch", ""),
                        "model": None,
                    }
                else:
                    meta = session_meta[session_id]
                    if timestamp and (not meta["first_timestamp"] or timestamp < meta["first_timestamp"]):
                        meta["first_timestamp"] = timestamp
                    if timestamp and (not meta["last_timestamp"] or timestamp > meta["last_timestamp"]):
                        meta["last_timestamp"] = timestamp

                if rtype == "assistant":
                    msg = record.get("message", {})
                    usage = msg.get("usage", {})
                    model = msg.get("model", "")
                    message_id = msg.get("id", "")

                    input_tokens = usage.get("input_tokens", 0) or 0
                    output_tokens = usage.get("output_tokens", 0) or 0
                    cache_read = usage.get("cache_read_input_tokens", 0) or 0
                    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0

                    if input_tokens + output_tokens + cache_read + cache_creation == 0:
                        continue

                    tool_name = None
                    for item in msg.get("content", []):
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            tool_name = item.get("name")
                            break

                    if model:
                        session_meta[session_id]["model"] = model

                    cost = _estimate_cost(model, input_tokens, output_tokens, cache_read, cache_creation)

                    turn = {
                        "session_id": session_id,
                        "timestamp": timestamp,
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read,
                        "cache_creation_tokens": cache_creation,
                        "tool_name": tool_name,
                        "cwd": cwd,
                        "message_id": message_id,
                        "estimated_api_cost": cost,
                    }

                    if message_id:
                        seen_messages[message_id] = turn
                    else:
                        turns_no_id.append(turn)

    except Exception as e:
        print(f"Warning: error reading {filepath}: {e}")

    return list(session_meta.values()), turns_no_id + list(seen_messages.values()), line_count


def aggregate_sessions(session_metas, turns):
    session_stats = defaultdict(lambda: {
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_creation": 0,
        "turn_count": 0, "model": None, "estimated_api_cost": 0,
    })
    session_model_counts = defaultdict(Counter)

    for t in turns:
        s = session_stats[t["session_id"]]
        s["total_input_tokens"] += t["input_tokens"]
        s["total_output_tokens"] += t["output_tokens"]
        s["total_cache_read"] += t["cache_read_tokens"]
        s["total_cache_creation"] += t["cache_creation_tokens"]
        s["turn_count"] += 1
        s["estimated_api_cost"] += t.get("estimated_api_cost", 0)
        if t["model"]:
            session_model_counts[t["session_id"]][t["model"]] += 1

    for sid, counts in session_model_counts.items():
        if counts:
            session_stats[sid]["model"] = counts.most_common(1)[0][0]

    return [{**meta, **session_stats[meta["session_id"]]} for meta in session_metas]


def upsert_sessions(conn, sessions):
    for s in sessions:
        existing = conn.execute(
            "SELECT model FROM sessions WHERE session_id = ?", (s["session_id"],)
        ).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO sessions
                    (session_id, project_name, first_timestamp, last_timestamp,
                     git_branch, total_input_tokens, total_output_tokens,
                     total_cache_read, total_cache_creation, model, turn_count, estimated_api_cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["session_id"], s["project_name"], s["first_timestamp"], s["last_timestamp"],
                s["git_branch"], s["total_input_tokens"], s["total_output_tokens"],
                s["total_cache_read"], s["total_cache_creation"], s["model"],
                s["turn_count"], s.get("estimated_api_cost", 0)
            ))
        else:
            existing_model = existing["model"]
            new_model = s["model"]
            model_to_set = new_model if _model_priority(new_model) > _model_priority(existing_model) else existing_model
            conn.execute("""
                UPDATE sessions SET
                    last_timestamp = MAX(last_timestamp, ?),
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    total_cache_read = total_cache_read + ?,
                    total_cache_creation = total_cache_creation + ?,
                    turn_count = turn_count + ?,
                    estimated_api_cost = estimated_api_cost + ?,
                    model = ?
                WHERE session_id = ?
            """, (
                s["last_timestamp"], s["total_input_tokens"], s["total_output_tokens"],
                s["total_cache_read"], s["total_cache_creation"], s["turn_count"],
                s.get("estimated_api_cost", 0), model_to_set, s["session_id"]
            ))


def insert_turns(conn, turns):
    conn.executemany("""
        INSERT OR IGNORE INTO turns
            (session_id, timestamp, model, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, tool_name, cwd, message_id, estimated_api_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (t["session_id"], t["timestamp"], t["model"],
         t["input_tokens"], t["output_tokens"],
         t["cache_read_tokens"], t["cache_creation_tokens"],
         t["tool_name"], t["cwd"], t.get("message_id", ""),
         t.get("estimated_api_cost", 0))
        for t in turns
    ])


def scan(verbose=False):
    conn = get_db()
    init_db(conn)

    jsonl_files = sorted(glob.glob(str(PROJECTS_DIR / "**" / "*.jsonl"), recursive=True))
    stats = {"new": 0, "updated": 0, "skipped": 0, "turns": 0, "sessions": set()}

    for filepath in jsonl_files:
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue

        row = conn.execute("SELECT mtime, lines FROM processed_files WHERE path = ?", (filepath,)).fetchone()

        if row and abs(row["mtime"] - mtime) < 0.01:
            stats["skipped"] += 1
            continue

        session_metas, turns, line_count = parse_jsonl_file(filepath)

        if turns or session_metas:
            sessions = aggregate_sessions(session_metas, turns)
            upsert_sessions(conn, sessions)
            insert_turns(conn, turns)
            for s in sessions:
                stats["sessions"].add(s["session_id"])
            stats["turns"] += len(turns)
            if row is None:
                stats["new"] += 1
            else:
                stats["updated"] += 1

        conn.execute(
            "INSERT OR REPLACE INTO processed_files (path, mtime, lines) VALUES (?, ?, ?)",
            (filepath, mtime, line_count)
        )
        conn.commit()

    if stats["new"] or stats["updated"]:
        conn.execute("""
            UPDATE sessions SET
                total_input_tokens = COALESCE((SELECT SUM(input_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_output_tokens = COALESCE((SELECT SUM(output_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_read = COALESCE((SELECT SUM(cache_read_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_creation = COALESCE((SELECT SUM(cache_creation_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                estimated_api_cost = COALESCE((SELECT SUM(estimated_api_cost) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                turn_count = COALESCE((SELECT COUNT(*) FROM turns WHERE turns.session_id = sessions.session_id), 0)
        """)
        conn.commit()

    conn.close()
    return {**stats, "sessions": len(stats["sessions"])}


if __name__ == "__main__":
    result = scan(verbose=True)
    print(result)
