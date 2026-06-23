# UoL V2G Communication Protocol Prototype

A Python prototype for bidirectional Vehicle-to-Grid (V2G) energy exchange. Two Raspberry Pis communicate over three industrial protocols to enable real-time monitoring and grid-driven control of EV charging and discharge.

---

## Overview

Two physical nodes run concurrently:

| Node | Role | Protocols |
|------|------|-----------|
| **Grid Pi** | Grid operator | IEC 104 client + OCPP 2.1 server + web dashboard |
| **Charger Pi** | EV charger (SECC) + EV simulator (EVCC) | IEC 104 server + OCPP 2.1 client + ISO 15118 |

The grid Pi runs a pandapower load-flow on a 3-bus CIGRE network every second and sends HIGHER/LOWER step commands over IEC 104. The charger Pi translates those commands into EVSE charge-loop limits that the EV respects via ISO 15118 DC BPT.

---

## Tech Stack

- **Language**: Python 3.11 (`c104` has issues on 3.13+)
- **Protocols**:
  - **IEC 60870-5-104** (`c104`): SCADA link — grid operator controls charger
  - **OCPP 2.1** (`ocpp` + `websockets`): Charge point telemetry to CPMS
  - **ISO 15118-20 DC BPT** (Josev + custom controllers): In-cable EV↔EVSE negotiation
- **Key libraries**: `pandapower`, `fastapi`, `uvicorn`, `asyncio`

---

## Project Structure

```text
.
├── grid.py                     # Grid Pi entry point (IEC 104 client + OCPP server + dashboard)
├── charger.py                  # Simple mode charger entry point (no ISO 15118)
├── config.py                   # Shared config: IPs, ports, IOAs
├── code_battery_sim/           # Battery models and CSV discharge profiles
├── code_charger/iso15118/      # Josev ISO 15118 submodule (unmodified)
├── code_cpms/                  # OCPP charge point and central system
├── code_grid/                  # IEC 104 client, pandapower grid model, web dashboard
├── code_iso15118_custom/       # Custom ISO 15118 controllers and launchers
│   ├── charger_state.py        # SharedState singleton (bridges all three protocols)
│   ├── telemetry_evse_controller.py  # SECC: forwards telemetry, relays grid setpoint
│   ├── battery_ev_controller.py      # EVCC: drives SoC from battery model
│   ├── run_secc.py             # Charger Pi launcher (full mode)
│   └── run_evcc.py             # EV simulator launcher (full mode)
└── code_ev/                    # Placeholder (empty)
```

---

## Requirements

- Python 3.11
- `pip install ocpp c104 websockets pandapower pandas fastapi uvicorn`
- `c104` may require C++ build tools on some platforms

For ISO 15118 full mode, the Josev submodule needs Poetry:
```bash
cd code_charger/iso15118
poetry install
cd iso15118/shared/pki && ./create_certs.sh -v iso-2   # one-time
```

---

## Running

### Simple mode (IEC 104 + OCPP, no ISO 15118)

```bash
# Grid Pi
python grid.py
# Web dashboard available at http://<grid-pi-ip>:8080

# Charger Pi
python charger.py
```

### Full ISO 15118 mode

```bash
# Charger Pi — SECC (ISO 15118 server + IEC 104 server + OCPP client)
cd code_charger/iso15118
PYTHONPATH=/path/to/UoL_V2G/code_iso15118_custom \
    poetry run python /path/to/UoL_V2G/code_iso15118_custom/run_secc.py

# Charger Pi — EVCC (EV battery simulator)
cd code_charger/iso15118
PYTHONPATH=/path/to/UoL_V2G/code_iso15118_custom \
    poetry run python /path/to/UoL_V2G/code_iso15118_custom/run_evcc.py
```

EVCC environment variables:
| Variable | Default | Effect |
|----------|---------|--------|
| `EVCC_CONTROLLER` | `battery` | Battery model (`battery`, `battery_csv`, `sim`) |
| `EVCC_PROFILE_PATH` | built-in LFP | Path to CSV profile override |
| `EVCC_MAX_STEPS` | unlimited | Cap charge loop iterations |
| `EVCC_INIT_SETPOINT_KW` | `17.0` | Initial charge power before first IEC 104 command |

---

## Configuration

All network addresses and IOAs are in `config.py` (`Config` dataclass, frozen):

| Field | Default | Meaning |
|-------|---------|---------|
| `IP_ADDRESS` | — | Charger Pi IP (IEC 104 server) |
| `OCPP_SERVER` | — | `"<ip>:<port>"` of the grid Pi |
| `PORT` | 2404 | IEC 104 standard port |
| `COMMON_ADDRESS` | 47 | IEC 104 common address |

IEC 104 IOA map:

| IOA | Direction | Meaning |
|-----|-----------|---------|
| 11 | charger → grid | Active power [kW] |
| 12 | grid → charger | Step command (HIGHER / LOWER) |
| 13 | charger → grid | State of Charge [%] |
| 14 | charger → grid | Temperature [°C] (placeholder) |

Cross-subnet OCPP tunnel:
```bash
ssh -L 9000:localhost:9000 <user>@<grid-pi-host> -N
```

---

## Web Dashboard

Served at `http://<grid-pi-ip>:8080` by the grid Pi. Displays:

- **Power Flow** — IEC 104 active power + OCPP power/energy
- **State of Charge** — SoC from IEC 104 and OCPP
- **Grid Health** — bus voltage, transformer loading from pandapower
- **Power History** — 60 s rolling chart
- **Protocol Timing** — IEC 104 read, pandapower, and transmit latencies
- **ISO 15118 Charge Loop** — EV voltage/current/power, EVSE max charge/discharge limits
- **Grid Demand Control** — Auto / Force V2G / Force Charge buttons
- **Command Log** — last 20 IEC 104 commands with source

---

## Known WIPs / TODOs

- `code_ev/` directory is empty (placeholder).
- ISO 15118 integration into `charger.py` is incomplete — full mode uses `run_secc.py` instead.
- Temperature IOA 14 returns a hardcoded 25.0 °C placeholder.
- SoC-floor enforcement in `on_step_command` is a TODO — the floor is currently only enforced in `SimulatedBattery.tick()` on the EVCC side.
- EVSE limits from `DC_ChargeLoopRes` back to `BatterySimEVController.update_evse_limits()` require a hook in the Josev state machine.

---

## License

TODO: Add license information.
