"""Generate IEEE 14-bus test case CSV files for the PLN state estimation tool.

Loads pandapower's built-in case14, runs a power flow, then writes:
  examples/ieee14/buses.csv
  examples/ieee14/lines.csv
  examples/ieee14/transformers.csv
  examples/ieee14/ext_grids.csv
  examples/ieee14/measurements.csv   (standard CSV format, not SCADA)

Measurements are the power-flow solution values with small Gaussian noise
added to simulate realistic SCADA readings.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
from pathlib import Path

OUT_DIR = Path(__file__).parent / "ieee14"
OUT_DIR.mkdir(exist_ok=True)

RNG = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# Load and solve
# ---------------------------------------------------------------------------
net = pn.case14()
pp.runpp(net, numba=False)
print(f"Power flow converged: {net.converged}")

# ---------------------------------------------------------------------------
# buses.csv
# ---------------------------------------------------------------------------
slack_bus_idx = net.ext_grid["bus"].iloc[0]

bus_rows = []
for idx, row in net.bus.iterrows():
    bus_rows.append({
        "bus_id": idx + 1,
        "name": row["name"] if row["name"] else f"BUS_{idx + 1}",
        "vn_kv": row["vn_kv"],
        "bus_type": 3 if idx == slack_bus_idx else 1,
        "zone": 1,
        "in_service": True,
    })
buses_df = pd.DataFrame(bus_rows)
buses_df.to_csv(OUT_DIR / "buses.csv", index=False)
print(f"Wrote {len(buses_df)} buses")

# ---------------------------------------------------------------------------
# lines.csv
# ---------------------------------------------------------------------------
line_rows = []
for idx, row in net.line.iterrows():
    line_rows.append({
        "line_id": idx + 1,
        "name": row["name"] if row["name"] else f"LINE_{idx + 1}",
        "from_bus": int(row["from_bus"]) + 1,
        "to_bus": int(row["to_bus"]) + 1,
        "length_km": row["length_km"],
        "r_ohm_per_km": row["r_ohm_per_km"],
        "x_ohm_per_km": row["x_ohm_per_km"],
        "c_nf_per_km": row["c_nf_per_km"],
        "max_i_ka": row["max_i_ka"],
        "parallel": int(row.get("parallel", 1)),
        "in_service": bool(row["in_service"]),
    })
lines_df = pd.DataFrame(line_rows)
lines_df.to_csv(OUT_DIR / "lines.csv", index=False)
print(f"Wrote {len(lines_df)} lines")

# ---------------------------------------------------------------------------
# transformers.csv
# ---------------------------------------------------------------------------
trafo_rows = []
for idx, row in net.trafo.iterrows():
    trafo_rows.append({
        "trafo_id": idx + 1,
        "name": row["name"] if row["name"] else f"TRAFO_{idx + 1}",
        "hv_bus": int(row["hv_bus"]) + 1,
        "lv_bus": int(row["lv_bus"]) + 1,
        "sn_mva": row["sn_mva"],
        "vn_hv_kv": row["vn_hv_kv"],
        "vn_lv_kv": row["vn_lv_kv"],
        "vk_percent": row["vk_percent"],
        "vkr_percent": row["vkr_percent"],
        "pfe_kw": row["pfe_kw"],
        "i0_percent": row["i0_percent"],
        "shift_degree": row["shift_degree"],
        "tap_pos": int(row["tap_pos"]) if not pd.isna(row["tap_pos"]) else 0,
        "in_service": bool(row["in_service"]),
    })
trafos_df = pd.DataFrame(trafo_rows)
trafos_df.to_csv(OUT_DIR / "transformers.csv", index=False)
print(f"Wrote {len(trafos_df)} transformers")

# ---------------------------------------------------------------------------
# ext_grids.csv
# ---------------------------------------------------------------------------
eg_rows = []
for idx, row in net.ext_grid.iterrows():
    eg_rows.append({
        "ext_grid_id": idx + 1,
        "name": f"SLACK_BUS{int(row['bus']) + 1}",
        "bus": int(row["bus"]) + 1,
        "vm_pu": row["vm_pu"],
        "va_degree": row.get("va_degree", 0.0) if "va_degree" in row.index else 0.0,
        "in_service": True,
    })
eg_df = pd.DataFrame(eg_rows)
eg_df.to_csv(OUT_DIR / "ext_grids.csv", index=False)
print(f"Wrote {len(eg_df)} ext_grids")

# ---------------------------------------------------------------------------
# measurements.csv  – standard CSV (not SCADA) format
# measurement element index for lines/trafos is 0-based row index
# measurement element index for buses is the bus_id (1-based) from buses.csv
# ---------------------------------------------------------------------------
meas_rows = []
mid = 0

# Voltage at every bus
for bus_idx, row in net.res_bus.iterrows():
    noise = RNG.normal(0, 0.002)
    meas_rows.append({
        "meas_id": mid,
        "name": f"V_bus{bus_idx + 1}",
        "meas_type": "v",
        "element_type": "bus",
        "element": bus_idx + 1,     # bus_id (1-based)
        "value": round(float(row["vm_pu"]) + noise, 5),
        "std_dev": 0.004,
        "side": "",
    })
    mid += 1

# P and Q at every bus (net injection)
for bus_idx, row in net.res_bus.iterrows():
    for sig, col, sdev in [("p", "p_mw", 1.0), ("q", "q_mvar", 1.0)]:
        noise = RNG.normal(0, sdev * 0.2)
        meas_rows.append({
            "meas_id": mid,
            "name": f"{sig.upper()}_bus{bus_idx + 1}",
            "meas_type": sig,
            "element_type": "bus",
            "element": bus_idx + 1,
            "value": round(float(row[col]) + noise, 4),
            "std_dev": sdev,
            "side": "",
        })
        mid += 1

# P, Q, I on every line (from end)
for line_idx, row in net.res_line.iterrows():
    for sig, col, sdev in [
        ("p", "p_from_mw", 1.0),
        ("q", "q_from_mvar", 1.0),
        ("i", "i_from_ka", 0.001),
    ]:
        noise = RNG.normal(0, sdev * 0.1)
        meas_rows.append({
            "meas_id": mid,
            "name": f"{sig.upper()}_line{line_idx + 1}_from",
            "meas_type": sig,
            "element_type": "line",
            "element": line_idx,    # 0-based row index
            "value": round(float(row[col]) + noise, 5),
            "std_dev": sdev,
            "side": "from",
        })
        mid += 1

# P and Q on every line (to end)
for line_idx, row in net.res_line.iterrows():
    for sig, col, sdev in [
        ("p", "p_to_mw", 1.0),
        ("q", "q_to_mvar", 1.0),
    ]:
        noise = RNG.normal(0, sdev * 0.1)
        meas_rows.append({
            "meas_id": mid,
            "name": f"{sig.upper()}_line{line_idx + 1}_to",
            "meas_type": sig,
            "element_type": "line",
            "element": line_idx,
            "value": round(float(row[col]) + noise, 5),
            "std_dev": sdev,
            "side": "to",
        })
        mid += 1

# P and Q on every transformer (HV side)
for trafo_idx, row in net.res_trafo.iterrows():
    for sig, col, sdev in [
        ("p", "p_hv_mw", 1.0),
        ("q", "q_hv_mvar", 1.0),
    ]:
        noise = RNG.normal(0, sdev * 0.1)
        meas_rows.append({
            "meas_id": mid,
            "name": f"{sig.upper()}_trafo{trafo_idx + 1}_hv",
            "meas_type": sig,
            "element_type": "trafo",
            "element": trafo_idx,   # 0-based row index
            "value": round(float(row[col]) + noise, 5),
            "std_dev": sdev,
            "side": "hv",
        })
        mid += 1

meas_df = pd.DataFrame(meas_rows)
meas_df.to_csv(OUT_DIR / "measurements.csv", index=False)
print(f"Wrote {len(meas_df)} measurements")
print(f"\nIEEE 14-bus test case written to: {OUT_DIR.resolve()}")
