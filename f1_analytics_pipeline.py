#!/usr/bin/env python3
"""
F1 Telemetry & Head-to-Head Analytics Pipeline
================================================
Designed for high-performance telemetry analysis, Python data science workflows,
and automated race engineering data pipelines.

Features:
- Head-to-Head comparison engine (Points, Poles, Podiums, Wins, Quali/Race H2H)
- Monza circuit (5,793m) sector speed profile generator (18 micro-zones)
- Time gain/loss delta matrix calculation
- Exports formatted CSV and JSON telemetry artifacts
"""

import sys
import json
import math
from typing import Dict, List, Any

from f1_data import OpenF1Error, build_timing_table, fetch_latest_session, fetch_session_bundle, latest_data_timestamp

# ==============================================================================
# 1. 2024 FIA FORMULA 1 DRIVER DATABASE (ROUND 16 MONZA STANDINGS)
# ==============================================================================
F1_DRIVERS = {
    "VER": {"name": "Max Verstappen", "team": "Red Bull Racing", "number": 1, "points": 303, "wins": 7, "poles": 8, "podiums": 11, "fastest_laps": 2, "rank": 1, "team_color": "#3671C6"},
    "NOR": {"name": "Lando Norris", "team": "McLaren", "number": 4, "points": 241, "wins": 2, "poles": 4, "podiums": 10, "fastest_laps": 3, "rank": 2, "team_color": "#FF8000"},
    "LEC": {"name": "Charles Leclerc", "team": "Ferrari", "number": 16, "points": 217, "wins": 2, "poles": 2, "podiums": 8, "fastest_laps": 2, "rank": 3, "team_color": "#E80020"},
    "PIA": {"name": "Oscar Piastri", "team": "McLaren", "number": 81, "points": 197, "wins": 1, "poles": 1, "podiums": 6, "fastest_laps": 1, "rank": 4, "team_color": "#FF8000"},
    "SAI": {"name": "Carlos Sainz", "team": "Ferrari", "number": 55, "points": 184, "wins": 1, "poles": 1, "podiums": 5, "fastest_laps": 1, "rank": 5, "team_color": "#E80020"},
    "HAM": {"name": "Lewis Hamilton", "team": "Mercedes", "number": 44, "points": 164, "wins": 2, "poles": 0, "podiums": 4, "fastest_laps": 2, "rank": 6, "team_color": "#27F4D2"},
    "RUS": {"name": "George Russell", "team": "Mercedes", "number": 63, "points": 128, "wins": 1, "poles": 2, "podiums": 3, "fastest_laps": 2, "rank": 7, "team_color": "#27F4D2"},
    "PER": {"name": "Sergio Perez", "team": "Red Bull Racing", "number": 11, "points": 143, "wins": 0, "poles": 0, "podiums": 4, "fastest_laps": 1, "rank": 8, "team_color": "#3671C6"},
    "ALO": {"name": "Fernando Alonso", "team": "Aston Martin", "number": 14, "points": 50, "wins": 0, "poles": 0, "podiums": 0, "fastest_laps": 2, "rank": 9, "team_color": "#229971"},
    "STR": {"name": "Lance Stroll", "team": "Aston Martin", "number": 18, "points": 24, "wins": 0, "poles": 0, "podiums": 0, "fastest_laps": 0, "rank": 10, "team_color": "#229971"},
    "HUL": {"name": "Nico Hulkenberg", "team": "Haas", "number": 27, "points": 22, "wins": 0, "poles": 0, "podiums": 0, "fastest_laps": 0, "rank": 11, "team_color": "#B6BABD"},
    "TSU": {"name": "Yuki Tsunoda", "team": "RB", "number": 22, "points": 22, "wins": 0, "poles": 0, "podiums": 0, "fastest_laps": 0, "rank": 12, "team_color": "#6692FF"},
}

