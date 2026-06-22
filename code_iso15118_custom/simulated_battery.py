"""
simulated_battery.py
====================
A command-driven, computed-state battery model for the V2G prototype.

Where ``CsvProfile`` (in battery_profile.py) *replays* a recorded charging
session, ``SimulatedBattery`` *computes* the battery state live:

  - State of charge (SoC) is integrated from the commanded power by coulomb
    counting, so the grid can change the charge/discharge power and the SoC
    responds. This is Eq. (3) of Ranjith Kumar et al. (IEEE Access, 2023),
    written in energy terms because our data is in kW against a kWh pack:

        dSoC[%] = (P_kW * dt_h * eta) / (E_usable_kWh) * 100

    where E_usable = nominal_capacity * SOH (a faded pack holds less, so the
    same kWh moves SoC further).

  - State of health (SOH) follows an energy-throughput cycle-aging model
    (the "weighted Ah-throughput" approach referenced in the paper's SOH
    section). Cumulative throughput is converted to equivalent full cycles
    (EFC) and SOH fades linearly toward the 80% end-of-life convention:

        EFC = throughput_kWh / (2 * nominal_capacity_kWh)
        SOH = 1 - (1 - SOH_eol) * (EFC / cycle_life)

    Because V2G adds discharge/charge throughput that wouldn't otherwise
    happen, running a profile with and without V2G and comparing the SOH
    drop gives the "cost of providing grid services" figure the project
    wants to show (and, times a replacement cost, an owner-compensation
    number).

Command interface (grid side)
-----------------------------
The IEC 104 server receives regulating step commands (C_RC_TA_1, HIGHER /
LOWER). Map those onto ``apply_step()``; or set an absolute target with
``set_power_setpoint()``. Battery-protection limits are enforced here: a
charge command is ignored at 100% SoC, and a discharge command is ignored
at or below the configured V2G floor, so the grid can never push the pack
outside its safe window.

Consumer interface (ISO 15118 side)
-----------------------------------
``SimulatedBattery`` implements the ``BatteryProfile`` interface, so it
drops straight into BatterySimEVController in place of a CsvProfile:
``current()`` returns the live BatteryState, ``advance()`` integrates one
tick and returns the new state.

The class deliberately has no c104 / charger_state / iso15118 imports so it
can be unit-tested on its own; wire it into shared state at the call sites.

Sign convention (matches the OCPP MeterValues usage in the project):
    power > 0  ->  charging  (drawing from grid, SoC rises)
    power < 0  ->  discharging / V2G export (SoC falls)
"""

import json
import math
import threading
import time
from typing import Optional

from battery_profile import BatteryProfile, BatteryState

# Shared preferences file written by the SECC when the dashboard changes settings.
# The EVCC polls this file to pick up a new target_soc without requiring a restart.
_PREFS_FILE = "/tmp/v2g_prefs.json"


