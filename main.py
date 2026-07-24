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

import weather

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

POLAR_CLIENT_ID = os.getenv("POLAR_CLIENT_ID")
POLAR_CLIENT_SECRET = os.getenv("POLAR_CLIENT_SECRET")
POLAR_REDIRECT_URI = os.getenv("POLAR_REDIRECT_URI")

# Verified 2026-07-23 against Polar's own OpenAPI spec
# (https://www.polar.com/accesslink-api/swagger.yaml) and the reference
# client at github.com/polarofficial/accesslink-example-python - do not
# trust these from memory, Polar has changed hosts/paths before.
POLAR_AUTHORIZATION_URL = "https://flow.polar.com/oauth2/authorization"
POLAR_TOKEN_URL = "https://polarremote.com/v2/oauth2/token"
POLAR_ACCESSLINK_URL = "https://www.polaraccesslink.com/v3"
POLAR_SCOPE = "accesslink.read_all"

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
    try:
        # Polar's token response identifies the user via a Polar-assigned
        # "x_user_id" (not present at all for Google/Fitbit) - later
        # AccessLink calls need it in the URL path, so it's persisted
        # alongside the token rather than re-derived each time.
        cursor.execute("ALTER TABLE auth_tokens ADD COLUMN external_user_id TEXT")
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
    elevation_idx = idx_map.get("directElevation")
    distance_idx = idx_map.get("sumDistance")
    latitude_idx = idx_map.get("directLatitude")
    longitude_idx = idx_map.get("directLongitude")
    grade_adjusted_speed_idx = idx_map.get("directGradeAdjustedSpeed")

    data = []
    for entry in metrics_list:
        m = entry.get("metrics", [])

        # Guard clause: Ensure indices exist and data isn't null
        if time_idx is not None and m[time_idx] is not None:
            data.append({
                "time": pd.to_datetime(m[time_idx], unit='ms'),
                "garmin_hr": float(m[hr_idx]) if hr_idx is not None and m[hr_idx] is not None else None,
                "cadence_spm": float(m[cadence_idx]) if cadence_idx is not None and m[cadence_idx] is not None else 0.0,
                "speed_mps": float(m[speed_idx]) if speed_idx is not None and m[speed_idx] is not None else 0.0,
                # No factor scaling applied - despite metricDescriptors listing a
                # 'factor' field (e.g. 100.0), activityDetailMetrics values arrive
                # already in natural units (meters, m/s, decimal degrees), same as
                # directSpeed/directHeartRate above. Verified against real Garmin
                # data (activity 23672318504) on 2026-07-23. Missing values stay
                # None (no 0.0 fallback) per the project's no-fill rule.
                "elevation_m": float(m[elevation_idx]) if elevation_idx is not None and m[elevation_idx] is not None else None,
                "distance_m": float(m[distance_idx]) if distance_idx is not None and m[distance_idx] is not None else None,
                "latitude": float(m[latitude_idx]) if latitude_idx is not None and m[latitude_idx] is not None else None,
                "longitude": float(m[longitude_idx]) if longitude_idx is not None and m[longitude_idx] is not None else None,
                "grade_adjusted_speed_mps": float(m[grade_adjusted_speed_idx]) if grade_adjusted_speed_idx is not None and m[grade_adjusted_speed_idx] is not None else None,
            })
    
    df = pd.DataFrame(data)
    df.set_index('time', inplace=True)
    # No .interpolate() here - an empty 1s bin is a real sensor gap, not a
    # value to fabricate. Matches fetch_fitbit_hr_df's identical treatment.
    return df.resample('1s').mean()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <body style="font-family: sans-serif; max-width: 500px; margin: 50px auto;">
            <h2>Cadence Lock Investigator</h2>
            <p><strong>Phase 2: Data Ingestion</strong></p>
            <p><a href="/login/google" style="padding: 10px 15px; background: #4285F4; color: white; text-decoration: none; border-radius: 4px; display: inline-block;">1. Re-Connect Google Health</a></p>
            <p><a href="/login/polar" style="padding: 10px 15px; background: #E4022F; color: white; text-decoration: none; border-radius: 4px; display: inline-block;">2. Connect Polar</a></p>
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
        f"redirect_uri={GOOGLE_REDIRECT_URI}&"
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
        "code": code, "grant_type": "authorization_code", "redirect_uri": GOOGLE_REDIRECT_URI,
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


