# N-of-1 Study Protocol: Empirical Validation of a Kinetic Effort-Consistency Model for Optical Heart Rate Cadence Lock Artifacts

**Document Type:** Formal Scientific Study Registration & Preregistration Protocol
**Revision:** 4 (2026-07-28) — see Section 9 for the full changelog
**Target Publication Venues:** IEEE Journal of Biomedical and Health Informatics, Sensors, or JMIR mHealth and uHealth

**Provenance of this revision:** the original draft was independently reviewed by four domain specialists (physiology, biomechanics, sensor-engineering, non-parametric statistics — `.claude/agents/`), reconciled against this repo's own prior work (`CADENCE_LOCK_DETECTOR_PROPOSAL.md`, `phase1_eda/REPORT.md`, `phase4_calibration/results.json`, `WEARABLE_HR_VALIDATION_FRAMEWORK_PROPOSAL.md`), and rewritten by the project's Architect. Section 9 lists what changed and why; Section 10 lists what is still an open call for the owner.

---

## 1. Study Administrative Details

| Field | Specification |
|---|---|
| Study Title | Benchmarking a Kinetic Effort-Consistency Model for Detecting Optical PPG Cadence Lock in Running, Grounded in a First-Principles Metabolic Model Comparison |
| Principal Investigator | Ross Wilkinson, Ph.D. |
| Study Design | Single-Subject (n=1) Prospective Observational Time-Series Validation Study, Confirmatory on a Frozen Detector |
| Target Dataset Size | 24–30 runs with full three-device (Garmin + Fitbit + Polar H10) coverage. Current coverage: **3 of 35** published runs. Wearing the H10 on every session is the single highest-leverage action toward this target. |
| IRB Status | **Exemption Determination Letter to be requested.** Owner's self-assessment: n=1, no risk beyond activities of daily living, no IRB required substantively — but a *documented* exemption determination (not a self-assertion) is still the right artifact for a real submission. See Section 10.2. |
| Companion documents | `CADENCE_LOCK_DETECTOR_PROPOSAL.md` (detector design lineage), `WEARABLE_HR_VALIDATION_FRAMEWORK_PROPOSAL.md` (reference-device literature, statistical methodology precedent), `METABOLIC_MODEL_LITERATURE_REVIEW.md` (Aim 2 tournament candidate models, verified citations), `phase3_labels/labels.csv` (existing hand-labels), `phase4_calibration/results.json` (incumbent detector's real performance) |

---

## 2. Rationale & Specific Aims

Wrist-worn optical PPG sensors are prone to a documented failure mode during running in which the reported HR value converges onto stride cadence instead of a plausible cardiac trajectory. This is not this project's discovery: **Bent, Goldstein, Kibbe & Dunn, *npj Digital Medicine* 3:18 (2020), doi:10.1038/s41746-020-0226-6** — 53 subjects, six consumer devices, a Bittium Faros 180 clinical ECG reference — describe optical sensors "lock[ing] on to the periodic signal stemming from repetitive motion... and mistak[ing] that motion as the cardiovascular cycle." This project's contribution is not naming the phenomenon; it is documenting it longitudinally, per-device, at the committed-value-domain level this project's data actually permits, and building a per-individual, first-principles-validated correction for it.

### Specific Aims

- **Aim 1 (Detection):** evaluate a kinetic effort-consistency detector — an implausibility gate built from a personal expected-HR model, not from disagreement between two watches — against blinded human labels, on identical leave-one-run-out folds to the existing rule-based detector (`phase4_calibration/`), which serves as the preregistered comparator.
- **Aim 2 (Metabolic Model Selection Tournament):** the project's central methodological contribution. Fit a set of competing published cost-of-locomotion models (candidates verified and specified in `METABOLIC_MODEL_LITERATURE_REVIEW.md`: Minetti 2002, a Margaria-derived piecewise-linear form, Pontzer 2016 corrected 2023, plus Garmin's own GAP as baseline — 4 models × 2 variants (published-fixed / refit) = 8 fits) to this runner's own grade/pace data from the Topographically Decoupled arm, and select among them by **out-of-sample predictive accuracy against the Polar H10 reference HR** — not against Garmin/Fitbit's own (possibly lock-corrupted) HR, and not by citation precedent. **One primary, no-selection-freedom comparison is prespecified now, before any data collection**: M1 (Minetti, published coefficients) vs. M0 (Garmin's undocumented GAP), one-sided, LORO RMSE against Polar H10. All 28 remaining pairwise contrasts among the 8 fits are secondary/exploratory, Holm-corrected, and never substitute for the primary claim (statistical review, Section 9 Revision 4). **Required pre-check, corrected after statistical review (Section 9, Revision 4):** the naive "Pearson r>0.99 on raw demand" check was itself an unanchored threshold and checked the wrong signal — see Section 5A and 6.4 for the corrected, bpm-scale identifiability check. The winning (or tied, per the prespecified tie-break rule in Section 6.4) model becomes Stage A's metabolic-demand input for Aim 1; the comparison itself is a reportable result independent of the detector.
- **Aim 3 (Training Metric Preservation):** measure downstream impact of detection on HR-zone distribution and TRIMP.

**Reconstruction (kinetic-guided repair of flagged intervals) is out of scope for this study, moved to a follow-up paper** — decided 2026-07-28. It introduces evaluation and provenance concerns (Section 5, former Stage D) that are tangential to this paper's actual claims and would give reviewers an easy way to get stuck on the wrong argument. Detection and model selection are the whole scope now.

---

## 3. Instrumentation & Experimental Apparatus

| Device Role | Make & Model | Function |
|---|---|---|
| Reference | Polar H10 Chest Strap | Silver-standard reference. **Not gold standard — see Section 6.1.** Ingestion already operational (`publish_reference_run.py`, `publish_polar_run.py`); ~1 Hz already-committed HR (confirmed: `main.py:1001` reads `record.get_value("heart_rate")`, not raw R-R intervals — a prior draft's "R-R interval derived HR" description was inaccurate). |
| Optical A | Garmin Instinct 2X Solar | Primary test device |
| Optical B | Fitbit Inspire 3 | Secondary test device. No onboard cadence or GPS — its `Cad[t]`/`v[t]` inputs come from Garmin's independent sampling grid, which co-drops with Garmin HR. State this cross-device dependency explicitly in any manuscript. |
| Environmental | Open-Meteo archive API | Already ingested (`weather.py`), historical (not live-only), per-run `temperature_c`/`humidity_pct` |

---

## 4. Protocol Matrix

Every run the owner completes going forward is tagged against whichever arm it naturally fits, in addition to the dedicated sessions below — this is not a separate training plan, it rides on top of existing training. **Wear the H10 on every run, dedicated or not**; that is the actual bottleneck (3 of 35 today).

| # | Arm | Execution | Purpose, revised |
|---|---|---|---|
| 1 | Resonance Zone | Steady-state, 150–170 spm ≈ 150–170 bpm | Precision against false positives at true harmonic coincidence. Unchanged. |
| 2 | Topographically Decoupled | Hill repeats, ±8–15% grade, continuous terrain. **Amendment (statistical review, Section 9 Revision 4): extending the grade range is NOT the fix.** Simulated: widening to ±20–25% *raises* Minetti-vs-Pontzer correlation (the shared monotone trend across a wider range grows faster than their curvature difference), and ±25% sustained grade likely forces walking pace, which the speed-support guard (Section 5A) would mark unassessable for Minetti's running-specific polynomial anyway. **Real fix: balance the grade design, don't widen it** — prespecified steady blocks (≥2 min each) at ~9 grade levels spanning the existing ±15% range, deliberately including −20% (Minetti's own measured cost minimum) and +5%/+10% (where the candidates diverge most), plus fast-uphill/slow-downhill blocks that deliberately decouple pace from grade (on ordinary hill repeats pace is a near-deterministic function of grade, which is itself a large part of the identifiability problem). | **Dual purpose now:** stress-tests the detector *and* is the primary data source for Aim 2's model comparison (candidates specified in `METABOLIC_MODEL_LITERATURE_REVIEW.md`). Grade computed over a ~15–20 m distance window, not a time window (barometric/GPS noise at 1 Hz is too coarse otherwise) — this is a covariate-input transform, isolated from the HR signal itself, consistent with the no-fill rule's existing narrow-exception pattern (`_interpolate_gaps`, `paired_trimp`). Minetti's polynomial evaluated **unclamped**; its minimum sits near g≈−0.18 (computed directly, not assumed), inside the stated ±15% range — any downstream step assuming monotonic cost-vs-grade will break there, so don't build one. |
| 3 | High-Transient Step Response | 10×400m or 4×1000m, full recovery | Also constrains the fast time-constant fit (τ_fast) in Stage A. Unchanged otherwise. |
| 4 | Prolonged Thermal Drift | 75–105 min, >20°C | 3–4 runs cannot identify a separate thermal-strain state (see Stage A) — treat as data accumulation now, modeling later. Not a near-term deliverable. |
| 5 | Biomechanical Perturbation | **Track or road, never treadmill** (Garmin's treadmill speed is itself derived from stride accelerometry — using it as ground truth for cadence-decoupled effort is circular). Fixed pace, 150 spm vs. 185+ spm, ≥5 min blocks, counterbalanced order. **Add metronome-paced walking blocks** — imposed cadence is the only externally-verified step-rate reference this project can obtain, confirmed (independently, in an earlier session) as having no precedent in the published literature reviewed for `WEARABLE_HR_VALIDATION_FRAMEWORK_PROPOSAL.md` — a genuine contribution, not an application of existing method. Off-preferred-cadence running has a real, asymmetric metabolic cost (~2–8% VO2 at ~10–15% cadence deviation, both directions) — record each block's own steady-state H10 HR and subtract that offset before scoring; don't treat the perturbation as effort-free. |
| 6 | Environmental Baseline | <5°C or cold-start | Unchanged. |

---

## 5. Algorithmic Pipeline

### Stage A: Metabolic Demand & Expected-HR Model

**This stage now formally absorbs and supersedes the separately-developed detector redesign** (see [[project-cadence-lock-detector-redesign]] in project memory) — one design going forward, not two parallel efforts.

**Metabolic demand, treated as a hypothesis, not an import.** Minetti's published cost-of-running polynomial (Minetti et al., *J Appl Physiol* 93:1039, 2002; valid |g| ≤ 0.45) is the **initial working hypothesis** for u[t] = v·C_r(g), and it is a real structural upgrade over Garmin's undocumented `directGradeAdjustedSpeed` regardless of what else in this document survives — but per the owner's explicit standard, it must be validated against this runner's own Topographically Decoupled data (Aim 2) before being trusted as a production input, not adopted because it's published. **Naming the alternatives to test it against is the owner's call, not mine to fabricate**: cost-of-force-production and cost-of-work/muscle-tendon-mechanics frameworks were named as candidates; their precise citable functional forms need to be specified with the owner's own literature access before a real model comparison can run. Comparison methodology once forms are specified: fit each candidate to the runner's own hill-repeat data, compare out-of-sample predictive error via leave-one-run-out, select (or justify an ensemble) on that basis.

**Kinetics — causal only.** The zero-phase (forward+backward-averaged) filtering in the original draft is rejected: physiology review found it would specifically erase onset-lag evidence right at the moments lock is most detectable, by leaking post-transition recovery backward into the pre-transition prediction. The model is the already-reviewed two-compartment (bi-exponential) kinetics form:

```
HR_exp(t) = a + b·ṽ_fast(t) + c·(ṽ_slow(t) − ṽ_fast(t))
          ≡ a + (b−c)·ṽ_fast(t) + c·ṽ_slow(t)      [collapsed form]
```

where ṽ_fast/ṽ_slow are time-aware (real-Δt, not row-count) exponentially-weighted averages of u[t] (or whichever metabolic-demand signal wins Aim 2) at two time constants, fit per-run within bounded, coarse grids (τ_fast ∈ {15,30,60}s, τ_slow ∈ {180,300,480,900}s, capped at duration/4), never against labels. Constraint: **0 ≤ c ≤ b** (both compartment weights non-negative in the collapsed form). If the τ_slow objective is flat across the grid, fall back to the prespecified default (480s) rather than take a spurious argmin.

**Correction for Aim 2's tournament specifically (statistical review, Section 9 Revision 4):** fitting all five parameters (a, b, c, τ_fast, τ_slow) per run, per candidate model, gives the kinetics fit enough freedom to absorb genuine differences between competing demand models — which would manufacture a false null in the identifiability check below. When scoring the Aim 2 tournament, τ_fast/τ_slow are estimated once on training folds only and held fixed across candidates; only `a` (and optionally `b`) are refit per run per candidate. This constraint applies to Aim 2's model comparison only — Aim 1's detector calibration keeps full per-run fitting as already specified.

**Validity guards, not clips.** HR_exp outside a data-derived range (the run's own 5th-percentile trusted HR to HRmax as a coarse regime marker) marks samples **unassessable**, never clipped — clipping would inflate residuals exactly in the hard-effort regime that's this project's known false-positive source. A candidate stretch whose pace falls outside the fit set's own support is also unassessable (the speed-support guard — this specifically protects the anchor run `23672318504`, whose 18-minute walking lock sits outside the running-pace data the model would otherwise be fit on).

**Downhill gate.** Bin residuals by grade sign (`directVerticalSpeed`); gate scoring off on sustained steep downhill (≤−4% for ≥30s, reasoned not swept) rather than modeling the uphill/downhill cost asymmetry directly.

**Thermal strain — deferred, not modeled yet.** If built, it is a separate third state with its own ~20–40 min time constant, never conflated with τ_slow (~300s) — the two are numerically unidentifiable together within a single 30–45 min run. Gated on Arm 4 accumulating enough runs to identify it at all.

**Kinetics asymmetry — deferred, flagged.** Onset and recovery genuinely differ physiologically; the symmetric fast/slow form is weakest in the 2–5 minutes after a large downward effort transition. Emit a `kinetics_uncertain` flag on stretches overlapping that window rather than adding parameters now. This is also the one place the demoted cross-device signal (below) earns a role, as a corroborating check specifically in this weak window — never globally.

### Stage B: Feature Extraction

- **Harmonic proximity: use the full, already-validated grid** (`CADENCE_LOCK_K_GRID`, 0.25–4.0 in quarter-steps plus thirds). The original draft's {0.5, 1.0, 1.5, 2.0} set is a regression against `phase1_eda/REPORT.md`'s own confirmed finding that k=4/3 (1.333×) recurs across 5+ runs — do not narrow the grid.
- **Kinetic residual:** |HR_meas − HR_exp|, per Stage A.
- **Correlation-differential feature:** retained, but scoped honestly — it is near-zero and noisy during ordinary running, where cadence and effort co-vary, and only carries real information when Arms 2 and 5 experimentally decouple them. Report it as conditionally informative, not a general-purpose feature.
- **Cross-device gap:** demoted to an off-by-default diagnostic (`gap_bpm` alongside `residual_bpm`), retained only to measure how much of any recall gain is genuinely new versus already-known from the frozen incumbent detector, and as the corroborating signal in the kinetics-asymmetry window above.

### Stage C: Temporal Structure

The original draft's 2-state, 3-feature Gaussian HMM is **not statistically defensible at the current label count** (statistical review: ~21 free parameters against an effective N of 19 labeled positive episodes needs a >10:1 floor, i.e. roughly 140–200 episodes). Use instead: a **1-D emission on the existing rule/residual score**, with transition probabilities **fixed a priori** from the already-asserted 15-second dwell constraint rather than fit (2–3 effective free parameters against 19 episodes — honest at this N). The full multi-feature HMM is a concrete, falsifiable future milestone: revisit once **≥80–100 labeled positive episodes** exist, not before.

### Stage D: Kinetic-Guided Reconstruction — out of scope, follow-up paper

**Decided 2026-07-28: removed from this study entirely, not merely phase-gated.** This is the correction algorithm `CADENCE_LOCK_DETECTOR_PROPOSAL.md` already deferred once as future work separate from detection; keeping it even as a conditional Aim in this preregistration invited reviewers to litigate reconstruction-specific concerns (provenance marking, a much higher evidentiary bar than detection) that are orthogonal to this paper's actual claims (Aims 1–2). If pursued later, the required design constraints already worked out — `HR_reconstructed` as a separately-named series, never overwriting the raw trace, visually distinct, carrying a per-point provenance flag, excluded from headline stats — remain valid and are preserved here for whenever that follow-up paper starts, but they are not this document's concern.

---

## 6. Statistical Benchmarking & Evaluation

### 6.1 Ground truth — fixed

The original draft's rule (`|HR_PPG − Cad| ≤ 4 bpm AND |HR_PPG − HR_ECG| ≥ 10 bpm`) is retired. Four independent reasons converged on this: it mislabels the project's own confirmed 1.5×/4×3 lock evidence as negative (only checks the 1× harmonic); it is near-circular with the classifier's own `D_cad` feature at m=1; it is internally inconsistent with a detector that searches multiple harmonics; and — the sharpest finding — its 4 bpm threshold is **numerically identical** to the incumbent detector's own tuned `stretch_mae_lock_bpm` (4.5–5.0), so any F1 computed against it partly measures threshold agreement with itself, not detection.

**Primary ground truth: blinded human labels**, extending the existing Phase 3 protocol (`phase3_labels/labels.csv`: single-device-blinded, `locked`/`good`/unpainted-unsure, a `reviewed` sentinel per run+device) prospectively across the new protocol-collected runs. Any automated proxy rule, if used at all, must be **ECG-vs-PPG only with no cadence term in its own definition**, and is auxiliary/weak-signal only — never primary truth.

### 6.2 Reference device — silver standard, not gold, with real citations

`Bland, J.M. & Altman, D.G. (1986), The Lancet 1(8476):307–310`; `Lin, L.I. (1989), Biometrics 45(1):255–268`. Polar H10 validity against ECG: `Gilgen-Ammann, Schweizer & Wyss (2019), Eur J Appl Physiol 119:1525–1532` (RR-interval signal quality 99.6% vs. Holter ECG's 94.6%, holding 99.4% during high-intensity activity where Holter fell to 89.8%); `Pasadyn et al. (2019), Cardiovasc Diagn Ther 9(4):379–385` (predecessor H7: CCC=0.98 vs. 3-lead ECG, graded treadmill, highest of all devices tested). CCC threshold convention: **McBride (2005)**, NIWA HAM2005-062 (<0.90 poor, 0.90–0.95 moderate, 0.95–0.99 substantial, >0.99 excellent) — the stricter of two incompatible conventions in the literature, chosen deliberately for publication rigor over the looser Nelson & Allen 0.5/0.7 scale. MAPE threshold: <10%, per `Nelson & Allen (2019), JMIR mHealth uHealth 7(3):e10828`.

### 6.3 Unit of analysis — corrected

"75,000–90,000 paired 1 Hz frames" is not a sample size; within-run samples are heavily autocorrelated. Real N is runs (24–30 target) and episodes (~50–60 projected at full protocol).

| Metric | Original unit | Corrected unit | Method |
|---|---|---|---|
| Sensitivity/Specificity/F1 | sample | episode, aggregated per run | Per-episode detected/missed + timeline-IoU |
| ROC-AUC | sample | run | AUC per run → median + run-cluster bootstrap |
| RMSE/MAE | sample | run, stratified by lock state | Per-run, then paired run-level comparison |
| Bland-Altman LoA | sample | run | Primary = per-run mean difference, n=24–30 pairs, LoA = mean ± 1.96·SD of run means, stratified lock/non-lock. The ±5.0 bpm figure is the one externally-anchored number here (consumer-HR-monitor convention) — spend it only on this, not on a metric it wasn't validated for. |
| TRIMP / zone dwell | run | run | Unchanged — already correct |

CIs: run-clustered bootstrap (BCa, B=10,000). Within-run quantities: moving-block bootstrap, block length ≥ episode duration.

### 6.4 Benchmarks — derived, not asserted

Absolute round-number targets (F1≥0.88, AUC≥0.92, 60% RMSE reduction) are retired — the incumbent detector's own real leave-one-run-out per-run F1 (`phase4_calibration/results.json`, `candidate.loro_joint`: 0.41, 0.95, 0.99, 0.70, 0.85, 0.78 — median 0.81) already sits inside 0.88 as a spread, making an absolute target unfalsifiable as originally stated. **Restated as a comparative claim**: paired per-run F1 improvement over the frozen Phase-4 detector on identical leave-one-run-out folds — Hodges-Lehmann median difference, run-cluster bootstrap CI, one-sided Wilcoxon signed-rank.

**Aim 2 freezing discipline (statistical review, Section 9 Revision 4) — this study had none until now, and needed it.** 8 model fits (4 candidates × fixed/refit) mean 28 possible pairwise contrasts — reporting "lowest LORO RMSE wins" as originally drafted is model-shopping without correction. Prespecified before any data collection:
- The exact 8-fit list, functional forms, and coefficient sources (Section 2, `METABOLIC_MODEL_LITERATURE_REVIEW.md`) are frozen now, identically to how Aim 1's detector thresholds are frozen in Section 6.5.
- **One primary comparison, no selection freedom**: M1-fixed (Minetti, published coefficients) vs. M0 (Garmin's undocumented GAP baseline), one-sided LORO RMSE against Polar H10. This is the headline Aim 2 claim.
- All other pairwise contrasts are secondary/exploratory, Holm-corrected, and never substitute for the primary claim.
- Fixed-vs-refit variants are scored **only** out-of-sample under LORO, with any refitting happening strictly inside the training fold.
- **Identifiability check, corrected** (the original "Pearson r>0.99 on raw predicted demand" was itself an unanchored round number, echoing the same flaw already found in the retired F1/AUC targets): check correlation on the actual regressors entering the fit (ṽ_fast/ṽ_slow, not raw u — EWMA filtering materially changes the correlation), and check design-matrix collinearity (canonical correlation/angle between `[1, ṽ_fast, ṽ_slow]` for each candidate pair), not just a bivariate correlation on the demand signal — two candidates can be pairwise-correlated below any raw threshold while still spanning near-identical subspaces in the actual fit. Express the "can we tell these apart" question in the outcome's own units, not an arbitrary r: δ_AB = RMS(HR_exp^A − HR_exp^B), each independently best-fit to the same Polar H10 trace; declare "no power to separate" only when δ_AB falls below the minimum detectable per-run RMSE difference at N=24–30 (derived empirically from pilot-run spread, not asserted).
- **Tie-break rule, prespecified now, not decided post hoc**: if the identifiability check finds M1 and M3 (Minetti, Pontzer) cannot be separated, report them as tied and use M1-fixed as Stage A's production input by this prior rule.
- Aim 1 and Aim 2 are separate multiplicity families — corrections within each, never pooled across them.

### 6.5 Freezing discipline — the confirmatory core

The Phase-4 calibration already found `min_stretch_seconds` (30–45s) and `stretch_mae_lock_bpm` (5.0–6.0) stable — unanimous leave-one-run-out optimal sets — while `frac_within_min` and `window_seconds` were fold-unstable. **This preregistration freezes all of them at their current incumbent values, explicitly out of scope for retuning during the 24–30-run study.** The study is confirmatory on a frozen detector; added runs sharpen confidence-interval precision, they never reopen a threshold. This is the concrete mechanism by which this preregistration avoids repeating the free-parameter trap this project already renounced once (`analysis.py`, `stagno_trimp` docstring).

### 6.6 Tests

- "Detector B recovers more locked time than detector A": paired run-level difference, exact Wilcoxon signed-rank (one-sided) or sign-flip permutation over runs, Hodges-Lehmann median difference + CI.
- **Aim 2's tournament, corrected (statistical review, Section 9 Revision 4):** the original spec (pairwise Wilcoxon signed-rank) is a two-sample test, insufficient once there are 8 candidate fits rather than one A/B comparison. Use **Friedman's test** as the omnibus (runs as blocks, the correct non-parametric repeated-measures test at n=24–30 runs, k=8 fits) on per-run RMSE against Polar H10. Only if Friedman rejects, proceed to post-hoc **Conover-Iman with Holm correction**; report each surviving contrast's Hodges-Lehmann median difference plus a run-clustered BCa CI. The one exception is the single prespecified primary comparison (M1-fixed vs. M0, Section 6.4), which is tested directly by one-sided Wilcoxon signed-rank regardless of the omnibus result — it doesn't need Friedman's protection since it was never a selected-after-looking contrast. Flag explicitly in any manuscript: LORO folds share training data across folds, which mildly underestimates variance (Nadeau-Bengio) — either apply their corrected-variance estimator or state the test is slightly anti-conservative.
- Agreement: per-run Lin's CCC aggregated by median, or repeated-measures correlation (rmcorr, run as repeated factor) — never pooled Pearson on 1 Hz frames.
- Any slope estimate (e.g. drift): Theil-Sen, per the project's existing robust-estimator convention.
- **Negative controls retained from the internal calibration, now part of the preregistered battery**, not just internal validation: cadence phase-randomization (must collapse the score; the incumbent detector's own circular-shift result — median 0.638× real — is a known weakness to explicitly re-test against the new model), and near-zero firing on negative-flagged runs.
- **New negative control for Aim 2 specifically**: a cadence-proxied Kram & Taylor (1990) cost-of-generating-force model (`METABOLIC_MODEL_LITERATURE_REVIEW.md` §3) is deliberately excluded as a real tournament candidate because its only available ground-contact-time proxy makes predicted demand directly proportional to cadence — circular for a cadence-lock detector. If it's ever run and "wins," that's evidence of feature leakage into the tournament, not a real result, and should be reported as such.

---

## 7. Data Management & Open Science Commitments

Unchanged from the original draft (open-access raw data archive, MIT-licensed code release on submission, documented deviations in supplementary materials), with `phase3_labels/`, `phase4_calibration/`, and this document's own revision history (Section 9) added to the released artifact trail.

---

## 8. Full Reference List

1. Bent, B., Goldstein, B.A., Kibbe, W.A., & Dunn, J.P. (2020). Investigating sources of inaccuracy in wearable optical heart rate sensors. *npj Digital Medicine*, 3, 18. doi:10.1038/s41746-020-0226-6
2. Minetti, A.E., Moia, C., Roi, G.S., Susta, D., & Ferretti, G. (2002). Energy cost of walking and running at extreme uphill and downhill slopes. *J Appl Physiol*, 93(3), 1039–1046. doi:10.1152/japplphysiol.01177.2001
10. Margaria, R., Cerretelli, P., Aghemo, P., & Sassi, G. (1963). Energy cost of running. *J Appl Physiol*, 18(2), 367–370. doi:10.1152/jappl.1963.18.2.367
11. Margaria, R. (1968). Positive and negative work performances and their efficiencies in human locomotion. *Int Z Angew Physiol*, 25, 339–351. doi:10.1007/BF00699624
12. Kram, R., & Taylor, C.R. (1990). Energetics of running: a new perspective. *Nature*, 346(6281), 265–267. doi:10.1038/346265a0 — **excluded as an Aim 2 candidate, retained as a negative control; see `METABOLIC_MODEL_LITERATURE_REVIEW.md` §3.**
13. Pontzer, H. (2016). A unified theory for the energy cost of legged locomotion. *Biol Lett*, 12(2), 20150935. doi:10.1098/rsbl.2015.0935. **Correction:** Pontzer, H. (2023). *Biol Lett*, doi:10.1098/rsbl.2023.0492 — equation (3.3) had a typographical error in the original printing; use the corrected form only.
14. Cavagna, G.A., & Kaneko, M. (1977). Mechanical work and efficiency in level walking and running. *J Physiol*, 268, 467–481. doi:10.1113/jphysiol.1977.sp011866 — level-only, not a tournament entrant; qualitative context.
3. Gilgen-Ammann, R., Schweizer, T., & Wyss, T. (2019). RR interval signal quality of a heart rate monitor and an ECG Holter at rest and during exercise. *Eur J Appl Physiol*, 119, 1525–1532.
4. Pasadyn, S.R. et al. (2019). Accuracy of commercially available heart rate monitors in athletes: a prospective study. *Cardiovasc Diagn Ther*, 9(4), 379–385. doi:10.21037/cdt.2019.06.05
5. Bland, J.M., & Altman, D.G. (1986). Statistical methods for assessing agreement between two methods of clinical measurement. *The Lancet*, 1(8476), 307–310.
6. Lin, L.I. (1989). A concordance correlation coefficient to evaluate reproducibility. *Biometrics*, 45(1), 255–268.
7. McBride, G.B. (2005). A proposal for strength-of-agreement criteria for Lin's CCC. NIWA Client Report HAM2005-062.
8. Nelson, B.W., & Allen, N.B. (2019). Accuracy of Consumer Wearable Heart Rate Measurement During an Ecologically Valid 24-Hour Period. *JMIR mHealth uHealth*, 7(3), e10828.
9. Stagno, K.M., Thatcher, R., & van Someren, K.A. (2007). A modified TRIMP to quantify in-season training load. *J Sports Sci*, 25(6), 629–634.

*(Full extended list, including Polar H10 HRV-validity papers and the raw-PPG-domain lineage citations not directly load-bearing here, in `WEARABLE_HR_VALIDATION_FRAMEWORK_PROPOSAL.md` and `CADENCE_LOCK_LITERATURE_NOTES.md`.)*

---

## 9. Revision Log

**Revision 4 (2026-07-28).** Scoped statistician review (not a full re-audit — the core architecture already cleared Revision 1's review; this targeted only Aim 2's promotion to the study's centerpiece and the identifiability pre-check, both drafted by the Architect and never actually checked by a statistician). Findings and fixes: Pearson confirmed correct over Spearman for the affine-invariance the kinetics fit needs, but the check itself had three real defects — wrong signal (raw demand instead of the actual filtered regressors), wrong dimensionality (bivariate instead of design-matrix collinearity), and an unanchored r>0.99 threshold repeating the same flaw already found in the original F1/AUC targets; replaced with a derived, bpm-scale δ_AB quantity (Section 6.4). Aim 2 had no freezing/multiplicity discipline despite 8 model fits (28 possible pairwise contrasts) — added a single prespecified primary comparison (M1-fixed vs. M0 baseline) plus Holm-corrected secondary contrasts, and a prespecified tie-break rule (Section 2, Section 6.4). Section 6.6's test spec (pairwise Wilcoxon) was insufficient for an 8-fit tournament — replaced with Friedman's omnibus gated to Conover-Iman post-hoc. Stage A's per-run fitting of all 5 kinetics parameters was found capable of manufacturing a false null by absorbing genuine model differences — τ_fast/τ_slow now fixed from training folds only when scoring Aim 2 (Section 5A). The ±20-25% grade-extension contingency was simulated and found counterproductive (widening the range raises inter-model correlation, not lowers it) — replaced with a balanced-grade-level design at the existing ±15% range (Section 4, Arm 2). Consistency pass (Architect, same session): fixed the Revision 2/3 log ordering (had been inserted out of sequence), the stale header revision number, and a cross-reference pointing to the wrong section for the identifiability check.

**Revision 1 (2026-07-27).** Four-reviewer audit (physiology, biomechanics, sensor-engineering, statistics) plus reconciliation against existing repo literature. Changes: ground-truth definition replaced (Section 6.1); zero-phase filtering replaced with causal-only bi-exponential kinetics (Section 5A); harmonic grid restored to full validated set (Section 5B); HMM replaced with a fixed-transition 1-D emission, full HMM deferred to ≥80-100 episodes (Section 5C); Stage D reconstruction reframed as conditional Phase 2 with required provenance constraints (Section 5D); benchmark targets replaced with comparisons against the frozen incumbent detector, unit-of-analysis corrected from sample to episode/run throughout (Section 6); Perturbation arm moved off treadmill, metronome-walking added (Section 4, Arm 5); Minetti reframed as a hypothesis requiring first-principles validation, not an import (Section 5A, Aim 2); "gold standard" language corrected to "silver standard" with real citations (Section 6.2); IRB status corrected from asserted to pending (Section 1).

**Revision 2 (2026-07-28).** Owner resolved all four Section 10 open items from Revision 1. Reconstruction (former Aim 3 / Stage D) removed from this study's scope entirely, moved to a follow-up paper — not merely phase-gated anymore (Section 2, Section 5D). Aim 2 reframed from a validation check into the study's central contribution: a metabolic-cost model-selection tournament scored against Polar H10 reference HR, not against Garmin/Fitbit's own HR (Section 2, Section 6.4, Section 6.6). IRB status updated to reflect the owner's self-assessment (no risk beyond daily-living activity) plus intent to request a formal Exemption Determination Letter rather than proceed on self-assertion alone (Section 1). Repo placement decided: stays in this repo, no standalone spin-out. A literature-review task for Aim 2's competing models (Margaria 1968, Cavagna & Kaneko 1977, Kram & Taylor 1990, Pontzer 2016, alongside the already-cited Minetti 2002) was dispatched — see Section 10 for status.

**Revision 3 (2026-07-28).** Literature review for Aim 2's tournament candidates completed and verified (`METABOLIC_MODEL_LITERATURE_REVIEW.md`, all citations checked via WebSearch/WebFetch, not recalled from memory). Corrected the Minetti reference to its full author list (Section 8); added four new verified references (Margaria 1963 and 1968 — the owner's "Margaria et al. 1968" conflated two separate papers, Pontzer 2016 with its real 2023 erratum independently re-confirmed, Cavagna & Kaneko 1977); Kram & Taylor 1990 excluded as an Aim 2 candidate and repurposed as a dedicated negative control (Section 6.6) because its only available proxy input would make predicted metabolic demand directly proportional to cadence — circular in a cadence-lock detector; Cavagna & Kaneko 1977 found to be level-only, not competable, kept as qualitative context; added a required pre-tournament identifiability check (pairwise correlation of candidate demand predictions across the observed grade range) to Aim 2 and Stage A, since the current ±8–15% grade arm may not have the curvature range to separate the surviving candidates, with a possible protocol amendment flagged in Section 4, Arm 2. Section 10.3 closed.

---

## 10. Open Items — Status

**10.1 Reconstruction scope — DECIDED (2026-07-28).** Moved to a follow-up paper, removed from this study entirely (Section 2, Section 5D).

**10.2 IRB — IN PROGRESS.** Owner's self-assessment: n=1, no risk beyond activities of daily living, substantively no IRB required. Pursuing a formal Exemption Determination Letter as the documented artifact a real submission needs, rather than proceeding on self-assertion alone. Still not something any agent can obtain — an owner action item.

**10.3 Alternative metabolic-cost models for Aim 2 — DONE (2026-07-28).** See `METABOLIC_MODEL_LITERATURE_REVIEW.md`. Tournament candidates: Minetti 2002, a Margaria-derived piecewise-linear model, Pontzer 2016 (corrected 2023), Garmin's own GAP as baseline. Kram & Taylor 1990 excluded as a candidate (circular via its only available proxy input), repurposed as a negative control. Cavagna & Kaneko 1977 excluded (level-only, no grade data). One thing still genuinely open: whether the current ±8-15% grade arm has enough curvature range to separate the surviving candidates at all — the required pre-check (Section 2, Aim 2) will settle that empirically; if it doesn't separate them, Section 4 Arm 2 already flags the fix (extend toward ±20-25%).

**10.4 Standalone framework vs. extension in this repo — DECIDED (2026-07-28).** Stays in this repo.