class SimulatedBattery(BatteryProfile):
    """
    Live battery model with coulomb-counted SoC and throughput-based SOH.

    Parameters
    ----------
    capacity_kwh:
        Beginning-of-life usable energy of the pack, in kWh. Default 82.5
        (BYD LFP pack).
    cycle_life:
        Number of equivalent full cycles to the 80% end-of-life point. LFP
        is long-lived; 5000 is a reasonable BYD-blade figure. Lower it to
        make degradation show faster in a demo.
    soc_init:
        Starting SoC [%]. Seed this from the first row of the old profile if
        you want continuity with the recorded data.
    soc_floor:
        Lowest SoC [%] the grid is allowed to discharge to (V2G protection
        floor). Discharge commands are clamped to zero at/below this.
    soc_ceiling:
        Highest SoC [%]; charge commands are clamped to zero at/above this.
    charge_efficiency:
        Coulombic/charge efficiency applied to charging power (0-1). Energy
        leaving the pack on discharge is counted at full value.
    soh_eol:
        SOH fraction defining end of life (0.8 = 80%, the usual convention).
    max_charge_kw, max_discharge_kw:
        Setpoint magnitude limits [kW] for charge (+) and discharge (-).
    default_step_kw:
        Power change per HIGHER/LOWER regulating step [kW].
    target_soc:
        If set, ``at_end()`` reports True once SoC reaches it (e.g. charge to
        full). Set None for an open-ended, grid-controlled run that only
        stops when the controller stops it.
    max_step_s:
        Safety clamp on the integration timestep [s]; guards against a huge
        SoC jump if a long real-time gap occurs between advance() calls.
    ambient_temp_c:
        Baseline cell temperature [°C] when the pack is idle.
    thermal_resistance_c_per_kw:
        Steady-state temperature rise per kW of delivered power [°C/kW].
        At 20 kW discharge → +1 °C; at 300 kW charge → +15 °C above ambient.
    thermal_time_constant_s:
        RC time constant of the pack thermal mass [s]. 300 s ≈ 5-minute lag
        typical of an actively-cooled large-format LFP pack.
    """

    def __init__(
        self,
        capacity_kwh: float = 82.5,
        cycle_life: int = 5000,
        soc_init: float = 50.0,
        soc_floor: float = 20.0,
        soc_ceiling: float = 100.0,
        charge_efficiency: float = 0.98,
        soh_eol: float = 0.80,
        max_charge_kw: float = 100.0,
        max_discharge_kw: float = 100.0,
        default_step_kw: float = 1.0,
        target_soc: Optional[float] = 100.0,
        max_step_s: float = 30.0,
        ambient_temp_c: float = 25.0,
        thermal_resistance_c_per_kw: float = 0.05,
        thermal_time_constant_s: float = 300.0,
    ):
        if capacity_kwh <= 0:
            raise ValueError("capacity_kwh must be positive")
        if not 0.0 < soh_eol < 1.0:
            raise ValueError("soh_eol must be between 0 and 1")
        if not 0.0 <= soc_floor < soc_ceiling <= 100.0:
            raise ValueError("require 0 <= soc_floor < soc_ceiling <= 100")

        self.capacity_kwh = float(capacity_kwh)
        self.cycle_life = int(cycle_life)
        self.soc_floor = float(soc_floor)
        self.soc_ceiling = float(soc_ceiling)
        self.charge_efficiency = float(charge_efficiency)
        self.soh_eol = float(soh_eol)
        self.max_charge_kw = float(max_charge_kw)
        self.max_discharge_kw = float(max_discharge_kw)
        self.default_step_kw = float(default_step_kw)
        self.target_soc = target_soc
        self.max_step_s = float(max_step_s)
        self.ambient_temp_c = float(ambient_temp_c)
        self.thermal_resistance_c_per_kw = float(thermal_resistance_c_per_kw)
        self.thermal_time_constant_s = float(thermal_time_constant_s)

        self._soc_init = float(soc_init)

        # Live state (guarded by _lock; mutated from both the asyncio charge
        # loop via advance() and the c104 callback thread via apply_step()).
        self._lock = threading.Lock()
        # Counter used to throttle polling of the shared prefs file.
        self._prefs_counter = 0
        self._soc = self._soc_init            # [%]
        self._soh = 1.0                       # [0-1]
        self._throughput_kwh = 0.0            # cumulative |energy| through cells
        self._power_setpoint_kw = 0.0         # commanded (+charge / -discharge)
        self._actual_power_kw = 0.0           # delivered after limit clamping
        self._temperature_c = float(ambient_temp_c)  # pack temperature [°C]
        self._elapsed_min = 0.0               # wall-clock minutes since reset
        self._last_t: Optional[float] = None  # monotonic timestamp of last tick

    # ------------------------------------------------------------------
    #  Command side (grid / IEC 104)
    # ------------------------------------------------------------------
    def set_power_setpoint(self, power_kw: float) -> float:
        """
        Set the commanded power absolutely [kW] (+charge / -discharge),
        clamped to the configured charge/discharge limits. Returns the
        clamped setpoint.
        """
        with self._lock:
            self._power_setpoint_kw = self._clamp_setpoint(power_kw)
            return self._power_setpoint_kw

    def apply_step(self, higher: bool, step_kw: Optional[float] = None) -> float:
        """
        Apply one IEC 104 regulating step to the commanded power.

        Mirrors the IOA 12 semantics documented in iecc104_server.py:
          HIGHER -> increase discharge / reduce charge (setpoint more negative)
          LOWER  -> increase charge   / reduce discharge (setpoint more positive)

        Returns the new clamped setpoint [kW].
        """
        step = self.default_step_kw if step_kw is None else float(step_kw)
        with self._lock:
            delta = -step if higher else step
            self._power_setpoint_kw = self._clamp_setpoint(
                self._power_setpoint_kw + delta
            )
            return self._power_setpoint_kw

    def _clamp_setpoint(self, power_kw: float) -> float:
        return max(-self.max_discharge_kw, min(self.max_charge_kw, float(power_kw)))

    # ------------------------------------------------------------------
    #  Integration
    # ------------------------------------------------------------------
    def tick(self, dt_s: Optional[float] = None) -> None:
        """
        Advance the model by ``dt_s`` seconds of the commanded power. If
        ``dt_s`` is None the elapsed wall-clock time since the previous tick
        is used (so the model runs in real time). The first tick only seeds
        the clock and does nothing.
        """
        now = time.monotonic()
        with self._lock:
            if dt_s is None:
                # Wall-clock timestep: seed the clock on first use, and clamp
                # to max_step_s so a long real-time gap can't cause a huge
                # SoC jump. An explicitly supplied dt_s is trusted as-is
                # (e.g. for fast-forward simulation or unit tests).
                if self._last_t is None:
                    self._last_t = now
                    return
                dt_s = min(now - self._last_t, self.max_step_s)
            self._last_t = now

            dt_s = max(0.0, dt_s)
            if dt_s == 0.0:
                self._actual_power_kw = 0.0
                return

            # Enforce battery-protection limits: no charging a full pack, no
            # discharging below the V2G floor. If a step would cross a limit,
            # only the fraction of it that reaches the limit is delivered, so
            # SoC lands exactly on the boundary and the reported (delivered)
            # power tapers accordingly.
            power = self._power_setpoint_kw
            if (power > 0.0 and self._soc >= self.soc_ceiling) or (
                power < 0.0 and self._soc <= self.soc_floor
            ):
                power = 0.0

            if power == 0.0:
                self._actual_power_kw = 0.0
                # Temperature decays toward ambient when idle
                _alpha = 1.0 - math.exp(-dt_s / self.thermal_time_constant_s)
                self._temperature_c += _alpha * (self.ambient_temp_c - self._temperature_c)
                self._elapsed_min += dt_s / 60.0
                return

            dt_h = dt_s / 3600.0

            # --- SoC: energy-form coulomb counting against faded capacity ---
            usable_kwh = self.capacity_kwh * self._soh
            soc_energy_kwh = power * dt_h
            if power > 0.0:
                soc_energy_kwh *= self.charge_efficiency  # charge losses
            delta_soc = (soc_energy_kwh / usable_kwh) * 100.0
            new_soc = self._soc + delta_soc

            # Fraction of the step that fits before hitting a limit.
            frac = 1.0
            if power > 0.0 and new_soc > self.soc_ceiling:
                frac = (self.soc_ceiling - self._soc) / delta_soc
            elif power < 0.0 and new_soc < self.soc_floor:
                frac = (self.soc_floor - self._soc) / delta_soc
            frac = max(0.0, min(1.0, frac))

            self._soc = max(0.0, min(100.0, self._soc + delta_soc * frac))
            self._actual_power_kw = power * frac

            # --- SOH: throughput-based cycle aging ---
            self._throughput_kwh += abs(self._actual_power_kw) * dt_h
            efc = self._throughput_kwh / (2.0 * self.capacity_kwh)
            self._soh = max(
                0.0, 1.0 - (1.0 - self.soh_eol) * (efc / self.cycle_life)
            )

            # --- Thermal: RC relaxation toward (ambient + resistive heating) ---
            _alpha = 1.0 - math.exp(-dt_s / self.thermal_time_constant_s)
            _target_t = self.ambient_temp_c + self.thermal_resistance_c_per_kw * abs(self._actual_power_kw)
            self._temperature_c += _alpha * (_target_t - self._temperature_c)

            self._elapsed_min += dt_s / 60.0

    # ------------------------------------------------------------------
    #  BatteryProfile interface (ISO 15118 / EVCC consumer)
    # ------------------------------------------------------------------
    def current(self) -> BatteryState:
        """Live battery state, without advancing the model."""
        with self._lock:
            return self._snapshot_state()

    def _refresh_target_soc(self) -> None:
        """Check the shared prefs file every 10 steps and update target_soc if changed."""
        self._prefs_counter += 1
        if self._prefs_counter % 10 != 0:
            return
        try:
            with open(_PREFS_FILE) as f:
                prefs = json.load(f)
            new_target = float(prefs["target_soc_pct"])
            if new_target != self.target_soc:
                self.target_soc = new_target
        except (OSError, ValueError, KeyError):
            pass

    def advance(self) -> BatteryState:
        """Integrate one real-time tick and return the new state."""
        self._refresh_target_soc()
        self.tick(dt_s=None)
        with self._lock:
            return self._snapshot_state()

    def at_end(self) -> bool:
        """
        True when a charge target is configured and has been reached. With
        ``target_soc=None`` the model is open-ended (grid-controlled) and
        this is always False.
        """
        if self.target_soc is None:
            return False
        with self._lock:
            return self._soc >= self.target_soc

    def reset(self) -> None:
        """Start a fresh session: SoC back to its initial value, clocks zeroed.
        SOH and cumulative throughput are *preserved* across sessions (aging
        is permanent). Use reset_health() to also clear them."""
        with self._lock:
            self._soc = self._soc_init
            self._actual_power_kw = 0.0
            self._temperature_c = self.ambient_temp_c
            self._elapsed_min = 0.0
            self._last_t = None
            # NOTE: self._power_setpoint_kw is NOT reset here, because in a
            # live simulation we want to preserve the grid's last setpoint
            # across communication session restarts.

    def reset_health(self) -> None:
        """Clear SOH and throughput (e.g. to model a brand-new pack)."""
        with self._lock:
            self._soh = 1.0
            self._throughput_kwh = 0.0

    def _snapshot_state(self) -> BatteryState:
        # Caller holds the lock.
        if self._actual_power_kw > 0.0:
            phase = "charge"
        elif self._actual_power_kw < 0.0:
            phase = "discharge"
        else:
            phase = "idle"
        if self.target_soc is not None and self._soc >= self.target_soc:
            phase = "done"
        return BatteryState(
            time_min=self._elapsed_min,
            soc_percent=self._soc,
            soh_percent=self._soh * 100.0,
            power_kw=self._actual_power_kw,
            phase=phase,
            temperature_c=self._temperature_c,
        )

    # ------------------------------------------------------------------
    #  Reporting helpers (IEC 104 monitoring points / OCPP meter values)
    # ------------------------------------------------------------------
    @property
    def soc_percent(self) -> float:
        return self._soc

    @property
    def soh_percent(self) -> float:
        """SOH as a percentage (100 = healthy, 80 = end of life)."""
        return self._soh * 100.0

    @property
    def power_kw(self) -> float:
        """Delivered power after limit clamping (+charge / -discharge)."""
        return self._actual_power_kw

    @property
    def power_setpoint_kw(self) -> float:
        """The currently commanded power, before SoC-limit clamping."""
        return self._power_setpoint_kw

    @property
    def temperature_c(self) -> float:
        """Battery pack temperature [°C]."""
        return self._temperature_c

    @property
    def usable_capacity_kwh(self) -> float:
        return self.capacity_kwh * self._soh

    @property
    def throughput_kwh(self) -> float:
        return self._throughput_kwh

    @property
    def equivalent_full_cycles(self) -> float:
        return self._throughput_kwh / (2.0 * self.capacity_kwh)