# Monza 18 track zones spanning Sector 1, 2, and 3
MONZA_TRACK_ZONES = [
    # Sector 1
    {"id": "Z1", "sector": "S1", "name": "Pit Straight DRS", "start_m": 0, "end_m": 450, "type": "straight", "base_speed": 348.0},
    {"id": "Z2", "sector": "S1", "name": "Rettifilo Braking", "start_m": 450, "end_m": 750, "type": "braking", "base_speed": 185.0},
    {"id": "Z3", "sector": "S1", "name": "Rettifilo T1-T2 Chicane", "start_m": 750, "end_m": 1050, "type": "chicane", "base_speed": 78.5},
    {"id": "Z4", "sector": "S1", "name": "Rettifilo Exit Traction", "start_m": 1050, "end_m": 1300, "type": "traction", "base_speed": 220.0},
    {"id": "Z5", "sector": "S1", "name": "Curva Grande (Biassono)", "start_m": 1300, "end_m": 1550, "type": "corner", "base_speed": 305.0},
    # Sector 2
    {"id": "Z6", "sector": "S2", "name": "Roggia Approach Straight", "start_m": 1550, "end_m": 1850, "type": "straight", "base_speed": 332.0},
    {"id": "Z7", "sector": "S2", "name": "Variante della Roggia T4-T5", "start_m": 1850, "end_m": 2150, "type": "chicane", "base_speed": 118.0},
    {"id": "Z8", "sector": "S2", "name": "Roggia Exit to Lesmo", "start_m": 2150, "end_m": 2500, "type": "traction", "base_speed": 245.0},
    {"id": "Z9", "sector": "S2", "name": "Curva di Lesmo 1 T6", "start_m": 2500, "end_m": 2800, "type": "corner", "base_speed": 210.0},
    {"id": "Z10", "sector": "S2", "name": "Lesmo 1-2 Short Chute", "start_m": 2800, "end_m": 3050, "type": "straight", "base_speed": 268.0},
    {"id": "Z11", "sector": "S2", "name": "Curva di Lesmo 2 T7", "start_m": 3050, "end_m": 3350, "type": "corner", "base_speed": 215.0},
    {"id": "Z12", "sector": "S2", "name": "Serraglio High-Speed Straight", "start_m": 3350, "end_m": 3850, "type": "straight", "base_speed": 338.0},
    # Sector 3
    {"id": "Z13", "sector": "S3", "name": "Ascari Braking Zone T8", "start_m": 3850, "end_m": 4200, "type": "braking", "base_speed": 195.0},
    {"id": "Z14", "sector": "S3", "name": "Variante Ascari T9-T10", "start_m": 4200, "end_m": 4600, "type": "chicane", "base_speed": 228.0},
    {"id": "Z15", "sector": "S3", "name": "Back Straight DRS Run", "start_m": 4600, "end_m": 5100, "type": "straight", "base_speed": 344.0},
    {"id": "Z16", "sector": "S3", "name": "Parabolica Entry & Braking", "start_m": 5100, "end_m": 5400, "type": "braking", "base_speed": 240.0},
    {"id": "Z17", "sector": "S3", "name": "Curva Parabolica Mid-Apex", "start_m": 5400, "end_m": 5650, "type": "corner", "base_speed": 212.0},
    {"id": "Z18", "sector": "S3", "name": "Parabolica Exit & Finish Line", "start_m": 5650, "end_m": 5793, "type": "traction", "base_speed": 295.0},
]


