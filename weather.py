"""Looks up historical hourly temperature and humidity for a run's start time/location via Open-Meteo's free archive API."""
import numpy as np
import pandas as pd
import httpx

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_weather(latitude: float, longitude: float, timestamp_iso: str) -> dict:
    """Fetches the Open-Meteo hourly temperature (°C) / relative humidity (%)
    closest to timestamp_iso (converted to UTC) at the given coordinates.
    Never raises - returns {"temperature_c": None, "humidity_pct": None} and
    prints a clear message on any failure (network, timeout, empty response),
    since this is an enrichment step that must never block a publish.
    """
    empty = {"temperature_c": None, "humidity_pct": None}
    try:
        ts = pd.to_datetime(timestamp_iso)
        ts_utc = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
        date_str = ts_utc.strftime("%Y-%m-%d")

        with httpx.Client(timeout=20.0) as client:
            response = client.get(OPEN_METEO_URL, params={
                "latitude": latitude,
                "longitude": longitude,
                "start_date": date_str,
                "end_date": date_str,
                "hourly": "temperature_2m,relative_humidity_2m",
            })
        response.raise_for_status()
        data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        humidity = hourly.get("relative_humidity_2m", [])

        if not times:
            print(f"Weather lookup: no hourly data returned for ({latitude}, {longitude}) on {date_str}")
            return empty

        hours = pd.to_datetime(times, utc=True)
        idx = int(np.argmin(np.abs((hours - ts_utc).to_numpy())))

        return {
            "temperature_c": float(temps[idx]) if idx < len(temps) and temps[idx] is not None else None,
            "humidity_pct": float(humidity[idx]) if idx < len(humidity) and humidity[idx] is not None else None,
        }
    except Exception as e:
        print(f"Weather lookup failed for ({latitude}, {longitude}) @ {timestamp_iso}: {type(e).__name__}: {e}")
        return empty
