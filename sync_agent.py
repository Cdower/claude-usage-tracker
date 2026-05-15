#!/usr/bin/env python3
"""
sync_agent.py — Reads local Claude Code JSONL logs and pushes new data to
the hub machine running the claude-usage-tracker Flask app.

Zero external dependencies — stdlib only. Copy this single file to any
machine that runs Claude Code.

Setup:
  1. Copy this file to the remote machine (e.g. ~/claude-usage-tracker/)
  2. Create agent_config.json next to it (see CONFIG below)
  3. Run once manually to verify: python3 sync_agent.py
  4. Add to crontab: */15 * * * * /usr/bin/python3 ~/claude-usage-tracker/sync_agent.py

agent_config.json:
  {
    "hub_url": "http://192.168.1.10:5000",
    "token": "<paste REMOTE_TOKEN from hub .env>",
    "machine_name": "laptop"
  }
"""

import json
import os
import glob
import sqlite3
import socket
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "agent_config.json"
STATE_PATH  = Path(__file__).parent / ".agent_state.json"

PROJECTS_DIR = Path.home() / ".claude" / "projects"

MODEL_PRICING = {
    "claude-opus-4":    (15.00, 75.00),
    "claude-sonnet-4":  (3.00,  15.00),
    "claude-haiku-4":   (0.80,  4.00),
}

MODEL_PRIORITY = {"opus": 3, "sonnet": 2, "haiku": 1}


def load_config():
    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found. Create it with hub_url, token, machine_name.")
        raise SystemExit(1)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for key in ("hub_url", "token", "machine_name"):
        if not cfg.get(key):
            print(f"ERROR: '{key}' missing from {CONFIG_PATH}")
            raise SystemExit(1)
    return cfg


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"processed": {}}   # {filepath: {"mtime": float, "lines": int}}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── JSONL parsing (mirrors scanner.py, stdlib only) ───────────────────────────

def _model_priority(model):
    if not model:
        return 0
    m = model.lower()
    for kw, p in MODEL_PRIORITY.items():
        if kw in m:
            return p
    return 0


def _estimate_cost(model, inp, out, cache_read, cache_creation):
    rates = (3.00, 15.00)
    if model:
        ml = model.lower()
        for prefix, r in MODEL_PRICING.items():
            if prefix in ml:
                rates = r
                break
    return (
        (inp / 1_000_000) * rates[0] +
        (out / 1_000_000) * rates[1] +
        (cache_read / 1_000_000) * (rates[0] * 0.1) +
        (cache_creation / 1_000_000) * (rates[0] * 1.25)
    )


def _project_name(cwd):
    if not cwd:
        return "unknown"
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1] or "unknown"


def parse_jsonl(filepath, skip_lines=0):
    """Parse a JSONL file, skipping already-processed lines. Returns (session_metas, turns, line_count)."""
    seen_messages = {}
    turns_no_id = []
    session_meta = {}
    line_count = 0

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for line_count, line in enumerate(f, 1):
                if line_count <= skip_lines:
                    continue
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
                        "project_name": _project_name(cwd),
                        "first_timestamp": timestamp,
                        "last_timestamp": timestamp,
                        "git_branch": record.get("gitBranch", ""),
                        "model": None,
                    }
                else:
                    m = session_meta[session_id]
                    if timestamp:
                        if not m["first_timestamp"] or timestamp < m["first_timestamp"]:
                            m["first_timestamp"] = timestamp
                        if not m["last_timestamp"] or timestamp > m["last_timestamp"]:
                            m["last_timestamp"] = timestamp

                if rtype == "assistant":
                    msg     = record.get("message", {})
                    usage   = msg.get("usage", {})
                    model   = msg.get("model", "")
                    msg_id  = msg.get("id", "")

                    inp  = usage.get("input_tokens", 0) or 0
                    out  = usage.get("output_tokens", 0) or 0
                    cr   = usage.get("cache_read_input_tokens", 0) or 0
                    cc   = usage.get("cache_creation_input_tokens", 0) or 0

                    if inp + out + cr + cc == 0:
                        continue

                    tool_name = None
                    for item in msg.get("content", []):
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            tool_name = item.get("name")
                            break

                    if model:
                        session_meta[session_id]["model"] = model

                    turn = {
                        "session_id": session_id,
                        "timestamp": timestamp,
                        "model": model,
                        "input_tokens": inp,
                        "output_tokens": out,
                        "cache_read_tokens": cr,
                        "cache_creation_tokens": cc,
                        "tool_name": tool_name,
                        "cwd": cwd,
                        "message_id": msg_id,
                        "estimated_api_cost": _estimate_cost(model, inp, out, cr, cc),
                    }
                    if msg_id:
                        seen_messages[msg_id] = turn
                    else:
                        turns_no_id.append(turn)

    except Exception as e:
        print(f"  Warning reading {filepath}: {e}")

    turns = turns_no_id + list(seen_messages.values())
    return list(session_meta.values()), turns, line_count


