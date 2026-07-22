"""Pure analysis functions for HR-zone and pace-bucket comparisons between
Garmin and Fitbit heart-rate series. No I/O - callers own reading/writing.

Objective #4 (Active Zone Minutes replication): back-calculates Fitbit's
stated Active Zone Minutes (AZM) methodology - simple integer multipliers
per %HRmax zone - and applies that *same* weighting scheme to both Garmin's
and Fitbit's HR series independently. The totals diverging is the
overestimation signal.

Objective #8 (pace-bucketed HR distribution): buckets a run by pace (from
speed_mps, converted to min/km) and, for each bucket, collects the
distribution of HR values each device reported while running at that pace.

Zone thresholds/multipliers below are Fitbit's stated AZM methodology as
best recalled: Fat Burn (50-69% HRmax) counts 1x, Cardio (70-84%) and Peak
(85%+) both count 2x, below 50% counts 0x. Not fully verified against any
particular Fitbit account's configuration (some devices use Karvonen/HRR-
based custom zones instead of straight %HRmax) - kept as a single,
clearly-labeled constants table so it's a one-place fix if it turns out to
differ.

Per the project's data-integrity rule: gaps (None) in the input series are
skipped, never filled/estimated/interpolated.
"""
import math
import statistics


# (name, lower_pct_hrmax, upper_pct_hrmax, multiplier)
HR_ZONES = [
    ("fat_burn", 0.50, 0.70, 1),
    ("cardio",   0.70, 0.85, 2),
    ("peak",     0.85, 1.01, 2),  # open-ended upper bound
]


def active_zone_minutes(hr_series: list, hr_max: float, sample_seconds: float = 1.0) -> dict:
    """Replicates Fitbit's Active Zone Minutes methodology: buckets each
    valid HR sample into a %HRmax zone (see HR_ZONES) and accumulates
    weighted minutes (sample_seconds/60 * multiplier per sample).

    None samples are skipped, not estimated/interpolated - a gap in the
    input HR series simply doesn't contribute to any zone.

    Returns {"fat_burn_minutes", "cardio_minutes", "peak_minutes",
    "total_azm"} in minutes. All 0.0 (never None) when there are no valid
    samples - a real run with zero zone-minutes is a valid, meaningful
    result, unlike "no HR max configured" (that guard lives with callers).
    """
    minutes_by_zone = {name: 0.0 for name, _, _, _ in HR_ZONES}
    total_azm = 0.0
    last_zone_name = HR_ZONES[-1][0]
    lowest_floor = HR_ZONES[0][1]

    for hr in hr_series:
        if hr is None:
            continue
        pct = hr / hr_max
        if pct < lowest_floor:
            continue  # below the lowest zone floor - 0x, no contribution

        for name, lower, upper, multiplier in HR_ZONES:
            if pct >= lower and (pct < upper or name == last_zone_name):
                minutes = (sample_seconds / 60.0) * multiplier
                minutes_by_zone[name] += minutes
                total_azm += minutes
                break

    return {
        "fat_burn_minutes": minutes_by_zone["fat_burn"],
        "cardio_minutes": minutes_by_zone["cardio"],
        "peak_minutes": minutes_by_zone["peak"],
        "total_azm": total_azm,
    }


def pace_bucket_label(speed_mps, bucket_width_min_per_km: float = 0.5):
    """Converts a speed sample (m/s) to a pace bucket label, e.g.
    "5:00-5:30/km". Returns None if speed_mps is None or <= 0 - no pace can
    be derived from missing/zero motion data, so it's excluded rather than
    estimated.
    """
    if speed_mps is None or speed_mps <= 0:
        return None

    pace_min_per_km = (1000.0 / speed_mps) / 60.0
    bucket_index = math.floor(pace_min_per_km / bucket_width_min_per_km)
    lower = bucket_index * bucket_width_min_per_km
    upper = lower + bucket_width_min_per_km

    def fmt(minutes_value):
        m = int(minutes_value)
        s = int(round((minutes_value - m) * 60))
        if s == 60:
            m += 1
            s = 0
        return f"{m}:{s:02d}"

    return f"{fmt(lower)}-{fmt(upper)}/km"


def _percentile(sorted_values: list, pct: float) -> float:
    """Linear-interpolation percentile (matches the common definition used
    for the 1.5*IQR outlier rule)."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[int(f)] * (c - k) + sorted_values[int(c)] * (k - f)


def _distribution_stats(values: list) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "std": None, "outlier_count": 0}

    sorted_values = sorted(values)
    mean = sum(values) / n
    median = statistics.median(sorted_values)
    std = statistics.stdev(values) if n > 1 else 0.0

    q1 = _percentile(sorted_values, 0.25)
    q3 = _percentile(sorted_values, 0.75)
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr
    outlier_count = sum(1 for v in values if v < lower_fence or v > upper_fence)

    return {"n": n, "mean": mean, "median": median, "std": std, "outlier_count": outlier_count}


def hr_distribution_by_pace(speed_mps_series: list, hr_series: list, bucket_width_min_per_km: float = 0.5) -> dict:
    """Groups same-index (speed, hr) pairs into pace buckets (keyed off
    speed_mps via pace_bucket_label) and computes the HR distribution
    within each bucket.

    Pairs where either value is None, or where speed_mps yields no bucket
    (<=0 / None), are skipped - not filled/estimated.

    Returns {bucket_label: {"n", "mean", "median", "std", "outlier_count"}},
    outliers via the standard 1.5*IQR rule.
    """
    buckets = {}
    for speed, hr in zip(speed_mps_series, hr_series):
        if speed is None or hr is None:
            continue
        label = pace_bucket_label(speed, bucket_width_min_per_km)
        if label is None:
            continue
        buckets.setdefault(label, []).append(hr)

    return {label: _distribution_stats(values) for label, values in buckets.items()}
