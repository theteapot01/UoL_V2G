import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional


@dataclass
class IEC104Snapshot:
    power_kw: float = 0.0
    soc_percent: float = 0.0
    temp_c: float = 25.0
    timestamp: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    iso_loop_ms: float = 0.0
    iso_timestamp: float = 0.0


@dataclass
class OCPPSnapshot:
    power_w: float = 0.0
    energy_wh: float = 0.0
    soc_percent: float = 0.0
    timestamp: float = 0.0
    # ISO 15118 stats forwarded via OCPP MeterValues custom measurands
    voltage_v: float = 0.0
    current_a: float = 0.0
    evse_max_charge_kw: float = 0.0
    evse_max_discharge_kw: float = 0.0


@dataclass
class GridSnapshot:
    bus2_voltage_pu: float = 1.0
    trafo_loading_pct: float = 0.0
    line_loading_pct: float = 0.0


@dataclass
class UserPreferences:
    min_soc_pct: float = 20.0    # don't V2G discharge below this
    max_soc_pct: float = 80.0    # stop charging above this
    target_soc_pct: float = 80.0 # desired SoC at departure
    departure_time: str = ""     # "HH:MM" or "" = no window


@dataclass
class CommandEntry:
    timestamp: float
    command: str
    source: str  # "auto" or "manual"


class GridDashboardState:
    def __init__(self):
        self.iec104 = IEC104Snapshot()
        self.ocpp = OCPPSnapshot()
        self.grid = GridSnapshot()
        self.command_log: Deque[CommandEntry] = deque(maxlen=20)

        # Per-step timing (milliseconds)
        self.iec104_read_ms: float = 0.0
        self.pandapower_ms: float = 0.0
        self.transmit_ms: float = 0.0
        self.cycle_ms: float = 0.0  # read + pandapower (does not include transmit)

        # Manual control state
        self.manual_override: Optional[str] = None  # "HIGHER", "LOWER", or None
        self.auto_control: bool = True

        # User preferences (editable via dashboard, pushed to charge point via OCPP SetVariables)
        self.prefs: UserPreferences = UserPreferences()

        # Live reference to the connected OCPP ChargePoint instance (set by CPMS on_connect).
        # Used by the dashboard to send SetVariables without a circular import.
        self.connected_charge_point: Optional[Any] = None

    def log_command(self, command: str, source: str) -> None:
        self.command_log.appendleft(
            CommandEntry(timestamp=time.time(), command=command, source=source)
        )


grid_state = GridDashboardState()
