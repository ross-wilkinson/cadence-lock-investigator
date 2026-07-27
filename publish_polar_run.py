"""Publishes a manually-exported Polar FIT session (paired with Fitbit HR
for the same window) into docs/data/, the same way publish_run.py does for
a Garmin/Fitbit run.

Exists because Polar's AccessLink API only ever returns exercises uploaded
to Flow *after* the user was registered with this client (see
main.list_polar_exercises' docstring) - a run recorded before that point
is permanently unreachable via the API, so this is the only path for it.

Design note for future readers: this reuses main.merge_telemetry() and
publish_run.write_run() completely unchanged. Polar's parsed columns are
renamed onto the "garmin_hr"/"cadence_spm" slots those functions expect,
because in this pipeline that slot means "the device that supplies both HR
and cadence/pace", not literally Garmin hardware - Polar fills exactly the
same role for this run. See docs/index.html and docs/run.html for the
corresponding device-name-aware label fixes on the display side.

    python publish_polar_run.py raw_data/polar/Ross_Wilkinson_2026-07-23_19-03-00.FIT --garmin-flags unreviewed --fitbit-flags positive_cadence

Note: "--garmin-flags" reviews whichever device fills the primary HR/cadence
slot for this run - Polar itself here, per the design note above, not
literal Garmin hardware.
"""
import argparse
import sys

import pandas as pd
import httpx

import main
import publish_run


def build_polar_run_payload(fit_path: str, google_access_token: str) -> dict:
    polar_df, polar_device_name = main.parse_polar_fit_file(fit_path)
    if polar_df.empty:
        raise RuntimeError(f"No record samples parsed from {fit_path}.")

    polar_df = polar_df.rename(columns={"polar_hr": "garmin_hr", "polar_cadence_spm": "cadence_spm"})

    start_iso = polar_df.index.min().isoformat()
    end_iso = (polar_df.index.max() + pd.Timedelta(seconds=1)).isoformat()

    headers = {"Authorization": f"Bearer {google_access_token}"}
    with httpx.Client(timeout=20.0) as client:
        fitbit_df, fitbit_device_name = main.fetch_fitbit_hr_df(client, headers, start_iso, end_iso)
    fitbit_firmware_version = main.fetch_fitbit_firmware_version(fitbit_device_name)

    # No Garmin activityId exists for a manual publish - synthesize one
    # guaranteed never to collide with a real (always-positive) Garmin id,
    # deterministic on the run's own start time so re-running this script
    # on the same file always upserts the same manifest entry rather than
    # duplicating it.
    activity_id = -int(polar_df.index.min().timestamp())

    payload = main.merge_telemetry(
        polar_df, fitbit_df, activity_id, polar_device_name, fitbit_device_name,
        fitbit_firmware_version=fitbit_firmware_version,
    )
    payload = main.enrich_with_weather(payload)  # no GPS extracted -> no-ops to None, not a crash
    return payload


def main_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fit_path")
    parser.add_argument("--garmin-flags", nargs="+", choices=publish_run.FLAG_CHOICES, default=["unreviewed"])
    parser.add_argument("--fitbit-flags", nargs="+", choices=publish_run.FLAG_CHOICES, default=["unreviewed"])
    args = parser.parse_args(argv)

    garmin_flags = publish_run.normalize_flags(args.garmin_flags)
    fitbit_flags = publish_run.normalize_flags(args.fitbit_flags)

    refresh_token = publish_run._get_refresh_token()
    access_token = main.refresh_google_token(refresh_token)
    payload = build_polar_run_payload(args.fit_path, access_token)
    entry = publish_run.write_run(payload, garmin_flags, fitbit_flags)
    print(f"Published run {entry['id']} ({entry['start']} -> {entry['end']}), garmin_flags={entry['garmin_flags']}, fitbit_flags={entry['fitbit_flags']}, device={entry['garmin_device_name']}")
    return entry


if __name__ == "__main__":
    try:
        main_cli()
    except Exception as e:
        print(f"publish_polar_run failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
