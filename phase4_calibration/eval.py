"""Phase 4 - calibrate and evaluate the unsupervised cadence-lock detector
against Phase 3's manual per-second labels.

Offline analysis only. Imports the live detector from analysis.py, reads the
published payloads read-only, and writes its artifacts into this directory.
Nothing here touches docs/, the publish pipeline, or any stored series.

Label semantics (this is the whole reason the metric design below is not a
plain precision/recall):
  * Phase 3 is fitbit-only, single-device-blinded. 11 runs carry a `reviewed`
    sentinel row ("I looked at this whole run for this device"); 19 `locked`
    ranges exist across 6 of them.
  * The owner painted `locked` DELIBERATELY CONSERVATIVELY: the gradual
    onset/offset ramp into and out of a lock was excluded on purpose ("so
    that I'm not capturing the gradual increase, just the definitive high
    lock period"), and short transient discrepancies were excluded as "not
    clean enough for tuning".
  * Therefore an unpainted second inside a `reviewed` run is NOT a confirmed
    negative. It is "not clearly locked" - a mixture of genuine good HR AND
    ambiguous ramp/transient seconds the owner declined to commit on.
    Counting all of them as hard negatives would penalise the detector for
    firing exactly where the owner told us they abstained.

Metric design (see report):
  - STRICT  precision: every unpainted assessable second is a negative.
  - BUFFERED precision at guard band B: seconds within B s of any labeled
    range are neither positive nor negative - they are excluded ("gray").
    Firings there are reported separately, never silently forgiven.
  Both are reported at every calibration point. The buffered variant is the
  primary objective; the strict variant is the honesty floor.

No-fill compliance: this script performs no interpolation of any kind. Nulls
are skipped by the detector exactly as in production.
"""
import csv
import json
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analysis  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "docs", "data")
LABELS = os.path.join(REPO, "phase3_labels", "labels.csv")
OUT = os.path.dirname(os.path.abspath(__file__))

# Runs the Phase-1 EDA already showed fire zero candidates while the owner
# reported lock - the hard-effort blind spot. Tracked separately throughout.
BLIND_SPOT_RUNS = {"23634763296", "23107688892"}
# Manifest `negative`-flagged runs present in the label set.
NEGATIVE_FLAG_RUNS = {"23331599739", "23084380435"}
# Polar-reference race run - labeled, zero locked ranges.
POLAR_REF_RUN = "23728858378"

GUARD_BANDS = [0, 15, 30, 60, 90]
PRIMARY_GUARD = 30

# The detector's thresholds AS THEY STOOD BEFORE Phase 4 (the EDA's carried-over
# provisional values). Pinned explicitly rather than read from analysis.py's
# defaults, so this script keeps reporting a true before/after even now that
# two of those defaults have been changed as a RESULT of running it.
PRE_PHASE4 = {"window_kwargs": {}, "merge_kwargs": {},
              "score_kwargs": {"min_stretch_seconds": 45.0,
                               "stretch_mae_lock_bpm": 4.5,
                               "directional_gap_min_bpm": 25.0,
                               "gap_saturation_bpm": 50.0}}


def with_base(stage=None, name=None, value=None):
    """PRE_PHASE4 kwargs with one threshold overridden - so every 1-D sweep
    profile is measured around the incumbent, not around a moving target."""
    kw = {k: dict(v) for k, v in PRE_PHASE4.items()}
    if stage is not None:
        kw[f"{stage}_kwargs"][name] = value
    return kw


# ---------------------------------------------------------------- loading


def load_labels():
    """Returns {run_id: {"reviewed": bool, "locked": [(start_idx, end_idx), ...]}}.

    Clock -> index conversion is verified per run against that run's own
    time[0], not assumed: every payload is re-checked for a dense 1 Hz grid
    (index == elapsed second) before any label is converted, and every
    converted index is bounds-checked.
    """
    by_run = {}
    with open(LABELS, newline="") as fh:
        for row in csv.DictReader(fh):
            rid = row["run_id"].strip()
            entry = by_run.setdefault(rid, {"reviewed": False, "locked_clock": []})
            if row["label"].strip() == "reviewed":
                entry["reviewed"] = True
            elif row["label"].strip() == "locked":
                entry["locked_clock"].append(
                    (row["start_clock"].strip(), row["end_clock"].strip(), row["conf"].strip())
                )
    return by_run


