"""One-off, disk-only backfill for Objective #9's Summary Dashboard: adds
three new coverage fields to every docs/data/index.json entry, computed
purely from data already sitting in docs/data/<id>.json. No live
Garmin/Fitbit/Google/Polar API calls are made.

New manifest fields per run (see publish_run._coverage_pct / _summarize):
    garmin_coverage_pct   - % of a naive "1 sample/sec for the run's
                             duration" expectation Garmin actually delivered.
    fitbit_coverage_pct   - same, for Fitbit.
    polar_coverage_pct    - same, for Polar H10 (None on 2-way runs with no
                             Polar data - elapsed_seconds is still valid
                             there, but see the None-vs-0.0 rule below).

All three devices are judged against the same flat 1Hz assumed baseline -
not each manufacturer's own advertised sampling rate, which isn't reliably
known for Garmin/Fitbit and wouldn't be a fair comparison for Polar's 130Hz
ECG anyway. This is a deliberate product decision (see PROJECT_DIRECTIVE.md
Objective #9 discussion), not an oversight - the dashboard itself caveats
this when it lands (separate future task).

formula per device: min(100.0, non_null_count / elapsed_seconds * 100),
rounded to 1 decimal, where elapsed_seconds is the run's full merged `time`
array's last-minus-first offset (same denominator for every device on that
run). The field is None (not 0.0) only when elapsed_seconds itself can't be
computed (missing, zero, or fewer than 2 timestamps) - a device present but
delivering zero samples over a valid window is a real 0.0%, not undefined.

Run:
    python backfill_coverage.py

Verifies and prints that every run's garmin_flags/fitbit_flags are byte-
identical before and after - a manual cadence-lock review judgment must
never be touched by an automated recompute (same check used by
recompute_dashboard_stats.py and this session's earlier TRIMP migration).
"""
import json
import os

import pandas as pd

import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _coverage_fields_for_payload(payload: dict) -> dict:
    garmin_values = [v for v in payload.get("garmin_hr") or [] if v is not None]
    fitbit_values = [v for v in payload.get("fitbit_hr") or [] if v is not None]
    polar_values = [v for v in payload.get("polar_hr") or [] if v is not None]

    time = payload.get("time") or []
    ts = pd.to_datetime(time, utc=True)
    offsets = list((ts - ts[0]).total_seconds()) if len(ts) else []
    elapsed_seconds = offsets[-1] if len(offsets) >= 2 else None

    return {
        "garmin_coverage_pct": publish_run._coverage_pct(len(garmin_values), elapsed_seconds),
        "fitbit_coverage_pct": publish_run._coverage_pct(len(fitbit_values), elapsed_seconds),
        "polar_coverage_pct": publish_run._coverage_pct(len(polar_values), elapsed_seconds),
    }


def main():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    flags_before = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest}

    updated = 0
    missing_run_json = []

    for run in manifest:
        activity_id = run["id"]
        run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
        if not os.path.exists(run_path):
            missing_run_json.append(activity_id)
            run["garmin_coverage_pct"] = None
            run["fitbit_coverage_pct"] = None
            run["polar_coverage_pct"] = None
            continue

        with open(run_path, "r") as f:
            payload = json.load(f)

        run.update(_coverage_fields_for_payload(payload))
        updated += 1

    flags_after = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest}
    mismatches = [
        activity_id for activity_id in flags_before
        if flags_before[activity_id] != flags_after.get(activity_id)
    ]

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Updated with coverage fields: {updated}")
    if missing_run_json:
        print(f"Missing docs/data/<id>.json (fields set to null): {missing_run_json}")
    if mismatches:
        print(f"Flag mismatches: {mismatches}")
    else:
        print("Flag mismatches: NONE - all flags preserved")


if __name__ == "__main__":
    main()
