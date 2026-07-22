"""Bulk backfill + incremental sync of published runs.

Lists every Garmin running activity and every Google Health RUNNING session
(Fitbit HR data flows through Google Health) in a date range, matches each
Garmin activity to its corresponding Google Health session by time-window
correlation, skips anything already published, and publishes the rest via
publish_run.write_run().

No separate cursor/state file is needed: docs/data/index.json (keyed by
Garmin activityId) already IS the "already done" ledger, so re-running this
script later only picks up runs not yet published.

    python sync_runs.py --dry-run                                    # safe: list + match only, writes nothing
    python sync_runs.py                                              # full backfill from 2026-01-01 to today
    python sync_runs.py --start-date 2026-07-01 --end-date 2026-07-21
"""
import argparse
import json
import os
import sys
import time
from datetime import date

import httpx
import pandas as pd
from garminconnect import Garmin, GarminConnectTooManyRequestsError

import publish_run
# Imported by name, not `import main`, because this module's own CLI entry
# point is also called `main()` - `import main` at module scope would get
# permanently shadowed the moment `def main(...)` is defined below (both
# bind the same global name), silently breaking every main.xxx call in this
# file, not just the CLI.
from main import fetch_fitbit_hr_df, merge_telemetry, parse_garmin_metrics, refresh_google_token


CACHE_DIR = ".sync_cache"
GOOGLE_EXERCISE_URL = "https://health.googleapis.com/v4/users/me/dataTypes/exercise/dataPoints"


class NoFitbitDataError(Exception):
    """Raised when a matched Google Health session has zero Fitbit-platform
    heart-rate samples anywhere in its window - a real historical data gap
    (Fitbit wasn't syncing/worn), not a fetch or matching failure. Callers
    treat this as a skip, not a publish failure.
    """
    pass


