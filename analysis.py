"""Pure analysis functions for HR-zone and pace-bucket comparisons between
Garmin and Fitbit heart-rate series. No I/O - callers own reading/writing.

Objective #4 (training-load overestimation): computes Stagno's Modified
TRIMP (Stagno, Thatcher & van Someren, 2007, J Sports Sci 25:6, 629-634,
doi:10.1080/02640410600811817) - a published, exact formula, not a
reverse-engineered proprietary one - independently for each device's own HR
series, and compares the two totals. The totals diverging is the
overestimation signal. Superseded an earlier Active Zone Minutes (AZM)
replication attempt: AZM is Fitbit's own undisclosed algorithm, and
back-calculating it turned out to be systematically sensitive to exactly
the kind of sensor artifact (transient HR spikes) this project exists to
investigate, with no principled way to pick free parameters (bin width,
HRmax) that wasn't just curve-fitting five data points. TRIMP has no such
free parameters.

Objective #8 (pace-bucketed HR distribution): buckets a run by pace (from
speed_mps, converted to min/km) and, for each bucket, collects the
distribution of HR values each device reported while running at that pace.

Per the project's data-integrity rule: gaps (None) in the input series are
skipped, never filled/estimated/interpolated.
"""
import math
import statistics


# (lower_pct_hrmax, upper_pct_hrmax, weight) - Table I, Stagno et al. (2007).
# Below 65% HRmax counts 0x (a deliberate floor - the zones are anchored on
# lactate-threshold breakpoints, not evenly-spaced %HRmax bands, and the
# weights themselves come from an exponential fit to blood-lactate response,
# not a linear 1-5 scale like Edwards'. Unlike Fitbit's AZM this is a
# published, citable formula we're not reverse-engineering.
TRIMP_ZONES = [
    (0.65, 0.72, 1.25),
    (0.72, 0.79, 1.71),
    (0.79, 0.86, 2.54),
    (0.86, 0.93, 3.61),
    (0.93, 2.00, 5.16),  # open-ended upper bound
]


def stagno_trimp(hr_series: list, hr_max: float, sample_seconds: float = 1.0) -> float:
    """Stagno's Modified TRIMP: each valid HR sample is classified into a
    %HRmax band (see TRIMP_ZONES) and accumulates weighted minutes
    (sample_seconds/60 * band weight). Classification happens per real
    sample, never on an averaged/smoothed value - averaging before
    classifying would systematically undercount (Jensen's inequality on
    this convex weighting), and would wash out exactly the transient spikes
    this project investigates.

    None samples are skipped, not estimated/interpolated. Returns 0.0 (never
    None) when there are no valid samples - a real run with zero load is a
    valid result, unlike "no HR max configured" (that guard lives with
    callers).
    """
    total = 0.0
    lowest_floor = TRIMP_ZONES[0][0]
    top_weight = TRIMP_ZONES[-1][2]

    for hr in hr_series:
        if hr is None:
            continue
        pct = hr / hr_max
        if pct < lowest_floor:
            continue  # below the lowest band - no contribution

        for lower, upper, weight in TRIMP_ZONES:
            if pct >= lower and (pct < upper or weight == top_weight):
                total += (sample_seconds / 60.0) * weight
                break

    return total


def _interpolate_gaps(hr_series: list) -> list:
    """Linear-in-time interpolation across internal None gaps in an
    evenly-spaced, 1Hz-indexed HR series. Only bridges gaps strictly
    *between* two real samples - positions before the first or after the
    last valid sample are left as None, never extrapolated.

    This is a deliberate, narrow exception to the project's no-fill rule,
    authorized specifically for paired_trimp(): median-rate weighting was
    found to systematically undercount whichever device has more/uneven
    real dropouts within the shared window (empirically Fitbit, via Google
    Health sync, far more than Garmin's native telemetry), because a median
    gap ignores a skewed dropout distribution. Interpolating both devices
    onto the exact same instants removes the asymmetry at the root, rather
    than trying to statistically correct for it. Never used for the
    stored/displayed series - charting keeps real gaps as visual breaks.
    """
    valid_idx = [i for i, v in enumerate(hr_series) if v is not None]
    if len(valid_idx) < 2:
        return list(hr_series)

    filled = list(hr_series)
    for a, b in zip(valid_idx, valid_idx[1:]):
        if b - a <= 1:
            continue
        v0, v1 = hr_series[a], hr_series[b]
        for i in range(a + 1, b):
            frac = (i - a) / (b - a)
            filled[i] = v0 + frac * (v1 - v0)
    return filled


