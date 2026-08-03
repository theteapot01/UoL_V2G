"""
battery_profile.py
==================
Battery data source abstraction for the V2G EVCC controller.

The ISO 15118 EVCC controller needs to know, at each step of a charging
session, the current battery state: state of charge (SoC), instantaneous
power, and a coarse "phase" label. This module decouples *where* that data
comes from (a recorded CSV profile now; a dynamic battery model later) from
*how* the controller consumes it.

Usage:
    profile = CsvProfile("/home/pi/UoL_V2G/code_battery_sim/profiles/lfp_50kwh.csv")
    state = profile.current()        # state at the current step
    state = profile.advance()        # move one step forward, return new state
    profile.reset()                  # rewind to the start (new session)

The CSV is expected to have the columns produced by the battery simulator:
    time_min, soc_percent, power_kw, phase

`phase` is a free-text label such as "ramp", "charge", "hold", "done".
By convention the final row(s) carry phase == "done".

Core functions/classes:
    load_battery_parameters() — reads an EV chemistry CSV (e.g. nominal voltage) from code_battery_sim/evtype/.
    BatteryProfile             — abstract interface (current/advance/at_end/reset) implemented by both CsvProfile and SimulatedBattery.
    CsvProfile.current()/advance()/at_end()/reset() — step through a pre-recorded CSV charge profile row by row.
"""

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List


def load_battery_parameters(params_path: str) -> Dict[str, str]:
    """
    Load a battery parameter file from the simulator's evtype/ directory.

    These files have the columns: Parameter Name, Parameter Value, Comment.
    Returns a dict mapping parameter name -> value (as a string; callers cast
    as needed). Example keys: 'ev_nominalvoltage', 'ev_packcapacity',
    'ev_crate', 'ev_batterychemistry'.
    """
    params: Dict[str, str] = {}
    with open(params_path, "r", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("Parameter Name") or "").strip()
            value = (row.get("Parameter Value") or "").strip()
            if name:
                params[name] = value
    return params


@dataclass
class BatteryState:
    """A single sampled point of battery state."""

    time_min: float
    soc_percent: float
    power_kw: float
    phase: str
    soh_percent: float = 100.0
    temperature_c: float = 25.0

    @property
    def is_done(self) -> bool:
        """True if this profile point marks the end of the charging activity."""
        return self.phase.strip().lower() == "done"

    @property
    def is_discharging(self) -> bool:
        """Negative power means the battery is exporting (V2G discharge)."""
        return self.power_kw < 0


class BatteryProfile(ABC):
    """
    Abstract battery data source.

    A profile is a forward-stepping sequence of BatteryState points. The
    controller advances the profile once per charge-loop iteration. Concrete
    implementations decide how each state is produced.
    """

    @abstractmethod
    def current(self) -> BatteryState:
        """Return the state at the current step without advancing."""
        ...

    @abstractmethod
    def advance(self) -> BatteryState:
        """Advance one step and return the new current state."""
        ...

    @abstractmethod
    def at_end(self) -> bool:
        """True if there are no further steps to advance to."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Rewind to the first step (e.g. at the start of a new session)."""
        ...


class CsvProfile(BatteryProfile):
    """
    A battery profile backed by a recorded CSV file.

    Each row of the CSV becomes one BatteryState. Advancing the profile moves
    to the next row. When the last row is reached, advance() stays on the last
    row and at_end() returns True (the controller uses this to end the loop).
    """

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self._states: List[BatteryState] = self._load(csv_path)
        if not self._states:
            raise ValueError(f"Battery profile {csv_path} contains no rows")
        self._index = 0

    @staticmethod
    def _load(csv_path: str) -> List[BatteryState]:
        states: List[BatteryState] = []
        with open(csv_path, "r", newline="") as fh:
            reader = csv.DictReader(fh)
            required = {"time_min", "soc_percent", "power_kw", "phase"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f"Battery profile {csv_path} is missing columns: {missing}"
                )
            for row in reader:
                states.append(
                    BatteryState(
                        time_min=float(row["time_min"]),
                        soc_percent=float(row["soc_percent"]),
                        power_kw=float(row["power_kw"]),
                        phase=row["phase"],
                    )
                )
        return states

    def current(self) -> BatteryState:
        return self._states[self._index]

    def advance(self) -> BatteryState:
        if self._index < len(self._states) - 1:
            self._index += 1
        return self._states[self._index]

    def at_end(self) -> bool:
        return self._index >= len(self._states) - 1

    def reset(self) -> None:
        self._index = 0

    def __len__(self) -> int:
        return len(self._states)
