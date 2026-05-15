import anthropic
import sqlite3
from datetime import datetime
import os
import json

DB_PATH = "usage.db"

def collect_usage_data():
    """Fetch usage data from Anthropic Admin API and store in database."""
    admin_api_key = os.getenv("ANTHROPIC_ADMIN_API_KEY")

    if not admin_api_key:
        raise ValueError("ANTHROPIC_ADMIN_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=admin_api_key)

    try:
        response = client.beta.admin.cost_report.get_cost_report()

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        timestamp = datetime.now().isoformat()

        total_tokens = response.usage.get("total_input_tokens", 0) + response.usage.get("total_output_tokens", 0)
        total_cost = response.cost.get("total_usd", 0)

        c.execute("""
            INSERT INTO usage (timestamp, total_tokens, input_tokens, output_tokens, total_cost, model)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            total_tokens,
            response.usage.get("total_input_tokens", 0),
            response.usage.get("total_output_tokens", 0),
            total_cost,
            "claude-3"
        ))

        conn.commit()
        conn.close()

        return {
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "timestamp": timestamp
        }
    except Exception as e:
        print(f"Error fetching usage data: {e}")
        raise

if __name__ == "__main__":
    result = collect_usage_data()
    print(f"Usage data collected: {result}")
