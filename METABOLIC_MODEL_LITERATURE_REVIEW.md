# Metabolic Model Literature Review — Aim 2 Tournament Candidates

*Companion to `N_OF_1_STUDY_PROTOCOL.md`, Aim 2 (Metabolic Model Selection Tournament). Every citation below was verified via WebSearch/WebFetch, not recalled from memory. Confidence key: **[FT]** verified in full text · **[AB]** verified via publisher abstract/indexed metadata · **[2°]** verified only via a peer-reviewed secondary source that quotes it · **[!]** uncertain, flagged explicitly.*

---

## 1. Margaria — the owner's citation string is imprecise; two papers, not one

**"Margaria et al., 1968" as given conflates two distinct, real papers:**

- **Margaria, R. (1968).** "Positive and negative work performances and their efficiencies in human locomotion." *Int. Z. Angew. Physiol.* 25:339–351. DOI 10.1007/BF00699624, PMID 5658204. [AB] **Single-authored** — "et al." is wrong for this one.
- **Margaria, R., Cerretelli, P., Aghemo, P. & Sassi, G. (1963).** "Energy cost of running." *J Appl Physiol* 18(2):367–370. DOI 10.1152/jappl.1963.18.2.367, PMID 13932993. [AB] This is the actual "et al." grade-cost paper: two athletes, speeds to 22 km/h, **grade −20% to +15%** — brackets this project's protocol arm almost exactly. Net cost (kcal·kg⁻¹·km⁻¹) found **independent of speed, a function of incline only**.

**Cite both.** The 1963 paper supplies the grade-cost data; the 1968 paper supplies the mechanism: efficiency of positive (uphill) work ≈ **0.25**, of negative (downhill) work ≈ **−1.20** [AB, from the abstract — not verified against full text].

**Testable form (derived, not published verbatim — labelled "Margaria-derived"):** a two-branch piecewise-linear cost-vs-grade model, C_r(i) ≈ C_r(0) + g·i/η, η=0.25 (i>0) or −1.20 (i<0). 2–3 parameters. **Input: grade only** — fully available in this project's data.

## 2. Cavagna & Kaneko (1977) — not competable

"Mechanical work and efficiency in level walking and running." *J Physiol* 268:467–481. DOI 10.1113/jphysiol.1977.sp011866, PMID 874922. [AB]

Measured external (COM) and internal (limb-relative-to-trunk) mechanical work via cinematography, 3–33 km/h. Internal work rises roughly as speed², with efficiency exceeding muscle's ~25% ceiling in running — the paper's actual argument for elastic tendon recoil.

**Level-only — no grade condition was tested at all.** Cannot produce a grade-cost prediction, which is what Aim 2 needs. Also requires limb-segment kinematics this project has no sensor for. No verifiable closed-form equation for internal work was found in the source — do not print one. **Qualitative context only, not a tournament entrant.**

## 3. Kram & Taylor (1990) — excluded as a demand model; use as a negative control

"Energetics of running: a new perspective." *Nature* 346(6281):265–267. DOI 10.1038/346265a0, PMID 2374590. [AB]

Cost-of-generating-force hypothesis: metabolic rate tracks the cost of supporting body weight, scaled by how *fast* that force must be generated. Quoted form, via Full & Tu (2022, *J Exp Biol* 225:jeb244755) [2°]:

**Ė_met = F_BW · t_c⁻¹ · c**

**Cannot be used here, and not only for missing data.** Ground-contact time (t_c) is confirmed absent from this device's schema (`CADENCE_LOCK_DETECTOR_PROPOSAL.md` §3b). The only available proxy is duty-factor-derived, which makes predicted demand **directly proportional to cadence** — feeding a cadence-proportional signal into a detector whose entire purpose is flagging HR that collapses onto cadence. A genuine lock episode would inflate the model's own demand estimate and suppress exactly the residual meant to catch it.

**Recommendation: exclude as a demand model. Repurpose as a negative control** — if a cadence-proxied Kram–Taylor form ever "wins" the tournament, that is evidence of feature leakage, not of physiology. Worth stating explicitly in the manuscript as the principled reason force-based cost models are out of scope here — it strengthens Aim 1's argument rather than weakening Aim 2.

## 4. Pontzer (2016) — the strongest new candidate, with a real erratum

"A unified theory for the energy cost of legged locomotion." *Biol Lett* 12(2):20150935. DOI 10.1098/rsbl.2015.0935, PMID 26911339. Single-authored. [AB]