def match_activities(garmin_activities: list[dict], google_sessions: list[dict], tolerance_minutes: float = 15.0) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Matches Garmin activities to Google Health RUNNING sessions by time
    correlation. Pure function, no I/O.

    Algorithm: process Garmin activities sorted by start time ascending; for
    each, prefer a still-unclaimed Google session whose window actually
    overlaps the Garmin window; if none overlap, fall back to the unclaimed
    session whose start time is within tolerance_minutes (closest wins); if
    found, claim it (remove from the remaining pool) and record the pair, so
    two back-to-back activities can never both claim the same session; if
    not found, the Garmin activity goes to the unmatched list.

    Returns (matched pairs, unmatched Garmin activities, unmatched Google
    sessions - i.e. sessions never claimed by any activity).
    """
    def garmin_window(activity):
        start = pd.Timestamp(activity["startTimeGMT"], tz="UTC")
        end = start + pd.Timedelta(seconds=activity["duration"])
        return start, end

    def google_window(session):
        interval = session.get("exercise", {}).get("interval", {})
        start = pd.Timestamp(interval["startTime"])
        end = pd.Timestamp(interval["endTime"])
        return start, end

    sorted_activities = sorted(garmin_activities, key=lambda a: a["startTimeGMT"])
    remaining = list(google_sessions)

    matched = []
    unmatched_garmin = []

    for activity in sorted_activities:
        g_start, g_end = garmin_window(activity)

        overlap_candidates = []
        near_candidates = []  # (delta_minutes, session)
        for session in remaining:
            s_start, s_end = google_window(session)
            if s_start <= g_end and g_start <= s_end:
                overlap_candidates.append(session)
            else:
                delta_minutes = abs((s_start - g_start).total_seconds()) / 60.0
                if delta_minutes <= tolerance_minutes:
                    near_candidates.append((delta_minutes, session))

        chosen = None
        if overlap_candidates:
            chosen = overlap_candidates[0]
        elif near_candidates:
            near_candidates.sort(key=lambda pair: pair[0])
            chosen = near_candidates[0][1]

        if chosen is not None:
            remaining.remove(chosen)
            matched.append((activity, chosen))
        else:
            unmatched_garmin.append(activity)

    return matched, unmatched_garmin, remaining


def list_garmin_running_activities(client, start_date: str, end_date: str) -> list[dict]:
    """Lists all Garmin activities in [start_date, end_date] and filters
    client-side to running activities - doesn't trust the server-side
    activitytype enum for correctness across treadmill/trail/indoor subtypes.
    """
    activities = client.get_activities_by_date(start_date, end_date)
    return [
        activity for activity in activities
        if "running" in activity.get("activityType", {}).get("typeKey", "")
    ]


def list_google_running_sessions(client: httpx.Client, headers: dict, start_date: str, end_date: str) -> list[dict]:
    """Lists Google Health RUNNING exercise sessions in [start_date, end_date].

    Tries a server-side filtered request first; falls back to an unfiltered
    fetch + client-side filtering if the filter is rejected or the response
    doesn't look like the expected shape (a year of one person's sessions is
    small, so the fallback is always cheap and viable). Loops on
    nextPageToken if present either way, so a wide range can't silently
    truncate to one page.
    """
    filter_str = (
        f'exercise.interval.start_time >= "{start_date}T00:00:00Z" AND '
        f'exercise.interval.start_time < "{end_date}T23:59:59Z"'
    )

    def fetch_all(use_filter: bool):
        points = []
        page_token = None
        while True:
            params = {"pageSize": 1000}
            if use_filter:
                params["filter"] = filter_str
            if page_token:
                params["pageToken"] = page_token

            res = client.get(GOOGLE_EXERCISE_URL, headers=headers, params=params)
            if res.status_code != 200:
                return None, res

            try:
                body = res.json()
            except ValueError:
                return None, res

            if "dataPoints" not in body:
                return None, res

            points.extend(body.get("dataPoints", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                break

        return points, None

    data_points, failed_res = fetch_all(use_filter=True)
    used_filter = True
    if data_points is None:
        status = failed_res.status_code if failed_res is not None else "?"
        print(f"DEBUG: Google Health filtered exercise request failed/malformed (status {status}) - falling back to unfiltered fetch.")
        used_filter = False
        data_points, failed_res = fetch_all(use_filter=False)
        if data_points is None:
            raise RuntimeError(f"Google Health exercise API returned {failed_res.status_code}: {failed_res.text}")

    sessions = [dp for dp in data_points if dp.get("exercise", {}).get("exerciseType") == "RUNNING"]

    if not used_filter:
        sessions = [
            dp for dp in sessions
            if start_date <= dp.get("exercise", {}).get("interval", {}).get("startTime", "")[:10] <= end_date
        ]

    return sessions


def already_published_ids() -> set:
    """Reads docs/data/index.json (the existing publish ledger) and returns
    the set of Garmin activity ids already present. Empty set if the file
    doesn't exist yet.
    """
    index_path = os.path.join(publish_run.DOCS_DATA_DIR, "index.json")
    if not os.path.exists(index_path):
        return set()
    with open(index_path, "r") as f:
        manifest = json.load(f)
    return {run["id"] for run in manifest if "id" in run}


def fetch_and_publish_pair(garmin_client, google_client: httpx.Client, headers: dict, garmin_activity: dict, google_session: dict, cache_dir: str) -> dict:
    """Fetches Garmin activity details (using a local cache to survive an
    interrupted/rate-limited run without re-fetching) and the matched Google
    session's Fitbit HR window, merges them, and publishes via the same
    write path publish_run.py's single-run flow uses.
    """
    activity_id = garmin_activity["activityId"]
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{activity_id}.json")

    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            details = json.load(f)
    else:
        details = garmin_client.get_activity_details(activity_id)
        with open(cache_path, "w") as f:
            json.dump(details, f)

    garmin_df = parse_garmin_metrics(details)
    garmin_df['garmin_hr'] = pd.to_numeric(garmin_df['garmin_hr'], errors='coerce')
    garmin_df['cadence_spm'] = pd.to_numeric(garmin_df['cadence_spm'], errors='coerce')

    interval = google_session.get("exercise", {}).get("interval", {})
    start_t = interval.get("startTime")
    end_t = interval.get("endTime")
    fitbit_df = fetch_fitbit_hr_df(google_client, headers, start_t, end_t)

    if fitbit_df.empty:
        raise NoFitbitDataError(
            f"No Fitbit-platform heart-rate data found for activity {activity_id} "
            f"in window {start_t} -> {end_t} (matched session was likely a "
            f"Health Connect / Garmin-sourced duplicate, not a real Fitbit sync)."
        )

    payload = merge_telemetry(garmin_df, fitbit_df, activity_id)
    return publish_run.write_run(payload, "unreviewed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tolerance-minutes", type=float, default=15.0)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args(argv)

    garmin_client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    garmin_client.login()

    refresh_token = publish_run._get_refresh_token()
    google_access_token = refresh_google_token(refresh_token)
    headers = {"Authorization": f"Bearer {google_access_token}"}

    with httpx.Client(timeout=20.0) as google_client:
        print(f"Listing Garmin running activities {args.start_date} -> {args.end_date}...")
        garmin_activities = list_garmin_running_activities(garmin_client, args.start_date, args.end_date)
        print(f"Found {len(garmin_activities)} Garmin running activities.")

        print(f"Listing Google Health RUNNING sessions {args.start_date} -> {args.end_date}...")
        google_sessions = list_google_running_sessions(google_client, headers, args.start_date, args.end_date)
        print(f"Found {len(google_sessions)} Google Health RUNNING sessions.")

        matched, unmatched_garmin, unmatched_google = match_activities(
            garmin_activities, google_sessions, tolerance_minutes=args.tolerance_minutes
        )

        published_ids = already_published_ids()
        to_publish = []
        skipped_already_published = []
        for activity, session in matched:
            if activity["activityId"] in published_ids:
                skipped_already_published.append(activity["activityId"])
            else:
                to_publish.append((activity, session))

        if args.dry_run:
            print("--- DRY RUN SUMMARY (nothing fetched or written) ---")
            print(f"Garmin activities found:      {len(garmin_activities)}")
            print(f"Google sessions found:        {len(google_sessions)}")
            print(f"Matched pairs:                {len(matched)}")
            print(f"Already published (skipped):  {len(skipped_already_published)}")
            print(f"Would publish:                {len(to_publish)}")
            print(f"Unmatched Garmin activities:  {len(unmatched_garmin)}")
            print(f"Unmatched Google sessions:    {len(unmatched_google)}")
            return

        published = []
        failed = []
        skipped_no_fitbit = []
        for activity, session in to_publish:
            activity_id = activity["activityId"]
            attempt = 0
            while True:
                try:
                    entry = fetch_and_publish_pair(garmin_client, google_client, headers, activity, session, CACHE_DIR)
                    published.append(activity_id)
                    print(f"Published {activity_id} ({entry['start']} -> {entry['end']}), flag={entry['flag']}")
                    break
                except GarminConnectTooManyRequestsError as e:
                    if attempt < args.max_retries:
                        delay = args.delay_seconds * (2 ** attempt)
                        print(f"Rate limited on {activity_id} (attempt {attempt + 1}/{args.max_retries}) - retrying in {delay:.1f}s...")
                        time.sleep(delay)
                        attempt += 1
                        continue
                    print(f"Giving up on {activity_id} after {args.max_retries} retries: {e}")
                    failed.append((activity_id, f"{type(e).__name__}: {e}"))
                    break
                except NoFitbitDataError as e:
                    print(f"Skipping {activity_id}: {e}")
                    skipped_no_fitbit.append(activity_id)
                    break
                except Exception as e:
                    print(f"Failed to publish {activity_id}: {type(e).__name__}: {e}")
                    failed.append((activity_id, f"{type(e).__name__}: {e}"))
                    break
            time.sleep(args.delay_seconds)

        print("--- SYNC COMPLETE ---")
        print(f"Published ({len(published)}): {published}")
        print(f"Failed ({len(failed)}): {failed}")
        print(f"Skipped, no Fitbit data ({len(skipped_no_fitbit)}): {skipped_no_fitbit}")
        print(f"Skipped already-published ({len(skipped_already_published)}): {skipped_already_published}")
        print(f"Unmatched Garmin ({len(unmatched_garmin)}): {[a.get('activityId') for a in unmatched_garmin]}")
        print(f"Unmatched Google ({len(unmatched_google)})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"sync_runs failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
