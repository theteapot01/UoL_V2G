import time
from collections import deque
from dataclasses import dataclass, field
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
    energy_wh: float = 0.0        # cumulative charge energy (Wh) — from Energy.Active.Import.Register
    v2g_energy_wh: float = 0.0    # cumulative V2G export energy (Wh) — integrated from negative power
    soc_percent: float = 0.0
    timestamp: float = 0.0
    # ISO 15118 stats forwarded via OCPP MeterValues custom measurands
    voltage_v: float = 0.0
    current_a: float = 0.0
    evse_max_charge_kw: float = 0.0
    evse_max_discharge_kw: float = 0.0


@dataclass
class TariffSettings:
    charge_pence_per_kwh: float = 28.0   # cost to charge EV (p/kWh)
    v2g_pence_per_kwh: float = 15.0      # credit for V2G export (p/kWh)


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
class ProtocolSecurity:
    """Live TLS status for one protocol link."""
    configured: bool = False   # cert files exist and are readable
    connected: bool = False    # at least one authenticated connection is up
    tls_version: str = ""      # e.g. "TLSv1.3"  (populated from live socket where possible)
    cipher: str = ""           # e.g. "TLS_AES_256_GCM_SHA384"
    cert_expiry: str = ""      # "YYYY-MM-DD" parsed from the cert file


@dataclass
class SecurityState:
    ocpp: ProtocolSecurity = field(default_factory=ProtocolSecurity)
    iec104: ProtocolSecurity = field(default_factory=ProtocolSecurity)
    iso15118: ProtocolSecurity = field(default_factory=ProtocolSecurity)


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

        # True when the charger is reporting ~0 kW (no EV connected / standby)
        self.charger_idle: bool = True

        # User preferences (editable via dashboard, pushed to charge point via OCPP SetVariables)
        self.prefs: UserPreferences = UserPreferences()

        # Tariff configuration — used by billing card on the dashboard.
        self.tariff: TariffSettings = TariffSettings()

        # Live reference to the connected OCPP ChargePoint instance (set by CPMS on_connect).
        # Used by the dashboard to send SetVariables without a circular import.
        self.connected_charge_point: Optional[Any] = None

        # Per-protocol TLS/security status — populated at startup and on connect events.
        self.security: SecurityState = SecurityState()

    def log_command(self, command: str, source: str) -> None:
        self.command_log.appendleft(
            CommandEntry(timestamp=time.time(), command=command, source=source)
        )


grid_state = GridDashboardState()
