import json
import numpy as np
import os
import pandas as pd
import sqlite3
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from garminconnect import Garmin
import httpx

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

app = FastAPI()


def init_db():
    conn = sqlite3.connect("investigator.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            provider TEXT PRIMARY KEY,
            access_token TEXT NOT NULL
        )
    """)
    try:
        cursor.execute("ALTER TABLE auth_tokens ADD COLUMN refresh_token TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()


init_db()


def get_token(provider: str = "google") -> str:
    conn = sqlite3.connect("investigator.db")
    cursor = conn.cursor()
    cursor.execute("SELECT access_token FROM auth_tokens WHERE provider = ?", (provider,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="No active token found. Please login first.")
    return row[0]


def refresh_google_token(refresh_token: str) -> str:
    """Mints a fresh Google access token from a long-lived refresh token.

    Used by the offline publish pipeline, which has no local investigator.db
    and can't run the interactive /login/google browser flow.
    """
    with httpx.Client(timeout=20.0) as client:
        response = client.post("https://oauth2.googleapis.com/token", data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    tokens = response.json()
    if response.status_code != 200 or "access_token" not in tokens:
        raise RuntimeError(f"Google token refresh failed ({response.status_code}): {tokens}")
    return tokens["access_token"]


def align_telemetry(hr_points, step_points):
    """Transforms raw Google Health JSON streams into a 1Hz synchronized Pandas DataFrame."""
    try:
        # 1. Parse Heart Rate Data
        hr_list = []
        for dp in hr_points:
            src = dp.get("dataSource", {})
            platform = src.get("platform", "")
            app = src.get("application", {}).get("packageName", "")
            
            if platform == "FITBIT" or "fitbit" in app.lower():
                device = "fitbit_hr"
            elif "garmin" in app.lower():
                device = "garmin_hr"
            else:
                continue
                
            hr_data = dp.get("heartRate", {})
            t_str = hr_data.get("sampleTime", {}).get("physicalTime")
            bpm = hr_data.get("beatsPerMinute")
            
            if t_str and bpm:
                hr_list.append({"time": pd.to_datetime(t_str), device: float(bpm)})
                
        df_hr = pd.DataFrame(hr_list)
        if not df_hr.empty:
            df_hr.set_index('time', inplace=True)
            df_hr = df_hr.groupby(level=0).mean()
            df_hr = df_hr.resample('1s').mean().interpolate(method='time')
            
        # 2. Parse Step Data (Cadence)
        step_list = []
        for dp in step_points:
            src = dp.get("dataSource", {})
            app = src.get("application", {}).get("packageName", "")
            
            if "garmin" in app.lower():
                step_data = dp.get("steps", {})
                start_t = step_data.get("interval", {}).get("startTime")
                count = step_data.get("count")
                
                if start_t and count:
                    step_list.append({"time": pd.to_datetime(start_t), "cadence_spm": float(count)})
                    
        df_steps = pd.DataFrame(step_list)
        if not df_steps.empty:
            df_steps.set_index('time', inplace=True)
            df_steps = df_steps.groupby(level=0).mean()
            df_steps = df_steps.resample('1s').ffill()
            
        # 3. Merge HR and Steps
        df_merged = df_hr
        if not df_steps.empty:
            df_merged = df_merged.join(df_steps, how='outer').ffill().bfill()
            
        df_merged = df_merged.round(2)
        
        if df_merged.empty:
            return {"error": "No overlapping telemetry found for this session window."}
            
        df_reset = df_merged.reset_index()
        df_reset['time'] = df_reset['time'].astype(str)
        
        return {
            "time": df_reset['time'].tolist(),
            "garmin_hr": df_reset['garmin_hr'].tolist() if 'garmin_hr' in df_reset.columns else [],
            "fitbit_hr": df_reset['fitbit_hr'].tolist() if 'fitbit_hr' in df_reset.columns else [],
            "cadence_spm": df_reset['cadence_spm'].tolist() if 'cadence_spm' in df_reset.columns else []
        }
        
    except Exception as e:
        return {"pandas_alignment_error": str(e)}


def parse_garmin_metrics(details):
    """Dynamically maps indices based on the metricDescriptors found in the activity JSON."""
    descriptors = details.get("metricDescriptors", [])
    metrics_list = details.get("activityDetailMetrics", [])
    
    # 1. Create a lookup map (e.g., {'directRunCadence': 2, 'directTimestamp': 7, ...})
    idx_map = {d.get("key"): d.get("metricsIndex") for d in descriptors}
    
    # 2. Extract the index we need (provide defaults if key not found)
    cadence_idx = idx_map.get("directDoubleCadence") or idx_map.get("directRunCadence")
    time_idx = idx_map.get("directTimestamp")
    hr_idx = idx_map.get("directHeartRate")
    speed_idx = idx_map.get("directSpeed")

    data = []
    for entry in metrics_list:
        m = entry.get("metrics", [])
        
        # Guard clause: Ensure indices exist and data isn't null
        if time_idx is not None and m[time_idx] is not None:
            data.append({
                "time": pd.to_datetime(m[time_idx], unit='ms'),
                "garmin_hr": float(m[hr_idx]) if hr_idx is not None and m[hr_idx] is not None else None,
                "cadence_spm": float(m[cadence_idx]) if cadence_idx is not None and m[cadence_idx] is not None else 0.0,
                "speed_mps": float(m[speed_idx]) if speed_idx is not None and m[speed_idx] is not None else 0.0
            })
    
    df = pd.DataFrame(data)
    df.set_index('time', inplace=True)
    return df.resample('1s').mean().interpolate(method='time')


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <body style="font-family: sans-serif; max-width: 500px; margin: 50px auto;">
            <h2>Cadence Lock Investigator</h2>
            <p><strong>Phase 2: Data Ingestion</strong></p>
            <p><a href="/login/google" style="padding: 10px 15px; background: #4285F4; color: white; text-decoration: none; border-radius: 4px; display: inline-block;">1. Re-Connect Google Health</a></p>
            <hr style="margin: 20px 0;">
            <p><strong>Phase 3: Diagnostic Engine</strong></p>
            <p><a href="/visualize" style="padding: 10px 15px; background: #9b59b6; color: white; text-decoration: none; border-radius: 4px; display: inline-block;">Visualize Latest Run</a></p>
        </body>
    </html>
    """


