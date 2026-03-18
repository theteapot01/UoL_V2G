import pandapower as pp
import pandapower.networks as pn
import matplotlib.pyplot as plt
from pandapower.plotting.plotly import pf_res_plotly
from pandapower.plotting import create_generic_coordinates

# ── 1. Load the CIGRE MV network with PV and Wind ─────────────────────────
net = pn.create_cigre_network_mv(with_der="pv_wind")

print("=== CIGRE MV Network with PV & Wind ===")
print(f"Buses:       {len(net.bus)}")
print(f"Lines:       {len(net.line)}")
print(f"Loads:       {len(net.load)}")
print(f"Generators:  {len(net.sgen)}")

print("\n=== Buses ===")
print(net.bus[['name', 'vn_kv']])

print("\n=== Loads ===")
print(net.load[['name', 'bus', 'p_mw']])

print("\n=== Generators (PV & Wind) ===")
print(net.sgen[['name', 'bus', 'p_mw', 'type']])

# ── 2. Add EV charging station ────────────────────────────────────────────
# CIGRE MV is a 20 kV network — connect to bus 3 (residential feeder)
# and step down to 0.4 kV LV for the EV station
connection_bus = 3

ev_bus = pp.create_bus(net, vn_kv=0.4, name="EV Charging Station")

ev_trafo = pp.create_transformer(
    net,
    hv_bus=connection_bus,
    lv_bus=ev_bus,
    std_type="0.4 MVA 20/0.4 kV",
    name="EV Station Transformer"
)

ev_load = pp.create_load(
    net,
    bus=ev_bus,
    p_mw=0.05,
    q_mvar=0.01,
    name="EV Charging Station"
)

# ── 3. Baseline run ────────────────────────────────────────────────────────
pp.runpp(net)

print("\n=== Baseline Results ===")
print(f"EV Bus Voltage:      {net.res_bus.at[ev_bus, 'vm_pu']:.4f} pu")
print(f"EV Trafo Loading:    {net.res_trafo.at[ev_trafo, 'loading_percent']:.1f}%")
print(f"\nAll Bus Voltages:")
print(net.res_bus[['vm_pu', 'va_degree']].round(4))

# ── 4. Simulate interaction between V2G and renewable generation ───────────
# Key insight: when PV/wind output is HIGH, V2G can absorb excess (charge EVs)
#              when PV/wind output is LOW,  V2G can inject power (discharge EVs)

print("\n=== Renewable + V2G Interaction Scenarios ===")
print(f"{'Scenario':<30} {'PV (MW)':>8} {'Wind (MW)':>10} {'EV (MW)':>8} "
      f"{'Bus3 V (pu)':>12} {'Trafo %':>8}")
print("-" * 82)

scenarios = [
    # (label,                  pv_scale, wind_scale, ev_p_mw)
    ("Night - V2G discharge",    0.0,      0.5,      -0.10),  # no PV, some wind, EVs discharge
    ("Morning - EV charging",    0.3,      0.3,       0.10),  # rising PV, EVs charge
    ("Noon peak PV - EV charge", 1.0,      0.2,       0.10),  # max PV, absorb with EV charging
    ("Noon peak PV - no EV",     1.0,      0.2,       0.00),  # max PV, no EV for comparison
    ("Afternoon - balanced",     0.6,      0.4,       0.05),  # moderate renewables
    ("Evening peak - V2G",       0.0,      0.3,      -0.10),  # no PV, EVs cover evening peak
    ("Storm - high wind + V2G",  0.0,      1.0,      -0.05),  # high wind, EVs absorb excess
]

# Store results for plotting
results = {s[0]: {} for s in scenarios}

# Get baseline PV and wind total capacity for scaling
pv_sgens  = net.sgen[net.sgen['type'] == 'PV']
wind_sgens = net.sgen[net.sgen['type'] == 'WP']  # WP = Wind Power in CIGRE
pv_base_mw   = pv_sgens['p_mw'].values.copy()
wind_base_mw = wind_sgens['p_mw'].values.copy()

create_generic_coordinates(net, overwrite=True)

for label, pv_scale, wind_scale, ev_p_mw in scenarios:
    # Scale PV output
    net.sgen.loc[pv_sgens.index, 'p_mw'] = pv_base_mw * pv_scale

    # Scale Wind output
    net.sgen.loc[wind_sgens.index, 'p_mw'] = wind_base_mw * wind_scale

    # Set EV load (negative = V2G discharge)
    net.load.at[ev_load, 'p_mw'] = ev_p_mw

    pp.runpp(net)

    pv_total   = net.sgen.loc[pv_sgens.index, 'p_mw'].sum()
    wind_total = net.sgen.loc[wind_sgens.index, 'p_mw'].sum()
    vm_bus3    = net.res_bus.at[connection_bus, 'vm_pu']
    trafo_load = net.res_trafo.at[ev_trafo, 'loading_percent']

    print(f"{label:<30} {pv_total:>8.3f} {wind_total:>10.3f} {ev_p_mw:>8.2f} "
          f"{vm_bus3:>12.4f} {trafo_load:>8.1f}%")

    results[label] = {
        'pv': pv_total, 'wind': wind_total, 'ev': ev_p_mw,
        'vm': vm_bus3, 'trafo': trafo_load
    }

    # Save plotly HTML for each scenario
    filename = f"cigre_{label.lower().replace(' ', '_').replace('-', '')}.html"
    pf_res_plotly(net, filename=filename, auto_open=False)

# ── 5. Plot summary ────────────────────────────────────────────────────────
labels      = list(results.keys())
voltages    = [results[l]['vm'] for l in labels]
trafo_loads = [results[l]['trafo'] for l in labels]
ev_vals     = [results[l]['ev'] for l in labels]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

# Voltage plot
colors = ['green' if 0.95 <= v <= 1.05 else 'red' for v in voltages]
ax1.bar(labels, voltages, color=colors, alpha=0.7)
ax1.axhline(y=1.05, color='red', linestyle='--', alpha=0.5, label='Upper limit (1.05 pu)')
ax1.axhline(y=0.95, color='orange', linestyle='--', alpha=0.5, label='Lower limit (0.95 pu)')
ax1.set_ylabel("Bus 3 Voltage (pu)")
ax1.set_title("CIGRE MV Network — V2G + Renewable Interaction")
ax1.legend()
ax1.tick_params(axis='x', rotation=30)

# Trafo loading plot
colors2 = ['red' if t > 100 else 'steelblue' for t in trafo_loads]
ax2.bar(labels, trafo_loads, color=colors2, alpha=0.7)
ax2.axhline(y=100, color='red', linestyle='--', alpha=0.5, label='Overload limit (100%)')
ax2.set_ylabel("EV Transformer Loading (%)")
ax2.legend()
ax2.tick_params(axis='x', rotation=30)

plt.tight_layout()
plt.savefig("cigre_v2g_summary.png", dpi=150, bbox_inches='tight')
plt.show()
print("\nSummary plot saved as cigre_v2g_summary.png")

# Open the last scenario in browser
create_generic_coordinates(net, overwrite=True)
pf_res_plotly(net, auto_open=True)