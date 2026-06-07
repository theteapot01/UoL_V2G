"""
run_evcc.py
===========
Custom EVCC launcher for the V2G prototype.

This mirrors iso15118/evcc/main.py from the upstream Josev implementation but
substitutes our BatterySimEVController (whose state of charge follows a real
battery profile) for the stock SimEVController. Keeping this launcher in
code_iso15118_custom/ means the upstream iso15118 tree is not edited to wire in
our controller.

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
from iso15118.shared.exificient_exi_codec import ExificientEXICodec

from battery_ev_controller import BatterySimEVController

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
    max_steps_env = os.environ.get("EVCC_MAX_STEPS", "").strip()
    max_steps = int(max_steps_env) if max_steps_env else None

    await EVCCHandler(
        evcc_config=evcc_config,
        iface=config.iface,
        exi_codec=ExificientEXICodec(),
        ev_controller=BatterySimEVController(evcc_config, max_steps=max_steps),
    ).start()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.debug("EVCC program terminated manually")


if __name__ == "__main__":
    run()
