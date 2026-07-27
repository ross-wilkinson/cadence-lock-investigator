"""One-off, disk-only migration: replaces docs/data/index.json's scalar
"garmin_flag"/"fitbit_flag" string fields with "garmin_flags"/"fitbit_flags"
list fields (see the per-device-flags-as-arrays migration this script is
part of - a device can show more than one failure mode in the same run,
e.g. a lag artifact AND a dropout, which a single scalar flag can't
express).

Every existing value is already a single valid FLAG_CHOICES string (checked
2026-07-26 - no run has ever had more than one failure mode recorded), so
the transform is a pure wrap: garmin_flag="x" -> garmin_flags=["x"], same
for fitbit, deleting the old singular keys.

No live API calls - pure JSON transform on data already on disk, same
disk-only pattern as migrate_per_device_flags.py. Idempotent: safe to
re-run (no-ops once "garmin_flag"/"fitbit_flag" are gone from every entry).

Run:
    python migrate_flags_to_arrays.py
"""
import json
import os

import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def main():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    migrated = 0
    already_done = 0

    for run in manifest:
        if "garmin_flag" not in run and "fitbit_flag" not in run:
            already_done += 1
            continue

        garmin_flag = run.pop("garmin_flag", None)
        fitbit_flag = run.pop("fitbit_flag", None)

        garmin_flags = publish_run.normalize_flags([garmin_flag] if garmin_flag else ["unreviewed"])
        fitbit_flags = publish_run.normalize_flags([fitbit_flag] if fitbit_flag else ["unreviewed"])

        run["garmin_flags"] = garmin_flags
        run["fitbit_flags"] = fitbit_flags
        migrated += 1

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Migrated: {migrated}")
    print(f"Already migrated (no 'garmin_flag'/'fitbit_flag' field found): {already_done}")


if __name__ == "__main__":
    main()
