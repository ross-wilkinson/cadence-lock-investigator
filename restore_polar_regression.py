"""One-off recovery script for a 2026-07-28 regression: an earlier version
of reprocess_runs.py rebuilt every run's payload via merge_telemetry(garmin_df,
fitbit_df, ...) with no polar_df argument at all, silently null-wiping
polar_hr/polar_device_name on every 3-way reference run it touched (3 runs:
23718324572, 23728858378, 23740991626 - the H10-reference set referenced in
N_OF_1_STUDY_PROTOCOL.md). reprocess_runs.py itself is now fixed (carries
Polar data forward from the pre-existing payload, same pattern as firmware).

This script repairs the already-damaged docs/data/*.json files. Recovery
source: git HEAD, which still has the last-committed (pre-regression)
polar_hr for every affected run - the regression was never committed. For
each run where HEAD has real Polar data but the current working copy
doesn't:
  1. Build a time -> polar_hr map from HEAD's payload (matched by exact
     timestamp string, not array position - safe even if the post-
     regression payload's time index shifted slightly from HEAD's, e.g.
     due to a live Fitbit re-fetch picking up different samples).
  2. Splice polar_hr + polar_device_name into the CURRENT payload (keeping
     everything the regression run's reprocess legitimately added -
     elevation_m/distance_m/grade_adjusted_speed_mps/temperature_c/
     humidity_pct - not reverting to HEAD wholesale).
  3. Re-run publish_run.write_run() so every polar-dependent summary field
     (avg_polar_hr, total_trimp_polar, trimp_difference_*_polar,
     polar_coverage_pct, polar_sample_rate_hz, garmin_vs_polar_*,
     fitbit_vs_polar_*, pace_hr_distribution.polar) is correctly recomputed
     from the merged payload rather than copied stale from HEAD.

No live Garmin/Fitbit/Google/Polar API calls are made - purely local
git + disk I/O. Existing garmin_flags/fitbit_flags are passed through
unchanged (write_run never touches them).

Run:
    python restore_polar_regression.py
"""
import json
import os
import subprocess

import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _load_head_json(path: str) -> dict | None:
    git_path = path.replace(os.sep, "/")
    result = subprocess.run(["git", "show", f"HEAD:{git_path}"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def main():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    flags_before = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest}

    restored = []
    skipped_no_head_polar = []
    skipped_no_regression = []
    missing = []

    for run in manifest:
        activity_id = run["id"]
        run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
        if not os.path.exists(run_path):
            missing.append(activity_id)
            continue

        with open(run_path, "r") as f:
            current_payload = json.load(f)

        head_payload = _load_head_json(run_path)
        if head_payload is None:
            continue

        head_polar = head_payload.get("polar_hr") or []
        if not any(v is not None for v in head_polar):
            skipped_no_head_polar.append(activity_id)
            continue

        current_polar = current_payload.get("polar_hr") or []
        if any(v is not None for v in current_polar):
            skipped_no_regression.append(activity_id)
            continue

        time_to_polar = dict(zip(head_payload["time"], head_polar))
        current_payload["polar_hr"] = [time_to_polar.get(t) for t in current_payload["time"]]
        current_payload["polar_device_name"] = head_payload.get("polar_device_name")

        existing_garmin_flags = run.get("garmin_flags", ["unreviewed"])
        existing_fitbit_flags = run.get("fitbit_flags", ["unreviewed"])
        entry = publish_run.write_run(current_payload, existing_garmin_flags, existing_fitbit_flags)
        restored.append(activity_id)
        recovered_nonnull = sum(1 for v in current_payload["polar_hr"] if v is not None)
        print(
            f"Restored {activity_id}: polar_hr non-null={recovered_nonnull}/{len(head_polar)} "
            f"(HEAD), avg_polar_hr={entry.get('avg_polar_hr')}, total_trimp_polar={entry.get('total_trimp_polar')}"
        )

    with open(INDEX_PATH, "r") as f:
        manifest_after = json.load(f)
    flags_after = {run["id"]: (run.get("garmin_flags"), run.get("fitbit_flags")) for run in manifest_after}
    mismatches = [
        activity_id for activity_id in flags_before
        if flags_before[activity_id] != flags_after.get(activity_id)
    ]

    print(f"Restored ({len(restored)}): {restored}")
    print(f"Skipped, no Polar data in HEAD ({len(skipped_no_head_polar)})")
    print(f"Skipped, already had Polar data / no regression ({len(skipped_no_regression)})")
    if missing:
        print(f"Missing docs/data/<id>.json: {missing}")
    if mismatches:
        print(f"Flag mismatches: {mismatches}")
    else:
        print("Flag mismatches: NONE - all flags preserved")


if __name__ == "__main__":
    main()