@app.get("/login/google")
def login_google():
    scopes = [
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    ]
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"response_type=code&"
        f"scope={' '.join(scopes)}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/google/callback", response_class=HTMLResponse)
async def google_callback(code: str = None, error: str = None):
    if error or not code:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=payload)
        tokens = response.json()
    if "error" in tokens:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {tokens}")
    
    conn = sqlite3.connect("investigator.db")
    cursor = conn.cursor()
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        # Google only reissues a refresh_token when consent is freshly granted
        cursor.execute(
            "INSERT OR REPLACE INTO auth_tokens (provider, access_token, refresh_token) VALUES ('google', ?, ?)",
            (tokens["access_token"], refresh_token),
        )
    else:
        cursor.execute("UPDATE auth_tokens SET access_token = ? WHERE provider = 'google'", (tokens["access_token"],))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO auth_tokens (provider, access_token) VALUES ('google', ?)", (tokens["access_token"],))
    conn.commit()
    conn.close()
    return "<html><body style='font-family: sans-serif; max-width: 500px; margin: 50px auto;'><h2 style='color: green;'>✓ Connected to Google Health API!</h2><p><a href='/'>← Return Home</a></p></body></html>"


@app.get("/fetch-latest-run")
async def fetch_latest_run():
    token = get_token("google")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        # 1. Fetch Exercise
        exercise_url = "https://health.googleapis.com/v4/users/me/dataTypes/exercise/dataPoints"
        exercise_res = await client.get(exercise_url, headers=headers)
        exercise_data = exercise_res.json().get("dataPoints", [])
        
        if not exercise_data:
            return {"message": "No exercise sessions found."}
            
        run_sessions = [
            dp for dp in exercise_data 
            if dp.get("exercise", {}).get("exerciseType") == "RUNNING"
        ]
        
        if not run_sessions:
            return {"message": "No recent RUNNING sessions found to analyze."}
            
        def get_start_time(dp):
            return dp.get("exercise", {}).get("interval", {}).get("startTime", "")
            
        latest_exercise = max(run_sessions, key=get_start_time)
        start_time = latest_exercise.get("exercise", {}).get("interval", {}).get("startTime")
        end_time = latest_exercise.get("exercise", {}).get("interval", {}).get("endTime")

        # 2. Fetch HR and Steps using explicitly validated filter prefixes
        base_url = "https://health.googleapis.com/v4/users/me/dataTypes"
        hr_filter = f'heart_rate.sample_time.physical_time >= "{start_time}" AND heart_rate.sample_time.physical_time < "{end_time}"'
        steps_filter = f'steps.interval.start_time >= "{start_time}" AND steps.interval.start_time < "{end_time}"'
        
        hr_res = await client.get(f"{base_url}/heart-rate/dataPoints", headers=headers, params={"filter": hr_filter, "pageSize": 5000})
        steps_res = await client.get(f"{base_url}/steps/dataPoints", headers=headers, params={"filter": steps_filter, "pageSize": 5000})

        if hr_res.status_code != 200:
            return {"error": "Heart rate API error", "details": hr_res.json()}
        if steps_res.status_code != 200:
            return {"error": "Steps API error", "details": steps_res.json()}

        hr_points = hr_res.json().get("dataPoints", [])
        step_points = steps_res.json().get("dataPoints", [])

        # 3. Align data
        aligned_grid = align_telemetry(hr_points, step_points)

    return {
        "status": "Telemetry Grid Synchronized (1Hz)",
        "total_heart_rate_samples": len(hr_points),
        "total_step_samples": len(step_points),
        "aligned_data_preview": aligned_grid
    }