**A correction exists and matters: Pontzer, H. (2023), *Biol Lett*, DOI 10.1098/rsbl.2023.0492 (13 Dec 2023).** A typographical error in equation (3.3) survived the original page proofs — **independently confirmed** (WebSearch, 2026-07-28, matching the review agent's finding exactly). Corrected form:

**E_COT = 8·M^(−0.34) + 50·[1 + sin(2θ − 74)]·M^(−0.12)**

θ and all angles in **degrees**; M in kg; E_COT in J·kg⁻¹·m⁻¹; θ = substrate slope angle. Anyone implementing the uncorrected 2016 printing gets silently wrong numbers — use the corrected form only.

**Cross-checked independently:** at θ=24.2° (grade +0.45), M=70kg, the corrected equation gives ≈18.9 J·kg⁻¹·m⁻¹ against Minetti's own *measured* 18.93±1.74 at the same grade; at level, ≈3.05 vs Minetti's 3.40. Two independently-derived models agreeing to three significant figures at the range extreme is real corroboration that the corrected equation is transcribed right.

**Inputs: grade (as angle) and body mass only** — both available. Directly competable with Minetti.

**Real limitation to state in the manuscript:** it's an interspecific allometric regression (insects→ungulates); applying it within one individual's grade variation is an extrapolation the paper doesn't itself license. Compete it two ways: published coefficients fixed, and the sinusoidal *form* refit to this runner's own data.

## 5. Minetti et al. (2002) — incumbent, re-verified, one citation fix needed

**Minetti, A.E., Moia, C., Roi, G.S., Susta, D. & Ferretti, G. (2002).** "Energy cost of walking and running at extreme uphill and downhill slopes." *J Appl Physiol* 93(3):1039–1046. DOI 10.1152/japplphysiol.01177.2001, PMID 12183501. [AB]

`N_OF_1_STUDY_PROTOCOL.md`'s reference list currently reads "Minetti, A.E. et al." — replace with the full author list above.

C_r(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6 (J·kg⁻¹·m⁻¹, i as a fraction). 10 subjects, treadmill, valid **−0.45 ≤ i ≤ +0.45**. Measured: 3.40±0.24 at level; 18.93±1.74 at +0.45; minimum **1.73±0.36 at i=−0.20**; 3.92±0.81 at −0.45. The measured minimum corroborates this project's own independently-computed polynomial minimum near g≈−0.18 (`N_OF_1_STUDY_PROTOCOL.md` §4, Arm 2). Note the polynomial's intercept (3.6) isn't identical to the measured level value (3.40) — it's a fit, not an interpolant.

---

## 6. Synthesis: the lineage, and the tournament design

**Not a clean linear history.** Margaria (1963/1968, empirical grade cost + work efficiencies) and Cavagna & Kaneko (1977, mechanical work/elastic recoil) form a real sequence within the *work-based* tradition. Kram & Taylor (1990) is a **deliberate break** from that tradition, reframing cost as force-rate rather than work — not a refinement of it. Minetti (2002) is an **independent empirical-fit branch** extending Margaria's grade data, not a synthesis of Cavagna's or Kram's work. Pontzer (2016) is the only paper here that explicitly attempts to unify the work- and force-based branches from muscle physiology.

### Tier 1 — directly competable (grade → cost; no cadence input, no missing sensors)

| Model | Free params | Inputs |
|---|---|---|
| M0 — Garmin `directGradeAdjustedSpeed` (incumbent baseline) | 0 | already available |
| M1 — Minetti 2002 polynomial | 6 (published) | grade |
| M2 — Margaria-derived piecewise-linear | 2–3 | grade |
| M3 — Pontzer 2016 (corrected) sinusoidal | 2 (+mass) | grade, body mass |

Each run in two variants — **published coefficients fixed**, and **functional form refit to this runner's own data**. The fixed variant tests the published models directly; the refit variant tests whether the *shape* generalizes even if the coefficients don't.

### Tier 2 — not competable, but reportable

- **Kram & Taylor 1990** — excluded per Section 3. Included only as a **negative control**.
- **Cavagna & Kaneko 1977** — level-only. Qualitative context; no grade-cost prediction available from it.

### Required pre-check before declaring a tournament winner — corrected by statistical review, 2026-07-28 (see `N_OF_1_STUDY_PROTOCOL.md` §9 Revision 4 for the full changelog; this section is a summary, §6.4/6.6 of that document are now authoritative)

Stage A's kinetics model (`N_OF_1_STUDY_PROTOCOL.md` §5A) is `HR_exp = a + b·ṽ_fast + c·(ṽ_slow − ṽ_fast)`. **Two demand models differing only by an affine transform are unidentifiable in this fit** — `a`/`b` absorb the difference. Discrimination only comes from *curvature* differences across the sampled grade range.

**The original pre-check here (pairwise Pearson r>0.99 on raw predicted u[t]) was independently audited by statistical review and found to have three real defects**, not just an arbitrary threshold: (1) it checked raw demand, not the actual EWMA-filtered regressors that enter the fit, which materially changes the correlation; (2) a bivariate correlation is insufficient for a 3-parameter design matrix `[1, ṽ_fast, ṽ_slow]` — two candidates can be pairwise-uncorrelated on paper yet span near-identical subspaces in the real fit; (3) r>0.99 was itself an unanchored round number, the same flaw already found in this document's original benchmark targets. The corrected check expresses "can we tell these apart" in bpm — δ_AB = RMS(HR_exp^A − HR_exp^B), each independently best-fit to Polar H10 — and declares no power only when δ_AB falls below the minimum detectable per-run RMSE difference at N=24–30, derived from pilot data rather than asserted.

**The originally-proposed fix — extending the hill arm to ±20–25% grade — does NOT work, confirmed by simulation.** Widening the sampled range *increases* Minetti-vs-Pontzer correlation rather than separating them (M1–M3 Pearson: 0.9969 at ±15% → 0.9938 at ±25% → 0.9892 at ±45%), because their shared monotone trend grows faster across a wider range than their curvature difference does. It also collides with Stage A's speed-support guard: sustained ±25% grade forces walking pace, which Minetti's running-specific polynomial isn't valid for. The real fix is a **balanced grade design** at the existing ±15% range — steady blocks at ~9 grade levels deliberately including −20% (Minetti's own measured cost minimum) and +5%/+10% (where the candidates diverge most), plus blocks that deliberately decouple pace from grade, since on ordinary hill repeats pace is a near-deterministic function of grade and that coupling is itself a large part of the identifiability problem. See `N_OF_1_STUDY_PROTOCOL.md` §4 Arm 2 for the full amended protocol.

Scoring otherwise as already specified in `N_OF_1_STUDY_PROTOCOL.md` §6.4/6.6: per-run RMSE against Polar H10 reference HR, leave-one-run-out, Wilcoxon signed-rank, run-clustered BCa bootstrap.

### Future work — deliberately parked, not this paper (2026-07-28)

If the identifiability check above finds M1–M3 indistinguishable, the natural next question is whether a novel or refined cost-of-locomotion model — informed by exactly the gap this comparison exposes — could do better. Worthwhile, but deliberately out of scope here: it would need its own held-out data (not the same hill-repeat runs used to select among M1–M3) and its own pre-registered validation, or "our model wins" collapses into the overfitting trap this project has worked to avoid everywhere else (`analysis.py`'s `stagno_trimp` docstring; the Phase 4 freezing discipline; `N_OF_1_STUDY_PROTOCOL.md` §6.5). This paper's claim stays clean — a rigorous comparison of the established literature against a real reference — and model development becomes the natural next paper, not an addendum to this one.

### Sources

[Margaria 1963](https://pubmed.ncbi.nlm.nih.gov/13932993/) · [Margaria 1968](https://link.springer.com/article/10.1007/BF00699624) · [Cavagna & Kaneko 1977](https://physoc.onlinelibrary.wiley.com/doi/abs/10.1113/jphysiol.1977.sp011866) · [Kram & Taylor 1990](https://www.nature.com/articles/346265a0) · [Kram–Taylor equation as quoted, Full & Tu, J Exp Biol 2022](https://journals.biologists.com/jeb/article/225/18/jeb244755/276926/Evaluating-the-cost-of-generating-force-hypothesis) · [Pontzer 2016](https://royalsocietypublishing.org/rsbl/article/12/2/20150935/50334/A-unified-theory-for-the-energy-cost-of-legged) · [Pontzer 2023 correction](https://royalsocietypublishing.org/doi/10.1098/rsbl.2023.0492) (independently re-confirmed 2026-07-28) · [Minetti 2002](https://journals.physiology.org/doi/full/10.1152/japplphysiol.01177.2001)
