"""One-off, disk-only backfill: applies main.normalize_device_name() to
every already-published run's garmin_device_name/fitbit_device_name, in
both docs/data/index.json (the manifest) and each docs/data/<id>.json (the
full payload) - runs published before this normalization existed still
have bare model names ("Instinct 2X Solar", "Inspire 3") instead of
brand-prefixed ones ("Garmin Instinct 2X Solar", "Fitbit Inspire 3").

No live API calls - pure string transform on data already on disk, same
disk-only pattern as recompute_dashboard_stats.py. Idempotent: safe to
re-run (normalize_device_name no-ops on an already-prefixed name), and
verifies "flag" is byte-identical before/after, same safety check
recompute_dashboard_stats.py uses.

Run:
    python normalize_device_names.py
"""
import json
import os

import main
import publish_run

DOCS_DATA_DIR = publish_run.DOCS_DATA_DIR
INDEX_PATH = os.path.join(DOCS_DATA_DIR, "index.json")


def _normalize_primary_slot(name):
    """garmin_device_name is the "primary" slot (HR+cadence+pace) - normally
    Garmin, but Polar for the one manually-published FIT run (see
    publish_polar_run.py). Blindly normalizing every entry as vendor=Garmin
    would wrongly turn "Polar Vantage V3" into "Garmin Polar Vantage V3" -
    the exact bug this whole normalization effort exists to fix - so any
    name already Polar-branded is left untouched instead.
    """
    if name and name.strip().lower().startswith("polar"):
        return name
    return main.normalize_device_name("Garmin", name)


def main_cli():
    with open(INDEX_PATH, "r") as f:
        manifest = json.load(f)

    flags_before = {run["id"]: run.get("flag") for run in manifest}
    changed_manifest = 0
    changed_payloads = 0

    for run in manifest:
        activity_id = run["id"]

        before = (run.get("garmin_device_name"), run.get("fitbit_device_name"))
        run["garmin_device_name"] = _normalize_primary_slot(run.get("garmin_device_name"))
        run["fitbit_device_name"] = main.normalize_device_name("Fitbit", run.get("fitbit_device_name"))
        if (run["garmin_device_name"], run["fitbit_device_name"]) != before:
            changed_manifest += 1

        run_path = os.path.join(DOCS_DATA_DIR, f"{activity_id}.json")
        if not os.path.exists(run_path):
            continue
        with open(run_path, "r") as f:
            payload = json.load(f)

        before = (payload.get("garmin_device_name"), payload.get("fitbit_device_name"))
        payload["garmin_device_name"] = _normalize_primary_slot(payload.get("garmin_device_name"))
        payload["fitbit_device_name"] = main.normalize_device_name("Fitbit", payload.get("fitbit_device_name"))
        if (payload["garmin_device_name"], payload["fitbit_device_name"]) != before:
            changed_payloads += 1
            with open(run_path, "w") as f:
                json.dump(payload, f)

    flags_after = {run["id"]: run.get("flag") for run in manifest}
    mismatches = [aid for aid in flags_before if flags_before[aid] != flags_after.get(aid)]

    with open(INDEX_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Runs in manifest: {len(manifest)}")
    print(f"Manifest entries updated: {changed_manifest}")
    print(f"Per-run payloads updated: {changed_payloads}")
    print("Flag mismatches: NONE - all flags preserved" if not mismatches else f"Flag mismatches: {mismatches}")


if __name__ == "__main__":
    main_cli()
