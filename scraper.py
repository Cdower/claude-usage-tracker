"""
Fetches claude.ai usage data via its internal API, using cookies borrowed
from the user's local browser. Supports Firefox, Chrome, Brave, and Edge
on macOS, Linux, and Windows. No login window, no headless browser.
"""

import json
import sqlite3
import shutil
import tempfile
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

from curl_cffi import requests as cffi_requests

DB_PATH = Path(__file__).parent / "usage.db"
SESSION_COOKIE_NAMES = {"sessionKey", "sessionKeyLC", "__Secure-next-auth.session-token"}

# Browsers tried via browser-cookie3, in priority order.
# Firefox is handled separately (direct SQLite read — no extra deps needed).
CHROMIUM_BROWSERS = ["chrome", "brave", "chromium", "edge"]


# ── Firefox (direct SQLite read) ──────────────────────────────────────────────

def _firefox_profiles_dirs():
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return [Path(appdata) / "Mozilla" / "Firefox" / "Profiles"] if appdata else []
    return [
        Path.home() / ".mozilla" / "firefox",
        Path.home() / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        Path.home() / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]


def _find_firefox_cookie_db():
    best, best_hits = None, 0
    for profiles_dir in _firefox_profiles_dirs():
        if not profiles_dir.exists():
            continue
        try:
            entries = list(profiles_dir.iterdir())
        except PermissionError:
            continue
        for profile_dir in entries:
            db = profile_dir / "cookies.sqlite"
            if not db.exists():
                continue
            try:
                tmp = tempfile.mktemp(suffix=".sqlite")
                shutil.copy2(db, tmp)
                conn = sqlite3.connect(tmp)
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM moz_cookies WHERE host LIKE '%claude.ai'"
                ).fetchall()}
                conn.close()
                os.unlink(tmp)
                hits = len(names & SESSION_COOKIE_NAMES)
                if hits > best_hits:
                    best_hits, best = hits, db
            except Exception:
                continue
    return best


def _load_firefox_cookies():
    db = _find_firefox_cookie_db()
    if not db:
        return {}
    tmp = tempfile.mktemp(suffix=".sqlite")
    shutil.copy2(db, tmp)
    try:
        conn = sqlite3.connect(tmp)
        rows = conn.execute(
            "SELECT name, value FROM moz_cookies WHERE host LIKE '%claude.ai'"
        ).fetchall()
        conn.close()
    finally:
        os.unlink(tmp)
    return {name: value for name, value in rows}


# ── Chromium-family (Chrome, Brave, Edge) via browser-cookie3 ─────────────────

def _load_chromium_cookies(browser_name):
    """Return {name: value} for claude.ai cookies from a Chromium-based browser."""
    try:
        import browser_cookie3
        fn = getattr(browser_cookie3, browser_name, None)
        if fn is None:
            return {}
        jar = fn(domain_name="claude.ai")
        return {c.name: c.value for c in jar}
    except Exception:
        return {}


# ── Unified cookie loader ─────────────────────────────────────────────────────

def _best_cookies():
    """
    Try each supported browser in order and return (cookies_dict, browser_name)
    for the first one that has a live claude.ai session.

    Order: Firefox → Chrome → Brave → Chromium → Edge
    """
    # Firefox first — direct SQLite, no decryption needed
    ff = _load_firefox_cookies()
    if set(ff) & SESSION_COOKIE_NAMES:
        return ff, "firefox"

    # Chromium-family browsers via browser-cookie3
    for browser in CHROMIUM_BROWSERS:
        cookies = _load_chromium_cookies(browser)
        if set(cookies) & SESSION_COOKIE_NAMES:
            return cookies, browser

    return {}, None


def is_authenticated():
    _, browser = _best_cookies()
    return browser is not None


def auth_browser():
    """Return the name of the browser providing the session, or None."""
    _, browser = _best_cookies()
    return browser


