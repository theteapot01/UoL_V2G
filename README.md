# UoL V2G Communication Protocol Prototype

A Python-based simulation environment for bidirectional energy exchange (V2G) between Electric Vehicles (EVs) and the power grid. This repository implements a prototype using standard industrial protocols to enable real-time monitoring and control of EV charging/discharging.

---

## Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Scripts](#scripts)
- [License](#license)

---

## Overview

This project facilitates a "Hardware-in-the-loop" style simulation where:
- **Charger side** (`charger.py`): Acts as an IEC 60870-5-104 Server (Controlled Station) and an OCPP 2.1 Client.
- **Grid side** (`grid.py`): Acts as an IEC 60870-5-104 Client (Controlling Station) and an OCPP 2.1 Server (CSMS).

The system uses battery profiles (located in `code_battery_sim/profiles`) to simulate realistic State of Charge (SoC) and power flow during charging and V2G discharging events.

---

## Tech Stack

- **Language**: Python 3.10+
- **Protocols**:
  - **OCPP 2.1**: For communication between the Charge Point and the Central System.
  - **IEC 60870-5-104**: For grid-level SCADA communication and control.
  - **ISO 15118**: (WIP) Support for EV-to-EVSE communication (see `code_charger/iso15118`).
- **Frameworks/Libraries**:
  - `ocpp`: Python implementation of the Open Charge Point Protocol.
  - `c104`: IEC 60870-5-104 protocol stack.
  - `pandapower`: For power flow simulation and grid analysis.
  - `websockets`: For OCPP transport.
  - `asyncio`: For concurrent execution of protocol handlers.

---

## Project Structure

```text
.
├── charger.py              # Main entry point for the Charger simulation
├── grid.py                 # Main entry point for the Grid/SCADA simulation
├── config.py               # Shared configuration (IPs, Ports, IOAs)
├── code_battery_sim/       # Battery models and discharge profiles (CSV)
├── code_charger/           # ISO 15118 implementation (EVCC/SECC)
├── code_cpms/              # OCPP Central System and Charge Point logic
├── code_ev/                # EV-specific logic (currently empty/WIP)
└── code_grid/              # IEC 104 logic and Pandapower grid simulations
```

---

## Requirements

### Prerequisites
- Python >= 3.10 (Tested up to 3.11, `c104` may have issues on 3.13+)
- `pip` package manager

### Installation
1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd UoL_V2G
   ```
2. Install dependencies:
   ```bash
   pip install ocpp c104 websockets pandapower pandas
   ```
   *Note: `c104` might require C++ build tools on some platforms.*

---

## Getting Started

To run the full simulation, you typically need to start both the Grid and Charger components.

1. **Start the Grid Simulation**:
   ```bash
   python grid.py
   ```
   This starts the OCPP Server and the IEC104 Client.

2. **Start the Charger Simulation**:
   ```bash
   python charger.py
   ```
   This starts the IEC104 Server and the OCPP Client.

---

## Configuration

Settings such as IP addresses, ports, and IEC104 Information Object Addresses (IOAs) are managed in `config.py`.

Key settings in `Config` class:
- `METER_VALUES`: IOA for power flow telemetry (default 11).
- `SOC_VAL`: IOA for battery State of Charge (default 13).
- `IP_ADDRESS`: IP of the charger station.
- `OCPP_SERVER`: WebSocket URL for the Central System.

---

## Scripts

- `code_grid/Pandapower/panda_cigr.py`: Runs grid simulations on CIGRE benchmark networks.
- `code_cpms/ocpp_charge_point_2.py`: Independent OCPP client script for testing.
- `code_cpms/ocpp_central_system_2.py`: Independent OCPP server script for testing.

---

## TODOs

- [ ] Complete integration of `code_charger/iso15118` into the main `charger.py` workflow.
- [ ] Implement real battery protection limits (SoC floor enforcement) in `on_step_command`.
- [ ] Add unit tests for protocol message parsing.
- [ ] Implement a compensation ledger for V2G participation.

---

## License

TODO: Add license information (e.g., MIT, Apache 2.0).
