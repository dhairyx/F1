"""Python-first live Formula 1 timing and telemetry dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from f1_data import (
    OpenF1Error,
    build_pace_correlation_table,
    build_timing_table,
    fetch_latest_session,
    fetch_openf1,
    fetch_session_bundle,
    format_lap_time,
    latest_data_timestamp,
)


st.set_page_config(page_title="F1 Live Timing", page_icon="🏁", layout="wide")

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
      :root { --bg:#101417; --panel:#171d20; --line:#2a3336; --muted:#9aa7a7; --red:#e10600; --lime:#b4e04b; --cyan:#67c8cf; }
      html, body, [class*="css"] { font-family:'Barlow Condensed',sans-serif; letter-spacing:0; }
      [data-testid="stAppViewContainer"] { background:var(--bg); }
      [data-testid="stHeader"] { background:transparent; }
      [data-testid="stSidebar"] { background:#141a1d; border-right:1px solid var(--line); }
      [data-testid="stMetric"] { background:var(--panel); border:1px solid var(--line); padding:12px 15px; border-radius:4px; }
      [data-testid="stMetricLabel"] { color:var(--muted); font-size:13px; }
      [data-testid="stMetricValue"] { font-family:'IBM Plex Mono',monospace; font-size:25px; }
      .mono { font-family:'IBM Plex Mono',monospace; }
      .masthead { border-bottom:1px solid var(--line); padding:5px 0 16px; margin-bottom:18px; display:flex; justify-content:space-between; align-items:end; gap:16px; }
      .masthead h1 { font-size:36px; line-height:1; margin:0; font-weight:800; }
      .masthead p { margin:6px 0 0; color:var(--muted); font-size:15px; }
      .live-pill { border:1px solid #385127; color:var(--lime); background:#1a2618; padding:5px 10px; font-family:'IBM Plex Mono',monospace; font-size:12px; white-space:nowrap; }
      .section-label { text-transform:uppercase; color:var(--muted); font-family:'IBM Plex Mono',monospace; font-size:11px; margin:14px 0 6px; }
      div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:4px; }
      .source-note { color:var(--muted); font-size:13px; border-top:1px solid var(--line); padding-top:10px; }
      @media(max-width:700px) { .masthead h1 {font-size:28px;} .live-pill {font-size:10px;} }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=45, max_entries=32, show_spinner=False)
def cached_openf1(endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return fetch_openf1(endpoint, params)


@st.cache_data(ttl=10, max_entries=32, show_spinner=False)
def cached_live_openf1(endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return fetch_openf1(endpoint, params)


@st.cache_data(ttl=15, max_entries=8, show_spinner=False)
def cached_bundle(session_key: int) -> dict[str, Any]:
    return fetch_session_bundle(session_key)


@st.cache_data(ttl=30, max_entries=4, show_spinner=False)
def cached_latest_session() -> dict[str, Any]:
    return fetch_latest_session()


@st.cache_data(ttl=86400, max_entries=16, show_spinner=False)
def cached_meeting(meeting_key: int) -> dict[str, Any]:
    meetings = cached_openf1("meetings", {"meeting_key": meeting_key})
    return meetings[-1] if meetings else {}


def _configure_openf1_credentials() -> None:
    try:
        secrets = st.secrets
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return
    for name in ("OPENF1_USERNAME", "OPENF1_PASSWORD"):
        try:
            value = os.getenv(name) or secrets.get(name)
        except st.errors.StreamlitSecretNotFoundError:
            value = os.getenv(name)
        if value:
            os.environ.setdefault(name, str(value))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _session_state(session: dict[str, Any], timestamp: datetime | None) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start, end = _parse_time(session.get("date_start")), _parse_time(session.get("date_end"))
    if start and end and start <= now <= end:
        if timestamp and (now - timestamp).total_seconds() <= 120:
            return "LIVE", "#b4e04b"
        return "SESSION IN PROGRESS", "#f1c453"
    if start and start > now:
        return "UPCOMING", "#67c8cf"
    if timestamp and (now - timestamp).total_seconds() <= 180:
        return "RECENT DATA", "#f1c453"
    return "LATEST SESSION", "#9aa7a7"


def _show_weather(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info("Weather readings are not available for this session.")
        return
    weather = rows[-1]
    metrics = (
        ("Air temperature", weather.get("air_temperature"), "°C"),
        ("Track temperature", weather.get("track_temperature"), "°C"),
        ("Humidity", weather.get("humidity"), "%"),
        ("Wind speed", weather.get("wind_speed"), "m/s"),
        ("Wind direction", weather.get("wind_direction"), "°"),
        ("Pressure", weather.get("pressure"), "mbar"),
        ("Rain", "YES" if weather.get("rainfall") else "NO" if weather.get("rainfall") is not None else "--", ""),
    )
    for start in range(0, len(metrics), 4):
        columns = st.columns(min(4, len(metrics) - start))
        for column, (label, value, suffix) in zip(columns, metrics[start:start + 4]):
            column.metric(label, f"{value}{suffix}" if value is not None else "--")


def _build_driver_lap_metrics(
    bundle: dict[str, Any], speed_rows: list[dict[str, Any]], timing: pd.DataFrame
) -> pd.DataFrame:
    columns = ["Pos", "Driver", "No.", "Avg lap speed (km/h)", "Avg lap time", "Laps sampled"]
    laps = pd.DataFrame(bundle.get("laps", []))
    speeds = pd.DataFrame(speed_rows)
    required_lap_columns = {"driver_number", "lap_number", "lap_duration"}
    if timing.empty or not required_lap_columns.issubset(laps.columns):
        return pd.DataFrame(columns=columns)

    laps["driver_number"] = pd.to_numeric(laps["driver_number"], errors="coerce")
    laps["lap_number"] = pd.to_numeric(laps["lap_number"], errors="coerce")
    laps["lap_duration"] = pd.to_numeric(laps["lap_duration"], errors="coerce")
    if "date_start" in laps:
        laps["_lap_start"] = pd.to_datetime(laps["date_start"], utc=True, errors="coerce")
    else:
        laps["_lap_start"] = pd.Series(pd.NaT, index=laps.index, dtype="datetime64[ns, UTC]")
    if "date_end" in laps:
        laps["_lap_end"] = pd.to_datetime(laps["date_end"], utc=True, errors="coerce")
    else:
        laps["_lap_end"] = pd.Series(pd.NaT, index=laps.index, dtype="datetime64[ns, UTC]")
    missing_end = laps["_lap_end"].isna() & laps["_lap_start"].notna() & laps["lap_duration"].notna()
    laps.loc[missing_end, "_lap_end"] = (
        laps.loc[missing_end, "_lap_start"]
        + pd.to_timedelta(laps.loc[missing_end, "lap_duration"], unit="s")
    )
    laps = laps.dropna(subset=["driver_number", "lap_number", "lap_duration"])
    if "is_pit_out_lap" in laps:
        laps = laps[~laps["is_pit_out_lap"].fillna(False).astype(bool)]

    if not speeds.empty and {"date", "driver_number", "speed"}.issubset(speeds.columns):
        speeds["date"] = pd.to_datetime(speeds["date"], utc=True, errors="coerce")
        speeds["driver_number"] = pd.to_numeric(speeds["driver_number"], errors="coerce")
        speeds["speed"] = pd.to_numeric(speeds["speed"], errors="coerce")
        speeds = speeds.dropna(subset=["date", "driver_number", "speed"])
    else:
        speeds = pd.DataFrame(columns=["date", "driver_number", "speed"])

    rows: list[dict[str, Any]] = []
    for _, driver in timing.iterrows():
        driver_number = pd.to_numeric(driver.get("_driver_key", driver.get("No.")), errors="coerce")
        if pd.isna(driver_number):
            continue
        driver_laps = (
            laps[laps["driver_number"] == driver_number]
            .sort_values(["lap_number", "_lap_start"])
            .tail(10)
        )
        driver_speeds = speeds[speeds["driver_number"] == driver_number]
        lap_speed_averages: list[float] = []
        for _, lap in driver_laps.iterrows():
            if pd.isna(lap["_lap_start"]) or pd.isna(lap["_lap_end"]):
                continue
            lap_samples = driver_speeds[
                driver_speeds["date"].between(lap["_lap_start"], lap["_lap_end"])
            ].sort_values("date")
            if len(lap_samples) < 10:
                continue
            sample_span = (lap_samples["date"].iloc[-1] - lap_samples["date"].iloc[0]).total_seconds()
            if sample_span >= float(lap["lap_duration"]) * 0.7:
                lap_speed_averages.append(float(lap_samples["speed"].mean()))

        average_lap_time = driver_laps["lap_duration"].mean() if not driver_laps.empty else None
        rows.append(
            {
                "Pos": driver.get("Pos", "--"),
                "Driver": driver.get("Driver", "---"),
                "No.": int(driver_number),
                "Avg lap speed (km/h)": round(sum(lap_speed_averages) / len(lap_speed_averages), 1)
                if lap_speed_averages else None,
                "Avg lap time": format_lap_time(average_lap_time),
                "Laps sampled": f"{len(lap_speed_averages)} / {len(driver_laps)}",
            }
        )
    result = pd.DataFrame(rows, columns=columns)
    result["Avg lap speed (km/h)"] = pd.to_numeric(result["Avg lap speed (km/h)"], errors="coerce")
    return result


def _render_timing(bundle: dict[str, Any], timing: pd.DataFrame) -> None:
    if timing.empty:
        st.warning("Timing records have not been published for this session yet.")
        return

    search = st.text_input("Filter drivers or teams", placeholder="Driver, team, or number")
    sort_by = st.selectbox("Order by", ["Pos", "Last lap", "Lap Δ", "Tyre age", "Stops"], label_visibility="collapsed")
    visible = timing.copy()
    if search.strip():
        query = search.casefold()
        mask = visible[["Driver", "Name", "Team", "No."]].astype(str).apply(
            lambda column: column.str.casefold().str.contains(query, regex=False)
        ).any(axis=1)
        visible = visible[mask]
    if sort_by == "Last lap":
        visible = visible.sort_values("Last lap")
    elif sort_by == "Lap Δ":
        visible = visible.sort_values(
            "Lap Δ",
            key=lambda values: pd.to_numeric(values.str.replace("s", "", regex=False).str.replace("+", "", regex=False), errors="coerce"),
        )
    elif sort_by == "Tyre age":
        visible = visible.sort_values("Tyre age", ascending=False, key=lambda values: pd.to_numeric(values, errors="coerce"))
    elif sort_by == "Stops":
        visible = visible.sort_values("Stops", ascending=False)

    st.dataframe(
        visible[["Pos", "No.", "Driver", "Team", "Gap", "Interval", "Lap", "Last lap", "Lap Δ", "Tyre", "Tyre age", "Stops"]],
        hide_index=True,
        use_container_width=True,
        height=min(650, 42 + len(visible) * 35),
        column_config={
            "Pos": st.column_config.NumberColumn("Pos", width="small"),
            "No.": st.column_config.NumberColumn("No.", width="small"),
            "Driver": st.column_config.TextColumn("Driver", width="small"),
            "Team": st.column_config.TextColumn("Team", width="medium"),
            "Gap": st.column_config.TextColumn("Gap to leader", width="small"),
            "Interval": st.column_config.TextColumn("Interval", width="small"),
            "Tyre": st.column_config.TextColumn("Tyre", width="small"),
            "Last lap": st.column_config.TextColumn("Last lap", width="small"),
            "Lap Δ": st.column_config.TextColumn("Lap Δ", help="Latest lap time minus the previous lap; negative is quicker.", width="small"),
            "Tyre age": st.column_config.TextColumn("Tyre age", width="small"),
        },
    )
    st.download_button(
        "Download timing CSV",
        visible.to_csv(index=False).encode("utf-8"),
        file_name="f1_live_timing.csv",
        mime="text/csv",
        icon=":material/download:",
    )


def _render_circuit(bundle: dict[str, Any], timing: pd.DataFrame, session: dict[str, Any]) -> None:
    session_key = int(session["session_key"])
    meeting: dict[str, Any] = {}
    if session.get("meeting_key"):
        try:
            meeting = cached_meeting(int(session["meeting_key"]))
        except OpenF1Error as exc:
            st.caption(f"Circuit reference image unavailable: {exc}")
    circuit_name = session.get("circuit_short_name") or meeting.get("circuit_short_name") or "Circuit"
    location = session.get("location") or meeting.get("location") or session.get("country_name")
    st.subheader(f"{circuit_name} · Live circuit map")
    if location:
        st.caption(f"{location} · {session.get('session_name', 'Latest session')} · {session.get('year', '')}")
    st.markdown("#### Track conditions")
    try:
        _show_weather(cached_openf1("weather", {"session_key": session_key}))
    except OpenF1Error as exc:
        st.warning(str(exc))
    if meeting.get("circuit_image"):
        st.image(meeting["circuit_image"], width=280, caption=f"{circuit_name} circuit layout")

    positions = pd.DataFrame(bundle.get("position", []))
    if positions.empty or "date" not in positions:
        st.info("Driver positions are not available for this session yet.")
        return

    position_dates = pd.to_datetime(positions["date"], utc=True, errors="coerce").dropna()
    if position_dates.empty:
        st.info("Timestamped driver positions are not available for this session yet.")
        return

    latest_position = position_dates.max()
    history_key = f"circuit_locations_{session_key}"
    cursor_key = f"circuit_location_cursor_{session_key}"
    location_rows = st.session_state.get(history_key, [])
    cursor = st.session_state.get(cursor_key)
    if cursor is None or (latest_position - cursor).total_seconds() > 120:
        cursor = latest_position - timedelta(seconds=90)

    try:
        new_rows = cached_live_openf1(
            "location",
            {"session_key": session_key, "date>": cursor.isoformat()},
        )
    except OpenF1Error as exc:
        st.warning(str(exc))
        new_rows = []

    if new_rows:
        location_rows.extend(new_rows)
        new_dates = pd.to_datetime(
            pd.DataFrame(new_rows).get("date"), utc=True, errors="coerce"
        ).dropna()
        if not new_dates.empty:
            cursor = new_dates.max()
            st.session_state[cursor_key] = cursor

    speed_history_key = f"circuit_speeds_{session_key}"
    speed_cursor_key = f"circuit_speed_cursor_{session_key}"
    speed_rows = st.session_state.get(speed_history_key, [])
    speed_cursor = st.session_state.get(speed_cursor_key)
    speed_anchor = latest_data_timestamp(bundle) or latest_position
    if latest_position > speed_anchor:
        speed_anchor = latest_position
    if speed_cursor is None:
        speed_cursor = speed_anchor - timedelta(minutes=10)
    elif speed_cursor > speed_anchor or (speed_anchor - speed_cursor).total_seconds() > 120:
        speed_cursor = speed_anchor - timedelta(minutes=2)
    try:
        new_speed_rows = cached_live_openf1(
            "car_data",
            {"session_key": session_key, "date>": speed_cursor.isoformat()},
        )
    except OpenF1Error as exc:
        st.warning(f"Car speed telemetry is unavailable: {exc}")
        new_speed_rows = []
    if new_speed_rows:
        speed_rows.extend(new_speed_rows)
        speed_dates = pd.to_datetime(
            pd.DataFrame(new_speed_rows).get("date"), utc=True, errors="coerce"
        ).dropna()
        if not speed_dates.empty:
            speed_cursor = speed_dates.max()
            st.session_state[speed_cursor_key] = speed_cursor
    speed_history = pd.DataFrame(speed_rows)
    if "date" in speed_history:
        speed_history["date"] = pd.to_datetime(speed_history["date"], utc=True, errors="coerce")
        if "driver_number" in speed_history:
            speed_history = speed_history.drop_duplicates(["driver_number", "date"], keep="last")
        latest_speed = speed_history["date"].max()
        if pd.notna(latest_speed):
            speed_history = speed_history[
                speed_history["date"] >= latest_speed - timedelta(minutes=10)
            ]
            speed_rows = speed_history.to_dict("records")
            st.session_state[speed_history_key] = speed_rows

    locations = pd.DataFrame(location_rows)
    required = {"date", "driver_number", "x", "y"}
    if not required.issubset(locations.columns):
        st.info("Car location data has not been published for this session yet.")
        return

    locations["date"] = pd.to_datetime(locations["date"], utc=True, errors="coerce")
    locations["driver_number"] = pd.to_numeric(locations["driver_number"], errors="coerce")
    locations["x"] = pd.to_numeric(locations["x"], errors="coerce")
    locations["y"] = pd.to_numeric(locations["y"], errors="coerce")
    locations = locations.dropna(subset=["date", "driver_number", "x", "y"])
    if locations.empty:
        st.info("Car location data has not been published for this session yet.")
        return

    locations = locations.drop_duplicates(["driver_number", "date"], keep="last")
    newest_location = locations["date"].max()
    locations = locations[locations["date"] >= newest_location - timedelta(minutes=10)]
    st.session_state[history_key] = locations.to_dict("records")

    if timing.empty:
        st.info("Live classification is not available to label the circuit positions.")
        return

    timing = timing.copy()
    timing["_driver_key"] = pd.to_numeric(timing["No."], errors="coerce")
    driver_labels = {
        row["_driver_key"]: f"{row['Driver']} · #{int(row['_driver_key'])} · P{row['Pos']} · L{row['Lap']}"
        for _, row in timing.dropna(subset=["_driver_key"]).iterrows()
    }
    driver_colors = {
        row["_driver_key"]: row["Team colour"]
        for _, row in timing.dropna(subset=["_driver_key"]).iterrows()
    }
    driver_positions = {
        row["_driver_key"]: row["Pos"]
        for _, row in timing.dropna(subset=["_driver_key"]).iterrows()
    }
    driver_laps = {
        row["_driver_key"]: row["Lap"]
        for _, row in timing.dropna(subset=["_driver_key"]).iterrows()
    }
    driver_numbers = sorted(driver_labels, key=lambda number: pd.to_numeric(driver_positions[number], errors="coerce"))
    selected_driver = st.selectbox(
        "Follow driver",
        driver_numbers,
        index=0,
        format_func=lambda number: driver_labels[number],
        key=f"circuit_follow_{session_key}",
    )

    sampled = locations.assign(_second=locations["date"].dt.floor("1s"))
    sampled = sampled.drop_duplicates(["driver_number", "_second"], keep="last")
    latest_by_driver = locations.sort_values("date").drop_duplicates("driver_number", keep="last")
    figure = go.Figure()
    selected_points = sampled[sampled["driver_number"] == selected_driver].sort_values("date")
    for driver_number, trace in sampled.groupby("driver_number"):
        if driver_number == selected_driver:
            continue
        trace = trace.sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=trace["x"], y=trace["y"], mode="lines",
                line=dict(color="#566064", width=1.2), opacity=0.72,
                hoverinfo="skip", showlegend=False,
            )
        )
    if not selected_points.empty:
        figure.add_trace(
            go.Scatter(
                x=selected_points["x"], y=selected_points["y"], mode="lines",
                name=f"{driver_labels.get(selected_driver, 'Driver')} trace",
                line=dict(color="#b4e04b", width=3), hoverinfo="skip",
            )
        )

    latest_by_driver["_label"] = latest_by_driver["driver_number"].map(
        lambda number: driver_labels.get(number, f"#{int(number)}")
    )
    latest_by_driver["_colour"] = latest_by_driver["driver_number"].map(driver_colors).fillna("#9aa7a7")
    latest_by_driver["_size"] = latest_by_driver["driver_number"].map(
        lambda number: 15 if number == selected_driver else 10
    )
    figure.add_trace(
        go.Scatter(
            x=latest_by_driver["x"], y=latest_by_driver["y"], mode="markers+text",
            text=latest_by_driver["_label"], textposition="top center",
            textfont=dict(size=10, color="#f3f5f4"),
            marker=dict(
                size=latest_by_driver["_size"], color=latest_by_driver["_colour"],
                line=dict(color="#f3f5f4", width=1),
            ),
            customdata=[
                [driver_positions.get(number, "--"), driver_laps.get(number, "--")]
                for number in latest_by_driver["driver_number"]
            ],
            hovertemplate="%{text}<br>Position: P%{customdata[0]}<br>Lap: %{customdata[1]}<extra></extra>",
            name="Latest position",
        )
    )
    figure.update_layout(
        template="plotly_dark", paper_bgcolor="#171d20", plot_bgcolor="#171d20",
        height=660, margin=dict(l=10, r=10, t=15, b=10),
        legend=dict(orientation="h", y=1.02),
        xaxis=dict(visible=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(visible=False),
        transition=dict(duration=900, easing="cubic-in-out"),
        uirevision=f"circuit-{session_key}",
    )
    st.plotly_chart(
        figure,
        use_container_width=True,
        config={"displayModeBar": False},
        key=f"circuit_live_map_{session_key}",
    )
    st.caption(
        f"{len(latest_by_driver)} cars · Driver locations sampled once per second · "
        f"Latest coordinate {newest_location.to_pydatetime().astimezone().strftime('%H:%M:%S %Z')}"
    )
    st.markdown("#### Driver lap averages")
    lap_metrics = _build_driver_lap_metrics(bundle, speed_rows, timing)
    if lap_metrics.empty:
        st.info("Completed lap averages are not available for this session yet.")
    else:
        st.caption("Lap time averages the last 10 completed laps; lap speed averages speed telemetry for the captured laps shown.")
        st.dataframe(
            lap_metrics,
            hide_index=True,
            use_container_width=True,
            height=min(650, 42 + len(lap_metrics) * 35),
            column_config={
                "Pos": st.column_config.NumberColumn("Pos", width="small"),
                "Driver": st.column_config.TextColumn("Driver", width="medium"),
                "No.": st.column_config.NumberColumn("No.", width="small"),
                "Avg lap speed (km/h)": st.column_config.NumberColumn(
                    "Avg speed", format="%.1f km/h", help="Blank when telemetry coverage is insufficient.", width="medium"
                ),
                "Avg lap time": st.column_config.TextColumn("Avg lap time", width="medium"),
                "Laps sampled": st.column_config.TextColumn("Laps sampled", width="small"),
            },
        )


def _render_pace(bundle: dict[str, Any], timing: pd.DataFrame, session_key: int) -> None:
    laps = pd.DataFrame(bundle.get("laps", []))
    if laps.empty:
        st.info("Lap data is not available for this session yet.")
        return

    drivers = timing["Driver"].dropna().astype(str).tolist() if not timing.empty else []
    chosen = st.multiselect("Compare lap pace", drivers, default=drivers[:4], max_selections=8)
    laps["lap_duration"] = pd.to_numeric(laps.get("lap_duration"), errors="coerce")
    chart_laps = laps[laps["lap_duration"].notna() & laps["driver_number"].notna()].copy()
    driver_numbers = dict(zip(timing["Driver"], timing["No."])) if not timing.empty else {}
    chart_laps["Driver"] = chart_laps["driver_number"].map(
        {number: code for code, number in driver_numbers.items()}
    )
    chart_laps = chart_laps[chart_laps["Driver"].isin(chosen)]
    if not chart_laps.empty:
        figure = px.line(
            chart_laps,
            x="lap_number",
            y="lap_duration",
            color="Driver",
            markers=True,
            template="plotly_dark",
            labels={"lap_number": "Lap", "lap_duration": "Lap time (s)"},
        )
        figure.update_layout(paper_bgcolor="#171d20", plot_bgcolor="#171d20", height=370, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(figure, use_container_width=True)

    driver_labels = {
        f"{row['Driver']} · #{int(row['No.'])}": int(row["No."])
        for _, row in timing.iterrows()
        if pd.notna(row["No."])
    } if not timing.empty else {}
    selected_label = st.selectbox("Telemetry lap", list(driver_labels), index=0 if driver_labels else None, disabled=not driver_labels)
    if not selected_label:
        return
    driver_number = driver_labels[selected_label]
    driver_laps = laps[
        (pd.to_numeric(laps["driver_number"], errors="coerce") == driver_number)
        & laps["lap_duration"].notna()
    ].sort_values("lap_number")
    if driver_laps.empty:
        st.info("No completed lap is available for this driver.")
        return

    lap = driver_laps.iloc[-1]
    params: dict[str, Any] = {"session_key": session_key, "driver_number": driver_number}
    if lap.get("date_start") and lap.get("date_end"):
        params.update({"date>": lap["date_start"], "date<": lap["date_end"]})
    try:
        telemetry_rows = cached_openf1("car_data", params)
    except OpenF1Error as exc:
        st.warning(str(exc))
        return
    telemetry = pd.DataFrame(telemetry_rows)
    if telemetry.empty or "date" not in telemetry:
        st.info("Car telemetry has not been published for that lap.")
        return

    telemetry["date"] = pd.to_datetime(telemetry["date"], utc=True, errors="coerce")
    telemetry = telemetry.dropna(subset=["date"]).sort_values("date")
    telemetry["Elapsed (s)"] = (telemetry["date"] - telemetry["date"].iloc[0]).dt.total_seconds()
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12, subplot_titles=("Speed", "Throttle and brake"))
    if "speed" in telemetry:
        figure.add_trace(go.Scatter(x=telemetry["Elapsed (s)"], y=telemetry["speed"], name="Speed km/h", mode="lines+markers", line=dict(color="#67c8cf", width=2), marker=dict(size=3)), row=1, col=1)
    if "throttle" in telemetry:
        figure.add_trace(go.Scatter(x=telemetry["Elapsed (s)"], y=telemetry["throttle"], name="Throttle %", mode="lines+markers", line=dict(color="#b4e04b", width=1.6), marker=dict(size=3)), row=2, col=1)
    if "brake" in telemetry:
        figure.add_trace(go.Scatter(x=telemetry["Elapsed (s)"], y=telemetry["brake"], name="Brake", mode="lines+markers", line=dict(color="#e10600", width=1.6), marker=dict(size=3)), row=2, col=1)
    figure.update_layout(template="plotly_dark", paper_bgcolor="#171d20", plot_bgcolor="#171d20", height=470, margin=dict(l=10, r=10, t=40, b=10), legend=dict(orientation="h", y=1.06), hovermode="x unified")
    figure.update_xaxes(title_text="Lap elapsed (s)", row=2, col=1)
    figure.update_yaxes(title_text="km/h", row=1, col=1)
    figure.update_yaxes(title_text="%", range=[0, 100], row=2, col=1)
    st.plotly_chart(figure, use_container_width=True)


def _render_strategy(bundle: dict[str, Any], timing: pd.DataFrame) -> None:
    if timing.empty:
        st.info("Tyre stint data is not available for this session.")
        return
    st.dataframe(
        timing[["Pos", "Driver", "Team", "Tyre", "Tyre age", "Stops", "Last lap"]],
        hide_index=True,
        use_container_width=True,
    )
    compounds = timing[timing["Tyre"].astype(str) != "--"].groupby("Tyre", as_index=False).size()
    if not compounds.empty:
        fig = px.bar(compounds, x="Tyre", y="size", color="Tyre", text="size", template="plotly_dark", labels={"size": "Cars"})
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(showlegend=False, paper_bgcolor="#171d20", plot_bgcolor="#171d20", height=290, margin=dict(l=10, r=10, t=15, b=10))
        st.plotly_chart(fig, use_container_width=True)


def _render_race_control(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info("No race-control messages have been published for this session.")
        return
    frame = pd.DataFrame(rows).sort_values("date", ascending=False, na_position="last")
    columns = [column for column in ("date", "category", "flag", "scope", "message") if column in frame]
    frame = frame[columns].rename(columns={"date": "Time", "category": "Category", "flag": "Flag", "scope": "Scope", "message": "Race control message"})
    st.dataframe(frame, hide_index=True, use_container_width=True, height=500)


def main() -> None:
    _configure_openf1_credentials()
    st.sidebar.markdown("### SESSION FEED")
    refresh_choice = st.sidebar.selectbox("Auto-refresh", ["15 seconds", "30 seconds", "60 seconds", "Paused"], index=0)
    auto_refresh = {"15 seconds": 15, "30 seconds": 30, "60 seconds": 60}.get(refresh_choice)
    st.sidebar.caption("Source: OpenF1 community timing API")
    st.sidebar.button("Refresh now", on_click=st.cache_data.clear, use_container_width=True, icon=":material/refresh:")

    @st.fragment(run_every=auto_refresh)
    def live_dashboard() -> None:
        try:
            session = cached_latest_session()
        except OpenF1Error as exc:
            st.error(str(exc))
            st.caption("Check your internet connection, then use Refresh now to retry.")
            return

        session_key = int(session["session_key"])
        bundle = cached_bundle(session_key)
        timing = build_timing_table(bundle)
        update_at = latest_data_timestamp(bundle)
        status, status_color = _session_state(session, update_at)
        session_name = " ".join(
            part for part in (session.get("country_name"), session.get("circuit_short_name"), session.get("session_name")) if part
        )

        st.markdown(
            f'<div class="masthead"><div><h1>F1 LIVE TIMING</h1><p>{session_name or "Latest published session"} · {session.get("year", "")}</p></div>'
            f'<span class="live-pill" style="color:{status_color};border-color:{status_color}">● {status}</span></div>',
            unsafe_allow_html=True,
        )

        if update_at:
            age_seconds = max(0, int((datetime.now(timezone.utc) - update_at).total_seconds()))
            if age_seconds < 60:
                age = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                age = f"{age_seconds // 60}m ago"
            else:
                age = f"{age_seconds // 3600}h {(age_seconds % 3600) // 60}m ago"
            st.caption(f"Latest timing record: {update_at.astimezone().strftime('%b %d, %H:%M:%S %Z')} · {age} · Session {session_key}")
        else:
            st.caption(f"No timestamped timing records published yet · Session {session_key}")

        if bundle.get("errors"):
            messages = "; ".join(f"{name}: {message}" for name, message in bundle["errors"].items())
            st.warning(f"Some OpenF1 feeds are unavailable: {messages}")

        top = st.columns(4)
        top[0].metric("Classified", len(timing))
        completed_laps = [row.get("lap_number") for row in bundle.get("laps", []) if row.get("lap_number") is not None]
        top[1].metric("Lap", max(completed_laps) if completed_laps else "--")
        fastest = pd.to_numeric(pd.DataFrame(bundle.get("laps", [])).get("lap_duration", pd.Series(dtype=float)), errors="coerce")
        top[2].metric("Fastest published lap", f"{fastest.min():.3f}s" if fastest.notna().any() else "--")
        top[3].metric("API feeds", "Partial" if bundle.get("errors") else "Connected")

        view = st.radio(
            "Dashboard view",
            ["Timing", "Circuit", "Pace & telemetry", "Top 15", "Tyres & weather", "Race control"],
            horizontal=True,
            label_visibility="collapsed",
            key="dashboard_view",
        )
        if view == "Timing":
            _render_timing(bundle, timing)
        elif view == "Circuit":
            _render_circuit(bundle, timing, session)
        elif view == "Pace & telemetry":
            _render_pace(bundle, timing, session_key)
        elif view == "Top 15":
            ranking, reference, available_laps = build_pace_correlation_table(bundle, timing)
            if ranking.empty:
                st.info("Lap-by-lap correlation is not available for this session yet.")
            else:
                st.markdown("### TOP 15 · LAP-PACE CORRELATION")
                st.caption(
                    f"Pearson correlation against {reference}, computed over all published completed laps through Lap {available_laps}. "
                    "Pit-out laps are excluded; at least 8 shared laps are required. Negative average delta means quicker than the reference."
                )
                st.dataframe(
                    ranking[["Rank", "Pos", "Driver", "Team", "Correlation", "Avg lap delta (s)", "Laps compared", "Laps completed"]],
                    hide_index=True,
                    use_container_width=True,
                    height=min(650, 42 + len(ranking) * 35),
                    column_config={
                        "Rank": st.column_config.NumberColumn("Rank", width="small"),
                        "Pos": st.column_config.NumberColumn("Finish", width="small"),
                        "Driver": st.column_config.TextColumn("Driver", width="medium"),
                        "Team": st.column_config.TextColumn("Team", width="medium"),
                        "Correlation": st.column_config.NumberColumn("Pace corr. (r)", format="%.3f", width="small"),
                        "Avg lap delta (s)": st.column_config.NumberColumn("Avg vs leader", format="%+.3f s", width="small"),
                        "Laps compared": st.column_config.NumberColumn("Matched laps", width="small"),
                        "Laps completed": st.column_config.NumberColumn("Completed", width="small"),
                    },
                )
        elif view == "Tyres & weather":
            _render_strategy(bundle, timing)
            try:
                _show_weather(cached_openf1("weather", {"session_key": session_key}))
            except OpenF1Error as exc:
                st.warning(str(exc))
        else:
            try:
                _render_race_control(cached_openf1("race_control", {"session_key": session_key}))
            except OpenF1Error as exc:
                st.warning(str(exc))

        st.markdown(
            '<p class="source-note">Timing, lap, weather, and position data are fetched from OpenF1. Between sessions, the dashboard shows the latest published session and its actual data timestamp; it does not simulate live values.</p>',
            unsafe_allow_html=True,
        )

    live_dashboard()


if __name__ == "__main__":
    main()