"""One-off, disk-only backfill: adds avg_elevation_m to every docs/data/
index.json entry, computed purely from data already sitting in
docs/data/<id>.json. No live Garmin/Fitbit/Google/Polar API calls are made.

avg_elevation_m is a run's mean elevation (base altitude, e.g. Denver vs.
sea level) - a physiologically relevant confound distinct from in-run
hilliness (the grade/elevation panel on the run detail page covers that).
None when a run has no elevation samples at all (no GPS fix), never 0 -
consistent with the project's no-fill rule.

Same pattern as backfill_coverage.py: reads each run's already-published
elevation_m array, computes the average, writes it into the manifest -
new publishes get this from publish_run._summarize() directly, this script
is only for runs published before that field existed.

Run:
    python backfill_avg_elevation.py

Verifies and prints that every run's garmin_flags/fitbit_flags are byte-
identical before and after - a manual cadence-lock review judgment must
never be touched by an automated recompute (same check used by
backfill_coverage.py and reprocess_runs.py).
"""
import json
import os

import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _avg_elevation_m(payload: dict) -> float | None:
    values = [v for v in payload.get("elevation_m") or [] if v is not None]
    return round(sum(values) / len(values), 1) if values else None


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
            run["avg_elevation_m"] = None
            continue

        with open(run_path, "r") as f:
            payload = json.load(f)

        run["avg_elevation_m"] = _avg_elevation_m(payload)
        updated += 1

    flags_after = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest}
    mismatches = [
        activity_id for activity_id in flags_before
        if flags_before[activity_id] != flags_after.get(activity_id)
    ]

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Updated with avg_elevation_m: {updated}")
    if missing_run_json:
        print(f"Missing docs/data/<id>.json (field set to null): {missing_run_json}")
    if mismatches:
        print(f"Flag mismatches: {mismatches}")
    else:
        print("Flag mismatches: NONE - all flags preserved")


if __name__ == "__main__":
    main()