def load_run(run_id):
    with open(os.path.join(DATA, f"{run_id}.json")) as fh:
        return json.load(fh)


def verify_grid(payload):
    """Confirms index == elapsed second from time[0] on a dense 1 Hz grid.

    Returns (ok: bool, t0: datetime, n: int). The docstrings claim the
    underlying grid stays 1 Hz dense with nulls filling low-rate runs' gaps;
    this checks it per run rather than trusting that.
    """
    t = payload["time"]
    t0 = datetime.fromisoformat(t[0])
    for i in range(len(t)):
        if (datetime.fromisoformat(t[i]) - t0).total_seconds() != i:
            return False, t0, len(t)
    return True, t0, len(t)


def clock_to_index(clock, t0, n):
    """HH:MM:SS time-of-day -> elapsed-second index, anchored on this run's
    own t0. Handles a run crossing midnight by rolling forward a day."""
    hh, mm, ss = (int(x) for x in clock.split(":"))
    cand = t0.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    idx = int((cand - t0).total_seconds())
    if idx < 0:
        idx += 86400
    if not (0 <= idx < n):
        raise ValueError(f"label clock {clock} -> idx {idx} outside run of length {n}")
    return idx


# ---------------------------------------------------------------- detector


def detect(payload, window_kwargs=None, merge_kwargs=None, score_kwargs=None,
           cadence_override=None):
    """Runs the production detector for fitbit-vs-garmin and returns a dict of
    per-second masks plus the qualifying stretch intervals.

    Why two firing views, not one - this is the single most consequential
    measurement decision in Phase 4:

      `fired_sample` mirrors exactly what cadence_lock_scan() returns per
      second: a firing only at instants that carry a real Fitbit HR AND a
      real gated Garmin cadence/speed. Fitbit runs ~0.45 Hz on an independent
      grid from Garmin's ~0.5 Hz, so their intersection is only ~25% of
      wall-clock seconds. The per-second stream is therefore PUNCTURED: a
      single continuous 350 s lock appears as ~90 isolated 1 s firings
      separated by None. Scoring range-painted labels against that stream
      measures Fitbit's sampling density far more than it measures the
      detector.

      `fired_stretch` marks every second inside a QUALIFYING STRETCH's span,
      including the interior nulls the stretch bridges. This is the
      detector's actual claim ("this interval is locked"), and it is the
      like-for-like comparison against a human-painted interval. It is the
      primary view. It is not a looser gate - the identical stretches
      qualify under the identical thresholds; only the readout differs.

    `covered` is the window-coverage domain (>= min_valid_samples in some
    window). Seconds outside it are ones the detector had too little data to
    judge in either direction, and are excluded from negative counting.
    """
    cad = cadence_override if cadence_override is not None else payload["cadence_spm"]
    hr, other, speed = payload["fitbit_hr"], payload["garmin_hr"], payload["speed_mps"]
    n = len(hr)

    windows = analysis._cadence_lock_window_scan(hr, other, cad, speed, **(window_kwargs or {}))
    covered = [False] * n
    for w in windows:
        for i in range(w["start"], w["end"]):
            covered[i] = True

    stretches = analysis._merge_loose_stretches(windows, **(merge_kwargs or {}))
    qualifying = []
    fired_stretch = [False] * n
    for st in stretches:
        q, score, mean_mae, gap = analysis._score_stretch(st, **(score_kwargs or {}))
        if not q:
            continue
        qualifying.append({"start": st["start"], "end": st["end"], "k": st["k"],
                           "score": score, "mae": mean_mae, "gap_bpm": gap})
        for i in range(st["start"], min(st["end"], n)):
            fired_stretch[i] = True

    assessable = [hr[i] is not None and cad[i] is not None and speed[i] is not None
                  and covered[i] for i in range(n)]
    fired_sample = [fired_stretch[i] and assessable[i] for i in range(n)]
    return {"fired_stretch": fired_stretch, "fired_sample": fired_sample,
            "assessable": assessable, "covered": covered, "stretches": qualifying, "n": n}


