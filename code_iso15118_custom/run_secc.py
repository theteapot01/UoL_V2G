"""
run_secc.py
===========
Custom SECC launcher for the V2G prototype.

This mirrors iso15118/secc/main.py from the upstream Josev implementation but
integrates the TelemetryEVSEController and concurrently runs the IEC 104 server
and OCPP client. Keeping this launcher in code_iso15118_custom/ means the
upstream iso15118 tree is not edited to wire in our custom controllers and
parallel services.

Run from the iso15118 project root (so the .env symlink and relative
SECC_CONFIG_PATH resolve), with this directory on PYTHONPATH:

    cd ~/UoL_V2G/code_charger/iso15118
    PYTHONPATH=/home/pi/UoL_V2G/code_iso15118_custom \
        poetry run python /home/pi/UoL_V2G/code_iso15118_custom/run_secc.py

An optional SECC config file path may be passed as the first argument, exactly
as with the upstream main.py.
"""

import asyncio
import logging
import sys

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from iso15118.secc import SECCHandler
from iso15118.secc.secc_settings import Config
from iso15118.shared.exificient_exi_codec import ExificientEXICodec

from telemetry_evse_controller import TelemetryEVSEController
from simulated_battery import SimulatedBattery
from charger_state import state as shared_state

from code_grid.iecc104_server import run_iec104_server
from code_cpms.ocpp_charge_point_2 import run_ocpp_client

logger = logging.getLogger(__name__)


async def main():
    config = Config()
    config.load_envs()
    if len(sys.argv) > 1:
        secc_config_file_path = sys.argv[1]
        if secc_config_file_path:
            config.secc_config_file_path = secc_config_file_path

    # Initialize the battery model and wire it into the shared state
    # so the IEC 104 server can apply grid commands to it.
    battery = SimulatedBattery(target_soc=80.0)
    shared_state.battery = battery
    logger.info("Live SimulatedBattery initialized in shared state")

    # Initialize the EVSE controller
    evse_controller = await TelemetryEVSEController.create()
    logger.info("Using TelemetryEVSEController")

    await asyncio.gather(
        SECCHandler(
            exi_codec=ExificientEXICodec(),
            evse_controller=evse_controller,
            config=config
        ).start(config.iface),
        run_iec104_server(),
        run_ocpp_client(),
    )


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Closing connections...")
    except Exception as e:
        logger.exception(f"SECC program terminated with error: {e}")


if __name__ == "__main__":
    run()