@app.get("/visualize", response_class=HTMLResponse)
def visualize_run():
    return """<!DOCTYPE html>
<html>
    <head>
        <meta charset="UTF-8">
        <title>Cadence Lock Investigation</title>
        <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
        <style>
            * { box-sizing: border-box; }
            html, body { height: 100%; }

            :root {
                color-scheme: light;
                --page-plane:      #f9f9f7;
                --surface-1:       #fcfcfb;
                --text-primary:    #0b0b0b;
                --text-secondary:  #52514e;
                --text-muted:      #898781;
                --gridline:        #e1e0d9;
                --baseline:        #c3c2b7;
                --border:          rgba(11,11,11,0.10);
                --series-garmin:   #7b3294;
                --series-fitbit:   #008837;
                --series-cadence:  #404040;
            }
            @media (prefers-color-scheme: dark) {
                :root {
                    color-scheme: dark;
                    --page-plane:      #0d0d0d;
                    --surface-1:       #1a1a19;
                    --text-primary:    #ffffff;
                    --text-secondary:  #c3c2b7;
                    --text-muted:      #898781;
                    --gridline:        #2c2c2a;
                    --baseline:        #383835;
                    --border:          rgba(255,255,255,0.10);
                    --series-garmin:   #7b3294;
                    --series-fitbit:   #008837;
                    --series-cadence:  #f7f7f7;
                }
            }

            body {
                font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
                margin: 0;
                padding: 20px;
                background: var(--page-plane);
                color: var(--text-primary);
                display: flex;
                flex-direction: column;
                gap: 12px;
            }

            header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
            h1 { font-size: 20px; font-weight: 600; margin: 0; color: var(--text-primary); }
            #subtitle { font-size: 13px; color: var(--text-secondary); margin: 2px 0 0; }

            #tableToggle {
                font: inherit;
                font-size: 13px;
                color: var(--text-secondary);
                background: var(--surface-1);
                border: 1px solid var(--border);
                border-radius: 6px;
                padding: 6px 12px;
                cursor: pointer;
            }
            #tableToggle:hover { color: var(--text-primary); }

            #loader { font-size: 14px; color: var(--text-secondary); text-align: center; margin-top: 50px; }

            #graph {
                flex: 1 1 auto;
                width: 100%;
                min-height: 0;
                background: var(--surface-1);
                border: 1px solid var(--border);
                border-radius: 8px;
            }

            #tableWrap {
                display: none;
                max-height: 40vh;
                overflow: auto;
                background: var(--surface-1);
                border: 1px solid var(--border);
                border-radius: 8px;
            }
            table { width: 100%; border-collapse: collapse; font-size: 13px; }
            thead th {
                position: sticky; top: 0;
                background: var(--surface-1);
                text-align: right;
                color: var(--text-muted);
                font-weight: 600;
                padding: 8px 12px;
                border-bottom: 1px solid var(--gridline);
            }
            thead th:first-child, td:first-child { text-align: left; }
            td {
                text-align: right;
                padding: 6px 12px;
                color: var(--text-secondary);
                font-variant-numeric: tabular-nums;
                border-bottom: 1px solid var(--gridline);
                white-space: nowrap;
            }
        </style>
    </head>
    <body>
        <header>
            <div>
                <h1>Cadence Lock Investigation</h1>
                <p id="subtitle">Garmin vs. Fitbit heart rate against stride cadence &mdash; gaps in sensor reporting are shown as breaks, never filled.</p>
            </div>
            <button id="tableToggle" type="button">View as table</button>
        </header>

        <div id="loader">Fetching and aligning telemetry&hellip;</div>
        <div id="graph"></div>
        <div id="tableWrap"><table>
            <thead><tr><th>Time</th><th>Garmin HR</th><th>Fitbit HR</th><th>Garmin Cadence (SPM)</th></tr></thead>
            <tbody id="tableBody"></tbody>
        </table></div>

        <script>
            const mql = window.matchMedia('(prefers-color-scheme: dark)');
            const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

            function theme() {
                return {
                    surface: cssVar('--surface-1'),
                    primary: cssVar('--text-primary'),
                    secondary: cssVar('--text-secondary'),
                    muted: cssVar('--text-muted'),
                    grid: cssVar('--gridline'),
                    baseline: cssVar('--baseline'),
                    border: cssVar('--border'),
                    garmin: cssVar('--series-garmin'),
                    fitbit: cssVar('--series-fitbit'),
                    cadence: cssVar('--series-cadence')
                };
            }

            function buildLayout(t) {
                return {
                    margin: { t: 16, r: 24, l: 56, b: 40 },
                    paper_bgcolor: t.surface,
                    plot_bgcolor: t.surface,
                    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: t.secondary, size: 12 },
                    legend: {
                        orientation: 'h',
                        x: 0, xanchor: 'left',
                        y: 1.08, yanchor: 'bottom',
                        font: { color: t.secondary }
                    },
                    xaxis: {
                        title: { text: 'Time', font: { color: t.muted } },
                        gridcolor: t.grid,
                        linecolor: t.baseline,
                        tickfont: { color: t.muted },
                        rangeslider: { visible: true, thickness: 0.06, bgcolor: t.surface, bordercolor: t.border, borderwidth: 1 }
                    },
                    yaxis: {
                        title: { text: 'BPM / SPM', font: { color: t.muted } },
                        gridcolor: t.grid,
                        zerolinecolor: t.baseline,
                        tickfont: { color: t.muted }
                    },
                    hovermode: 'x unified',
                    hoverlabel: { bgcolor: t.surface, bordercolor: t.border, font: { color: t.primary } }
                };
            }

            function buildTraces(data, t) {
                return [
                    {
                        x: data.time, y: data.garmin_hr, name: 'Garmin HR',
                        mode: 'lines+markers',
                        line: { color: t.garmin, width: 1 },
                        marker: { size: 1 },
                        connectgaps: false
                    },
                    {
                        x: data.time, y: data.fitbit_hr, name: 'Fitbit HR',
                        mode: 'lines+markers',
                        line: { color: t.fitbit, width: 1 },
                        marker: { size: 1 },
                        connectgaps: false
                    },
                    {
                        x: data.time, y: data.cadence_spm, name: 'Garmin Cadence (SPM)',
                        mode: 'lines+markers',
                        line: { color: t.cadence, width: 1 },
                        marker: { size: 1 },
                        connectgaps: false
                    }
                ];
            }

            let latestData = null;
            let tableBuilt = false;

            function render() {
                if (!latestData) return;
                const t = theme();
                Plotly.react('graph', buildTraces(latestData, t), buildLayout(t), { responsive: true });
            }

            function buildTable(data) {
                const rows = data.time.map((time, i) => {
                    const fmt = (v) => (v === null || v === undefined) ? '&mdash;' : v;
                    return `<tr><td>${time}</td><td>${fmt(data.garmin_hr[i])}</td><td>${fmt(data.fitbit_hr[i])}</td><td>${fmt(data.cadence_spm[i])}</td></tr>`;
                }).join('');
                document.getElementById('tableBody').innerHTML = rows;
            }

            document.getElementById('tableToggle').addEventListener('click', () => {
                const wrap = document.getElementById('tableWrap');
                const showing = wrap.style.display === 'block';
                if (!showing && !tableBuilt && latestData) {
                    buildTable(latestData);
                    tableBuilt = true;
                }
                wrap.style.display = showing ? 'none' : 'block';
                document.getElementById('tableToggle').innerText = showing ? 'View as table' : 'Hide table';
            });

            mql.addEventListener('change', render);
            window.addEventListener('resize', () => Plotly.Plots.resize('graph'));

            fetch('/fetch-all-data')
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('loader').innerText = "Backend Error: " + data.error;
                        return;
                    }

                    document.getElementById('loader').style.display = 'none';
                    latestData = data;
                    render();
                })
                .catch(error => {
                    document.getElementById('loader').innerText = "Load Error: " + error;
                });
        </script>
    </body>
</html>"""


