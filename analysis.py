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


def paired_trimp(time_offsets_seconds: list, hr_series_by_device: dict, hr_max: float):
    """Fair TRIMP comparison across two or more devices with different real
    sampling patterns.

    hr_series_by_device maps a device key (e.g. "garmin", "fitbit", "polar")
    to its HR series - any device whose series is entirely None (e.g. the
    "polar" key on a run with no reference device worn) is dropped before
    comparing, so old two-device runs and new three-device runs share the
    same function without a separate code path.

    Intersects every remaining device's valid-data window (so no device's
    lead-in/tail time with no counterpart inflates its total for free).
    Within that window, every device is interpolated (see
    _interpolate_gaps) onto the exact same instants, so all totals are
    built from the exact same number of samples at the exact same times -
    not an estimated per-device time weight applied to differently-gapped
    sample sets.

    Returns {device_key: total_trimp, ...} for every device with data, or
    None if no fair comparison is possible (fewer than two devices have any
    valid sample, or the valid windows don't overlap) - callers treat this
    the same as "no hr_max resolved".
    """
    valid_idx_by_device = {
        device: [i for i, v in enumerate(series) if v is not None]
        for device, series in hr_series_by_device.items()
    }
    valid_idx_by_device = {device: idx for device, idx in valid_idx_by_device.items() if idx}
    if len(valid_idx_by_device) < 2:
        return None

    start_idx = max(idx[0] for idx in valid_idx_by_device.values())
    end_idx = min(idx[-1] for idx in valid_idx_by_device.values())
    if start_idx >= end_idx:
        return None

    window_offsets = time_offsets_seconds[start_idx:end_idx + 1]
    sample_seconds = (window_offsets[-1] - window_offsets[0]) / (len(window_offsets) - 1)

    result = {}
    for device in valid_idx_by_device:
        filled = _interpolate_gaps(hr_series_by_device[device][start_idx:end_idx + 1])
        result[device] = stagno_trimp(filled, hr_max, sample_seconds=sample_seconds)

    return result


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


def worst_pace_divergence_vs_reference(pace_hr_distribution: dict, device_key: str, reference_key: str = "polar") -> dict | None:
    """Given a run's pace_hr_distribution, finds the pace bucket (among
    buckets shared between device_key and reference_key) with the largest
    |mean_device - mean_reference| gap. Returns {"bucket": label,
    "gap_bpm": float, "reference_mean_bpm": float} or None if no shared
    bucket exists (e.g. reference_key has no data - a 2-way run). Unlike
    worst_pace_divergence() (symmetric Garmin-vs-Fitbit, no presumed-
    accurate side), reference_key is a silver-standard device (Polar H10),
    so its mean IS the true HR at that bucket - no heuristic needed.
    """
    dist_device = pace_hr_distribution.get(device_key) or {}
    dist_reference = pace_hr_distribution.get(reference_key) or {}
    shared_buckets = set(dist_device) & set(dist_reference)
    if not shared_buckets:
        return None

    worst_bucket = None
    worst_gap = -1.0
    worst_reference_mean = None
    for bucket in shared_buckets:
        mean_device = dist_device[bucket]["mean"]
        mean_reference = dist_reference[bucket]["mean"]
        if mean_device is None or mean_reference is None:
            continue
        gap = abs(mean_device - mean_reference)
        if gap > worst_gap:
            worst_gap = gap
            worst_bucket = bucket
            worst_reference_mean = mean_reference

    if worst_bucket is None:
        return None

    return {
        "bucket": worst_bucket,
        "gap_bpm": round(worst_gap, 1),
        "reference_mean_bpm": round(worst_reference_mean, 1),
    }


