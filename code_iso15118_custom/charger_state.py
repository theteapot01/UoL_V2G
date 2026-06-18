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

@dataclass(frozen=True)
class Telemetry:
    """Immutable snapshot of the latest EV telemetry forwarded by the SECC."""
    soc_percent: float = 0.0
    soh_percent: float = 100.0
    power_kw: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
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
        self.max_charge_kw: float = 100.0
        self.max_discharge_kw: float = 100.0


state = SharedState()