# ---------------------------------------------------------------- scoring


def build_masks(n, locked_ranges, guard):
    """positive[i]  - inside a labeled locked range (inclusive endpoints)
    gray[i]      - within `guard` seconds of a labeled range but not inside it
    Everything else in a reviewed run is a candidate negative."""
    positive = [False] * n
    gray = [False] * n
    for a, b in locked_ranges:
        for i in range(a, min(b + 1, n)):
            positive[i] = True
    if guard > 0:
        for a, b in locked_ranges:
            for i in range(max(0, a - guard), min(n, b + 1 + guard)):
                if not positive[i]:
                    gray[i] = True
    return positive, gray


def score_run(det, positive, gray, view="stretch"):
    """view="stretch": firings and the evaluable domain are whole intervals
    (primary). view="sample": firings and the domain are restricted to
    instants with real data on both grids (what cadence_lock_scan literally
    emits) - reported alongside as the density-limited floor.
    """
    if view == "stretch":
        fired, domain = det["fired_stretch"], det["covered"]
    else:
        fired, domain = det["fired_sample"], det["assessable"]

    tp = fp = fn = tn = gray_fired = pos_outside_domain = 0
    for i in range(det["n"]):
        if positive[i]:
            if not domain[i]:
                pos_outside_domain += 1
            if fired[i]:
                tp += 1
            else:
                fn += 1
        elif gray[i]:
            if fired[i]:
                gray_fired += 1
        elif domain[i]:
            if fired[i]:
                fp += 1
            else:
                tn += 1
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "gray_fired": gray_fired,
        "pos_total": tp + fn,
        "pos_outside_domain": pos_outside_domain,
        "fired_total": sum(fired),
        "domain_total": sum(domain),
        "n": det["n"],
    }


def agg(counts_list):
    out = {k: sum(c[k] for c in counts_list) for k in counts_list[0]}
    tp, fp, fn = out["tp"], out["fp"], out["fn"]
    out["precision"] = tp / (tp + fp) if (tp + fp) else None
    out["recall"] = tp / (tp + fn) if (tp + fn) else None
    # recall restricted to labeled seconds the detector could evaluate at all
    # (fired_* is a subset of the domain by construction, so every positive
    # outside the domain is necessarily a miss - separating them says whether
    # a miss is a DETECTOR failure or a DATA-COVERAGE failure).
    in_dom = out["pos_total"] - out["pos_outside_domain"]
    out["recall_in_domain"] = tp / in_dom if in_dom else None
    p, r = out["precision"], out["recall"]
    out["f1"] = (2 * p * r / (p + r)) if (p and r) else 0.0
    union = out["pos_total"] + out["fired_total"] - tp
    out["iou"] = tp / union if union else None
    return out


# ---------------------------------------------------------------- harness


class Bench:
    """Loads every labeled run once, then evaluates arbitrary threshold sets
    against the cached payloads."""

    def __init__(self):
        self.labels = load_labels()
        self.runs = {}
        self.grid_report = {}
        for rid, lab in sorted(self.labels.items()):
            payload = load_run(rid)
            ok, t0, n = verify_grid(payload)
            self.grid_report[rid] = {"dense_1hz": ok, "n": n, "t0": payload["time"][0]}
            if not ok:
                raise SystemExit(f"{rid}: time grid is not a dense 1 Hz index - aborting")
            ranges = []
            for sc, ec, conf in lab["locked_clock"]:
                a = clock_to_index(sc, t0, n)
                b = clock_to_index(ec, t0, n)
                ranges.append((a, b))
                self.grid_report[rid].setdefault("locked_idx", []).append(
                    {"start_clock": sc, "end_clock": ec, "start_idx": a, "end_idx": b,
                     "seconds": b - a + 1, "conf": conf}
                )
            self.runs[rid] = {"payload": payload, "ranges": sorted(ranges), "n": n}

    def run_ids(self):
        return sorted(self.runs)

    def positive_run_ids(self):
        return [r for r in self.run_ids() if self.runs[r]["ranges"]]

    def evaluate(self, window_kwargs=None, merge_kwargs=None, score_kwargs=None,
                 guard=PRIMARY_GUARD, only=None, view="stretch"):
        per_run = {}
        for rid in (only or self.run_ids()):
            r = self.runs[rid]
            det = detect(r["payload"], window_kwargs, merge_kwargs, score_kwargs)
            pos, gray = build_masks(r["n"], r["ranges"], guard)
            per_run[rid] = score_run(det, pos, gray, view=view)
        return per_run


