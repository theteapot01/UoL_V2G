"""
charger_state.py
================
Shared telemetry state between the SECC's ISO 15118 controller, the IEC 104
server, and the OCPP client.

The SECC does NOT own a battery model. SoC and power are forwarded from the
EV via ISO 15118 (the EV is the single source of truth). The grid's power
commands arrive via IEC 104 and are stored here so the EVSE controller can
relay them to the EV through the charge-loop response limits.
"""

from dataclasses import dataclass
from pathlib import Path

# File written by the EVCC process (SimulatedBattery) and read by the SECC
# (TelemetryEVSEController) so IOA 14 reflects the actual pack temperature
# from the single authoritative battery model. Mirrors the _PREFS_FILE
# pattern in simulated_battery.py (where the direction is reversed: SECC→EVCC).
EVCC_TEMP_FILE = Path("/tmp/v2g_pack_temperature")

@dataclass(frozen=True)
class Telemetry:
    """Immutable snapshot of the latest EV telemetry forwarded by the SECC."""
    soc_percent: float = 0.0
    soh_percent: float = 100.0
    power_kw: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    temperature_c: float = 25.0
    charging: bool = False

class SharedState:
    def __init__(self):
        # Latest telemetry received from the EV via ISO 15118.
        # Rebind the whole object to update (it's frozen).
        self.latest = Telemetry()

        # Grid command: desired power setpoint [kW].
        #   positive = charge (grid → EV)
        #   negative = discharge / V2G (EV → grid)
        # Seeded at 0; the IEC 104 on_step_command callback adjusts it.
        self.grid_power_setpoint_kw: float = 0.0

        # Size of one HIGHER/LOWER step [kW].
        self.step_kw: float = 5.0

        # Absolute limits for clamping the setpoint.
        self.max_charge_kw: float = 300.0
        self.max_discharge_kw: float = 20.0

        # Set to True by on_step_command the moment the first IEC 104 command
        # arrives. Used by _grid_setpoint_to_evse_limits to tell apart
        # "startup before any command" (setpoint == 0 by default) from
        # "setpoint stepped through zero during a direction change".
        self.command_received: bool = False

        # True while an ISO 15118 charge session is active (EV plugged in).
        # Set by TelemetryEVSEController on first send_charging_command tick;
        # cleared by session_ended(). on_step_command ignores commands when False.
        self.ev_connected: bool = False

        # ISO 15118 charge-loop stats — written by TelemetryEVSEController
        # each DC_ChargeLoop iteration; read by the OCPP client for reporting.
        self.iso_evse_max_charge_w: float = 0.0    # last EVSE charge limit sent to EV [W]
        self.iso_evse_max_discharge_w: float = 0.0 # last EVSE discharge limit sent to EV [W]
        self.iso_loop_ms: float = 0.0              # processing time of last charge-loop tick [ms]

        # User preferences — pushed from CPMS via OCPP SetVariables
        self.pref_min_soc_pct: float = 20.0     # don't V2G below this
        self.pref_max_soc_pct: float = 80.0     # stop charging above this
        self.pref_target_soc_pct: float = 80.0  # desired SoC at departure
        self.pref_departure_time: str = ""       # "HH:MM" or "" = no window


state = SharedState()
