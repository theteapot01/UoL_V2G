"""
generate_profiles.py

Generates charging profile CSVs using PyChargeModel (NREL).
Run this script once from inside the cloned PyChargeModel repo directory,
or make sure ElectricVehicles.py and evse_class.py are on your Python path.

Output CSVs are compatible with the ChargingProfile loader (charging_profile.py).

Usage:
    python generate_profiles.py

Requirements:
    pip install numpy
"""

import os
import csv
import numpy as np

from PyChargeModel.ElectricVehicles import ElectricVehicles
from PyChargeModel.evse_class import EVSE_class


# ── Paths ─────────────────────────────────────────────────────────────────────

# Directory containing the evtype/ subfolder with parameter CSVs.
# Adjust this to wherever your parameter files live.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARAMS_DIR = os.path.join(BASE_DIR, "evtype")


# ── Profile definitions ───────────────────────────────────────────────────────
# All use a 20kW charger — realistic for V2G-capable AC Type 2 installations.
# C-rate = 20kW / battery capacity. Each profile has its own parameter file
# so PyChargeModel uses the correct C-rate per vehicle.

PROFILES = [
    {
        "output_filename": "profiles/lfp_50kwh.csv",
        "batterycapacity_kWh": 82.5,
        "Prated_kW": 20.0,
        "param_file": "lfp_parameters.csv",   # ev_crate = 0.23 (20/82.5)
        "initial_soc": 0.10,
        "target_soc": 1.0,
        "session_duration_s": 5 * 60 * 60,    # 4 hours
    },
    {
        "output_filename": "profiles/nmc_75kwh.csv",
        "batterycapacity_kWh": 75.0,
        "Prated_kW": 20.0,
        "param_file": "nmc_parameters.csv",   # ev_crate = 0.267 (20/75)
        "initial_soc": 0.10,
        "target_soc": 1.0,
        "session_duration_s": 5 * 60 * 60,    # 5 hours
    },
    {
        "output_filename": "profiles/nca_100kwh.csv",
        "batterycapacity_kWh": 77.0,
        "Prated_kW": 20.0,
        "param_file": "nca_parameters.csv",   # ev_crate = 0.25 (20/77)
        "initial_soc": 0.10,
        "target_soc": 1.0,
        "session_duration_s": 6 * 60 * 60,    # 6 hours
    },
]

# Simulation timestep in seconds.
# 60s is fine for protocol purposes; use 10s for smoother curves.
DT = 60

# Output directory (relative to where you run this script)
OUTPUT_DIR = "profiles"


# ── Phase detection ───────────────────────────────────────────────────────────

def detect_phase(soc: float, power_kw: float, prev_power_kw: float, prated_kw: float) -> str:
    """
    Infer the charging phase from current state.
    - ramp : power is still climbing toward rated capacity
    - CC   : power is at or near rated capacity
    - CV   : power is tapering off (SoC high, power dropping)
    - done : charging complete
    """
    if power_kw <= 0.01:
        return "done"

    at_rated = power_kw >= 0.95 * prated_kw

    if soc < 0.15 and not at_rated:
        return "ramp"
    elif at_rated:
        return "CC"
    elif power_kw < prev_power_kw and soc > 0.75:
        return "CV"
    else:
        return "CC"


# ── Generator ─────────────────────────────────────────────────────────────────

def generate_profile(config: dict) -> list[dict]:
    """
    Run a single charging session simulation and return rows of logged data.
    """
    capacity   = config["batterycapacity_kWh"]
    prated     = config["Prated_kW"]
    init_soc   = config["initial_soc"]
    target_soc = config["target_soc"]
    duration_s = config["session_duration_s"]

    # Resolve the parameter file — PyChargeModel expects the folder
    # containing evtype/, and uses vehicle_type to find {vehicle_type}_parameters.csv
    param_file   = config["param_file"]
    vehicle_type = param_file.replace("_parameters.csv", "")  # e.g. "lfp"

    # Sanity check — catch wrong paths before PyChargeModel silently falls back
    full_param_path = os.path.join(PARAMS_DIR, param_file)
    if not os.path.exists(full_param_path):
        raise FileNotFoundError(
            f"Parameter file not found: {full_param_path}\n"
            f"Make sure your evtype/ folder is at: {PARAMS_DIR}"
        )

    ev = ElectricVehicles(
        arrival_time=0,
        departure_time=duration_s,
        vehicle_type=vehicle_type,
        initial_soc=init_soc,
        target_soc=target_soc,
        batterycapacity_kWh=capacity,
        input_path=BASE_DIR,   # PyChargeModel looks for BASE_DIR/evtype/{vehicle_type}_parameters.csv
    )

    evse = EVSE_class(
        evse_id=1,
        efficiency=0.99,
        Prated_kW=prated,
    )

    ev.assign_evse(evse.evse_id)

    rows = []
    prev_power_kw = 0.0

    for t in np.arange(0, duration_s, DT):
        ev.chargevehicle(t, dt=DT)

        state    = ev.getvehiclestate()
        soc      = state["soc"]                    # fraction 0-1
        power_kw = state["packpower"] / 1000.0     # W → kW
        complete = state["chargecompletesignal"]

        phase = detect_phase(soc, power_kw, prev_power_kw, prated)

        rows.append({
            "time_min":    round(t / 60.0, 1),
            "soc_percent": round(soc * 100.0, 2),
            "power_kw":    round(max(power_kw, 0.0), 3),
            "phase":       phase,
        })

        prev_power_kw = power_kw

        if complete:
            rows.append({
                "time_min":    round((t + DT) / 60.0, 1),
                "soc_percent": round(soc * 100.0, 2),
                "power_kw":    0.0,
                "phase":       "done",
            })
            break

    return rows


def write_csv(rows: list[dict], output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = ["time_min", "soc_percent", "power_kw", "phase"]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for config in PROFILES:
        output_path = config["output_filename"]
        print(f"Generating: {output_path} ...", end=" ", flush=True)

        try:
            rows = generate_profile(config)
            write_csv(rows, output_path)

            peak_kw   = max(r["power_kw"] for r in rows)
            duration  = rows[-1]["time_min"]
            final_soc = rows[-1]["soc_percent"]
            print(f"done. {len(rows)} rows | {duration:.0f} min | "
                  f"peak {peak_kw:.1f} kW | final SoC {final_soc:.1f}%")

        except Exception as e:
            print(f"FAILED: {e}")

    print("\nAll profiles written to:", os.path.abspath(OUTPUT_DIR))
    print("These CSVs are ready to use with charging_profile.py")