def _session():
    cookies, browser = _best_cookies()
    # Impersonate Firefox for TLS fingerprinting regardless of source browser —
    # curl_cffi doesn't have a Chrome profile that passes Cloudflare, and
    # Firefox impersonation works fine for all cookie sources.
    s = cffi_requests.Session(impersonate="firefox133")
    s.cookies.update(cookies)
    return s


def _get_org_uuid(session):
    r = session.get("https://claude.ai/api/organizations", timeout=10)
    r.raise_for_status()
    orgs = r.json()
    if not orgs:
        raise ValueError("No organizations found")
    # Use first org that has an active subscription
    return orgs[0]["uuid"]


def detect_plan(usage_data):
    """
    Infer Pro / Max5 / Max20 from the usage API response.

    The extra_usage.monthly_limit field is denominated in cents:
      2000  cents = $20  → Pro
      10000 cents = $100 → Max5
      20000 cents = $200 → Max20

    Both Pro and Max plans may have extra_usage enabled, so we cannot use
    its presence alone — we must check the monthly_limit value.

    Returns "Pro", "Max5", "Max20", or None if the value is unrecognised.
    """
    extra = usage_data.get("extra_usage")
    if not extra:
        return None
    limit = extra.get("monthly_limit") or 0
    if limit <= 2000:       # $20 → Pro
        return "Pro"
    if limit <= 10000:      # $100 → Max5
        return "Max5"
    if limit <= 20000:      # $200 → Max20
        return "Max20"
    return None             # Unknown — fall back to user selection


def fetch_usage():
    """
    Returns dict with utilization percentages and reset times from claude.ai API.
    Includes 'detected_plan' key with the inferred plan name.
    """
    if not is_authenticated():
        return {"authenticated": False, "error": "Not logged into claude.ai in browser"}

    try:
        session = _session()
        org_uuid = _get_org_uuid(session)
        r = session.get(
            f"https://claude.ai/api/organizations/{org_uuid}/usage",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        return {
            "authenticated": True,
            "org_uuid": org_uuid,
            "detected_plan": detect_plan(data),
            **data,
        }
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


def save_snapshot(usage_data):
    """Persist usage snapshot to SQLite."""
    bars = []
    label_map = {
        "five_hour": "Current session",
        "seven_day": "All models (7-day)",
        "seven_day_sonnet": "Sonnet only",
        "seven_day_opus": "Opus only",
        "seven_day_oauth_apps": "OAuth apps",
    }
    for key, label in label_map.items():
        val = usage_data.get(key)
        if val and isinstance(val, dict) and val.get("utilization") is not None:
            resets_at = val.get("resets_at")
            bars.append({
                "label": label,
                "percentage": round(val["utilization"], 1),
                "resetInfo": _fmt_reset(resets_at),
            })

    extra = usage_data.get("extra_usage")
    credit_balance = None
    if extra and extra.get("utilization") is not None:
        bars.append({
            "label": "Extra usage",
            "percentage": round(extra["utilization"], 1),
            "resetInfo": f"${extra.get('used_credits', 0):.2f} of ${extra.get('monthly_limit', 0):.2f} used",
        })
        if extra.get("monthly_limit") and extra.get("used_credits") is not None:
            credit_balance = extra["monthly_limit"] - extra["used_credits"]

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO web_usage_snapshots
            (timestamp, plan, email, bars_json, reset_date, credit_balance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        None,
        None,
        json.dumps(bars),
        None,
        credit_balance,
    ))
    conn.commit()
    conn.close()
    return bars


def _fmt_reset(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = dt - now
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        if delta.total_seconds() < 0:
            return "Resetting soon"
        if hours >= 24:
            return f"Resets in {hours // 24}d {hours % 24}h"
        return f"Resets in {hours}h {mins}m"
    except Exception:
        return ""


def collect():
    usage = fetch_usage()
    if not usage.get("authenticated"):
        return usage
    bars = save_snapshot(usage)
    return {**usage, "bars_summary": bars}


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