@app.get("/test-garmin")
def test_garmin():
    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    
    if not garmin_email or not garmin_password:
        return {"error": "Garmin credentials missing from .env file."}

    try:
        # 1. Authenticate with Garmin Connect
        client = Garmin(garmin_email, garmin_password)
        client.login()

        # 2. Fetch the most recent activity summary
        activities = client.get_activities(0, 1)
        if not activities:
            return {"message": "No activities found in Garmin Connect."}
            
        latest_activity = activities[0]
        activity_id = latest_activity.get("activityId")
        activity_name = latest_activity.get("activityName")

        # 3. Fetch the high-resolution time-series arrays for this activity
        details = client.get_activity_details(activity_id)
        
        # Garmin stores the telemetry in a list called 'activityDetailMetrics'
        metrics = details.get("activityDetailMetrics", [])
        
        # Grab a snapshot of the first 10 seconds of data to inspect the structure
        sample_metrics = metrics[:10]

        return {
            "status": "Garmin Connect API Successfully Queried",
            "activity_id": activity_id,
            "activity_name": activity_name,
            "total_telemetry_samples": len(metrics),
            "sample_telemetry": sample_metrics
        }

    except Exception as e:
        return {"error": f"Garmin Connect API failed: {str(e)}"}

@app.get("/inspect-garmin-schema")
def inspect_schema():
    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    client = Garmin(garmin_email, garmin_password)
    client.login()

    # Get the latest activity
    activities = client.get_activities(0, 1)
    activity_id = activities[0].get("activityId")
    
    # Get details and extract the descriptors
    details = client.get_activity_details(activity_id)
    descriptors = details.get("metricDescriptors", [])

    # Format into a readable list of (Index, Key, Unit)
    schema_map = [
        {"index": i, "key": d.get("key"), "unit": d.get("unit")} 
        for i, d in enumerate(descriptors)
    ]
    
    return {"schema": schema_map}