# Objective #3, Phase 2: per-second cadence-lock scoring.
#
# This is the "production" form of phase1_eda/eda.py's validated design -
# same harmonic grid, same gating constants (WIN=40/STEP=5/MIN_VALID=8,
# CAD_MIN=60/SPEED_MIN=0.5, MAE_LOCK=5.0/FRAC_TOL=6.0), same directional
# (not merely magnitude) separation requirement - not a fresh redesign.
# phase1_eda/REPORT.md ran this exact design across all 31 published runs
# before it landed here, and found: naive HR-near-cadence proximity fires
# almost everywhere (including all three negative-flagged runs) and is
# uninformative alone; the real signature only separates out once proximity
# is combined with *sustained* tracking over a window AND the on-harmonic
# device reading *materially higher* than the other device's contemporaneous
# reading (genuine lock inflates HR upward onto a harmonic; a device
# reading LOW near a sub-harmonic, e.g. k=1/2 at an easy pace, is
# overwhelmingly a real low HR coinciding with cadence/2, not lock -
# REPORT.md Section 4). Magnitude-only separation (as opposed to signed,
# directional separation) was tried and rejected for exactly this reason.
#
# The thresholds below (MAE_LOCK_BPM, DIRECTIONAL_GAP_MIN_BPM, etc.) are the
# same values the EDA validated against real data, carried over deliberately
# rather than re-guessed - but they are still PROVISIONAL. Per
# CADENCE_LOCK_DETECTOR_PROPOSAL.md Section 5/7.6, real thresholds get set
# against Phase 3's manual labels in Phase 4; picking them by eye against 31
# runs with no held-out check is the same free-parameter curve-fitting this
# project already rejected once (see stagno_trimp's docstring / the AZM
# story above). Treat this as Phase 2's deliverable - a per-second SCORE,
# not a finished decision boundary.

# 0.25..4.0 in 0.25 steps, plus rational thirds - phase1_eda/REPORT.md
# Section 2 found k=1.5 and k=1.333 (4/3) as real, recurring harmonics
# (5+ runs, not a one-off), not just the {1/2, 1, 2} the original proposal
# guessed at before running the EDA. Do not narrow this back down without
# re-running the EDA against the full dataset first.
CADENCE_LOCK_K_GRID = sorted(set(
    [round(0.25 * i, 4) for i in range(1, 17)]
    + [round(1 / 3, 4), round(2 / 3, 4), round(4 / 3, 4), round(5 / 3, 4), round(7 / 3, 4)]
))


def _cadence_lock_window_scan(
    hr: list,
    other_hr: list,
    cadence_spm: list,
    speed_mps: list,
    window_seconds: int = 40,
    step_seconds: int = 5,
    min_valid_samples: int = 8,
    cadence_min_spm: float = 60.0,
    speed_min_mps: float = 0.5,
    frac_within_tol_bpm: float = 6.0,
) -> list:
    """Slides a window over one device's HR series and fits the best cadence
    harmonic in each - raw fit statistics only, no lock/no-lock decision
    yet (that's _merge_loose_stretches + _score_stretch). Mirrors
    phase1_eda/eda.py's window_scan() exactly, so Phase 2 reproduces what
    was actually validated across all 31 runs rather than a fresh guess at
    the same idea.

    A window is skipped entirely (not included in the returned list) unless
    it has >= min_valid_samples instants with both a real HR value and a
    *gated* cadence reading (cadence_spm >= cadence_min_spm AND
    speed_mps >= speed_min_mps together - corroborating real mechanical
    motion, since cadence_spm alone can be a masked Garmin micro-dropout
    reported as 0.0 rather than genuine standing; see parse_garmin_metrics).

    Returns a list of dicts: start, end, n_valid, k (best-fitting harmonic
    from CADENCE_LOCK_K_GRID by mean absolute error), mae, frac_within
    (fraction of samples within frac_within_tol_bpm of k*cadence),
    hr_mean, other_mean (None if the other device had zero valid samples
    anywhere in the window - "no data to compare against", not a zero gap).
    """
    n = len(hr)
    windows = []
    for start in range(0, max(1, n - window_seconds + 1), step_seconds):
        end = min(start + window_seconds, n)
        idx = [
            i for i in range(start, end)
            if hr[i] is not None
            and cadence_spm[i] is not None and speed_mps[i] is not None
            and cadence_spm[i] >= cadence_min_spm and speed_mps[i] >= speed_min_mps
        ]
        if len(idx) < min_valid_samples:
            continue

        h = [hr[i] for i in idx]
        c = [cadence_spm[i] for i in idx]

        best_k, best_mae = None, float("inf")
        for k in CADENCE_LOCK_K_GRID:
            mae = sum(abs(h[j] - k * c[j]) for j in range(len(idx))) / len(idx)
            if mae < best_mae:
                best_mae, best_k = mae, k
        frac_within = sum(1 for j in range(len(idx)) if abs(h[j] - best_k * c[j]) <= frac_within_tol_bpm) / len(idx)

        other_valid = [other_hr[i] for i in range(start, end) if other_hr[i] is not None]
        other_mean = sum(other_valid) / len(other_valid) if other_valid else None
        hr_mean = sum(h) / len(h)

        windows.append({
            "start": start, "end": end, "n_valid": len(idx),
            "k": best_k, "mae": best_mae, "frac_within": frac_within,
            "hr_mean": hr_mean, "other_mean": other_mean,
        })
    return windows


