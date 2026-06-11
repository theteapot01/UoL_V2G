from dataclasses import dataclass
from typing import Optional
from code_iso15118_custom.simulated_battery import SimulatedBattery

@dataclass(frozen=True)
class Telemetry:
    soc_percent: float = 0.0
    soh_percent: float = 100.0
    power_kw: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    charging: bool = False

class SharedState:
    def __init__(self):
        self.latest = Telemetry()   # rebind this whole thing to update
        self.battery: Optional[SimulatedBattery] = None

state = SharedState()