# ==============================================================================
# 2. HEAD-TO-HEAD COMPARISON ENGINE
# ==============================================================================
def get_driver_head_to_head(code_a: str, code_b: str) -> Dict[str, Any]:
    stats_a = F1_DRIVERS.get(code_a, F1_DRIVERS["NOR"])
    stats_b = F1_DRIVERS.get(code_b, F1_DRIVERS["VER"])

    # Canonical 2024 qualifying and race head-to-head records
    h2h_records = {
        ("NOR", "VER"): {"quali": (6, 10), "race": (7, 9)},
        ("VER", "NOR"): {"quali": (10, 6), "race": (9, 7)},
        ("NOR", "PIA"): {"quali": (11, 5), "race": (12, 4)},
        ("PIA", "NOR"): {"quali": (5, 11), "race": (4, 12)},
        ("HAM", "RUS"): {"quali": (5, 11), "race": (10, 6)},
        ("RUS", "HAM"): {"quali": (11, 5), "race": (6, 10)},
        ("LEC", "SAI"): {"quali": (10, 6), "race": (9, 7)},
        ("SAI", "LEC"): {"quali": (6, 10), "race": (7, 9)},
    }

    record = h2h_records.get((code_a, code_b), {"quali": (9, 7), "race": (8, 8)})

    pts_delta = stats_a["points"] - stats_b["points"]
    wins_delta = stats_a["wins"] - stats_b["wins"]
    poles_delta = stats_a["poles"] - stats_b["poles"]
    podiums_delta = stats_a["podiums"] - stats_b["podiums"]

    return {
        "driver_a": {**stats_a, "code": code_a},
        "driver_b": {**stats_b, "code": code_b},
        "quali_h2h": {"a": record["quali"][0], "b": record["quali"][1]},
        "race_h2h": {"a": record["race"][0], "b": record["race"][1]},
        "points_delta": pts_delta,
        "wins_delta": wins_delta,
        "poles_delta": poles_delta,
        "podiums_delta": podiums_delta,
        "overall_leader": code_a if pts_delta >= 0 else code_b,
    }


# ==============================================================================
# 3. SECTOR SPEED PROFILES & DELTA HEATMAP ENGINE
# ==============================================================================
def generate_sector_speed_heatmap(code_a: str, code_b: str) -> List[Dict[str, Any]]:
    # Specific car aero offsets
    is_ver = code_a == "VER" or code_b == "VER"
    is_nor = code_a == "NOR" or code_b == "NOR"

    matrix = []
    total_delta_time = 0.0

    for idx, z in enumerate(MONZA_TRACK_ZONES):
        base = z["base_speed"]
        dist = z["end_m"] - z["start_m"]

        # Synthetic speed profiles reflecting car package characteristics
        # Red Bull: higher top speed on straights (+3-5 km/h)
        # McLaren: higher minimum speed and traction out of chicanes (+2-6 km/h)
        if z["type"] == "straight":
            speed_a = base + (3.8 if code_a == "VER" else 1.2)
            speed_b = base + (3.8 if code_b == "VER" else 1.2)
        elif z["type"] in ("chicane", "traction"):
            speed_a = base + (4.5 if code_a == "NOR" else 1.5)
            speed_b = base + (4.5 if code_b == "NOR" else 1.5)
        else:
            speed_a = base + math.sin(idx * 0.7) * 2.5
            speed_b = base + math.cos(idx * 0.7) * 2.0

        # Physical travel time: t = dist / (speed_kmh / 3.6)
        time_a = dist / (speed_a / 3.6)
        time_b = dist / (speed_b / 3.6)
        time_delta = time_a - time_b  # < 0 means Driver A is faster
        total_delta_time += time_delta

        speed_delta = speed_a - speed_b
        advantage = code_a if speed_delta > 0.3 else code_b if speed_delta < -0.3 else "EQUAL"

        matrix.append({
            "zone_id": z["id"],
            "sector": z["sector"],
            "zone_name": z["name"],
            "distance_range": f"{z['start_m']}m - {z['end_m']}m",
            f"speed_{code_a}_kmh": round(speed_a, 1),
            f"speed_{code_b}_kmh": round(speed_b, 1),
            "speed_delta_kmh": round(speed_delta, 1),
            "time_delta_sec": round(time_delta, 3),
            "time_impact_ms": round(time_delta * 1000),
            "advantage": advantage,
        })

    return matrix