def _merge_loose_stretches(windows: list, mae_lock_bpm: float = 5.0, frac_within_min: float = 0.6) -> list:
    """Filters to the "loose" lock gate (tight-and-sustained-within-one-
    window: mae <= mae_lock_bpm AND frac_within >= frac_within_min - no
    plausibility check yet), then merges same-k loose-qualifying windows
    into stretches wherever they overlap or touch in time.

    This bridges gaps the way phase1_eda/eda.py's merge_stretches() does:
    a stretch extends across windows whose time ranges overlap, not only
    windows adjacent in the full window list - so one intervening
    non-qualifying window doesn't necessarily break a stretch, since 40s
    windows at a 5s step already overlap by 35s. Caught during
    verification: an earlier draft of this code required strict list-
    adjacency instead, which was measurably stricter than what the EDA
    actually validated (produced far fewer locked seconds than
    phase1_eda/REPORT.md's own per-run totals on the same real runs) - not
    a safe conservative substitute, a different, unvalidated method.

    Returns a list of {"k", "start", "end", "members": [window dicts]}.
    """
    loose = [w for w in windows if w["mae"] <= mae_lock_bpm and w["frac_within"] >= frac_within_min]
    loose.sort(key=lambda w: w["start"])

    stretches = []
    cur = None
    for w in loose:
        if cur is not None and w["k"] == cur["k"] and w["start"] <= cur["end"]:
            cur["end"] = max(cur["end"], w["end"])
            cur["members"].append(w)
        else:
            if cur is not None:
                stretches.append(cur)
            cur = {"k": w["k"], "start": w["start"], "end": w["end"], "members": [w]}
    if cur is not None:
        stretches.append(cur)
    return stretches


def _score_stretch(
    stretch: dict,
    min_stretch_seconds: float = 45.0,
    stretch_mae_lock_bpm: float = 4.5,
    directional_gap_min_bpm: float = 25.0,
    gap_saturation_bpm: float = 50.0,
) -> tuple:
    """Applies the "strict" + "directional" tiers to one merged stretch -
    phase1_eda/REPORT.md Section 4's central finding that proximity alone
    is ~80% coincidence, and only genuinely separates from coincidence once
    BOTH a minimum sustained duration AND a materially-higher-than-the-
    other-device reading are required together, not either alone.

    A stretch qualifies only if ALL of:
      - span (end - start) >= min_stretch_seconds. A single isolated 40s
        window (the window_seconds default) falls short of this by
        design - one window is not sustained evidence by itself.
      - mean MAE across member windows <= stretch_mae_lock_bpm. Tighter
        than the 5.0 mae_lock_bpm used to decide which windows merge in
        the first place - the two are deliberately different thresholds
        in the EDA (5.0 gates window inclusion, 4.5 gates the merged
        stretch), not the same number checked twice. Caught during
        verification: an earlier draft used only the looser 5.0 at the
        merged level and let a real, dur>=45, mae_mean=4.76 stretch on a
        negative-flagged run through, where phase1_eda/REPORT.md's own
        pipeline (which does apply the tighter 4.5) correctly excluded it.
      - the stretch's mean HR reads >= directional_gap_min_bpm higher than
        the other device's mean HR over the same members (only upward
        deviation onto a harmonic counts as lock evidence; a device
        reading LOW near a sub-harmonic, e.g. k=1/2 at an easy pace, is
        overwhelmingly a real low HR coinciding with cadence/2, not lock -
        REPORT.md Section 4). Default 25.0: phase1_eda/eda.py's
        "directional" filter is layered ON TOP of its "strict" filter
        (magnitude gap >= 25, either sign), then adds a same-sign gap >= 15
        check - since magnitude >= 25 already implies signed >= 25 on the
        positive branch, the real combined effective minimum is 25, not
        15. A stretch with no member window carrying any valid
        other-device reading has nothing to compare against and is
        rejected outright (mean_mae is still returned for diagnostics).

    Returns (qualifies: bool, score: float in [0,1], mean_mae: float,
    gap_bpm: float | None). score is 0.0 for a non-qualifying stretch, else
    scaled linearly from directional_gap_min_bpm (0.0) to
    gap_saturation_bpm (1.0) - how far past the validated minimum
    separation this stretch sits, never used to let a *non*-qualifying
    stretch score above 0 (the gates above are gates, not inputs blended
    with everything else).
    """
    members = stretch["members"]
    dur = stretch["end"] - stretch["start"]
    mean_mae = sum(w["mae"] for w in members) / len(members)

    other_means = [w["other_mean"] for w in members if w["other_mean"] is not None]
    if not other_means:
        return False, 0.0, mean_mae, None
    hr_means = [w["hr_mean"] for w in members]
    gap_bpm = (sum(hr_means) / len(hr_means)) - (sum(other_means) / len(other_means))

    qualifies = dur >= min_stretch_seconds and mean_mae <= stretch_mae_lock_bpm and gap_bpm >= directional_gap_min_bpm
    score = (
        min(1.0, max(0.0, (gap_bpm - directional_gap_min_bpm) / (gap_saturation_bpm - directional_gap_min_bpm)))
        if qualifies else 0.0
    )
    return qualifies, score, mean_mae, gap_bpm


