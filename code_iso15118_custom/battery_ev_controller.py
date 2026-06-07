"""
battery_ev_controller.py
========================
EVCC controller that drives the ISO 15118 charging session from a real
battery profile (recorded CSV now, dynamic model later) instead of the
artificial fixed-cycle behaviour of Josev's SimEVController.

Design
------
SimEVController already implements every method of EVControllerInterface and
produced a clean ISO 15118-2 DC session. Almost all of its DC behaviour
derives from a single piece of state: `self._soc`. This subclass keeps that
contract but sources `self._soc` (and the loop's start/stop decisions) from a
BatteryProfile rather than incrementing a counter.

We override only the few methods that decide:
  - how SoC evolves over the charge loop  (continue_charging)
  - when the charge loop ends             (is_charging_complete)
  - the reported EV status / SoC          (get_dc_ev_status, get_dc_ev_status_dinspec)

Everything else (charge parameter discovery, precharge logic, target/present
voltage, welding detection, all AC and ISO 15118-20 methods) is inherited
unchanged from SimEVController.
"""

import logging

from iso15118.evcc import EVCCConfig
from iso15118.evcc.controller.simulator import SimEVController
from iso15118.shared.messages.enums import DCEVErrorCode, UnitSymbol
from iso15118.shared.messages.iso15118_2.datatypes import DCEVStatus
from iso15118.shared.messages.din_spec.datatypes import (
    DCEVStatus as DCEVStatusDINSPEC,
)
from iso15118.shared.messages.datatypes import (
    DCEVChargeParams,
    PVEVMaxCurrentLimit,
    PVEVMaxPowerLimit,
    PVEVMaxVoltageLimit,
    PVEVEnergyCapacity,
    PVEVTargetCurrent,
    PVEVTargetVoltage,
)

from battery_profile import BatteryProfile, CsvProfile, load_battery_parameters

logger = logging.getLogger(__name__)