def fetch_fitbit_hr_df(client: httpx.Client, headers: dict, start_iso: str, end_iso: str) -> pd.DataFrame:
    """Fetches Fitbit heart-rate samples (via Google Health) for the given
    [start_iso, end_iso) window and returns a 1s-resampled DataFrame indexed
    by time with a single 'fitbit_hr' column. Returns an empty DataFrame if
    no Fitbit-sourced samples fall in the window.

    Callers choose the window (e.g. "the latest Google Health RUNNING
    session", or any matched session in a bulk backfill) - this function only
    knows how to fetch and parse HR samples for a window it's given.
    """
    fitbit_df = pd.DataFrame()

    hr_res = client.get(
        "https://health.googleapis.com/v4/users/me/dataTypes/heart-rate/dataPoints",
        headers=headers,
        params={"filter": f'heart_rate.sample_time.physical_time >= "{start_iso}" AND heart_rate.sample_time.physical_time < "{end_iso}"', "pageSize": 5000}
    )
    if hr_res.status_code != 200:
        raise RuntimeError(f"Google Health heart-rate API returned {hr_res.status_code}: {hr_res.text}")

    hr_data_points = hr_res.json().get("dataPoints", [])
    fitbit_data = []
    for dp in hr_data_points:
        src = dp.get("dataSource", {})
        platform = src.get("platform", "")
        app_pkg = src.get("application", {}).get("packageName", "")

        if not (platform == "FITBIT" or "fitbit" in app_pkg.lower()):
            continue

        t = dp.get("heartRate", {}).get("sampleTime", {}).get("physicalTime")
        bpm = dp.get("heartRate", {}).get("beatsPerMinute")
        if t and bpm is not None:
            fitbit_data.append({"time": pd.to_datetime(t), "fitbit_hr": bpm})

    fitbit_df = pd.DataFrame(fitbit_data)
    if not fitbit_df.empty:
        fitbit_df['fitbit_hr'] = pd.to_numeric(fitbit_df['fitbit_hr'], errors='coerce')
        fitbit_df = fitbit_df.set_index('time').resample('1s').mean()

    return fitbit_df


