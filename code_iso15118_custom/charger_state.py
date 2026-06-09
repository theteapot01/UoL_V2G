from dataclasses import dataclass

@dataclass(frozen=True)
class Telemetry:
    soc_percent: float = 0.0
    power_kw: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    charging: bool = False

class SharedState:
    def __init__(self):
        self.latest = Telemetry()   # rebind this whole thing to update

state = SharedState()
