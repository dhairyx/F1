"""OpenF1 data access and shaping helpers for the dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time
from typing import Any

import pandas as pd
import requests

API_BASE = "https://api.openf1.org/v1"
API_TIMEOUT_SECONDS = 15
SESSION_ENDPOINTS = (
    "position",
    "drivers",
    "intervals",
    "laps",
    "stints",
)
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_TOKEN_LOCK = threading.Lock()
_ACCESS_TOKEN: str | None = None
_TOKEN_EXPIRES_AT = 0.0


class OpenF1Error(RuntimeError):
    """Raised when OpenF1 cannot provide a usable response."""


def _openf1_access_token() -> str | None:
    """Exchange configured account credentials for a cached live-data token."""
    global _ACCESS_TOKEN, _TOKEN_EXPIRES_AT
    username = os.getenv("OPENF1_USERNAME")
    password = os.getenv("OPENF1_PASSWORD")
    if not username or not password:
        return None

    with _TOKEN_LOCK:
        if _ACCESS_TOKEN and time.monotonic() < _TOKEN_EXPIRES_AT:
            return _ACCESS_TOKEN
        try:
            response = requests.post(
                f"{API_BASE.rsplit('/v1', 1)[0]}/token",
                data={"username": username, "password": password},
                timeout=API_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            token_data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OpenF1Error("OpenF1 authentication failed. Check the configured account credentials.") from exc

        token = token_data.get("access_token") if isinstance(token_data, dict) else None
        if not token:
            raise OpenF1Error("OpenF1 authentication returned no access token.")
        try:
            expires_in = int(token_data.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        _ACCESS_TOKEN = str(token)
        _TOKEN_EXPIRES_AT = time.monotonic() + max(60, expires_in - 60)
        return _ACCESS_TOKEN


def fetch_openf1(endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fetch one OpenF1 collection, raising a readable error on bad responses."""
    global _LAST_REQUEST_AT
    response = None
    headers = {"User-Agent": "F1-Live-Telemetry-Dashboard/1.0"}
    access_token = _openf1_access_token()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    for attempt in range(2):
        with _REQUEST_LOCK:
            wait = 0.4 - (time.monotonic() - _LAST_REQUEST_AT)
            if wait > 0:
                time.sleep(wait)
            _LAST_REQUEST_AT = time.monotonic()
        try:
            response = requests.get(
                f"{API_BASE}/{endpoint.lstrip('/')}",
                params=params or {},
                headers=headers,
                timeout=API_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise OpenF1Error(f"Could not reach OpenF1: {exc}") from exc
        if response.status_code != 429:
            break
        if attempt == 0:
            try:
                retry_after = float(response.headers.get("Retry-After", 1))
            except ValueError:
                retry_after = 1
            time.sleep(min(max(retry_after, 1), 2))

    if response is None or response.status_code == 429:
        raise OpenF1Error("OpenF1 rate limit reached. The dashboard will retry shortly.")
    try:
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise OpenF1Error(f"OpenF1 request failed: {exc}") from exc
    except ValueError as exc:
        raise OpenF1Error("OpenF1 returned an unreadable response.") from exc

    if not isinstance(payload, list):
        raise OpenF1Error(f"OpenF1 returned an unexpected response for {endpoint}.")
    return payload


def fetch_latest_session() -> dict[str, Any]:
    sessions = fetch_openf1("sessions", {"session_key": "latest"})
    if not sessions:
        raise OpenF1Error("OpenF1 has not published a session yet.")
    return sessions[-1]


def fetch_session_bundle(session_key: int) -> dict[str, Any]:
    """Fetch core feeds sequentially to respect OpenF1's public rate limit."""
    result: dict[str, Any] = {endpoint: [] for endpoint in SESSION_ENDPOINTS}
    errors: dict[str, str] = {}
    for endpoint in SESSION_ENDPOINTS:
        try:
            result[endpoint] = fetch_openf1(endpoint, {"session_key": session_key})
        except OpenF1Error as exc:
            errors[endpoint] = str(exc)

    result["errors"] = errors
    return result


def _latest_rows(rows: list[dict[str, Any]], order_by: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty or "driver_number" not in frame:
        return pd.DataFrame()
    sort_columns = [column for column in order_by if column in frame.columns]
    if sort_columns:
        frame = frame.sort_values(sort_columns, na_position="first")
    return frame.drop_duplicates("driver_number", keep="last").set_index("driver_number")


def format_lap_time(seconds: Any) -> str:
    if pd.isna(seconds):
        return "--"
    value = float(seconds)
    return f"{int(value // 60)}:{value % 60:06.3f}"


def _format_gap(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    try:
        return f"{float(value):+.3f}s"
    except (TypeError, ValueError):
        return str(value)


def build_timing_table(bundle: dict[str, Any]) -> pd.DataFrame:
    """Combine the newest published records into one row per classified driver."""
    positions = _latest_rows(bundle.get("position", []), ["date"])
    drivers = _latest_rows(bundle.get("drivers", []), ["meeting_key"])
    intervals = _latest_rows(bundle.get("intervals", []), ["date"])
    stint_rows = pd.DataFrame(bundle.get("stints", []))
    stints = _latest_rows(bundle.get("stints", []), ["stint_number"])
    laps = pd.DataFrame(bundle.get("laps", []))
    pits = pd.DataFrame(bundle.get("pit", []))
    lap_deltas: dict[Any, str] = {}

    if positions.empty:
        return pd.DataFrame()

    if not laps.empty and "driver_number" in laps:
        if "lap_duration" in laps:
            laps["lap_duration"] = pd.to_numeric(laps["lap_duration"], errors="coerce")
        if "lap_number" in laps:
            laps["lap_number"] = pd.to_numeric(laps["lap_number"], errors="coerce")
        if {"lap_number", "lap_duration"}.issubset(laps.columns):
            lap_history = laps.dropna(subset=["lap_number", "lap_duration"]).copy()
            for driver_number, driver_laps in lap_history.groupby("driver_number"):
                driver_laps = driver_laps.sort_values(["lap_number", "date_start"] if "date_start" in driver_laps else ["lap_number"])
                latest_lap = driver_laps.iloc[-1]
                lap_delta = "--"
                current_is_pit_out = latest_lap.get("is_pit_out_lap", False)
                current_is_pit_out = pd.notna(current_is_pit_out) and bool(current_is_pit_out)
                previous_laps = driver_laps[
                    driver_laps["lap_number"] == latest_lap["lap_number"] - 1
                ]
                if not current_is_pit_out and not previous_laps.empty:
                    previous_lap = previous_laps.iloc[-1]
                    previous_is_pit_out = previous_lap.get("is_pit_out_lap", False)
                    previous_is_pit_out = pd.notna(previous_is_pit_out) and bool(previous_is_pit_out)
                    if not previous_is_pit_out:
                        lap_delta = _format_gap(latest_lap["lap_duration"] - previous_lap["lap_duration"])
                lap_deltas[driver_number] = lap_delta
        lap_order = [column for column in ("lap_number", "date_start") if column in laps.columns]
        if lap_order:
            laps = laps.sort_values(lap_order, na_position="first")
        if "lap_duration" in laps:
            laps = laps.dropna(subset=["lap_duration"])
        laps = laps.drop_duplicates("driver_number", keep="last").set_index("driver_number")
    else:
        laps = pd.DataFrame()
    current_lap = (
        pd.to_numeric(laps.get("lap_number"), errors="coerce").max()
        if not laps.empty and "lap_number" in laps
        else pd.NA
    )

    if not pits.empty and "driver_number" in pits.columns:
        pit_counts = pits.groupby("driver_number").size()
    elif not stint_rows.empty and {"driver_number", "stint_number"}.issubset(stint_rows.columns):
        pit_counts = stint_rows.groupby("driver_number")["stint_number"].nunique().sub(1).clip(lower=0)
    else:
        pit_counts = pd.Series(dtype="int64")

    rows: list[dict[str, Any]] = []
    for driver_number, position in positions.iterrows():
        driver = drivers.loc[driver_number] if driver_number in drivers.index else pd.Series(dtype=object)
        interval = intervals.loc[driver_number] if driver_number in intervals.index else pd.Series(dtype=object)
        stint = stints.loc[driver_number] if driver_number in stints.index else pd.Series(dtype=object)
        lap = laps.loc[driver_number] if driver_number in laps.index else pd.Series(dtype=object)
        lap_number = lap.get("lap_number")
        tyre_age = stint.get("tyre_age_at_start")
        stint_start = stint.get("lap_start")
        age_lap = lap_number if pd.notna(lap_number) else current_lap
        if pd.notna(tyre_age) and pd.notna(stint_start) and pd.notna(age_lap):
            tyre_age = int(tyre_age) + max(0, int(age_lap) - int(stint_start))
        team_colour = driver.get("team_colour")
        team_colour = "777777" if pd.isna(team_colour) or not str(team_colour).strip() else str(team_colour).lstrip("#")

        rows.append(
            {
                "Pos": position.get("position", "--"),
                "No.": driver_number,
                "Driver": driver.get("name_acronym", "---"),
                "Name": driver.get("full_name", "Unknown"),
                "Team": driver.get("team_name", "Unknown"),
                "Team colour": f"#{team_colour}",
                "Gap": _format_gap(interval.get("gap_to_leader")),
                "Interval": _format_gap(interval.get("interval")),
                "Lap": lap_number if pd.notna(lap_number) else "--",
                "Last lap": format_lap_time(lap.get("lap_duration")),
                "Lap Δ": lap_deltas.get(driver_number, "--"),
                "Tyre": stint.get("compound", "--"),
                "Tyre age": int(tyre_age) if pd.notna(tyre_age) else "--",
                "Stops": int(pit_counts.get(driver_number, 0)),
                "Fastest": bool(lap.get("is_personal_best", False)),
            }
        )

    return pd.DataFrame(rows).sort_values(
        "Pos", key=lambda column: pd.to_numeric(column, errors="coerce"), na_position="last"
    )


def latest_data_timestamp(bundle: dict[str, Any]) -> datetime | None:
    timestamps: list[datetime] = []
    for endpoint in ("position", "intervals", "laps", "weather", "location", "race_control"):
        for row in bundle.get(endpoint, []):
            raw = row.get("date") or row.get("date_start")
            if not raw:
                continue
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                timestamps.append(value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value)
            except ValueError:
                continue
    return max(timestamps) if timestamps else None


def build_pace_correlation_table(
    bundle: dict[str, Any],
    timing: pd.DataFrame,
    limit: int = 15,
    minimum_laps: int = 8,
) -> tuple[pd.DataFrame, str, int]:
    """Rank drivers by lap-time correlation with the current race leader."""
    laps = pd.DataFrame(bundle.get("laps", []))
    required = {"driver_number", "lap_number", "lap_duration"}
    if laps.empty or not required.issubset(laps.columns):
        return pd.DataFrame(), "Unavailable", 0

    laps["driver_number"] = pd.to_numeric(laps["driver_number"], errors="coerce")
    laps["lap_number"] = pd.to_numeric(laps["lap_number"], errors="coerce")
    laps["lap_duration"] = pd.to_numeric(laps["lap_duration"], errors="coerce")
    laps = laps.dropna(subset=["driver_number", "lap_number", "lap_duration"])
    if "is_pit_out_lap" in laps:
        laps = laps[~laps["is_pit_out_lap"].fillna(False).astype(bool)]
    if laps.empty:
        return pd.DataFrame(), "Unavailable", 0

    ordering = ["driver_number", "lap_number"]
    if "date_start" in laps:
        ordering.append("date_start")
    laps = laps.sort_values(ordering).drop_duplicates(["driver_number", "lap_number"], keep="last")
    lap_matrix = laps.pivot(index="lap_number", columns="driver_number", values="lap_duration")
    available_laps = int(laps["lap_number"].max())

    if timing.empty:
        reference_number = int(lap_matrix.notna().sum().idxmax())
        reference_code = str(reference_number)
        reference_team = ""
    else:
        leader = timing.iloc[0]
        reference_number = int(leader["No."])
        reference_code = str(leader["Driver"])
        reference_team = str(leader["Team"])

    if reference_number not in lap_matrix:
        return pd.DataFrame(), "Unavailable", available_laps

    reference_name = f"{reference_code} · {reference_team}".strip(" ·")
    driver_info = timing.set_index("No.").to_dict("index") if not timing.empty else {}
    ranked_rows: list[dict[str, Any]] = []
    for driver_number in lap_matrix.columns:
        paired = pd.concat(
            [lap_matrix[reference_number].rename("leader"), lap_matrix[driver_number].rename("driver")],
            axis=1,
        ).dropna()
        correlation = paired["leader"].corr(paired["driver"]) if len(paired) >= minimum_laps else float("nan")
        info = driver_info.get(driver_number, {})
        ranked_rows.append(
            {
                "Pos": info.get("Pos", "--"),
                "Driver": info.get("Name") or info.get("Driver", str(int(driver_number))),
                "Team": info.get("Team", "Unknown"),
                "Correlation": correlation,
                "Avg lap delta (s)": (paired["driver"] - paired["leader"]).mean() if not paired.empty else float("nan"),
                "Laps compared": len(paired),
                "Laps completed": int(lap_matrix[driver_number].count()),
            }
        )

    ranking = pd.DataFrame(ranked_rows).sort_values(
        ["Correlation", "Laps compared", "Avg lap delta (s)"],
        ascending=[False, False, True],
        na_position="last",
    ).head(limit).reset_index(drop=True)
    ranking.insert(0, "Rank", range(1, len(ranking) + 1))
    return ranking, reference_name, available_laps