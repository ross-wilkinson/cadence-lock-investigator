"""Publishes a Garmin + Fitbit + Polar H10 three-way comparison run - the
first run type where a silver-standard reference device (the H10 chest
strap) was worn alongside the two wrist devices under test.

Pulls Garmin (via garminconnect) and Fitbit (via Google Health) exactly like
sync_runs.py's single-pair flow, then additionally looks up the matching
Polar exercise via AccessLink (main.list_polar_exercises) - matched to the
Garmin activity by time-window overlap, tolerant of the H10 recording
starting/ending well before/after Garmin+Fitbit (this project's protocol has
the H10 running solo during the walk to/from the actual test site - see
PROJECT_DIRECTIVE.md's v1 protocol). If no overlapping Polar exercise is
found, publishes the Garmin+Fitbit pair anyway with polar_hr all-null,
same as any other run.

Always operates on the single latest Garmin running activity not yet
published - Polar's AccessLink API has no historical range query (only a
rolling recent window, see list_polar_exercises' docstring), so unlike
sync_runs.py this isn't built as a bulk backfill.

    python publish_reference_run.py --flag unreviewed
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta, timezone

import httpx
import pandas as pd
from garminconnect import Garmin

import main
import publish_run
import sync_runs


def find_matching_polar_exercise(polar_access_token: str, g_start: pd.Timestamp, g_end: pd.Timestamp, tolerance_minutes: float = 60.0):
    """Returns the Polar exercise (raw dict) whose window overlaps
    [g_start, g_end], or - if none overlap - the closest one starting
    within tolerance_minutes, else None. A wide default tolerance because
    the H10 is expected to run for a walk-to-the-site lead-in/lead-out
    around the shorter Garmin+Fitbit window, not run in lockstep with it.
    """
    exercises = main.list_polar_exercises(polar_access_token, samples=False)
    best = None
    best_delta = None
    for ex in exercises:
        start_time = ex.get("start_time")
        offset_min = ex.get("start_time_utc_offset")
        if not start_time or offset_min is None:
            continue
        ex_start = pd.to_datetime(start_time).tz_localize(timezone(timedelta(minutes=offset_min)))
        duration_iso = ex.get("duration") or "PT0S"
        ex_seconds = pd.Timedelta(duration_iso).total_seconds()
        ex_end = ex_start + pd.Timedelta(seconds=ex_seconds)

        if ex_start <= g_end and g_start <= ex_end:
            return ex  # overlap -> immediate match, no ambiguity to resolve

        delta_minutes = min(
            abs((ex_start - g_start).total_seconds()),
            abs((ex_end - g_end).total_seconds()),
        ) / 60.0
        if delta_minutes <= tolerance_minutes and (best_delta is None or delta_minutes < best_delta):
            best, best_delta = ex, delta_minutes

    return best


def build_latest_reference_run_payload(garmin_client, polar_access_token: str, google_client: httpx.Client, google_headers: dict, garmin_device_map: dict) -> dict:
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=7)).isoformat()

    garmin_activities = sync_runs.list_garmin_running_activities(garmin_client, start_date, end_date)
    if not garmin_activities:
        raise RuntimeError(f"No Garmin running activities found in the last 7 days ({start_date} -> {end_date}).")

    published_ids = sync_runs.already_published_ids()
    candidates = [a for a in garmin_activities if a["activityId"] not in published_ids]
    if not candidates:
        raise RuntimeError("Every recent Garmin running activity is already published - nothing new to do.")
    garmin_activity = sorted(candidates, key=lambda a: a["startTimeGMT"])[-1]

    google_sessions = sync_runs.list_google_running_sessions(google_client, google_headers, start_date, end_date)
    matched, _, _ = sync_runs.match_activities([garmin_activity], google_sessions)
    if not matched:
        raise RuntimeError(f"No matching Google Health (Fitbit) session found for Garmin activity {garmin_activity['activityId']}.")
    _, google_session = matched[0]

    activity_id = garmin_activity["activityId"]
    os.makedirs(sync_runs.CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(sync_runs.CACHE_DIR, f"{activity_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            details = json.load(f)
    else:
        details = garmin_client.get_activity_details(activity_id)
        with open(cache_path, "w") as f:
            json.dump(details, f)

    garmin_df = main.parse_garmin_metrics(details)
    garmin_df['garmin_hr'] = pd.to_numeric(garmin_df['garmin_hr'], errors='coerce')
    garmin_df['cadence_spm'] = pd.to_numeric(garmin_df['cadence_spm'], errors='coerce')
    garmin_device_name = garmin_device_map.get(str(garmin_activity.get('deviceId')))

    interval = google_session.get("exercise", {}).get("interval", {})
    start_t = interval.get("startTime")
    end_t = interval.get("endTime")
    fitbit_df, fitbit_device_name = main.fetch_fitbit_hr_df(google_client, google_headers, start_t, end_t)
    if fitbit_df.empty:
        raise sync_runs.NoFitbitDataError(f"No Fitbit-platform heart-rate data found for activity {activity_id} in window {start_t} -> {end_t}.")

    g_start = pd.Timestamp(garmin_activity["startTimeGMT"], tz="UTC")
    g_end = g_start + pd.Timedelta(seconds=garmin_activity["duration"])
    polar_exercise = find_matching_polar_exercise(polar_access_token, g_start, g_end)

    polar_df, polar_device_name = pd.DataFrame(), None
    if polar_exercise is not None:
        polar_df, polar_device_name = main.fetch_polar_exercise_samples(polar_access_token, polar_exercise["id"])
        print(f"Matched Polar exercise {polar_exercise['id']} (device={polar_device_name}, {len(polar_df)} HR samples).")
    else:
        print("No matching Polar exercise found within tolerance - publishing Garmin+Fitbit only.")

    payload = main.merge_telemetry(garmin_df, fitbit_df, activity_id, garmin_device_name, fitbit_device_name, polar_df, polar_device_name)
    payload = main.enrich_with_weather(payload)
    return payload


def main_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--flag",
        choices=["positive_garmin", "positive_fitbit", "positive_both", "negative", "unreviewed"],
        default="unreviewed",
    )
    args = parser.parse_args(argv)

    garmin_client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    garmin_client.login()
    garmin_device_map = sync_runs.build_garmin_device_map(garmin_client)

    refresh_token = publish_run._get_refresh_token()
    google_access_token = main.refresh_google_token(refresh_token)
    google_headers = {"Authorization": f"Bearer {google_access_token}"}

    polar_access_token = main.get_token("polar")

    with httpx.Client(timeout=20.0) as google_client:
        payload = build_latest_reference_run_payload(garmin_client, polar_access_token, google_client, google_headers, garmin_device_map)

    entry = publish_run.write_run(payload, args.flag)
    print(
        f"Published run {entry['id']} ({entry['start']} -> {entry['end']}), flag={entry['flag']}\n"
        f"  Garmin: {entry['garmin_device_name']} (avg {entry.get('avg_garmin_hr')} bpm)\n"
        f"  Fitbit: {entry['fitbit_device_name']} (avg {entry.get('avg_fitbit_hr')} bpm)\n"
        f"  Polar:  {entry.get('polar_device_name')} (avg {entry.get('avg_polar_hr')} bpm)"
    )
    return entry


if __name__ == "__main__":
    try:
        main_cli()
    except Exception as e:
        print(f"publish_reference_run failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
