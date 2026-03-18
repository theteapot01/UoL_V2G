import pandapower as pp
import matplotlib.pyplot as plt

net = pp.create_empty_network()

b1 = pp.create_bus(net, vn_kv=20., name="Bus 1")
b2 = pp.create_bus(net, vn_kv=0.4, name="Bus 2")

pp.create_ext_grid(net, bus=b1, vm_pu=1.02, name="Grid Connection")
pp.create_load(net, bus=b2, p_mw=0.1, q_mvar=0.05, name="Load")

pp.create_transformer(net, hv_bus=b1, lv_bus=b2, std_type="0.4 MVA 20/0.4 kV", name="Trafo")

load_values = [-0.15, -0.10, 0.05, 0.10, 0.25, 0.30, 0.35]

voltages = []
trafo_loadings = []

for p_mw in load_values:
    net.load.at[0, 'p_mw'] = p_mw
    pp.runpp(net)

    vm_pu_b2 = net.res_bus.at[b2, 'vm_pu']
    trafo_loading = net.res_trafo.at[0, 'loading_percent']

    voltages.append(vm_pu_b2)
    trafo_loadings.append(trafo_loading)

    print(f"Load: {p_mw:.2f} MW | Bus 2 Voltage: {vm_pu_b2:.4f} pu | Trafo Loading: {trafo_loading:.1f}%")

# Plot
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