"""One-off, disk-only migration: replaces docs/data/index.json's single
"flag" field with independent "garmin_flag"/"fitbit_flag" fields (see
PROJECT_DIRECTIVE.md Objectives #3, #10, #11 - different devices have shown
genuinely different failure modes on the same run, which a single
mutually-exclusive flag can't express).

Mapping from the old vocabulary (agreed with the user 2026-07-25):
    unreviewed     -> garmin_flag=unreviewed,      fitbit_flag=unreviewed
    negative       -> garmin_flag=negative,        fitbit_flag=negative
    positive_fitbit -> garmin_flag=negative,        fitbit_flag=positive_cadence
    positive_both  -> garmin_flag=positive_cadence, fitbit_flag=positive_cadence

positive_garmin never appears in the actual data (checked 2026-07-25), so no
mapping is defined for it - the script raises loudly if it ever encounters
one rather than silently guessing at a failure mode.

One explicit carve-out: run 23718324572 is tagged positive_fitbit, but it's
the exact run whose Garmin lag artifact became Objective #10 (cross-
correlation confirmed a genuine ~10s delay against the Polar H10
reference) - applying the blanket mapping would wrongly set its
garmin_flag to "negative", contradicting analysis already trusted. That one
run gets garmin_flag=positive_lag instead.

No live API calls - pure string transform on data already on disk, same
disk-only pattern as normalize_device_names.py. Idempotent: safe to re-run
(no-ops once "flag" is gone from every entry).

Run:
    python migrate_per_device_flags.py
"""
import json
import os

import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")

LAG_CARVEOUTS = {23718324572}


def _migrate_flag(run: dict) -> tuple[str, str]:
    old_flag = run.get("flag")
    activity_id = run.get("id")

    if old_flag == "unreviewed" or old_flag is None:
        return "unreviewed", "unreviewed"
    if old_flag == "negative":
        return "negative", "negative"
    if old_flag == "positive_fitbit":
        garmin_flag = "positive_lag" if activity_id in LAG_CARVEOUTS else "negative"
        return garmin_flag, "positive_cadence"
    if old_flag == "positive_both":
        return "positive_cadence", "positive_cadence"

    raise RuntimeError(
        f"Run {activity_id} has flag={old_flag!r}, which has no defined migration "
        f"(positive_garmin was never expected to appear in real data - see module docstring). "
        f"Resolve this run's mapping manually before re-running."
    )


def main():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    migrated = 0
    already_done = 0
    for run in manifest:
        if "flag" not in run:
            already_done += 1
            continue
        garmin_flag, fitbit_flag = _migrate_flag(run)
        run["garmin_flag"] = garmin_flag
        run["fitbit_flag"] = fitbit_flag
        del run["flag"]
        migrated += 1

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Migrated: {migrated}")
    print(f"Already migrated (no 'flag' field found): {already_done}")


if __name__ == "__main__":
    main()