@app.get("/login/polar")
def login_polar():
    auth_url = (
        f"{POLAR_AUTHORIZATION_URL}?"
        f"response_type=code&"
        f"client_id={POLAR_CLIENT_ID}&"
        f"redirect_uri={POLAR_REDIRECT_URI}&"
        f"scope={POLAR_SCOPE}"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/polar/callback", response_class=HTMLResponse)
async def polar_callback(code: str = None, error: str = None):
    if error or not code:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    # Polar authenticates the CLIENT via HTTP Basic (base64 client_id:client_secret)
    # on the token exchange, not client_id/secret fields in the POST body like
    # Google - confirmed against polarofficial/accesslink-example-python's
    # oauth2.py reference client.
    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": POLAR_REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            POLAR_TOKEN_URL,
            data=token_payload,
            headers={"Accept": "application/json;charset=UTF-8"},
            auth=(POLAR_CLIENT_ID, POLAR_CLIENT_SECRET),
        )
        tokens = token_res.json()
    if token_res.status_code != 200 or "access_token" not in tokens:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {tokens}")

    access_token = tokens["access_token"]
    # Polar's assigned user id arrives as "x_user_id" in the token response
    # (confirmed against example_web_app.py's callback route) - every later
    # AccessLink call needs it in the URL path. Polar's own docs describe
    # "API user-id" and "Polar User Id (polar-user-id)" as interchangeable
    # terms for this same value.
    external_user_id = tokens.get("x_user_id")
    # Not currently issued by Polar's token endpoint (no rotation/expiry
    # scheme requiring one, unlike Google) - stored if that ever changes.
    refresh_token = tokens.get("refresh_token")

    # Mandatory one-time registration: required once per user per client
    # before any other AccessLink call will succeed. A 409 means this user
    # is already registered to this client (expected on every re-login
    # after the first) and is treated as success, not an error.
    async with httpx.AsyncClient() as client:
        register_res = await client.post(
            f"{POLAR_ACCESSLINK_URL}/users",
            json={"member-id": "cadence-lock-investigator"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if register_res.status_code not in (200, 201, 409):
        raise HTTPException(
            status_code=400,
            detail=f"Polar user registration failed ({register_res.status_code}): {register_res.text}",
        )

    conn = sqlite3.connect("investigator.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO auth_tokens (provider, access_token, refresh_token, external_user_id) VALUES ('polar', ?, ?, ?)",
        (access_token, refresh_token, external_user_id),
    )
    conn.commit()
    conn.close()
    return "<html><body style='font-family: sans-serif; max-width: 500px; margin: 50px auto;'><h2 style='color: green;'>✓ Connected to Polar AccessLink!</h2><p><a href='/'>← Return Home</a></p></body></html>"


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


def list_polar_exercises(access_token: str, samples: bool = True) -> list[dict]:
    """Lists the logged-in Polar user's exercises via AccessLink's current
    non-transactional `GET /v3/exercises` endpoint.

    NOTE for whoever wires this into sync_runs.py next: the exercise-
    transaction create/list/commit flow (POST .../exercise-transactions,
    GET .../exercise-transactions/{id}, PUT to commit) that older Polar
    integration guides describe is now labelled "Exercises (deprecated)"
    in Polar's own OpenAPI spec, in favor of this simpler transaction-free
    resource - confirmed 2026-07-23 against
    https://www.polar.com/accesslink-api/swagger.yaml. There is no polling
    cursor/transaction id to track here; Polar just returns whatever of
    the user's exercises are still visible (only samples uploaded to Flow
    in the last 30 days, and only after this user was registered to this
    client, are ever returned - there's no date-range query param). A
    stateless re-list + skip-already-published (the same pattern
    sync_runs.already_published_ids() already uses for Garmin/Google) is
    the natural fit, not a stored transaction cursor.

    Pass samples=True (default) so Polar embeds each exercise's raw sample
    arrays (heart rate, speed, cadence, ...) inline, avoiding a second
    per-exercise request for the common case of wanting HR data too.
    Returns [] on a 204 (no data available).
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"samples": str(samples).lower()}
    with httpx.Client(timeout=20.0) as client:
        response = client.get(f"{POLAR_ACCESSLINK_URL}/exercises", headers=headers, params=params)
    if response.status_code == 204:
        return []
    if response.status_code != 200:
        raise RuntimeError(f"Polar exercises API returned {response.status_code}: {response.text}")
    return response.json()


def fetch_polar_exercise_samples(access_token: str, exercise_id: str) -> tuple[pd.DataFrame, str | None]:
    """Fetches one Polar exercise (by the hashed `id` string returned in
    list_polar_exercises' entries) with samples embedded, and returns its
    heart-rate stream as a 1s-resampled DataFrame indexed by time with a
    single 'polar_hr' column - deliberately the same shape
    fetch_fitbit_hr_df returns, so a future third source slots into
    main.merge_telemetry / sync_runs.py's match/merge logic the same way
    Fitbit did (not built in this pass - see list_polar_exercises'
    docstring for the natural next step). Also returns the device name
    (e.g. "Polar Vantage V3", straight off the exercise's `device` field)
    for the same kind of summary field garmin_device_name/
    fitbit_device_name already are.

    Unlike Fitbit's individually-timestamped HR points, a Polar sample is
    a fixed-recording-rate series (recording-rate in seconds, typically
    1-5s) - each sample's timestamp is derived from the exercise's
    start_time plus (index * recording_rate). A null entry in the
    comma-separated data string means the sensor was offline for that
    tick - dropped, not filled, per this project's no-fill rule.

    The HR stream is picked out of the exercise's `samples` array by
    matching sample-type == "HEARTRATE". Polar's own OpenAPI spec only
    shows a placeholder numeric example ('1') for this field rather than
    documenting the real string enum - "HEARTRATE" is confirmed instead
    against a third-party reference client
    (github.com/StuMason/polar-flow) that names it explicitly. Flagged
    here in case a real API response ever disagrees.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            f"{POLAR_ACCESSLINK_URL}/exercises/{exercise_id}",
            headers=headers,
            params={"samples": "true"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Polar exercise API returned {response.status_code}: {response.text}")

    exercise = response.json()
    device_name = exercise.get("device")
    start_time = exercise.get("start_time")

    hr_sample = next(
        (s for s in exercise.get("samples", []) if str(s.get("sample-type", "")).upper() == "HEARTRATE"),
        None,
    )
    if hr_sample is None or not start_time:
        return pd.DataFrame(), device_name

    recording_rate = hr_sample.get("recording-rate") or 1
    raw_values = str(hr_sample.get("data", "")).split(",")

    base_time = pd.to_datetime(start_time)
    rows = []
    for i, raw_value in enumerate(raw_values):
        raw_value = raw_value.strip()
        if raw_value == "" or raw_value.lower() == "null":
            continue  # sensor offline for this tick - a real gap, never fabricated
        rows.append({
            "time": base_time + pd.Timedelta(seconds=i * recording_rate),
            "polar_hr": float(raw_value),
        })

    polar_df = pd.DataFrame(rows)
    if not polar_df.empty:
        polar_df = polar_df.set_index("time").resample("1s").mean()

    return polar_df, device_name


def parse_polar_fit_file(file_path: str) -> tuple[pd.DataFrame, str | None]:
    """Parses a Polar Flow FIT export (e.g. from a manual "Export training
    session" download) into a DataFrame indexed by time with 'polar_hr' and
    'polar_cadence_spm' columns (Polar-prefixed, since HR/cadence are the
    ones this pipeline also gets from Garmin - see merge_telemetry), plus
    unprefixed 'speed_mps'/'distance_m'/'elevation_m' columns matching
    parse_garmin_metrics' own naming for those fields, and the device name.

    GPS (position_lat/position_long) is deliberately NOT extracted: FIT
    encodes them as semicircle integers requiring a scale conversion this
    project hasn't verified against a known-good reference, and getting it
    wrong would silently corrupt weather-lookup geocoding (main.
    enrich_with_weather) rather than fail loudly. speed/distance/altitude
    have no such ambiguity - FIT's base units for these are already m/s and
    meters, confirmed directly against this file's real values.

    Used as the manual-file fallback for a run that predates AccessLink
    registration - see list_polar_exercises' docstring: the API only ever
    returns exercises uploaded to Flow *after* registration, so a run
    recorded before that point must come in this way instead.

    FIT is parsed directly (not the TCX export) because TCX is a converted
    export whose internal resampling/rounding behavior Polar doesn't
    document, whereas FIT is the watch's own recording format - no
    intermediate lossy step, consistent with this project's no-smoothing/
    no-fill rule.

    Unit note verified against this project's actual FIT+TCX pair (not
    assumed): Polar's 'cadence' field is strides/min (one full gait cycle,
    both feet), confirmed by total_strides / total_timer_time in the FIT's
    session message matching the per-record cadence average exactly, and
    cross-checked against the TCX export's <Cadence> values (identical raw
    numbers). Garmin's pipeline in this project uses 'directDoubleCadence'
    (see parse_garmin_metrics) - already-doubled full steps/min. So Polar's
    raw cadence is multiplied by 2 here to land on the same cadence_spm
    convention Garmin data uses; skipping this would silently make Polar
    look like it has half Garmin's cadence.

    No RR-interval / beat-to-beat data is present in this file (confirmed:
    no 'hrv' FIT message at all) - expected for a wrist PPG sensor with no
    chest strap paired, and directly relevant to the original point of this
    test (does the Vantage V3's own AFE give a clean HR signal on its own).
    """
    import fitparse

    fitfile = fitparse.FitFile(file_path)

    device_name = None
    for msg in fitfile.get_messages("file_id"):
        product_name = msg.get_value("product_name")
        if product_name:
            device_name = product_name
            break

    rows = []
    for record in fitfile.get_messages("record"):
        timestamp = record.get_value("timestamp")
        if timestamp is None:
            continue
        cadence = record.get_value("cadence")
        speed = record.get_value("enhanced_speed")
        if speed is None:
            speed = record.get_value("speed")
        altitude = record.get_value("enhanced_altitude")
        if altitude is None:
            altitude = record.get_value("altitude")
        rows.append({
            "time": pd.Timestamp(timestamp, tz="UTC"),
            "polar_hr": record.get_value("heart_rate"),
            "polar_cadence_spm": cadence * 2 if cadence is not None else None,
            "speed_mps": speed,
            "distance_m": record.get_value("distance"),
            "elevation_m": altitude,
        })

    polar_df = pd.DataFrame(rows)
    if not polar_df.empty:
        polar_df = polar_df.set_index("time").resample("1s").mean()

    return polar_df, device_name


def fetch_fitbit_hr_df(client: httpx.Client, headers: dict, start_iso: str, end_iso: str) -> tuple[pd.DataFrame, str | None]:
    """Fetches Fitbit heart-rate samples (via Google Health) for the given
    [start_iso, end_iso) window and returns a 1s-resampled DataFrame indexed
    by time with a single 'fitbit_hr' column, plus the Fitbit device's
    display name (e.g. "Inspire 3"), read off the first FITBIT-platform
    sample's dataSource.device.displayName - no extra API call, since these
    samples are already being scanned. Returns an empty DataFrame / None
    device name if no Fitbit-sourced samples fall in the window.

    Callers choose the window (e.g. "the latest Google Health RUNNING
    session", or any matched session in a bulk backfill) - this function only
    knows how to fetch and parse HR samples for a window it's given.
    """
    fitbit_df = pd.DataFrame()
    device_name = None

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

        if device_name is None:
            device_name = src.get("device", {}).get("displayName")

        t = dp.get("heartRate", {}).get("sampleTime", {}).get("physicalTime")
        bpm = dp.get("heartRate", {}).get("beatsPerMinute")
        if t and bpm is not None:
            fitbit_data.append({"time": pd.to_datetime(t), "fitbit_hr": bpm})

    fitbit_df = pd.DataFrame(fitbit_data)
    if not fitbit_df.empty:
        fitbit_df['fitbit_hr'] = pd.to_numeric(fitbit_df['fitbit_hr'], errors='coerce')
        fitbit_df = fitbit_df.set_index('time').resample('1s').mean()

    return fitbit_df, device_name


def merge_telemetry(garmin_df: pd.DataFrame, fitbit_df: pd.DataFrame, activity_id, garmin_device_name: str | None = None, fitbit_device_name: str | None = None) -> dict:
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
        "speed_mps": merged_df['speed_mps'].tolist(),
        "elevation_m": merged_df['elevation_m'].tolist() if 'elevation_m' in merged_df.columns else [],
        "distance_m": merged_df['distance_m'].tolist() if 'distance_m' in merged_df.columns else [],
        "latitude": merged_df['latitude'].tolist() if 'latitude' in merged_df.columns else [],
        "longitude": merged_df['longitude'].tolist() if 'longitude' in merged_df.columns else [],
        "grade_adjusted_speed_mps": merged_df['grade_adjusted_speed_mps'].tolist() if 'grade_adjusted_speed_mps' in merged_df.columns else [],
        "garmin_device_name": garmin_device_name,
        "fitbit_device_name": fitbit_device_name,
    }


def enrich_with_weather(payload: dict) -> dict:
    """Adds top-level temperature_c/humidity_pct fields to a run payload via
    one Open-Meteo lookup, using the run's start timestamp and the first
    non-null (latitude, longitude) pair found in its telemetry. Does network
    I/O (unlike merge_telemetry, which stays pure) - called separately by
    the orchestration layer (build_run_payload here, and sync_runs.py's
    fetch_and_publish_pair) after merge_telemetry returns. If no GPS fix is
    present in the run at all, or the lookup fails, both fields are None.
    """
    latitudes = payload.get("latitude") or []
    longitudes = payload.get("longitude") or []
    times = payload.get("time") or []

    lat = lon = None
    for candidate_lat, candidate_lon in zip(latitudes, longitudes):
        if candidate_lat is not None and candidate_lon is not None:
            lat, lon = candidate_lat, candidate_lon
            break

    if lat is None or lon is None or not times:
        payload["temperature_c"] = None
        payload["humidity_pct"] = None
        return payload

    result = weather.fetch_weather(lat, lon, times[0])
    payload["temperature_c"] = result["temperature_c"]
    payload["humidity_pct"] = result["humidity_pct"]
    return payload


def build_run_payload(google_access_token: str, use_garmin_cache: bool = True) -> dict:
    """Fetches the latest Garmin + Fitbit (via Google Health) telemetry and
    merges them into a single 1Hz time-aligned payload.

    Raises RuntimeError on failure - callers translate that into an HTTP
    response (the live /fetch-all-data route) or a CLI error (publish_run.py).
    Set use_garmin_cache=False to always pull the real latest activity instead
    of replaying cache_garmin.json (the publish pipeline always does this).
    """
    fitbit_df = pd.DataFrame()
    fitbit_device_name = None
    garmin_df = pd.DataFrame()
    garmin_device_name = None
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

            fitbit_df, fitbit_device_name = fetch_fitbit_hr_df(client, headers, start_t, end_t)

        print(f"DEBUG: Fitbit rows: {len(fitbit_df)}")

    # --- SECTION 2: Garmin Data ---
    cache_file = "cache_garmin.json"
    details = None
    if use_garmin_cache and os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            details = json.load(f)
        # No device-name resolution here - the cached details blob (local dev
        # only, never used by the publish pipeline) has no accompanying
        # activity summary/deviceId to resolve against.
    else:
        garmin_client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        garmin_client.login()
        activities = garmin_client.get_activities(0, 1)
        if activities:
            details = garmin_client.get_activity_details(activities[0]['activityId'])
            device_id = str(activities[0].get('deviceId'))
            devices = garmin_client.get_devices()
            device_map = {str(d.get('deviceId')): d.get('displayName') or d.get('productDisplayName') for d in devices}
            garmin_device_name = device_map.get(device_id)
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
    payload = merge_telemetry(garmin_df, fitbit_df, activity_id, garmin_device_name, fitbit_device_name)

    # --- SECTION 4: Weather enrichment (network I/O, kept out of merge_telemetry) ---
    return enrich_with_weather(payload)


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
