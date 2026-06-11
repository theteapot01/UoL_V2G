import logging
from typing import Optional

from iso15118.secc.controller.simulator import SimEVSEController
from code_iso15118_custom.charger_state import state, Telemetry

logger = logging.getLogger(__name__)

class TelemetryEVSEController(SimEVSEController):
    def __init__(self):
        super().__init__()
        logger.info("TelemetryEVSEController initialized")

    @classmethod
    async def create(cls):
        return cls()

    async def send_charging_command(
        self,
        ev_target_voltage: Optional[float],
        ev_target_current: Optional[float],
        is_precharge: bool = False,
        is_session_bpt: bool = False,
    ):
        """
        Overrides SimEVSEController.send_charging_command to capture telemetry.
        This is called when the SECC receives a charging request from the EV.
        """
        await super().send_charging_command(
            ev_target_voltage, ev_target_current, is_precharge, is_session_bpt
        )
        
        # Access EV data context to get SoC and other metrics
        ev_data = self.get_ev_data_context()
        
        # Calculate power (P = V * I)
        voltage = ev_target_voltage if ev_target_voltage is not None else 0.0
        current = ev_target_current if ev_target_current is not None else 0.0
        
        # If we have a live simulated battery, use its state for telemetry
        if state.battery:
            # Advance the battery model to keep it in sync with the charge loop
            state.battery.advance()
            soc = state.battery.soc_percent
            power_kw = state.battery.power_kw
            soh = state.battery.soh_percent
        else:
            power_kw = (voltage * current) / 1000.0
            soc = ev_data.present_soc if ev_data.present_soc is not None else 0.0
            soh = 100.0
        
        # Update shared state
        state.latest = Telemetry(
            soc_percent=float(soc),
            soh_percent=float(soh),
            power_kw=float(power_kw),
            voltage_v=float(voltage),
            current_a=float(current),
            charging=not is_precharge and (power_kw != 0)
        )
        
        logger.debug(f"Telemetry updated: {state.latest}")
