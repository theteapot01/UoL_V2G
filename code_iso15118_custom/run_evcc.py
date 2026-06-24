"""
run_evcc.py
===========
Custom EVCC launcher for the V2G prototype.

This mirrors iso15118/evcc/main.py from the upstream Josev implementation but
substitutes our BatterySimEVController (whose state of charge follows a real
battery profile) for the stock SimEVController. Keeping this launcher in
code_iso15118_custom/ means the upstream iso15118 tree is not edited to wire in
our controller.

Architecture note
-----------------
The EVCC is the single source of truth for battery state (SoC, power).  The
battery profile (CSV or live SimulatedBattery) lives here.  The SECC reads
SoC from the ISO 15118 messages and forwards it to the grid — it has no
battery model of its own.

Run from the iso15118 project root (so the .env symlink and relative
EVCC_CONFIG_PATH resolve), with this directory on PYTHONPATH:

    cd ~/UoL_V2G/code_charger/iso15118
    PYTHONPATH=/home/pi/UoL_V2G/code_iso15118_custom \
        poetry run python /home/pi/UoL_V2G/code_iso15118_custom/run_evcc.py

An optional EVCC config file path may be passed as the first argument, exactly
as with the upstream main.py.

"""

import asyncio
import logging
import os
import sys

from iso15118.evcc import Config, EVCCHandler
from iso15118.evcc.evcc_config import load_from_file
from iso15118.evcc.controller.simulator import SimEVController
from iso15118.shared.exificient_exi_codec import ExificientEXICodec
import iso15118.evcc.transport.tcp_client as _tcp_evcc_mod

from battery_ev_controller import BatterySimEVController
from battery_profile import CsvProfile
from simulated_battery import SimulatedBattery
from charger_state import state as shared_state
from iso15118_perf import CountingStreamReader, CountingStreamWriter

# Wrap the EVCC TCP client's reader/writer to count raw ISO 15118 bytes without
# modifying the upstream Josev submodule.
_orig_tcp_evcc_create = _tcp_evcc_mod.TCPClient.create

@staticmethod
async def _counting_tcp_evcc_create(host, port, session_handler_queue, is_tls, iface):
    client = await _orig_tcp_evcc_create(
        host, port, session_handler_queue, is_tls, iface
    )
    client.reader = CountingStreamReader(client.reader)
    client.writer = CountingStreamWriter(client.writer)
    return client

_tcp_evcc_mod.TCPClient.create = _counting_tcp_evcc_create

logger = logging.getLogger(__name__)


async def main():
    config = Config()
    config.load_envs()
    if len(sys.argv) > 1:
        ev_config_file_path = sys.argv[1]
        if ev_config_file_path:
            config.ev_config_file_path = ev_config_file_path
    evcc_config = await load_from_file(config.ev_config_file_path)

    # Optional cap on the charge loop length, for bounded test runs.
    # Set e.g. EVCC_MAX_STEPS=15 in the environment; unset/empty means run the
    # full battery profile.
    max_steps_env = os.environ.get( "EVCC_MAX_STEPS", "" ).strip()
    max_steps = int( max_steps_env ) if max_steps_env else None

    # Optional battery profile CSV path.  If unset, falls back to the
    # controller's DEFAULT_PROFILE_PATH (lfp_82kwh.csv).
    profile_path = os.environ.get( "EVCC_PROFILE_PATH", "" ).strip()

    # Controller selection:
    #   "sim"         → stock SimEVController (no battery profile)
    #   "battery_csv" → BatterySimEVController with a CsvProfile
    #   "battery"     → BatterySimEVController with a live SimulatedBattery
    #                    (default; grid-responsive via update_evse_limits)
    controller_choice = (
        os.environ.get( "EVCC_CONTROLLER", "battery" ).strip().lower()
    )

    if controller_choice == "sim":
        ev_controller = SimEVController( evcc_config )
        logger.info( "Using stock SimEVController" )

    elif controller_choice == "battery_csv" or (
            controller_choice == "battery" and profile_path
    ):
        profile = CsvProfile( profile_path ) if profile_path else None
        ev_controller = BatterySimEVController(
            evcc_config, profile=profile, max_steps=max_steps
            )
        logger.info(
            f"Using BatterySimEVController with CsvProfile: "
            f"{profile_path or 'default'}"
            )

    else:
        # Default: live SimulatedBattery as the BatteryProfile.
        # The battery model lives entirely in this EVCC process — the SECC
        # has no battery model; it reads SoC from the ISO 15118 messages.
        target_soc_env = os.environ.get("EVCC_TARGET_SOC", "").strip()
        target_soc = float(target_soc_env) if target_soc_env else 80.0
        battery = SimulatedBattery(
            target_soc=target_soc,
            max_charge_kw=300.0,
            max_discharge_kw=20.0,
        )
        logger.info(f"SimulatedBattery target SoC: {target_soc:.0f}% (EVCC_TARGET_SOC)")

        # Seed an initial charging setpoint so the EV starts drawing power
        # straight away (like a normally plugged-in car), instead of sitting
        # idle at 0 kW waiting for a grid command that only arrives once load
        # appears. The grid then *modulates* this via IEC 104 step commands.
        # Override with EVCC_INIT_SETPOINT_KW (+charge / -discharge).
        init_setpoint_env = os.environ.get( "EVCC_INIT_SETPOINT_KW", "" ).strip()
        init_setpoint_kw = float( init_setpoint_env ) if init_setpoint_env else 17.0
        battery.set_power_setpoint( init_setpoint_kw )
        logger.info(
            f"Seeded SimulatedBattery with initial setpoint "
            f"{init_setpoint_kw:+.1f} kW"
            )
        ev_controller = BatterySimEVController(
            evcc_config, profile=battery, max_steps=max_steps
            )
        logger.info( "Using BatterySimEVController with live SimulatedBattery" )

    await EVCCHandler(
        evcc_config=evcc_config,
        iface=config.iface,
        exi_codec=ExificientEXICodec(),
        ev_controller=ev_controller,
        ).start()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.debug("EVCC program terminated manually")


if __name__ == "__main__":
    run()
