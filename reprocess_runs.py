"""Reprocesses every already-published run: re-parses Garmin telemetry with
the fixed (no-fabrication) parser, recomputes the TRIMP training-load
comparison via the fair windowed method, and adds Garmin/Fitbit device
names + median sample rates - fields that were wrong, missing, or unfair
before this fix.

Replaces backfill_azm.py, whose premise (the stored garmin_hr is
trustworthy, only the training-load stat needs recomputing) no longer holds
- the stored garmin_hr itself needs regenerating from raw sources.

    python reprocess_runs.py --dry-run     # print before/after diffs, write nothing
    python reprocess_runs.py               # apply
"""
import argparse
import json
import os
import sys
import time

import httpx
import pandas as pd
from garminconnect import Garmin, GarminConnectTooManyRequestsError

import publish_run
from main import fetch_fitbit_hr_df, merge_telemetry, parse_garmin_metrics, refresh_google_token
from sync_runs import build_garmin_device_map

GARMIN_CACHE_DIR = ".sync_cache"


def _with_garmin_retry(fn, label: str, max_retries: int, delay_seconds: float):
    attempt = 0
    while True:
        try:
            return fn()
        except GarminConnectTooManyRequestsError:
            if attempt >= max_retries:
                raise
            delay = delay_seconds * (2 ** attempt)
            print(f"Rate limited on {label} (attempt {attempt + 1}/{max_retries}) - retrying in {delay:.1f}s...")
            time.sleep(delay)
            attempt += 1


def load_manifest() -> list:
    index_path = os.path.join(publish_run.DOCS_DATA_DIR, "index.json")
    with open(index_path, "r") as f:
        return json.load(f)


