# V2G Communication Protocol

A open communication protocol enabling seamless, bidirectional energy exchange between Electric Vehicles (EVs) and the power grid — supporting real-time monitoring, grid stability, and smarter energy management.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Protocol Specification](#protocol-specification)
- [Battery Usage Policy](#battery-usage-policy)
- [Compensation Model](#compensation-model)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

The **V2G (Vehicle-to-Grid) Communication Protocol** defines a standardized interface for EVs to interact with the power grid in both directions:

- **Grid → EV**: Standard charging from the grid
- **EV → Grid**: Feeding stored energy back into the grid during peak demand

By enabling real-time data exchange between EVs and grid operators, this protocol contributes to a more resilient and efficient energy ecosystem — accelerating the integration of renewable energy sources and improving grid stability.

---

## Key Features

- **Bidirectional Energy Flow** — EVs can draw from and deliver power back to the grid
- **Real-Time Data Exchange** — Live monitoring of energy flow, battery state, and grid demand
- **Grid Operator Dashboard Support** — Structured data feeds for grid management systems
- **Battery Protection Limits** — Configurable thresholds to limit V2G usage and protect battery health
- **Compensation Tracking** — Built-in support for logging and calculating EV owner compensation for V2G participation
- **Renewable Integration** — Helps balance intermittent solar/wind supply by using EV batteries as distributed storage
- **Secure Communication** — Authenticated and encrypted messaging between EVs and grid endpoints

---

## Architecture

```
┌─────────────────────┐         ┌──────────────────────────┐
│      EV / EVSE      │◄───────►│     Grid Operator        │
│  - Battery State    │  V2G    │  - Energy Management     │
│  - Charge Level     │ Protocol│  - Demand Forecasting    │
│  - V2G Capability   │         │  - Compensation Ledger   │
└─────────────────────┘         └──────────────────────────┘
          ▲                                  ▲
          │                                  │
          ▼                                  ▼
┌─────────────────────┐         ┌──────────────────────────┐
│   Protocol Layer    │         │     Data & Analytics     │
│  - Message Format   │         │  - Usage Logs            │
│  - Auth & Security  │         │  - Battery Wear Tracking │
│  - Session Mgmt     │         │  - Grid Load Metrics     │
└─────────────────────┘         └──────────────────────────┘
```

The protocol operates over an existing network layer (e.g., OCPP, ISO 15118) and adds V2G-specific extensions for energy negotiation, battery limit enforcement, and compensation logging.

---

## Getting Started

### Prerequisites

- Node.js >= 18 or Python >= 3.10
- A compatible EV simulator or EVSE hardware
- Access to a grid operator test endpoint (or use the bundled mock server)

### Installation

```bash
git clone https://github.com/your-org/v2g-protocol.git
cd v2g-protocol
npm install        # or: pip install -r requirements.txt
```

### Running the Mock Grid Server

```bash
npm run grid:mock
# Grid operator mock running at ws://localhost:8080
```

### Connecting an EV Client

```bash
npm run ev:simulate -- --soc 80 --capacity 75 --v2g-enabled true
```

---

## Protocol Specification

### Message Types

| Message | Direction | Description |
|---|---|---|
| `HANDSHAKE` | EV ↔ Grid | Establish session, exchange capabilities |
| `CHARGE_REQUEST` | EV → Grid | Request power draw from grid |
| `V2G_OFFER` | EV → Grid | Offer available energy back to grid |
| `V2G_ACCEPT` | Grid → EV | Confirm V2G session, specify rate |
| `ENERGY_STATUS` | EV → Grid | Real-time battery state update |
| `GRID_SIGNAL` | Grid → EV | Demand signal (charge/discharge/hold) |
| `SESSION_END` | EV ↔ Grid | Terminate session, log final state |
| `COMPENSATION_LOG` | Grid → EV | Record V2G energy delivered + compensation |

### Example Message (JSON)

```json
{
  "type": "V2G_OFFER",
  "timestamp": "2025-06-14T10:32:00Z",
  "ev_id": "EV-DE-001",
  "battery": {
    "soc_percent": 82,
    "available_kwh": 18.5,
    "max_discharge_kwh": 10.0,
    "min_reserve_percent": 20
  },
  "offer": {
    "power_kw": 7.4,
    "duration_minutes": 60
  }
}
```

---

## Battery Usage Policy

To protect EV owner battery health, the protocol enforces configurable limits on V2G usage:

- **Minimum State of Charge (SoC) Reserve** — Default: 20%. The EV will never discharge below this threshold during V2G operation.
- **Maximum V2G Discharge per Session** — Configurable cap (kWh) on how much energy can be drawn per session.
- **Daily Cycle Limit** — Optional setting to cap the number of V2G discharge cycles per day to reduce battery wear.
- **Owner Override** — EV owners can tighten (but not loosen beyond protocol defaults) these limits via their vehicle or app settings.

These parameters are negotiated during the `HANDSHAKE` phase and enforced on both the EV and grid operator side.

---

## Compensation Model

EV owners who participate in V2G are compensated for the energy they contribute. The protocol supports:

- **Energy Rate (€/kWh)** — Grid operator sets the rate during `V2G_ACCEPT`
- **Battery Degradation Fee** — An optional additional compensation component to offset battery wear, calculated from cycle depth and frequency
- **Compensation Ledger** — Each session produces a signed `COMPENSATION_LOG` entry that can be submitted to a billing system or smart contract

> 💡 The specific compensation rates, battery degradation formulas, and payment integrations are implementation-defined and not mandated by this protocol spec.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

Areas of active development:
- Additional transport layer adapters (ISO 15118-20, OCPP 2.0.1)
- Battery degradation modeling
- Smart contract integration for automated compensation
- Grid operator dashboard reference implementation

---

## License

This project is licensed under the [MIT License](LICENSE).

---

> **Note:** This project is in active development. Protocol message formats and APIs are subject to change prior to a stable v1.0 release.
# UoL_V2G