class BatterySimEVController(SimEVController):
    """
    A SimEVController whose state of charge follows a BatteryProfile.

    Parameters
    ----------
    evcc_config:
        The EVCC configuration, as for SimEVController.
    profile:
        A BatteryProfile supplying the battery state at each charge-loop step.
        Defaults to a CsvProfile reading the LFP 50 kWh profile if not given.
    """

    DEFAULT_PROFILE_PATH = (
        "/home/pi/UoL_V2G/code_battery_sim/profiles/lfp_50kwh.csv"
    )
    DEFAULT_PARAMS_PATH = (
        "/home/pi/UoL_V2G/code_battery_sim/evtype/lfp_parameters.csv"
    )
    # Fallback nominal pack voltage [V] if no parameters file is available.
    FALLBACK_NOMINAL_VOLTAGE = 400.0

    def __init__(
        self,
        evcc_config: EVCCConfig,
        profile: BatteryProfile = None,
        max_steps: int = None,
        params_path: str = None,
    ):
        super().__init__(evcc_config)

        self.profile: BatteryProfile = profile or CsvProfile(
            self.DEFAULT_PROFILE_PATH
        )
        self.profile.reset()

        # Load battery pack parameters (nominal voltage etc.) so the requested
        # DC current can be derived from the profile's power. Falls back to a
        # representative voltage if the parameters file can't be read.
        self.nominal_voltage = self.FALLBACK_NOMINAL_VOLTAGE
        try:
            params = load_battery_parameters(params_path or self.DEFAULT_PARAMS_PATH)
            self.nominal_voltage = float(params["ev_nominalvoltage"])
            logger.info(
                f"Battery parameters loaded: nominal voltage "
                f"{self.nominal_voltage} V"
            )
        except (OSError, KeyError, ValueError) as exc:
            logger.warning(
                f"Could not load battery parameters ({exc!r}); "
                f"using fallback nominal voltage {self.nominal_voltage} V"
            )

        # The profile may begin with one or more 'done' rows representing the
        # idle state before charging starts (e.g. SoC flat, power 0). Skip past
        # them so the charge loop begins at the first active row, and so the
        # initial 'done' is not mistaken for "charging finished".
        while not self.profile.at_end() and self.profile.current().is_done:
            self.profile.advance()

        # Optional cap on the number of charge-loop steps, for bounded test
        # runs. None (the default) means "run the whole profile". When set, the
        # loop ends after this many advance() steps regardless of profile phase.
        self.max_steps = max_steps
        self._steps_taken = 0

        # During PreCharge the EV requests its pack voltage but only a trickle
        # of current (the charger ramps its output to match pack voltage before
        # the contactors close). Only after precharge completes does the EV
        # request the profile-derived charging current. This flag tracks that
        # transition; it is set when is_precharged() first returns True.
        self._precharge_complete = False
        # Trickle current requested during precharge, in amperes.
        self.precharge_current_a = 1.0

        # Initialise SoC from the first profile point so that
        # ChargeParameterDiscovery reports the real starting SoC.
        start = self.profile.current()
        self._soc = int(round(start.soc_percent))
        logger.info(
            "BatterySimEVController initialised from profile "
            f"(start SoC {self._soc}%, {len(self.profile)} steps"
            + (f", capped at {max_steps})" if max_steps else ")")
            if hasattr(self.profile, "__len__")
            else f"BatterySimEVController initialised (start SoC {self._soc}%)"
        )

    async def continue_charging(self) -> bool:
        """
        Advance the battery profile by one step and decide whether the charge
        loop should continue.

        Returns False (stop) when the profile reaches a 'done' phase, runs out
        of steps, or stop_charging() has been called externally. Otherwise
        advances one profile step, updates the reported SoC, and returns True.
        """
        if self._charging_is_completed:
            return False

        if self.max_steps is not None and self._steps_taken >= self.max_steps:
            logger.info(
                f"Charge loop ending: reached max_steps cap ({self.max_steps}), "
                f"SoC {self._soc}%"
            )
            return False

        # Report the current row, then advance for the next call. This means
        # the SoC set here is the one carried into the next CurrentDemandReq.
        state = self.profile.current()
        self._soc = int(round(state.soc_percent))
        self._steps_taken += 1
        logger.info(
            f"Charge loop step {self._steps_taken}: t={state.time_min:.0f} min, "
            f"SoC={state.soc_percent:.1f}%, power={state.power_kw:.2f} kW, "
            f"phase={state.phase}"
        )

        # End if we've run out of rows or hit a terminal 'done' row.
        if self.profile.at_end() or state.is_done:
            logger.info(
                f"Charge loop ending after step {self._steps_taken} "
                f"(SoC {self._soc}%, phase '{state.phase}')"
            )
            return False

        self.profile.advance()
        return True

    async def is_charging_complete(self) -> bool:
        """
        Charging is complete when the profile is exhausted, the current phase
        is 'done', or stop_charging() was called.
        """
        if self._charging_is_completed:
            return True
        if self.max_steps is not None and self._steps_taken >= self.max_steps:
            return True
        return self.profile.at_end() or self.profile.current().is_done

    async def get_dc_ev_status(self) -> DCEVStatus:
        """Report live SoC from the battery profile (ISO 15118-2)."""
        return DCEVStatus(
            ev_ready=True,
            ev_error_code=DCEVErrorCode.NO_ERROR,
            ev_ress_soc=self._soc,
        )

    async def get_dc_ev_status_dinspec(self) -> DCEVStatusDINSPEC:
        """Report live SoC from the battery profile (DIN SPEC 70121)."""
        return DCEVStatusDINSPEC(
            ev_ready=True,
            ev_error_code=DCEVErrorCode.NO_ERROR,
            ev_ress_soc=self._soc,
        )

    @staticmethod
    def _as_value_multiplier(value: float, max_multiplier: int = 3):
        """
        Express a float as (integer value, power-of-ten multiplier) for the
        ISO 15118-2 PhysicalValue encoding, keeping the integer value within
        the 16-bit signed range the schema allows.

        Example: 30.9 -> (309, -1); 12345 -> (1234, 1).
        """
        if value == 0:
            return 0, 0
        multiplier = 0
        v = float(value)
        # Scale up small fractional values for precision.
        while abs(v) < 1000 and multiplier > -3:
            v *= 10
            multiplier -= 1
        # Scale down large values to fit the 16-bit signed range (+/-32767).
        while abs(v) > 32767 and multiplier < max_multiplier:
            v /= 10
            multiplier += 1
        return int(round(v)), multiplier

    async def is_precharged(self, present_voltage_evse) -> bool:
        """
        Defer to the base precharge-completion logic, but latch a flag when
        precharge finishes so get_dc_charge_params() can switch from the
        precharge trickle current to the profile-derived charging current.
        """
        done = await super().is_precharged(present_voltage_evse)
        if done:
            self._precharge_complete = True
        return done

    async def get_dc_charge_params(self) -> DCEVChargeParams:
        """
        Build the DC charge parameters with a *live* target current and voltage
        derived from the current battery profile point.

        The requested current follows from the profile's instantaneous power
        and the pack's nominal voltage: I = P / V. The target voltage is the
        nominal pack voltage. The static max-limit fields are preserved from
        the base controller so ChargeParameterDiscovery still advertises sane
        ceilings.

        Overrides SimEVController.get_dc_charge_params(), which returned a fixed
        1 A target.
        """
        state = self.profile.current()
        power_w = state.power_kw * 1000.0
        voltage = self.nominal_voltage if self.nominal_voltage > 0 else 1.0

        if self._precharge_complete:
            # Charging loop: request the profile-derived current, I = P / V.
            target_current_a = power_w / voltage
        else:
            # PreCharge: request only a trickle so the charger can safely ramp
            # its output voltage to the pack voltage before contactors close.
            target_current_a = self.precharge_current_a

        cur_value, cur_mult = self._as_value_multiplier(target_current_a)
        volt_value, volt_mult = self._as_value_multiplier(voltage)

        base = self.dc_ev_charge_params
        return DCEVChargeParams(
            dc_max_current_limit=base.dc_max_current_limit,
            dc_max_power_limit=base.dc_max_power_limit,
            dc_max_voltage_limit=base.dc_max_voltage_limit,
            dc_energy_capacity=base.dc_energy_capacity,
            dc_target_current=PVEVTargetCurrent(
                multiplier=cur_mult, value=cur_value, unit=UnitSymbol.AMPERE
            ),
            dc_target_voltage=PVEVTargetVoltage(
                multiplier=volt_mult, value=volt_value, unit=UnitSymbol.VOLTAGE
            ),
        )