def aggregate(session_metas, turns):
    stats = defaultdict(lambda: {
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_creation": 0,
        "turn_count": 0, "estimated_api_cost": 0.0, "model": None,
    })
    model_counts = defaultdict(Counter)

    for t in turns:
        s = stats[t["session_id"]]
        s["total_input_tokens"]  += t["input_tokens"]
        s["total_output_tokens"] += t["output_tokens"]
        s["total_cache_read"]    += t["cache_read_tokens"]
        s["total_cache_creation"]+= t["cache_creation_tokens"]
        s["turn_count"]          += 1
        s["estimated_api_cost"]  += t["estimated_api_cost"]
        if t["model"]:
            model_counts[t["session_id"]][t["model"]] += 1

    for sid, counts in model_counts.items():
        if counts:
            stats[sid]["model"] = counts.most_common(1)[0][0]

    return [{**meta, **stats[meta["session_id"]]} for meta in session_metas]


# ── Push to hub ───────────────────────────────────────────────────────────────

def push(hub_url, token, machine_name, sessions, turns):
    payload = json.dumps({
        "machine": machine_name,
        "sessions": sessions,
        "turns": turns,
    }).encode()

    req = urllib.request.Request(
        f"{hub_url.rstrip('/')}/api/remote/push",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    cfg   = load_config()
    state = load_state()

    hub_url      = cfg["hub_url"]
    token        = cfg["token"]
    machine_name = cfg["machine_name"]

    jsonl_files = sorted(glob.glob(str(PROJECTS_DIR / "**" / "*.jsonl"), recursive=True))
    if not jsonl_files:
        print(f"No JSONL files found in {PROJECTS_DIR}")
        return

    all_sessions = {}   # session_id -> merged session dict
    all_turns    = []

    changed = False
    for filepath in jsonl_files:
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue

        prev = state["processed"].get(filepath, {})
        if prev.get("mtime") and abs(prev["mtime"] - mtime) < 0.01:
            continue  # unchanged

        skip_lines = prev.get("lines", 0) if prev else 0
        session_metas, turns, line_count = parse_jsonl(filepath, skip_lines=skip_lines)

        if turns or session_metas:
            sessions = aggregate(session_metas, turns)
            for s in sessions:
                sid = s["session_id"]
                if sid not in all_sessions:
                    all_sessions[sid] = s
                else:
                    # Merge: accumulate tokens
                    existing = all_sessions[sid]
                    existing["total_input_tokens"]   += s["total_input_tokens"]
                    existing["total_output_tokens"]  += s["total_output_tokens"]
                    existing["total_cache_read"]     += s["total_cache_read"]
                    existing["total_cache_creation"] += s["total_cache_creation"]
                    existing["turn_count"]           += s["turn_count"]
                    existing["estimated_api_cost"]   += s["estimated_api_cost"]
                    if s.get("last_timestamp", "") > existing.get("last_timestamp", ""):
                        existing["last_timestamp"] = s["last_timestamp"]
                    if _model_priority(s.get("model")) > _model_priority(existing.get("model")):
                        existing["model"] = s["model"]
            all_turns.extend(turns)

        state["processed"][filepath] = {"mtime": mtime, "lines": line_count}
        changed = True

    if not changed:
        print("Nothing new to push.")
        return

    sessions_list = list(all_sessions.values())
    print(f"Pushing {len(sessions_list)} sessions, {len(all_turns)} turns → {hub_url} ...")

    try:
        result = push(hub_url, token, machine_name, sessions_list, all_turns)
        print(f"✓ {result}")
        save_state(state)
    except Exception as e:
        print(f"✗ Push failed: {e}")
        # Don't save state so we retry next run


if __name__ == "__main__":
    main()