def pooled(per_run, subset=None):
    keys = subset if subset is not None else list(per_run)
    return agg([per_run[k] for k in keys])


# ---------------------------------------------------------------- main


def main():
    bench = Bench()
    artifacts = {}
    artifacts["grid_verification"] = bench.grid_report

    pos_runs = bench.positive_run_ids()
    print("labeled runs:", len(bench.run_ids()), "| with positives:", pos_runs)

    # ---------- 1. baseline at current production defaults -------------
    baseline = {}
    for view in ("stretch", "sample"):
        for guard in GUARD_BANDS:
            pr = bench.evaluate(guard=guard, view=view, **with_base())
            baseline[f"{view}/{guard}"] = {"per_run": pr, "pooled": pooled(pr)}
    artifacts["baseline_by_view_and_guard"] = baseline
    for key in (f"stretch/{PRIMARY_GUARD}", "stretch/0", f"sample/{PRIMARY_GUARD}", "sample/0"):
        b = baseline[key]["pooled"]
        print(f"BASELINE {key:<12}: P={b['precision']:.4f} R={b['recall']:.4f} "
              f"R_in_domain={b['recall_in_domain']:.4f} F1={b['f1']:.3f} "
              f"IoU={b['iou']:.4f} grayfired={b['gray_fired']}")

    # ---------- 2. one-parameter-at-a-time sweeps ----------------------
    # Deliberately NOT a joint grid search. Six thresholds x 6 positive runs
    # is the free-parameter curve-fitting this project already renounced
    # once (analysis.stagno_trimp docstring). 1-D profiles around the
    # incumbent defaults show whether the incumbent sits on a plateau or a
    # cliff; that is all this N can honestly support.
    INCUMBENT = {
        ("score", "min_stretch_seconds"): 45.0,
        ("score", "stretch_mae_lock_bpm"): 4.5,
        ("score", "directional_gap_min_bpm"): 25.0,
        ("merge", "mae_lock_bpm"): 5.0,
        ("merge", "frac_within_min"): 0.6,
        ("window", "window_seconds"): 40,
        ("window", "frac_within_tol_bpm"): 6.0,
    }
    SWEEPS = {
        ("score", "min_stretch_seconds"): [30.0, 40.0, 45.0, 60.0, 75.0, 90.0, 120.0],
        ("score", "stretch_mae_lock_bpm"): [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
        ("score", "directional_gap_min_bpm"): [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0],
        ("merge", "mae_lock_bpm"): [3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0],
        ("merge", "frac_within_min"): [0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        ("window", "window_seconds"): [20, 30, 40, 50, 60],
        ("window", "frac_within_tol_bpm"): [4.0, 5.0, 6.0, 7.0, 8.0],
    }
    sweeps = {}
    for (stage, name), values in SWEEPS.items():
        rows = []
        for v in values:
            kw = with_base(stage, name, v)
            pr = bench.evaluate(guard=PRIMARY_GUARD, **kw)
            po = pooled(pr)
            pr0 = bench.evaluate(guard=0, **kw)
            po0 = pooled(pr0)
            zero_label_fired = {k: pr[k]["fired_total"] for k in pr
                                if not bench.runs[k]["ranges"]}
            rows.append({"value": v, "precision": po["precision"], "recall": po["recall"],
                         "f1": po["f1"], "iou": po["iou"], "tp": po["tp"], "fp": po["fp"],
                         "fn": po["fn"], "gray_fired": po["gray_fired"],
                         "precision_strict_guard0": po0["precision"],
                         "per_run_fired": {k: pr[k]["fired_total"] for k in pr},
                         "per_run_fp": {k: pr[k]["fp"] for k in pr},
                         "zero_label_run_fired": zero_label_fired})
            print(f"  sweep {stage}.{name}={v}: P={po['precision']:.4f} "
                  f"Pstrict={po0['precision']:.4f} R={po['recall']:.4f} "
                  f"F1={po['f1']:.3f} IoU={po['iou']:.4f} "
                  f"zerolabel_fired={sum(zero_label_fired.values())}")
        sweeps[f"{stage}.{name}"] = rows
    artifacts["sweeps_1d"] = sweeps

    # ---------- 3. leave-one-run-out ----------------------------------
    # LORO over the 6 runs that carry positives. With N=6 this cannot
    # estimate generalisation error in any statistical sense; it is used
    # strictly as a STABILITY probe - does the 1-D argmax move when one run
    # is removed? An argmax that jumps around is evidence the incumbent
    # should not be moved, not evidence for the fold-average optimum.
    loro = {}
    for (stage, name), values in SWEEPS.items():
        folds = {}
        for held in pos_runs:
            train = [r for r in bench.run_ids() if r != held]
            f1_by_value = {}
            for v in values:
                kw = with_base(stage, name, v)
                pr = bench.evaluate(guard=PRIMARY_GUARD, only=train, **kw)
                f1_by_value[v] = pooled(pr)["f1"]
            best_f1 = max(f1_by_value.values())
            # A bare argmax lies when the profile is FLAT: several sweeps have
            # plateaus where 3+ consecutive values are bit-identical, and
            # "unanimous across folds" would then just be reporting which end
            # of the plateau the loop happened to visit first. Record the whole
            # tied/near-tied optimal SET per fold; a change is only defensible
            # if the incumbent falls OUTSIDE that set in every fold.
            opt_set = sorted(v for v, f in f1_by_value.items() if f >= best_f1 - 1e-9)
            near_set = sorted(v for v, f in f1_by_value.items() if f >= best_f1 - 0.005)
            best_v = opt_set[0]
            kw = with_base(stage, name, best_v)
            hr_ = bench.evaluate(guard=PRIMARY_GUARD, only=[held], **kw)
            folds[held] = {"optimal_set": opt_set, "near_optimal_set": near_set,
                           "train_f1": best_f1,
                           "held_out": {k: hr_[held][k] for k in
                                        ("tp", "fp", "fn", "gray_fired", "pos_total", "fired_total")}}
        incumbent = INCUMBENT[(stage, name)]
        excluded = [h for h in folds if incumbent not in folds[h]["optimal_set"]]
        sets = [tuple(folds[h]["optimal_set"]) for h in folds]
        loro[f"{stage}.{name}"] = {
            "incumbent": incumbent,
            "folds": folds,
            "optimal_sets": [list(s) for s in sets],
            "identical_across_folds": len(set(sets)) == 1,
            "incumbent_excluded_in_all_folds": len(excluded) == len(folds),
            "incumbent_excluded_in_folds": excluded,
        }
        print(f"  LORO {stage}.{name} (incumbent {incumbent}): opt sets {[list(s) for s in sets]} "
              f"| identical={len(set(sets)) == 1} "
              f"| incumbent excluded in {len(excluded)}/{len(folds)} folds")
    artifacts["loro"] = loro

    # ---------- 3b. joint candidate + off-label-set silence check ------
    # Only the two thresholds whose LORO optimal set was IDENTICAL across all
    # six folds AND excluded the incumbent in all six are moved. Applied
    # jointly (they interact) and re-checked against the negative controls.
    CANDIDATE = with_base()
    CANDIDATE["score_kwargs"]["stretch_mae_lock_bpm"] = 5.0
    CANDIDATE["score_kwargs"]["directional_gap_min_bpm"] = 15.0
    cand = {}
    for guard in GUARD_BANDS:
        pr = bench.evaluate(guard=guard, **CANDIDATE)
        cand[str(guard)] = {"per_run": pr, "pooled": pooled(pr)}
    c = cand[str(PRIMARY_GUARD)]["pooled"]
    c0 = cand["0"]["pooled"]
    print(f"CANDIDATE stretch/{PRIMARY_GUARD}: P={c['precision']:.4f} R={c['recall']:.4f} "
          f"F1={c['f1']:.3f} IoU={c['iou']:.4f} grayfired={c['gray_fired']}")
    print(f"CANDIDATE stretch/0 : P={c0['precision']:.4f}")
    # LORO of the joint change: does each held-out run individually improve?
    joint_loro = {}
    for held in pos_runs:
        base_h = bench.evaluate(guard=PRIMARY_GUARD, only=[held], **with_base())[held]
        cand_h = bench.evaluate(guard=PRIMARY_GUARD, only=[held], **CANDIDATE)[held]
        joint_loro[held] = {"baseline": agg([base_h]), "candidate": agg([cand_h])}
        bh, ch = joint_loro[held]["baseline"], joint_loro[held]["candidate"]
        print(f"  held-out {held}: R {bh['recall']:.3f}->{ch['recall']:.3f} "
              f"P {bh['precision'] if bh['precision'] is None else round(bh['precision'], 3)}"
              f"->{ch['precision'] if ch['precision'] is None else round(ch['precision'], 3)} "
              f"IoU {bh['iou']:.3f}->{ch['iou']:.3f}")

    # Section 5.2 weak corroboration on the 20 published runs that were NEVER
    # labeled - genuinely held out from every calibration decision above.
    with open(os.path.join(DATA, "index.json")) as fh:
        manifest = json.load(fh)
    offset_rows = []
    for entry in manifest:
        rid = str(entry["id"])
        if rid in bench.runs or not os.path.exists(os.path.join(DATA, f"{rid}.json")):
            continue
        p = load_run(rid)
        if "fitbit_hr" not in p or "cadence_spm" not in p:
            continue
        d_base = detect(p, **with_base())
        d_cand = detect(p, **CANDIDATE)
        offset_rows.append({"run": rid, "fitbit_flags": entry.get("fitbit_flags"),
                            "baseline_fired": sum(d_base["fired_stretch"]),
                            "candidate_fired": sum(d_cand["fired_stretch"]),
                            "run_seconds": d_base["n"]})
    artifacts["candidate"] = {"kwargs": CANDIDATE, "by_guard": cand,
                              "loro_joint": joint_loro,
                              "unlabeled_holdout": offset_rows}
    # The manifest's fitbit_flags are 32x ["positive_cadence"] and 3x
    # ["negative"]. Two of the three negatives are inside the label set; the
    # third is genuinely untouched by any decision made above.
    neg_holdout = [r for r in offset_rows if "negative" in (r["fitbit_flags"] or [])]
    fired_pos = [r for r in offset_rows if r["candidate_fired"] > 0
                 and "positive_cadence" in (r["fitbit_flags"] or [])]
    print("  unlabeled holdout runs:", len(offset_rows),
          "| negative-flagged holdouts:", [(r["run"], r["baseline_fired"], r["candidate_fired"])
                                           for r in neg_holdout],
          "| positive_cadence holdouts firing under candidate:",
          len(fired_pos), "of",
          sum(1 for r in offset_rows if "positive_cadence" in (r["fitbit_flags"] or [])))

    # ---------- 4. negative controls ----------------------------------
    nc = {}
    # negative controls are reported against the POST-Phase-4 candidate - the
    # thresholds actually being shipped are the ones that have to survive them.
    base_pr = cand[str(PRIMARY_GUARD)]["per_run"]
    nc["a_quiet_on_zero_label_runs"] = {
        rid: {"fired_seconds": base_pr[rid]["fired_total"],
              "covered_seconds": base_pr[rid]["domain_total"],
              "run_seconds": base_pr[rid]["n"],
              "class": ("negative_flag" if rid in NEGATIVE_FLAG_RUNS else
                        "blind_spot" if rid in BLIND_SPOT_RUNS else
                        "polar_reference" if rid == POLAR_REF_RUN else "labeled_positive")}
        for rid in bench.run_ids() if not bench.runs[rid]["ranges"]
    }
    print("  negcontrol (a):", {k: v["fired_seconds"] for k, v in nc["a_quiet_on_zero_label_runs"].items()})

    # (a2) The blind spot, probed rather than asserted. The owner reports lock
    #      in 23634763296 / 23107688892 and Phase 3 painted zero ranges there
    #      (they could not see it either on the blinded chart). Recall on them
    #      is UNMEASURABLE - there is no positive to recall. What CAN be
    #      answered is whether any setting of these thresholds would surface
    #      something: relax every gate simultaneously to the loosest value in
    #      its sweep and see whether anything fires at all.
    LOOSEST = {"window_kwargs": {"window_seconds": 60, "frac_within_tol_bpm": 8.0,
                                 "min_valid_samples": 4},
               "merge_kwargs": {"mae_lock_bpm": 7.0, "frac_within_min": 0.4},
               "score_kwargs": {"min_stretch_seconds": 30.0, "stretch_mae_lock_bpm": 6.0,
                                "directional_gap_min_bpm": 10.0}}
    blind = {}
    for rid in sorted(BLIND_SPOT_RUNS | NEGATIVE_FLAG_RUNS | {POLAR_REF_RUN}):
        p = bench.runs[rid]["payload"]
        d_inc = detect(p, **with_base())
        d_cand = detect(p, **CANDIDATE)
        d_loose = detect(p, **LOOSEST)
        blind[rid] = {"incumbent_fired": sum(d_inc["fired_stretch"]),
                      "candidate_fired": sum(d_cand["fired_stretch"]),
                      "loosest_fired": sum(d_loose["fired_stretch"]),
                      "loosest_stretches": d_loose["stretches"],
                      "covered_seconds": sum(d_inc["covered"]),
                      "run_seconds": d_inc["n"]}
        print(f"  blind/neg probe {rid}: incumbent={blind[rid]['incumbent_fired']} "
              f"candidate={blind[rid]['candidate_fired']} "
              f"loosest={blind[rid]['loosest_fired']}")
    nc["a2_blind_spot_and_negative_probe"] = {"loosest_kwargs": LOOSEST, "runs": blind}

    # (b) destroy cadence's temporal alignment with HR, keep its marginal
    #     distribution and (for the circular variant) its autocorrelation.
    #     If lock score survives this, it was never measuring cadence tracking.
    rnd = random.Random(20260727)
    shuffle_rows = []
    for rid in pos_runs:
        payload = bench.runs[rid]["payload"]
        cad = payload["cadence_spm"]
        n = len(cad)
        real_fired = base_pr[rid]["fired_total"]
        for trial in range(5):
            # circular shift: preserves autocorrelation + marginals exactly
            shift = rnd.randrange(int(0.1 * n), int(0.9 * n))
            rolled = cad[-shift:] + cad[:-shift]
            d1 = detect(payload, cadence_override=rolled, **CANDIDATE)
            # full permutation of non-null values in place: destroys
            # autocorrelation too, keeps the null pattern and the marginals
            vals = [v for v in cad if v is not None]
            rnd.shuffle(vals)
            it = iter(vals)
            permuted = [None if v is None else next(it) for v in cad]
            d2 = detect(payload, cadence_override=permuted, **CANDIDATE)
            shuffle_rows.append({"run": rid, "trial": trial, "shift": shift,
                                 "real_fired": real_fired,
                                 "circshift_fired": sum(d1["fired_stretch"]),
                                 "permuted_fired": sum(d2["fired_stretch"])})
            print(f"  negcontrol (b) {rid} trial{trial}: real={real_fired} "
                  f"circshift={sum(d1['fired_stretch'])} permuted={sum(d2['fired_stretch'])}")
    def ratios(key):
        rs = [r[key] / r["real_fired"] for r in shuffle_rows if r["real_fired"]]
        rs.sort()
        return {"mean": round(sum(rs) / len(rs), 3), "median": round(rs[len(rs) // 2], 3),
                "max": round(rs[-1], 3), "frac_trials_above_0.5": round(
                    sum(1 for x in rs if x > 0.5) / len(rs), 3),
                "frac_trials_zero": round(sum(1 for x in rs if x == 0) / len(rs), 3)}
    nc["b_cadence_destruction"] = {
        "trials": shuffle_rows,
        "circular_shift_ratio_vs_real": ratios("circshift_fired"),
        "full_permutation_ratio_vs_real": ratios("permuted_fired"),
    }
    print("  negcontrol (b) SUMMARY circshift:", nc["b_cadence_destruction"]["circular_shift_ratio_vs_real"])
    print("  negcontrol (b) SUMMARY permuted :", nc["b_cadence_destruction"]["full_permutation_ratio_vs_real"])

    # (c) do firings concentrate where HR actually sits on a cadence harmonic?
    prox_fired, prox_unfired = [], []
    for rid in bench.run_ids():
        payload = bench.runs[rid]["payload"]
        det = detect(payload, **CANDIDATE)
        fired, assessable = det["fired_sample"], det["assessable"]
        hr, cad = payload["fitbit_hr"], payload["cadence_spm"]
        for i in range(len(hr)):
            if not assessable[i] or hr[i] is None or cad[i] is None or cad[i] < 60.0:
                continue
            d = min(abs(hr[i] - k * cad[i]) for k in analysis.CADENCE_LOCK_K_GRID)
            (prox_fired if fired[i] else prox_unfired).append(d)

    def pct(xs, q):
        if not xs:
            return None
        s = sorted(xs)
        return round(s[min(len(s) - 1, int(q * len(s)))], 2)

    nc["c_proximity_concentration"] = {
        "fired": {"n": len(prox_fired), "median": pct(prox_fired, 0.5),
                  "p90": pct(prox_fired, 0.9),
                  "frac_within_5bpm": round(sum(1 for d in prox_fired if d <= 5) / len(prox_fired), 3) if prox_fired else None},
        "unfired": {"n": len(prox_unfired), "median": pct(prox_unfired, 0.5),
                    "p90": pct(prox_unfired, 0.9),
                    "frac_within_5bpm": round(sum(1 for d in prox_unfired if d <= 5) / len(prox_unfired), 3) if prox_unfired else None},
    }
    print("  negcontrol (c):", nc["c_proximity_concentration"])
    artifacts["negative_controls"] = nc

    # ---------- 5. boundary analysis (changepoint feasibility) ---------
    # Does the detector's on/off boundary sit systematically outside the
    # labeled one? If yes, that is the owner's documented conservative
    # painting convention, NOT detector boundary error - which is exactly
    # what makes a changepoint layer unevaluable against this label set.
    ba = {}
    for tag, kwset in (("pre_phase4", with_base()), ("candidate", CANDIDATE)):
        bounds = []
        for rid in pos_runs:
            payload = bench.runs[rid]["payload"]
            det = detect(payload, **kwset)
            segs = [(s["start"], s["end"] - 1) for s in det["stretches"]]
            for (la, lb) in bench.runs[rid]["ranges"]:
                best, bo = None, None
                for (da, db) in segs:
                    ov = min(lb, db) - max(la, da) + 1
                    if ov > 0 and (bo is None or ov > bo):
                        bo, best = ov, (da, db)
                bounds.append({"run": rid, "label": [la, lb], "label_len": lb - la + 1,
                               "matched_seg": list(best) if best else None,
                               "overlap": bo or 0,
                               "onset_err": (best[0] - la) if best else None,
                               "offset_err": (best[1] - lb) if best else None})
        on = [x["onset_err"] for x in bounds if x["onset_err"] is not None]
        off = [x["offset_err"] for x in bounds if x["offset_err"] is not None]
        ba[tag] = {
            "per_label": bounds,
            "matched": len(on), "unmatched": len(bounds) - len(on),
            "onset_err_mean": round(sum(on) / len(on), 1) if on else None,
            "offset_err_mean": round(sum(off) / len(off), 1) if off else None,
            "onset_err_median": sorted(on)[len(on) // 2] if on else None,
            "offset_err_median": sorted(off)[len(off) // 2] if off else None,
            "onset_err_sorted": sorted(on), "offset_err_sorted": sorted(off),
        }
        print(f"  boundaries[{tag}]: matched {len(on)}/{len(bounds)} "
              f"onset_err mean {ba[tag]['onset_err_mean']} med {ba[tag]['onset_err_median']} | "
              f"offset_err mean {ba[tag]['offset_err_mean']} med {ba[tag]['offset_err_median']}")
    artifacts["boundary_analysis"] = ba

    with open(os.path.join(OUT, "results.json"), "w") as fh:
        json.dump(artifacts, fh, indent=1, default=str)
    print("wrote", os.path.join(OUT, "results.json"))


if __name__ == "__main__":
    main()