def paired_trimp(time_offsets_seconds: list, garmin_hr: list, fitbit_hr: list, hr_max: float):
    """Fair TRIMP comparison between two devices with different real
    sampling patterns.

    Intersects both devices' valid-data windows (so neither device's
    lead-in/tail time with no counterpart inflates its total for free).
    Within that window, both devices are interpolated (see
    _interpolate_gaps) onto the exact same instants, so the two totals are
    built from the exact same number of samples at the exact same times -
    not an estimated per-device time weight applied to two differently-
    gapped sample sets.

    Returns {"garmin": total_trimp, "fitbit": total_trimp}, or None if no
    fair comparison is possible (a device has zero/one valid sample
    anywhere, or the two valid windows don't overlap) - callers treat this
    the same as "no hr_max resolved".
    """
    garmin_valid = [i for i, v in enumerate(garmin_hr) if v is not None]
    fitbit_valid = [i for i, v in enumerate(fitbit_hr) if v is not None]
    if not garmin_valid or not fitbit_valid:
        return None

    start_idx = max(garmin_valid[0], fitbit_valid[0])
    end_idx = min(garmin_valid[-1], fitbit_valid[-1])
    if start_idx >= end_idx:
        return None

    window_offsets = time_offsets_seconds[start_idx:end_idx + 1]
    sample_seconds = (window_offsets[-1] - window_offsets[0]) / (len(window_offsets) - 1)

    garmin_filled = _interpolate_gaps(garmin_hr[start_idx:end_idx + 1])
    fitbit_filled = _interpolate_gaps(fitbit_hr[start_idx:end_idx + 1])

    garmin_trimp = stagno_trimp(garmin_filled, hr_max, sample_seconds=sample_seconds)
    fitbit_trimp = stagno_trimp(fitbit_filled, hr_max, sample_seconds=sample_seconds)

    return {"garmin": garmin_trimp, "fitbit": fitbit_trimp}


def median_sample_rate_hz(time_offsets_seconds: list, hr_series: list):
    """Median real sampling rate (Hz) of a device's HR series, derived from
    the gaps between consecutive non-None samples' timestamps. Returns None
    if fewer than 2 valid samples exist (no interval to measure).
    """
    valid_offsets = [time_offsets_seconds[i] for i, v in enumerate(hr_series) if v is not None]
    if len(valid_offsets) < 2:
        return None

    deltas = sorted(b - a for a, b in zip(valid_offsets, valid_offsets[1:]))
    n = len(deltas)
    mid = n // 2
    median_delta = deltas[mid] if n % 2 else (deltas[mid - 1] + deltas[mid]) / 2
    if median_delta <= 0:
        return None
    return 1.0 / median_delta


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


def worst_pace_divergence(pace_hr_distribution: dict) -> dict | None:
    """Given a run's {"garmin": {...}, "fitbit": {...}} pace_hr_distribution
    (analysis.hr_distribution_by_pace output for each device), finds the
    shared pace bucket with the largest |mean_fitbit - mean_garmin| gap.
    Returns {"bucket": label, "gap_bpm": float} or None if no shared bucket
    exists. Only used for a summary stat, not detection - Objective #3's
    detection heuristic still doesn't exist.
    """
    dist_garmin = pace_hr_distribution.get("garmin") or {}
    dist_fitbit = pace_hr_distribution.get("fitbit") or {}
    shared_buckets = set(dist_garmin) & set(dist_fitbit)
    if not shared_buckets:
        return None

    worst_bucket = None
    worst_gap = -1.0
    for bucket in shared_buckets:
        mean_garmin = dist_garmin[bucket]["mean"]
        mean_fitbit = dist_fitbit[bucket]["mean"]
        if mean_garmin is None or mean_fitbit is None:
            continue
        gap = abs(mean_fitbit - mean_garmin)
        if gap > worst_gap:
            worst_gap = gap
            worst_bucket = bucket

    if worst_bucket is None:
        return None

    return {"bucket": worst_bucket, "gap_bpm": round(worst_gap, 1)}
