import pandapower as pp
import pandapower.networks as pn
from pandapower.plotting.plotly import pf_res_plotly
from pandapower.plotting.plotly import vlevel_plotly

# ── 1. Load the real Oberrhein medium voltage network ──────────────────────
net = pn.mv_oberrhein()

# Inspect the network so we can pick a sensible connection point
print("=== Network Overview ===")
print(f"Buses:        {len(net.bus)}")
print(f"Lines:        {len(net.line)}")
print(f"Loads:        {len(net.load)}")
print(f"Voltage levels (kV): {sorted(net.bus.vn_kv.unique())}")

print("\n=== First 10 Buses ===")
print(net.bus[['name', 'vn_kv']].head(10))

# ── 2. Pick a MV bus to connect our station to ────────────────────────────
# We'll connect to bus index 1 (a typical 20 kV MV bus in this network).
# After running the script once you can print net.bus to pick a better one.
connection_bus = 1
connection_vn_kv = net.bus.at[connection_bus, 'vn_kv']
print(f"\nConnecting EV station to Bus {connection_bus} "
      f"({net.bus.at[connection_bus, 'name']}, {connection_vn_kv} kV)")

# ── 3. Add a new LV bus for the EV charging station ───────────────────────
ev_bus = pp.create_bus(net, vn_kv=0.4, name="EV Charging Station Bus")

# ── 4. Connect it via a transformer (MV → LV) ─────────────────────────────
# The Oberrhein network is MV (20 kV), so we step down to 0.4 kV LV
ev_trafo = pp.create_transformer(
    net,
    hv_bus=connection_bus,
    lv_bus=ev_bus,
    std_type="0.4 MVA 20/0.4 kV",
    name="EV Station Transformer"
)

# ── 5. Add the EV charging station as a load ──────────────────────────────
# Positive p_mw = charging (drawing from grid)
# Negative p_mw = V2G discharging (feeding back into grid)
ev_load = pp.create_load(
    net,
    bus=ev_bus,
    p_mw=0.05,       # 50 kW charging load (e.g. ~5 EVs at 10 kW each)
    q_mvar=0.01,
    name="EV Charging Station"
)

# ── 6. Run power flow ──────────────────────────────────────────────────────
pp.runpp(net)


print("\n=== EV Station Bus Result ===")
print(f"Voltage at EV bus: {net.res_bus.at[ev_bus, 'vm_pu']:.4f} pu")

print("\n=== EV Station Transformer Result ===")
print(f"Transformer loading: {net.res_trafo.at[ev_trafo, 'loading_percent']:.1f}%")

# ── 7. Simulate V2G: sweep from full charge to full discharge ──────────────
print("\n=== V2G Sweep: Charging → Discharging ===")
print(f"{'Mode':<20} {'p_mw':>8} {'EV Bus Voltage':>16} {'Trafo Loading':>14}")
print("-" * 62)

scenarios = [
    ("Full Charging",     0.10),
    ("Half Charging",     0.05),
    ("Idle",              0.00),
    ("Half Discharging", -0.05),
    ("Full Discharging", -0.10),
]

for label, p_mw in scenarios:
    net.load.at[ev_load, 'p_mw'] = p_mw
    pp.runpp(net)

    vm   = net.res_bus.at[ev_bus, 'vm_pu']
    load = net.res_trafo.at[ev_trafo, 'loading_percent']
    print(f"{label:<20} {p_mw:>8.2f} {vm:>16.4f} pu {load:>12.1f}%")

    # Save each scenario as its own HTML file
    filename = f"network_{label.lower().replace(' ', '_')}.html"
    pf_res_plotly(net, filename=filename, auto_open=False)
    print(f"  → saved {filename}")

# Open just the last scenario in the browser
pf_res_plotly(net, auto_open=True)