# ==============================================================================
# 4. EXPORT & REPORT GENERATION
# ==============================================================================
def export_analysis(driver_a="NOR", driver_b="VER"):
    print(f"\n==================================================================")
    print(f"  F1 TELEMETRY & HEAD-TO-HEAD ANALYTICS REPORT: {driver_a} vs {driver_b}")
    print(f"==================================================================\n")

    # 1. Compute H2H
    h2h = get_driver_head_to_head(driver_a, driver_b)
    da = h2h["driver_a"]
    db = h2h["driver_b"]

    print("--- DRIVER HEAD-TO-HEAD SUMMARY PANEL ---")
    print(f"  {da['name']} ({da['team']}) vs {db['name']} ({db['team']})")
    print(f"  Points:     {da['points']} pts  vs  {db['points']} pts  (Delta: {h2h['points_delta']} pts)")
    print(f"  Wins:       {da['wins']} wins  vs  {db['wins']} wins  (Delta: {h2h['wins_delta']} wins)")
    print(f"  Poles:      {da['poles']} poles vs  {db['poles']} poles (Delta: {h2h['poles_delta']} poles)")
    print(f"  Podiums:    {da['podiums']} pods  vs  {db['podiums']} pods  (Delta: {h2h['podiums_delta']} pods)")
    print(f"  Quali H2H:  {h2h['quali_h2h']['a']} - {h2h['quali_h2h']['b']}")
    print(f"  Race H2H:   {h2h['race_h2h']['a']} - {h2h['race_h2h']['b']}")
    print(f"  WDC Rank:   P{da['rank']} vs P{db['rank']}\n")

    # 2. Compute Sector Heatmap Matrix
    matrix = generate_sector_speed_heatmap(driver_a, driver_b)

    print("--- MONZA TRACK SECTOR SPEED PROFILES HEATMAP (SAMPLE) ---")
    print(f"{'ZONE':<8} {'SECTOR':<8} {'NAME':<32} {driver_a+' KM/H':<10} {driver_b+' KM/H':<10} {'DELTA':<8} {'ADVANTAGE'}")
    print("-" * 88)
    for row in matrix:
        print(f"{row['zone_id']:<8} {row['sector']:<8} {row['zone_name']:<32} {row[f'speed_{driver_a}_kmh']:<10.1f} {row[f'speed_{driver_b}_kmh']:<10.1f} {row['speed_delta_kmh']:<+8.1f} {row['advantage']}")

    # 3. Export to CSV file
    csv_filename = f"f1_sector_speed_heatmap_{driver_a}_{driver_b}.csv"
    with open(csv_filename, "w", encoding="utf-8") as f:
        headers = ["zone_id", "sector", "zone_name", "distance_range", f"speed_{driver_a}_kmh", f"speed_{driver_b}_kmh", "speed_delta_kmh", "time_delta_sec", "advantage"]
        f.write(",".join(headers) + "\n")
        for row in matrix:
            line = [str(row[h]) for h in headers]
            f.write(",".join(line) + "\n")

    # 4. Export JSON summary
    json_filename = f"f1_h2h_report_{driver_a}_{driver_b}.json"
    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump({"head_to_head": h2h, "sector_speed_matrix": matrix}, f, indent=2)

    print(f"\n[OK] Artifacts generated:")
    print(f"  -> {csv_filename}")
    print(f"  -> {json_filename}")
    print("=" * 66 + "\n")


def export_latest_session() -> None:
    session = fetch_latest_session()
    bundle = fetch_session_bundle(int(session["session_key"]))
    timing = build_timing_table(bundle)
    if timing.empty:
        raise RuntimeError("No timing records are available for the latest session.")

    timing.to_csv("f1_live_timing.csv", index=False)
    timestamp = latest_data_timestamp(bundle)
    report = {
        "session": session,
        "latest_data_timestamp": timestamp.isoformat() if timestamp else None,
        "timing": timing.to_dict(orient="records"),
        "unavailable_feeds": bundle.get("errors", {}),
    }
    with open("f1_live_session.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, default=str)

    print(f"Exported {len(timing)} drivers from session {session['session_key']}.")
    print("Created f1_live_timing.csv and f1_live_session.json")


if __name__ == "__main__":
    try:
        export_latest_session()
    except (OpenF1Error, RuntimeError) as exc:
        print(f"Could not export the latest session: {exc}", file=sys.stderr)
        sys.exit(1)
