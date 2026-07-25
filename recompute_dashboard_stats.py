"""One-off, disk-only backfill for Objective #9's Summary Dashboard: adds
three new fields to every docs/data/index.json entry, computed purely from
data already sitting in docs/data/<id>.json (pace_hr_distribution, from
Objective #8). No live Garmin/Fitbit/Google API calls are made.

New manifest fields per run:
    worst_pace_bucket           - label of the shared pace bucket with the
                                   largest |mean_fitbit - mean_garmin| gap
                                   (analysis.worst_pace_divergence()).
    worst_pace_bucket_gap_bpm   - that gap, in bpm.
    worst_bucket_true_hr_bpm    - at that same bucket, the mean HR of
                                   whichever device is NOT flagged positive
                                   while the other IS (garmin_flag positive
                                   + fitbit_flag negative -> Fitbit's mean;
                                   fitbit_flag positive + garmin_flag
                                   negative -> Garmin's mean). None when
                                   both devices are positive, both negative,
                                   or either is unreviewed, since there's no
                                   presumed-accurate side to pick. This is an
                                   approximation bounded by manual review
                                   accuracy, NOT a true per-instant
                                   classification - the dashboard must
                                   caveat it as such (Objective #3's real
                                   per-instant detector doesn't exist yet).

Run:
    python recompute_dashboard_stats.py

Verifies and prints that every run's garmin_flag/fitbit_flag are byte-
identical before and after - a manual cadence-lock review judgment must
never be touched by an automated recompute (same check used for this
session's earlier TRIMP migration).
"""
import json
import os

import analysis
import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _non_suspect_true_hr(garmin_flag: str, fitbit_flag: str, worst_bucket: str, pace_hr_distribution: dict):
    """Returns the non-suspect device's mean HR at worst_bucket when exactly
    one device is flagged positive and the other negative, else None. See
    module docstring.
    """
    if worst_bucket is None:
        return None

    garmin_positive = bool(garmin_flag) and garmin_flag.startswith("positive")
    fitbit_positive = bool(fitbit_flag) and fitbit_flag.startswith("positive")

    if garmin_positive and fitbit_flag == "negative":
        non_suspect = "fitbit"
    elif fitbit_positive and garmin_flag == "negative":
        non_suspect = "garmin"
    else:
        # Both positive, both negative, or either unreviewed: no
        # presumed-accurate side.
        return None

    bucket_stats = pace_hr_distribution.get(non_suspect, {}).get(worst_bucket)
    if bucket_stats is None:
        return None
    return bucket_stats.get("mean")


def main():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    flags_before = {run["id"]: (run.get("garmin_flag"), run.get("fitbit_flag")) for run in manifest}

    updated = 0
    missing_run_json = []

    for run in manifest:
        activity_id = run["id"]
        run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
        if not os.path.exists(run_path):
            missing_run_json.append(activity_id)
            run["worst_pace_bucket"] = None
            run["worst_pace_bucket_gap_bpm"] = None
            run["worst_bucket_true_hr_bpm"] = None
            continue

        with open(run_path, "r") as f:
            payload = json.load(f)

        pace_hr_distribution = payload.get("pace_hr_distribution") or {}
        divergence = analysis.worst_pace_divergence(pace_hr_distribution)

        if divergence is None:
            run["worst_pace_bucket"] = None
            run["worst_pace_bucket_gap_bpm"] = None
            run["worst_bucket_true_hr_bpm"] = None
        else:
            worst_bucket = divergence["bucket"]
            run["worst_pace_bucket"] = worst_bucket
            run["worst_pace_bucket_gap_bpm"] = divergence["gap_bpm"]
            true_hr = _non_suspect_true_hr(run.get("garmin_flag"), run.get("fitbit_flag"), worst_bucket, pace_hr_distribution)
            run["worst_bucket_true_hr_bpm"] = round(true_hr, 1) if true_hr is not None else None

        updated += 1

    flags_after = {run["id"]: (run.get("garmin_flag"), run.get("fitbit_flag")) for run in manifest}
    mismatches = [
        activity_id for activity_id in flags_before
        if flags_before[activity_id] != flags_after.get(activity_id)
    ]

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Updated with worst-pace-divergence fields: {updated}")
    if missing_run_json:
        print(f"Missing docs/data/<id>.json (fields set to null): {missing_run_json}")
    if mismatches:
        print(f"Flag mismatches: {mismatches}")
    else:
        print("Flag mismatches: NONE - all flags preserved")


if __name__ == "__main__":
    main()