def cadence_lock_scan(
    hr: list,
    other_hr: list,
    cadence_spm: list,
    speed_mps: list,
    window_kwargs: dict = None,
    merge_kwargs: dict = None,
    score_kwargs: dict = None,
) -> list:
    """Objective #3, Phase 2: per-second cadence-lock score for ONE device,
    using the OTHER device's contemporaneous HR as a local plausibility
    reference. Call this twice per run (swap hr/other_hr) to get both
    devices' independent score streams -
    CADENCE_LOCK_DETECTOR_PROPOSAL.md Section 1 is explicit that lock must
    be scored per-device, not per-instant-global: positive_both runs show
    the two devices' suspected-lock windows occurring at *different*
    stretches of the run, not concurrently (phase1_eda/REPORT.md Section 3,
    the one run with both devices firing had exactly 0s of overlap) - so a
    single shared per-instant label would conflate two independent claims.

    Pipeline: _cadence_lock_window_scan (raw per-window harmonic fits) ->
    _merge_loose_stretches (bridge overlapping same-k windows into
    candidate stretches) -> _score_stretch (strict+directional gates,
    applied to each merged stretch) -> scatter each qualifying stretch's
    score across every second it spans, via max where stretches of
    different k overlap in time for the same device (rare, but not
    structurally impossible). Each stage takes its own kwargs dict so Phase
    4 can retune one without touching the others - see each function's
    docstring for what it owns.

    Returns a list the same length as hr. Each element is either:
      - None: this instant can't be assessed at all - hr[i], cadence_spm[i],
        or speed_mps[i] is missing, or no window anywhere covered it with
        enough valid samples. This is "no data", never "not locked" - the
        no-fill rule applies to this derived series exactly as it does to
        the stored telemetry.
      - a dict {"score": float in [0,1] (0.0 means assessed, no qualifying
        stretch found here), "k": harmonic, "mae": mean bpm error,
        "gap_bpm": signed separation from the other device} - k/mae/gap_bpm
        are None when score is 0.0 (nothing to attribute a "why" to), and
        otherwise identify the specific qualifying stretch this second's
        score came from, so a firing is always traceable to one concrete,
        explainable stretch rather than an opaque blend of several.

    All thresholds/window sizing are the EDA's own validated values,
    carried over deliberately rather than re-guessed - but still
    PROVISIONAL. Per CADENCE_LOCK_DETECTOR_PROPOSAL.md Section 5/7.6, real
    thresholds get set against Phase 3's manual labels in Phase 4; picking
    them by eye with no held-out check is the free-parameter curve-fitting
    this project already rejected once (see stagno_trimp's docstring).
    """
    window_kwargs = window_kwargs or {}
    merge_kwargs = merge_kwargs or {}
    score_kwargs = score_kwargs or {}

    n = len(hr)
    windows = _cadence_lock_window_scan(hr, other_hr, cadence_spm, speed_mps, **window_kwargs)

    covered = [False] * n
    for w in windows:
        for i in range(w["start"], w["end"]):
            covered[i] = True

    stretches = _merge_loose_stretches(windows, **merge_kwargs)
    best_score = [0.0] * n
    best_meta = [None] * n
    for stretch in stretches:
        qualifies, score, mean_mae, gap_bpm = _score_stretch(stretch, **score_kwargs)
        if not qualifies:
            continue
        for i in range(stretch["start"], stretch["end"]):
            if score >= best_score[i]:
                best_score[i] = score
                best_meta[i] = {"k": stretch["k"], "mae": round(mean_mae, 2), "gap_bpm": round(gap_bpm, 1)}

    result = []
    for i in range(n):
        assessable = hr[i] is not None and cadence_spm[i] is not None and speed_mps[i] is not None and covered[i]
        if not assessable:
            result.append(None)
            continue
        meta = best_meta[i] or {"k": None, "mae": None, "gap_bpm": None}
        result.append({"score": best_score[i], **meta})
    return result
