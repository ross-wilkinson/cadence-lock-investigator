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
from datetime import date

import analysis
import main


DOCS_DATA_DIR = os.path.join("docs", "data")


def _resolve_hr_max() -> float | None:
    """Resolves the HRmax used for Active Zone Minutes: prefers the
    explicit HR_MAX env var, falls back to the rough 220-age estimate
    (age derived from BIRTH_YEAR against the current year, so it doesn't
    need updating annually) if BIRTH_YEAR is set, else None (zone-based
    fields are simply omitted).
    """
    hr_max_env = os.getenv("HR_MAX")
    if hr_max_env:
        try:
            return float(hr_max_env)
        except ValueError:
            pass

    birth_year_env = os.getenv("BIRTH_YEAR")
    if birth_year_env:
        try:
            age = date.today().year - int(birth_year_env)
            return 220 - age
        except ValueError:
            pass

    return None


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

    # Objective #4: Active Zone Minutes replication - only computed if an
    # HRmax could be resolved (explicit HR_MAX, or 220-AGE fallback).
    hr_max = _resolve_hr_max()
    total_azm_garmin = None
    total_azm_fitbit = None
    azm_overestimation_pct = None
    if hr_max is not None:
        azm_garmin = analysis.active_zone_minutes(payload["garmin_hr"], hr_max)
        azm_fitbit = analysis.active_zone_minutes(payload["fitbit_hr"], hr_max)
        total_azm_garmin = round(azm_garmin["total_azm"], 2)
        total_azm_fitbit = round(azm_fitbit["total_azm"], 2)
        azm_overestimation_pct = (
            round((total_azm_garmin - total_azm_fitbit) / total_azm_fitbit * 100, 1)
            if total_azm_fitbit else None
        )

    return {
        "id": payload["activity_id"],
        "start": payload["time"][0] if payload["time"] else None,
        "end": payload["time"][-1] if payload["time"] else None,
        "duration_seconds": len(payload["time"]),
        "avg_garmin_hr": round(sum(hr_values) / len(hr_values), 1) if hr_values else None,
        "avg_fitbit_hr": round(sum(fitbit_values) / len(fitbit_values), 1) if fitbit_values else None,
        "avg_cadence_spm": round(sum(cadence_values) / len(cadence_values), 1) if cadence_values else None,
        "flag": flag,
        "total_azm_garmin": total_azm_garmin,
        "total_azm_fitbit": total_azm_fitbit,
        "azm_overestimation_pct": azm_overestimation_pct,
    }


def write_run(payload: dict, flag: str) -> dict:
    """Writes a run payload to docs/data/<id>.json and upserts its summary
    into docs/data/index.json. Shared by the single-run publish() flow and
    sync_runs.py's bulk backfill, so there's exactly one write path.
    """
    activity_id = payload.get("activity_id")
    if not activity_id:
        raise RuntimeError("No Garmin activity_id in the fetched payload - nothing to publish.")

    os.makedirs(DOCS_DATA_DIR, exist_ok=True)

    # Objective #8: pace-bucketed HR distribution, independently for each
    # device (both keyed off Garmin's speed_mps - Fitbit/Google Health
    # supplies no independent pace signal in this pipeline). Doesn't need
    # HR_MAX, only speed_mps + an HR series, so this always populates.
    dist_garmin = analysis.hr_distribution_by_pace(payload["speed_mps"], payload["garmin_hr"])
    dist_fitbit = analysis.hr_distribution_by_pace(payload["speed_mps"], payload["fitbit_hr"])
    payload["pace_hr_distribution"] = {"garmin": dist_garmin, "fitbit": dist_fitbit}

    run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
    with open(run_path, "w") as f:
        json.dump(payload, f)

    index_path = os.path.join(DOCS_DATA_DIR, "index.json")
    manifest = []
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            manifest = json.load(f)

    entry = _summarize(payload, flag)

    # Lightweight summary stat for the gallery: how many pace buckets
    # present in both devices' distributions show a mean-HR gap > 10 bpm
    # (threshold is a starting judgment call, easy to tune later).
    shared_buckets = set(dist_garmin) & set(dist_fitbit)
    entry["total_pace_buckets"] = len(shared_buckets)
    entry["buckets_with_hr_divergence"] = sum(
        1
        for bucket in shared_buckets
        if abs(dist_fitbit[bucket]["mean"] - dist_garmin[bucket]["mean"]) > 10
    )

    manifest = [run for run in manifest if run.get("id") != activity_id]
    manifest.append(entry)
    manifest.sort(key=lambda run: run.get("start") or "", reverse=True)

    with open(index_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return entry


def publish(flag: str) -> dict:
    refresh_token = _get_refresh_token()
    access_token = main.refresh_google_token(refresh_token)
    payload = main.build_run_payload(access_token, use_garmin_cache=False)
    return write_run(payload, flag)


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