def reprocess_run(activity_id, garmin_client, google_client: httpx.Client, headers: dict, garmin_device_map: dict, max_retries: int, delay_seconds: float):
    """Rebuilds one run's payload from raw sources. Returns (new_payload,
    old_payload) - does not write anything.

    Garmin telemetry: .sync_cache/<id>.json if present (covers every run
    from the original bulk backfill), else one live get_activity_details()
    call. Garmin device name: .sync_cache/<id>_summary.json if present, else
    one live get_activity() call (never cached before now - a new need).
    Fitbit: a live fetch_fitbit_hr_df() call, using the run's own already-
    published start/end as the window - needed regardless of caching, since
    the Fitbit device name only exists in the raw API response, not in the
    already-stored simplified JSON.
    """
    details_cache_path = os.path.join(GARMIN_CACHE_DIR, f"{activity_id}.json")
    if os.path.exists(details_cache_path):
        with open(details_cache_path, "r") as f:
            details = json.load(f)
    else:
        details = _with_garmin_retry(
            lambda: garmin_client.get_activity_details(activity_id), f"{activity_id} details", max_retries, delay_seconds
        )
        os.makedirs(GARMIN_CACHE_DIR, exist_ok=True)
        with open(details_cache_path, "w") as f:
            json.dump(details, f)

    summary_cache_path = os.path.join(GARMIN_CACHE_DIR, f"{activity_id}_summary.json")
    if os.path.exists(summary_cache_path):
        with open(summary_cache_path, "r") as f:
            summary = json.load(f)
    else:
        summary = _with_garmin_retry(
            lambda: garmin_client.get_activity(activity_id), f"{activity_id} summary", max_retries, delay_seconds
        )
        os.makedirs(GARMIN_CACHE_DIR, exist_ok=True)
        with open(summary_cache_path, "w") as f:
            json.dump(summary, f)
    device_id = str(summary.get("metadataDTO", {}).get("deviceMetaDataDTO", {}).get("deviceId"))
    garmin_device_name = garmin_device_map.get(device_id)

    garmin_df = parse_garmin_metrics(details)
    garmin_df['garmin_hr'] = pd.to_numeric(garmin_df['garmin_hr'], errors='coerce')
    garmin_df['cadence_spm'] = pd.to_numeric(garmin_df['cadence_spm'], errors='coerce')

    run_path = os.path.join(publish_run.DOCS_DATA_DIR, f"{activity_id}.json")
    with open(run_path, "r") as f:
        old_payload = json.load(f)
    if not old_payload["time"]:
        raise RuntimeError(f"Run {activity_id} has no stored time samples - can't determine its window.")

    # The stored time strings are America/Los_Angeles-localized
    # ("2026-06-01 12:38:36-07:00"); Google Health's filter wants UTC ISO8601
    # with a 'Z' suffix. +1s on the end boundary since the filter is
    # exclusive (< end) and old_payload["time"][-1] is the last *inclusive*
    # sample.
    start_iso = pd.Timestamp(old_payload["time"][0]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = (pd.Timestamp(old_payload["time"][-1]).tz_convert("UTC") + pd.Timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    fitbit_df, fitbit_device_name = fetch_fitbit_hr_df(google_client, headers, start_iso, end_iso)

    new_payload = merge_telemetry(garmin_df, fitbit_df, activity_id, garmin_device_name, fitbit_device_name)
    return new_payload, old_payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args(argv)

    manifest = load_manifest()

    garmin_client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    garmin_client.login()
    garmin_device_map = build_garmin_device_map(garmin_client)

    refresh_token = publish_run._get_refresh_token()
    google_access_token = refresh_google_token(refresh_token)
    headers = {"Authorization": f"Bearer {google_access_token}"}

    done = []
    failed = []

    with httpx.Client(timeout=20.0) as google_client:
        for run in manifest:
            activity_id = run["id"]
            existing_flag = run.get("flag", "unreviewed")

            try:
                new_payload, old_payload = reprocess_run(
                    activity_id, garmin_client, google_client, headers, garmin_device_map,
                    args.max_retries, args.delay_seconds
                )
            except Exception as e:
                print(f"Failed to reprocess {activity_id}: {type(e).__name__}: {e}")
                failed.append(activity_id)
                time.sleep(args.delay_seconds)
                continue

            if args.dry_run:
                new_summary = publish_run._summarize(new_payload, existing_flag)
                old_garmin_nonnull = sum(1 for v in old_payload["garmin_hr"] if v is not None)
                new_garmin_nonnull = sum(1 for v in new_payload["garmin_hr"] if v is not None)
                print(f"--- {activity_id} ---")
                print(f"  Garmin non-null: {old_garmin_nonnull} -> {new_garmin_nonnull} (of {len(old_payload['time'])})")
                print(f"  avg_garmin_hr: {run.get('avg_garmin_hr')} -> {new_summary['avg_garmin_hr']}")
                print(
                    f"  TRIMP garmin/fitbit/diff: {run.get('total_trimp_garmin')}/{run.get('total_trimp_fitbit')}/{run.get('trimp_difference')} "
                    f"-> {new_summary['total_trimp_garmin']}/{new_summary['total_trimp_fitbit']}/{new_summary['trimp_difference']}"
                )
                print(f"  garmin_device_name: {new_summary['garmin_device_name']}, fitbit_device_name: {new_summary['fitbit_device_name']}")
                print(f"  garmin_sample_rate_hz: {new_summary['garmin_sample_rate_hz']}, fitbit_sample_rate_hz: {new_summary['fitbit_sample_rate_hz']}")
                print(f"  flag unchanged: {run.get('flag') == new_summary['flag']}")
            else:
                entry = publish_run.write_run(new_payload, existing_flag)
                print(
                    f"Reprocessed {activity_id}: flag={entry['flag']}, "
                    f"TRIMP garmin/fitbit={entry['total_trimp_garmin']}/{entry['total_trimp_fitbit']}"
                )

            done.append(activity_id)
            time.sleep(args.delay_seconds)

    print("--- DRY RUN COMPLETE (nothing written) ---" if args.dry_run else "--- REPROCESS COMPLETE ---")
    print(f"{'Would reprocess' if args.dry_run else 'Reprocessed'} ({len(done)}): {done}")
    print(f"Failed ({len(failed)}): {failed}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"reprocess_runs failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
