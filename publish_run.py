"""Publishes the latest Garmin/Fitbit run as static JSON for the GitHub Pages
site under docs/. Run locally, or by .github/workflows/publish.yml.

The "flag" is a manual cadence-lock verdict (Objective #3's real detection
heuristic doesn't exist yet) - you review the chart yourself and pass your
judgment call in:

    python publish_run.py --flag positive
"""
import argparse
import json
import os
import sqlite3
import sys

import main


DOCS_DATA_DIR = os.path.join("docs", "data")


def _get_refresh_token() -> str:
    env_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    if env_token:
        return env_token

    if os.path.exists("investigator.db"):
        conn = sqlite3.connect("investigator.db")
        cursor = conn.cursor()
        cursor.execute("SELECT refresh_token FROM auth_tokens WHERE provider = 'google'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]

    raise RuntimeError(
        "No Google refresh token available. Set GOOGLE_REFRESH_TOKEN, or run "
        "/login/google locally (which now persists a refresh_token) and retry."
    )


def _summarize(payload: dict, flag: str) -> dict:
    hr_values = [v for v in payload["garmin_hr"] if v is not None]
    fitbit_values = [v for v in payload["fitbit_hr"] if v is not None]
    cadence_values = [v for v in payload["cadence_spm"] if v is not None and v > 0]
    return {
        "id": payload["activity_id"],
        "start": payload["time"][0] if payload["time"] else None,
        "end": payload["time"][-1] if payload["time"] else None,
        "duration_seconds": len(payload["time"]),
        "avg_garmin_hr": round(sum(hr_values) / len(hr_values), 1) if hr_values else None,
        "avg_fitbit_hr": round(sum(fitbit_values) / len(fitbit_values), 1) if fitbit_values else None,
        "avg_cadence_spm": round(sum(cadence_values) / len(cadence_values), 1) if cadence_values else None,
        "flag": flag,
    }


def publish(flag: str) -> dict:
    refresh_token = _get_refresh_token()
    access_token = main.refresh_google_token(refresh_token)
    payload = main.build_run_payload(access_token, use_garmin_cache=False)

    activity_id = payload.get("activity_id")
    if not activity_id:
        raise RuntimeError("No Garmin activity_id in the fetched payload - nothing to publish.")

    os.makedirs(DOCS_DATA_DIR, exist_ok=True)

    run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
    with open(run_path, "w") as f:
        json.dump(payload, f)

    index_path = os.path.join(DOCS_DATA_DIR, "index.json")
    manifest = []
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            manifest = json.load(f)

    entry = _summarize(payload, flag)
    manifest = [run for run in manifest if run.get("id") != activity_id]
    manifest.append(entry)
    manifest.sort(key=lambda run: run.get("start") or "", reverse=True)

    with open(index_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return entry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag", choices=["positive", "negative", "unreviewed"], default="unreviewed")
    args = parser.parse_args()

    try:
        entry = publish(args.flag)
    except Exception as e:
        print(f"Publish failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Published run {entry['id']} ({entry['start']} -> {entry['end']}), flag={entry['flag']}")
