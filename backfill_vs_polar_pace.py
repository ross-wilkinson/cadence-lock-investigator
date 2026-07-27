"""One-off, disk-only backfill for Objective #9's Summary Dashboard: adds
six new per-device-vs-Polar pace-divergence fields to docs/data/index.json
entries, computed purely from data already sitting in docs/data/<id>.json
(pace_hr_distribution, from Objective #8). No live API calls are made.

Scoped naturally, not hardcoded to specific IDs: only runs with real Polar
data have a "polar" bucket in pace_hr_distribution, so
analysis.worst_pace_divergence_vs_reference() returns None (all six fields
stay None) on every 2-way run without this script needing to know which
runs those are. Currently 3 of 35 manifest entries have polar_device_name
set, but this will also pick up any future 3-way run automatically if
re-run.

New manifest fields per run:
    garmin_vs_polar_worst_bucket / _gap_bpm / _true_hr_bpm
    fitbit_vs_polar_worst_bucket / _gap_bpm / _true_hr_bpm
        - the shared pace bucket (device vs Polar) with the largest mean-HR
          gap, that gap, and Polar's own mean HR at that bucket (the "true"
          HR - Polar H10 is the silver-standard reference device, so unlike
          recompute_dashboard_stats.py's worst_bucket_true_hr_bpm heuristic
          (Garmin-vs-Fitbit, presumed-accurate side inferred from manual
          flags), no heuristic is needed here).

This does NOT touch or replace worst_pace_bucket / worst_pace_bucket_gap_bpm
/ worst_bucket_true_hr_bpm (recompute_dashboard_stats.py) - those stay as
they are for 2-way-run analysis.

Run:
    python backfill_vs_polar_pace.py

Verifies and prints that every run's garmin_flags/fitbit_flags are byte-
identical before and after - a manual cadence-lock review judgment must
never be touched by an automated recompute (same check used by
recompute_dashboard_stats.py, backfill_coverage.py, and this session's
earlier TRIMP migration).
"""
import json
import os

import analysis
import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _vs_polar_fields_for_payload(payload: dict) -> dict:
    pace_hr_distribution = payload.get("pace_hr_distribution") or {}

    garmin_vs_polar = analysis.worst_pace_divergence_vs_reference(pace_hr_distribution, "garmin")
    fitbit_vs_polar = analysis.worst_pace_divergence_vs_reference(pace_hr_distribution, "fitbit")

    return {
        "garmin_vs_polar_worst_bucket": garmin_vs_polar["bucket"] if garmin_vs_polar else None,
        "garmin_vs_polar_gap_bpm": garmin_vs_polar["gap_bpm"] if garmin_vs_polar else None,
        "garmin_vs_polar_true_hr_bpm": garmin_vs_polar["reference_mean_bpm"] if garmin_vs_polar else None,
        "fitbit_vs_polar_worst_bucket": fitbit_vs_polar["bucket"] if fitbit_vs_polar else None,
        "fitbit_vs_polar_gap_bpm": fitbit_vs_polar["gap_bpm"] if fitbit_vs_polar else None,
        "fitbit_vs_polar_true_hr_bpm": fitbit_vs_polar["reference_mean_bpm"] if fitbit_vs_polar else None,
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
            run["garmin_vs_polar_worst_bucket"] = None
            run["garmin_vs_polar_gap_bpm"] = None
            run["garmin_vs_polar_true_hr_bpm"] = None
            run["fitbit_vs_polar_worst_bucket"] = None
            run["fitbit_vs_polar_gap_bpm"] = None
            run["fitbit_vs_polar_true_hr_bpm"] = None
            continue

        with open(run_path, "r") as f:
            payload = json.load(f)

        run.update(_vs_polar_fields_for_payload(payload))
        updated += 1

    flags_after = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest}
    mismatches = [
        activity_id for activity_id in flags_before
        if flags_before[activity_id] != flags_after.get(activity_id)
    ]

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Updated: {updated}")
    with_polar = sum(1 for run in manifest if run.get("garmin_vs_polar_worst_bucket") or run.get("fitbit_vs_polar_worst_bucket"))
    print(f"Runs with a non-null vs-Polar bucket on at least one device: {with_polar}")
    if missing_run_json:
        print(f"Missing docs/data/<id>.json (fields set to null): {missing_run_json}")
    if mismatches:
        print(f"Flag mismatches: {mismatches}")
    else:
        print("Flag mismatches: NONE - all flags preserved")


if __name__ == "__main__":
    main()
