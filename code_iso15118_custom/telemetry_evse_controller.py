"""
telemetry_evse_controller.py
============================
EVSE controller that forwards EV telemetry (from ISO 15118) into the shared
state read by the IEC 104 server and OCPP client, and relays the grid's power
setpoint back to the EV via the EVSE session limits in DC_ChargeLoopRes.

The SECC does NOT own a battery model.  SoC comes from the EV's
DisplayParameters.PresentSOC, and power is computed from the EV's target
voltage and current.  The grid's desired power direction/magnitude is stored
in ``state.grid_power_setpoint_kw`` by the IEC 104 on_step_command callback;
this controller translates it into EVSE DC charge-loop limits so the EV can
respect the grid's wishes.
"""

import logging
from typing import Optional

from iso15118.secc.controller.simulator import SimEVSEController
from charger_state import state, Telemetry

logger = logging.getLogger(__name__)

class TelemetryEVSEController(SimEVSEController):
    def __init__(self):
        super().__init__()
        logger.info("TelemetryEVSEController initialized")

    @classmethod
    async def create(cls):
        return cls()

    # ------------------------------------------------------------------
    #  Charge-loop hook: called every DC_ChargeLoopReq from the EV
    # ------------------------------------------------------------------

    async def send_charging_command(
        self,
        ev_target_voltage: Optional[float],
        ev_target_current: Optional[float],
        is_precharge: bool = False,
        is_session_bpt: bool = False,
    ):
        """
        Overrides SimEVSEController.send_charging_command.

        Called each time the SECC receives a DC_ChargeLoopReq from the EV.
        Two jobs:
          1. Forward the EV's telemetry into shared state (→ IEC 104 / OCPP).
          2. Push the grid's power setpoint into the EVSE session limits so the
             framework includes them in the next DC_ChargeLoopRes (→ EV).
        """
        await super().send_charging_command(
            ev_target_voltage, ev_target_current, is_precharge, is_session_bpt
        )

        # --- 1. Forward EV telemetry into shared state -------------------
        ev_data = self.get_ev_data_context()

        voltage = ev_target_voltage if ev_target_voltage is not None else 0.0
        current = ev_target_current if ev_target_current is not None else 0.0

        # If we have a live simulated battery, use its state for telemetry
        # (This handles the case where the battery model was initialized in
        # shared state by run_secc.py - optional, but helps sync)
        if getattr(state, "battery", None):
            state.battery.tick()  # Advance real-time
            soc = state.battery.soc_percent
            power_kw = state.battery.power_kw
            soh = state.battery.soh_percent
        else:
            power_kw = (voltage * current) / 1000.0
            soc = ev_data.present_soc if ev_data.present_soc is not None else 0.0
            soh = 100.0

        state.latest = Telemetry(
            soc_percent=float(soc),
            soh_percent=float(soh),
            power_kw=float(power_kw),
            voltage_v=float(voltage),
            current_a=float(current),
            charging=not is_precharge and (power_kw != 0),
        )

        logger.debug(f"Telemetry updated: {state.latest}")

        # --- 2. Relay grid setpoint → EVSE session limits ----------------
        # The framework reads self.evse_data_context.session_limits.dc_limits
        # when building BPT_Scheduled_DC_CLResControlMode in DC_ChargeLoopRes.
        # By updating these fields here, the next response carries the grid's
        # desired power envelope and the EV can clamp accordingly.
        max_charge_w, max_discharge_w = self._grid_setpoint_to_evse_limits()
        dc = self.evse_data_context.session_limits.dc_limits
        dc.max_charge_power = max_charge_w
        dc.max_discharge_power = max_discharge_w

        state.iso_evse_max_charge_w = max_charge_w
        state.iso_evse_max_discharge_w = max_discharge_w

        logger.debug(
            f"EVSE limits updated: charge={max_charge_w} W, "
            f"discharge={max_discharge_w} W  "
            f"(grid setpoint={state.grid_power_setpoint_kw:+.1f} kW)"
        )

    # ------------------------------------------------------------------
    #  Helper: translate grid setpoint → EVSE power limits
    # ------------------------------------------------------------------

    @staticmethod
    def _grid_setpoint_to_evse_limits():
        """
        Convert ``state.grid_power_setpoint_kw`` into (max_charge_W,
        max_discharge_W) for the EVSE session limits.

        The startup case (no command received yet) is handled via the
        ``state.command_received`` flag so that a setpoint of exactly 0.0
        during a direction-change transition is not mistaken for startup and
        does not incorrectly unlock both directions at full rated power.
        """
        if not state.command_received:
            # No IEC 104 command has arrived yet — let the EV charge freely
            # at its initial setpoint (EVCC_INIT_SETPOINT_KW).
            return (
                state.max_charge_kw * 1000.0,
                state.max_discharge_kw * 1000.0,
            )

        setpoint = state.grid_power_setpoint_kw

        if setpoint > 0:
            # Grid wants charging
            return abs(setpoint) * 1000.0, 0.0
        elif setpoint < 0:
            # Grid wants V2G discharge
            return 0.0, abs(setpoint) * 1000.0
        else:
            # Setpoint landed on exactly 0 mid-transition — hold idle.
            return 0.0, 0.0
