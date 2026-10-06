# F1 Live Timing Dashboard

A Python-based Formula 1 live timing and strategy dashboard built with Streamlit. It connects to the public [OpenF1 API](https://openf1.org/) to display current session timing, tyre strategy, weather, telemetry, and race control information in a clean, race-focused interface.

## Overview

This dashboard is designed for monitoring live race conditions and comparing performance trends during a session. It surfaces the most relevant data from a race weekend, including:

- live driver order and gaps
- lap pace and sector timing trends
- tyre stint information
- weather and track conditions
- telemetry summaries
- race control messages
- top-15 lap correlation ranking

## Features

- Real-time timing refresh from OpenF1
- Session status labels for live, upcoming, archived, and recent data states
- Driver leaderboard with position, gap, and pace context
- Correlation-based ranking to compare lap consistency against the leader
- Weather and track data panels
- Manual refresh controls and refresh interval settings
- CSV/JSON export support for session snapshots

## Tech stack

- Python 3.12
- Streamlit
- Pandas
- Plotly
- Requests

## Quick start

### 1) Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 2) Run the dashboard

```powershell
python -m streamlit run app.py --server.port 3000
```

Then open:

```text
http://localhost:3000
```

The VS Code task named "Python: F1 dashboard" runs the same command automatically.

## Data behavior and refresh model

The app pulls live timing data from OpenF1 during active race sessions. The dashboard refreshes at configurable intervals and can also be refreshed manually. It always shows the newest timestamp received from the API and clearly distinguishes between live, archived, or recent data states.

Key behavior notes:

- It uses the latest published session data from OpenF1
- It does not fabricate or simulate values
- Feed availability and older historical coverage depend on the upstream API
- Archived sessions are labeled accordingly on the dashboard

## Lap correlation analysis

The Top 15 view calculates a Pearson correlation between each driver's completed lap times and the race leader's lap times, using matching laps only. It excludes pit-out laps, requires a minimum number of shared laps, and shows average lap-time delta and coverage for context.

This helps highlight which drivers are most closely matching the leader's pace over the course of the session.

## Export pipeline

The project also includes a standalone export script for saving the latest timing table and session metadata to disk.

```powershell
python f1_analytics_pipeline.py
```

This writes the following files in the current directory:

- `f1_live_timing.csv`
- `f1_live_session.json`

## Project structure

```text
.
├── app.py                     # Streamlit dashboard entry point
├── f1_analytics_pipeline.py   # Export pipeline for session data
├── f1_data.py                # OpenF1 data fetching and analytics logic
├── requirements.txt          # Python dependencies
├── readme.md                 # Project documentation
├── public/                   # static/public assets
├── scripts/                  # supporting scripts
├── src/                      # frontend and app source assets
├── powerbi/                  # Power BI related assets
└── .env.example              # example environment settings
```

## Notes

- This project is intended for live race monitoring and analysis rather than a full historical race simulator.
- For the best experience, use it during an active F1 session where OpenF1 data is available.
- The app depends on public API availability and can vary by session coverage and timing.

## License

This project is provided as-is for educational and personal dashboard use. Please check any upstream data provider terms before using it in production or commercial workflows.

