# Cadence Lock Investigator

An investigation into a specific failure mode of wrist-worn optical heart-rate
sensors: **cadence lock**, where the sensor's PPG signal appears to sync onto
a periodic mechanical vibration from running and report that frequency back
as heart rate, instead of the true cardiovascular pulse.

For a wrist-worn sensor, there are at least two distinct mechanical pathways
that could plausibly corrupt the PPG signal at a running cadence: the shockwave
from footstrike impact, transmitted through bone and soft tissue up to the
wrist, and the more local, continuous motion of arm swing itself perturbing
the sensor directly. Footstrike and arm swing both cycle at essentially the
same frequency as stride cadence in normal running gait — each roughly 1:1
with stride — so either pathway, or both at once, could plausibly produce a
spurious periodic component in the PPG spectrogram at or near the cadence
frequency. Stride cadence is what's actually recorded in the telemetry
(Garmin's running dynamics data), rather than footstrike shock or arm-swing
frequency directly, so this repo compares Garmin's and Fitbit's heart-rate
readings for the same run against Garmin's recorded stride cadence, looking
for periods where a device's "heart rate" tracks that cadence rather than a
plausible cardiac signal, while keeping in mind that stride cadence is a
proxy for — and mechanistically distinct from — the underlying footstrike
and arm-swing signals that could actually be driving the artifact. It's an
open investigation, not a finished study — neither the cadence-lock effect
nor the mechanisms behind it is asserted here as proven, for either
platform. See [`PROJECT_DIRECTIVE.md`](PROJECT_DIRECTIVE.md) for the full
mission statement and roadmap.

## Architecture

The project has two deliberately separate halves:

1. **Local dev tool** (`main.py`) — a FastAPI app that does live OAuth
   against Google Health (for Fitbit HR data synced through it) and logs
   into Garmin Connect directly, fetches the latest run from both, aligns
   the two telemetry streams to a shared 1Hz timeline, and renders a live
   Plotly chart at `/visualize`. This is the day-to-day working tool, run
   only on the owner's machine, and it needs real credentials.

2. **Static published site** (`docs/`) — a plain HTML/JS site with no
   backend, served by GitHub Pages. It reads pre-computed JSON files from
   `docs/data/` (one file per published run, plus an `index.json`
   manifest) and renders the same kind of chart, a run gallery, and a
   pace-bucketed HR distribution table. It never talks to Garmin, Google,
   or any credentials — it only ever reads static JSON already committed to
   the repo.

The static site is updated only when the [publish pipeline](#publishing-a-run)
is run manually — nothing about it is live or auto-refreshing.

### Data integrity rule: gaps are signal, not noise

Raw HR and cadence data is **never smoothed, interpolated across sensor
gaps, or backfilled** in the published output. When a device stops
reporting for a stretch, that gap is treated as real information (probable
signal dropout) and rendered as a visible break in the chart rather than
papered over. Concretely: the Garmin/Fitbit merge in `build_run_payload`
(`main.py`) is an outer join with no fill, and every Plotly trace (in
`main.py`'s `/visualize` page as well as `docs/run.html`) is configured with
`connectgaps: false`. This is treated as a hard rule for this project's
credibility, not an incidental implementation detail.

## Local setup

Requirements: **Python 3.12**.

```
pip install -r requirements.txt
```

Core dependencies: FastAPI + uvicorn (server), pandas + numpy (telemetry
alignment), httpx (Google/Garmin HTTP calls), python-dotenv (`.env`
loading), garminconnect (Garmin Connect API client).

Create a `.env` file in the repo root (already gitignored — never commit
it) with:

| Key | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth client ID for the Google Health API app used to pull Fitbit HR data |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret paired with the above |
| `REDIRECT_URI` | OAuth callback URL registered for the app (points at `/auth/google/callback`) |
| `GARMIN_EMAIL` | Garmin Connect account email (direct login, not OAuth) |
| `GARMIN_PASSWORD` | Garmin Connect account password |
| `HR_MAX` *(optional)* | Explicit HR max, used to compute Active Zone Minutes (AZM) style zone stats |
| `BIRTH_YEAR` *(optional)* | Used to estimate HR max as `220 - age` if `HR_MAX` isn't set |

`HR_MAX`/`BIRTH_YEAR` are optional — if neither is set, zone-based fields
(AZM totals, overestimation %) are simply omitted from published runs rather
than estimated.

There is no `.env.example` checked in yet; the table above is the source of
truth for what's needed.

After your **first** successful `/login/google` run, Google's refresh token
is persisted to a local SQLite file, `investigator.db` (also gitignored) —
not to `.env`. `print_refresh_token.py` reads it back out so you can copy it
into a GitHub Actions secret for the publish workflow (see below).

## Running locally

```
uvicorn main:app --reload
```

- `/` — a minimal home page with links to connect Google and to visualize
  the latest run.
- `/login/google` — starts the Google OAuth consent flow (Fitbit HR access
  via Google Health); redirects back to `/auth/google/callback`, which
  stores the access/refresh tokens in `investigator.db`.
- `/visualize` — fetches and aligns the latest Garmin + Fitbit run
  (`/fetch-all-data` under the hood) and renders it as an interactive
  Plotly chart (HR from both devices plus Garmin cadence), with a table
  view toggle.

A couple of other routes (`/test-garmin`, `/inspect-garmin-schema`,
`/fetch-latest-run`) exist as diagnostic endpoints used while reverse
engineering Garmin's activity-detail schema and Google Health's data
format — useful for debugging, not part of the main flow.

## Publishing a run

The public site is updated by manually triggering the **Publish run**
GitHub Actions workflow (`.github/workflows/publish.yml`), which takes one
input: `flag`, a choice of `unreviewed` / `positive` / `negative`.

The flag is a **manual human judgment call** — you look at the chart
yourself and decide whether it shows cadence lock. There is no automated
detector yet; that's Objective #3 in `PROJECT_DIRECTIVE.md` and hasn't been
built.

The workflow installs dependencies, runs `python publish_run.py --flag
<value>` (using `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REFRESH_TOKEN`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`, and optionally
`HR_MAX`/`BIRTH_YEAR` from repo secrets), then commits and pushes whatever
lands in `docs/`.

`publish_run.py` re-fetches the latest Garmin + Fitbit run (bypassing the
local Garmin response cache), computes derived stats (Active Zone Minutes
per device when an HR max is available, and pace-bucketed HR distributions),
writes the full run payload to `docs/data/<activity_id>.json`, and
adds/updates a summary entry for it in `docs/data/index.json`, the manifest
the gallery page reads. `docs/index.html` lists published runs with their
flag badge; `docs/run.html` renders the full chart and tables for one run.

The same run can be published again later (e.g. to correct a `flag`) — it
overwrites its existing entry in the manifest rather than duplicating it.

## Status

This is an active, evolving personal investigation, not a finished product.
The ingestion and visualization pipeline works end to end; correlation
analysis and an actual cadence-lock detection heuristic are not implemented
yet. See [`PROJECT_DIRECTIVE.md`](PROJECT_DIRECTIVE.md) for the full list of
strategic objectives and current system state.
