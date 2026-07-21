# Project Directive: Cadence Lock Investigator

## Project Mission

This project is an automated biomechanical analysis pipeline designed to isolate and quantify "cadence lock" artifacts in wearable heart rate data. The goal is to correlate stride cadence (Garmin-sourced) against multi-platform heart rate telemetry (Garmin + Fitbit/Google Health) to identify signal synchronization between mechanical movement and cardiovascular metrics.

## Technical Architecture

* Backend: Python 3.12, FastAPI, Pandas, NumPy.
* Database: SQLite (token persistence).
* Visualization: Plotly.js (client-side).
* Data Sources: Garmin Connect (via garminconnect), Google Health Connect API.

## Operational Constraints (Strict)

1. Data Integrity: We utilize raw, non-interpolated data. Do not introduce ffill(), bfill(), or automated smoothing algorithms without explicit approval. Gaps in sensor reporting are features, not bugs—they must be represented as breaks in the visualization.
2. Temporal Accuracy: All telemetry must be time-aligned to the local timezone.
3. Schema Resilience: The Garmin data parser (parse_garmin_metrics) is dynamic. It must rely on metricDescriptors to resolve indices, as Garmin schema indices change based on watch models and activity types.
4. JSON Compliance: Ensure all output to the frontend replaces np.nan and np.inf with None to prevent serialization errors.

## Current System State
* Ingestion Pipeline: Functional. It aligns Fitbit (Google Health) and Garmin data streams into a synchronized 1Hz synchronized Pandas DataFrame.
* Visualization: Plotly is configured for lines+markers with connectgaps: false to ensure visual signal gaps are accurate.
* Known Fixes Applied:
    * Dynamic schema parsing using metricDescriptors.
    * Corrected Garmin metric mapping (prioritizing directDoubleCadence over directRunCadence).
    * Resolved FastAPI JSON encoding crashes via np.nan replacement.

## Strategic Objectives (Next Steps)
1. Correlation Analysis: Develop a statistical module to quantify the relationship between cadence_spm and HR variance during suspected lock periods.
2. Signal Processing: Implement custom low-pass filters to isolate cadence-frequency noise from true cardiovascular pulse signals.
3. Anomaly Detection: Create a heuristic for "Cadence Lock Detection" that flags high-correlation periods where cadence and HR signals exhibit phase-locked behavior.
4. Cardio Load Overestimation: Investigate the impact of cadence lock on HR-based training load metrics (e.g., TRIMP, Training Stress Score).
5. User Interface: Allows users to select from their most recent runs and visualize the cadence lock effect, with cardio load implications highlighted.
6. Github Hosting: Deploy the application on GitHub Pages or a similar platform for public access, ensuring that sensitive data is not exposed.
7. Visual Modernization: Enhance the Plotly visualization to look more modern and user-friendly.
8. VDOT Integration: Integrate Garmin pace data and Jack Daniels' VDOT calculations to provide an estimate of what a person's HR would be at the observed running pace. 