def merge_telemetry(garmin_df: pd.DataFrame, fitbit_df: pd.DataFrame, activity_id) -> dict:
    """Time-aligns Garmin and Fitbit telemetry (outer join, no filling - gaps
    are real signal) and returns the final JSON-serializable payload shape.
    """
    if fitbit_df.empty and garmin_df.empty:
        raise RuntimeError("No data found for both providers.")

    # Handle timezones safely
    if not garmin_df.empty:
        if garmin_df.index.tz is None:
            garmin_df.index = garmin_df.index.tz_localize('UTC')
        target_tz = garmin_df.index.tz
    else:
        target_tz = 'UTC'

    if not fitbit_df.empty:
        if fitbit_df.index.tz is None:
            fitbit_df.index = fitbit_df.index.tz_localize(target_tz)
        else:
            fitbit_df.index = fitbit_df.index.tz_convert(target_tz)

    # 1. Outer join (no filling). A completely empty fitbit_df (zero rows,
    # zero columns - no Fitbit data at all in this window) joins in without
    # ever creating a 'fitbit_hr' column, so guarantee it exists (as nulls,
    # not fabricated values - this is still "gaps are signal", just gapped
    # for the entire run rather than part of it).
    merged_df = garmin_df.join(fitbit_df, how='outer')
    if 'fitbit_hr' not in merged_df.columns:
        merged_df['fitbit_hr'] = None

    # 2. Localize time
    if merged_df.index.tz is not None:
        merged_df.index = merged_df.index.tz_convert('America/Los_Angeles')

    merged_df = merged_df.reset_index()
    merged_df['time'] = merged_df['time'].astype(str)

    # 3. ROBUST CLEANING: Replace NaNs with None for JSON compliance
    # This forces all NaN/inf values to become 'null' in the JSON output
    merged_df = merged_df.replace({np.nan: None})

    # 4. Final safety check: ensure no Inf/-Inf values remain
    merged_df = merged_df.replace([np.inf, -np.inf], None)

    return {
        "activity_id": activity_id,
        "time": merged_df['time'].tolist(),
        "garmin_hr": merged_df['garmin_hr'].tolist(),
        "fitbit_hr": merged_df['fitbit_hr'].tolist(),
        "cadence_spm": merged_df['cadence_spm'].tolist(),
        "speed_mps": merged_df['speed_mps'].tolist()
    }


