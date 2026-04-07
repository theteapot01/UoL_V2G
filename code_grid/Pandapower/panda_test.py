# ==============================================================================
#  V2G GRID SIMULATION — Power Flow Analysis with PandaPower
# ==============================================================================
#
#  Project:  Vehicle-to-Grid (V2G) Communication Protocol — Grid Code
#  Purpose:  Simulate bidirectional energy flow in a simplified distribution
#            network to evaluate voltage stability and transformer loading
#            under varying EV charging/discharging scenarios.
#
# ------------------------------------------------------------------------------
#  Network Topology
# ------------------------------------------------------------------------------
#
#   [External Grid]  vm_pu=1.02 (slack)
#         |
#      [Bus 1]  10 kV  (HV side)
#         |
#   [Transformer]  0.4 MVA · 10/0.4 kV  (std_type: "0.4 MVA 10/0.4 kV")
#         |
#      [Bus 2]  0.4 kV  (LV side — measurement point)
#         |
#      [line1]  NA2XS2Y 1x185 RM/25 6/10 kV · 0.1 km
#         |
#      [Bus 3]  0.4 kV  ──── [Load]  p=0.1 MW, q=0.05 MVAr
#
#  Note: Negative p_mw load values represent V2G feed-in (EV → Grid).
#        Positive p_mw values represent conventional demand (Grid → EV/Load).
#
# ------------------------------------------------------------------------------
#  Monitored Outputs
# ------------------------------------------------------------------------------
#
#   · Bus 2 voltage       [pu]   — must stay within 0.95–1.05 pu
#   · Transformer loading  [%]   — must stay below 100 %
#   · Line loading         [%]   — indicative, not strictly limited here
#
# ==============================================================================

import pandapower as pp
import matplotlib.pyplot as plt

net = pp.create_empty_network()

# --------------------------------------------------------------
#                   Voltage Setup
# --------------------------------------------------------------

# primary Voltage in kV
v_p = 10.0
# secondary Voltage in kV
v_s = 0.4
# trafo type according to PandaPower
trafo_type = "0.4 MVA 10/0.4 kV"
# line type according to PandaPower
line_type = "NA2XS2Y 1x185 RM/25 6/10 kV"  # "NAYY 4x120 SE"
line_length = 0.5  # in km
# NA2XS2Y 1x185 RM/25 6/10 kV

# --------------------------------------------------------------
#                   Net and Bus Setup
# --------------------------------------------------------------

b1 = pp.create_bus(net, vn_kv=v_p, name="Bus 1")
b2 = pp.create_bus(net, vn_kv=v_s, name="Bus 2")
b3 = pp.create_bus(net, vn_kv=v_s, name="Bus 3")

pp.create_ext_grid(net, bus=b1, vm_pu=1.02, name="Grid Connection")
pp.create_load(net, bus=b3, p_mw=0.1, q_mvar=0.05, name="Load")

pp.create_transformer(net, hv_bus=b1, lv_bus=b2, std_type=trafo_type, name="Trafo")

pp.create_line(
    net,
    from_bus=b2,
    to_bus=b3,
    length_km=line_length,
    std_type=line_type,
    name="line1",
)

# Load values for testing the calculation
load_values = [-0.010, -0.015, -0.020, 0.05, 0.010, 0.025, 0.030, 0.035]

voltages = []
trafo_loadings = []

# --------------------------------------------------------------
#                   Power Flow Calc
# --------------------------------------------------------------

for p_mw in load_values:
    net.load.at[0, "p_mw"] = p_mw
    pp.runpp(net)

    vm_pu_b2 = net.res_bus.at[b2, "vm_pu"]
    trafo_loading = net.res_trafo.at[0, "loading_percent"]
    line_loading = net.res_line.at[0, "loading_percent"]

    voltages.append(vm_pu_b2)
    trafo_loadings.append(trafo_loading)

    print(
        f"Load: {p_mw*1000:.2f} kW | Bus 2 Voltage: {vm_pu_b2:.4f} pu | Trafo Loading: {trafo_loading:.1f}% | Line {line_loading:.1f}%"
    )

# Plot
"""
fig, ax1 = plt.subplots()

ax1.set_xlabel("Load (MW)")
ax1.set_ylabel("Voltage (pu)", color="blue")
ax1.plot(load_values, voltages, color="blue", marker="o", label="Voltage")
ax1.tick_params(axis="y", labelcolor="blue")
ax1.axhline(y=0.95, color="blue", linestyle="--", alpha=0.4, label="Min voltage limit (0.95 pu)")

ax2 = ax1.twinx()  # second y-axis sharing the same x-axis
ax2.set_ylabel("Trafo Loading (%)", color="red")
ax2.plot(load_values, trafo_loadings, color="red", marker="s", label="Trafo Loading")
ax2.tick_params(axis="y", labelcolor="red")
ax2.axhline(y=100, color="red", linestyle="--", alpha=0.4, label="Max trafo limit (100%)")

plt.title("Bus Voltage and Transformer Loading vs Load")
fig.tight_layout()
plt.savefig("grid_results.png")
plt.show()
"""