def build_run_payload(google_access_token: str, use_garmin_cache: bool = True) -> dict:
    """Fetches the latest Garmin + Fitbit (via Google Health) telemetry and
    merges them into a single 1Hz time-aligned payload.

    Raises RuntimeError on failure - callers translate that into an HTTP
    response (the live /fetch-all-data route) or a CLI error (publish_run.py).
    Set use_garmin_cache=False to always pull the real latest activity instead
    of replaying cache_garmin.json (the publish pipeline always does this).
    """
    fitbit_df = pd.DataFrame()
    garmin_df = pd.DataFrame()
    activity_id = None
    headers = {"Authorization": f"Bearer {google_access_token}"}

    # --- SECTION 1: Fitbit Data (via Google Health) ---
    with httpx.Client(timeout=20.0) as client:
        ex_res = client.get("https://health.googleapis.com/v4/users/me/dataTypes/exercise/dataPoints", headers=headers)
        if ex_res.status_code != 200:
            raise RuntimeError(f"Google Health exercise API returned {ex_res.status_code}: {ex_res.text}")
        ex_data = [dp for dp in ex_res.json().get("dataPoints", []) if dp.get("exercise", {}).get("exerciseType") == "RUNNING"]

        if ex_data:
            latest = max(ex_data, key=lambda x: x.get("exercise", {}).get("interval", {}).get("startTime", ""))
            start_t = latest.get("exercise", {}).get("interval", {}).get("startTime")
            end_t = latest.get("exercise", {}).get("interval", {}).get("endTime")

            fitbit_df = fetch_fitbit_hr_df(client, headers, start_t, end_t)

        print(f"DEBUG: Fitbit rows: {len(fitbit_df)}")

    # --- SECTION 2: Garmin Data ---
    cache_file = "cache_garmin.json"
    details = None
    if use_garmin_cache and os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            details = json.load(f)
    else:
        garmin_client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        garmin_client.login()
        activities = garmin_client.get_activities(0, 1)
        if activities:
            details = garmin_client.get_activity_details(activities[0]['activityId'])
            if use_garmin_cache:
                with open(cache_file, "w") as f:
                    json.dump(details, f)

    if details is not None:
        activity_id = details.get("activityId")
        garmin_df = parse_garmin_metrics(details)
        garmin_df['garmin_hr'] = pd.to_numeric(garmin_df['garmin_hr'], errors='coerce')
        garmin_df['cadence_spm'] = pd.to_numeric(garmin_df['cadence_spm'], errors='coerce')

    print(f"DEBUG: Garmin rows: {len(garmin_df)}")

    if not garmin_df.empty:
        # --- DEBUG: Inspecting Garmin DF ---
        print(f"DEBUG: Garmin head (start): {garmin_df.head(5)}")
        print(f"DEBUG: Garmin tail (end): {garmin_df.tail(5)}")

        # Check for nulls/zeros
        null_cadence = garmin_df['cadence_spm'].isna().sum()
        zero_cadence = (garmin_df['cadence_spm'] == 0).sum()
        print(f"DEBUG: Null cadence values: {null_cadence}")
        print(f"DEBUG: Zero cadence values: {zero_cadence}")

    # --- SECTION 3: Merge ---
    return merge_telemetry(garmin_df, fitbit_df, activity_id)


@app.get("/fetch-all-data")
async def fetch_all_data():
    try:
        google_token = get_token("google")
        return build_run_payload(google_token)
    except HTTPException as e:
        print(f"DEBUG: Error: {e.status_code} {e.detail}")
        return {"error": e.detail}
    except Exception as e:
        message = str(e) or repr(e)
        print(f"DEBUG: Error: {type(e).__name__}: {message}")
        return {"error": f"{type(e).__name__}: {message}"}
