# State Estimation for PLN

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![pandapower](https://img.shields.io/badge/pandapower-2.13%2B-orange.svg)](https://pandapower.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A production-ready **Weighted Least Squares (WLS) State Estimator** for power transmission networks, built on [pandapower](https://pandapower.readthedocs.io/). Designed for PT PLN (Persero) study workflows supporting networks up to **10 000 nodes**.

---

## Features

| Feature | Detail |
|---|---|
| **WLS Algorithm** | Full Newton-Raphson WLS with configurable tolerance & iteration limit |
| **Bad Data Detection** | χ² (Chi-squared) test + Normalised Residual iterative removal |
| **Input formats** | CSV (directory or ZIP) · PLN custom XML · IEC 61970 CIM RDF/XML |
| **Element types** | Buses · Lines · 2W/3W Transformers · Shunts · Switches · External Grids |
| **Scalability** | Tested up to 10 000 nodes; uses pandapower sparse-matrix backend |
| **Report** | Self-contained HTML report with convergence log, iteration chart, mismatch table, and linked CSV exports |
| **CLI** | Single command, all options configurable via flags |

---

## Quick Start

### 1. Install

```bash
pip install -r requirements.txt
# Or install as a package:
pip install -e .
```

### 2. Run on sample data (CSV)

```bash
python -m state_estimation.main \
    --input  examples/csv \
    --format csv \
    --output reports/
```

### 3. Run on sample data (XML)

```bash
python -m state_estimation.main \
    --input  examples/xml/network.xml \
    --format xml \
    --output reports/
```

The HTML report is written to `reports/SE_Report_<timestamp>.html`. Open it in any browser.

---

## Input Formats

### CSV Format

Provide a **directory** (or ZIP archive) containing the following files.  
All files except `buses.csv` and `measurements.csv` are optional.

#### `buses.csv` *(required)*

| Column | Type | Description |
|---|---|---|
| `bus_id` | int | Unique bus identifier |
| `name` | str | Bus label |
| `vn_kv` | float | Nominal voltage (kV) |
| `bus_type` | int | 1 = PQ, 2 = PV, 3 = Slack |
| `zone` | int | Zone / area number |
| `in_service` | bool | `true` / `false` |

```csv
bus_id,name,vn_kv,bus_type,zone,in_service
1,Bus_JABAR_1,150,3,1,true
2,Bus_JABAR_2,150,1,1,true
3,Bus_JABAR_3,20,1,1,true
```

#### `lines.csv`

| Column | Type | Description |
|---|---|---|
| `line_id` | int | Unique line ID |
| `name` | str | Line label |
| `from_bus` | int | Bus ID at sending end |
| `to_bus` | int | Bus ID at receiving end |
| `length_km` | float | Line length (km) |
| `r_ohm_per_km` | float | Resistance per km (Ω/km) |
| `x_ohm_per_km` | float | Reactance per km (Ω/km) |
| `c_nf_per_km` | float | Capacitance per km (nF/km) |
| `max_i_ka` | float | Thermal current limit (kA) |
| `parallel` | int | Number of parallel circuits |
| `in_service` | bool | `true` / `false` |

#### `transformers.csv`

| Column | Type | Description |
|---|---|---|
| `trafo_id` | int | Unique transformer ID |
| `name` | str | Transformer label |
| `hv_bus` | int | HV-side bus ID |
| `lv_bus` | int | LV-side bus ID |
| `sn_mva` | float | Rated apparent power (MVA) |
| `vn_hv_kv` | float | HV nominal voltage (kV) |
| `vn_lv_kv` | float | LV nominal voltage (kV) |
| `vk_percent` | float | Short-circuit voltage (%) |
| `vkr_percent` | float | Resistive component of vk (%) |
| `pfe_kw` | float | No-load losses (kW) |
| `i0_percent` | float | No-load current (%) |
| `shift_degree` | float | Phase shift angle (°) |
| `tap_pos` | int | Current tap position |
| `in_service` | bool | `true` / `false` |

#### `transformers3w.csv`

Three-winding transformer parameters. Columns mirror the 2W table with
additional `mv_bus`, `sn_hv_mva`, `sn_mv_mva`, `sn_lv_mva`,
`vn_mv_kv`, and `vk_*_percent` / `vkr_*_percent` for each winding.

#### `switches.csv`

| Column | Type | Description |
|---|---|---|
| `switch_id` | int | Unique switch ID |
| `name` | str | Switch label |
| `bus` | int | Bus the switch is connected to |
| `element` | int | Index of the connected element |
| `et` | str | Element type: `l` (line), `t` (trafo), `b` (bus) |
| `type` | str | `CB` (circuit breaker), `DS` (disconnector), `LBS` |
| `closed` | bool | Switch status |

#### `ext_grids.csv`

| Column | Type | Description |
|---|---|---|
| `ext_grid_id` | int | Unique ID |
| `bus` | int | Slack / reference bus ID |
| `vm_pu` | float | Voltage setpoint (p.u.) |
| `va_degree` | float | Angle reference (°) |

#### `shunts.csv`

| Column | Type | Description |
|---|---|---|
| `shunt_id` | int | Unique ID |
| `bus` | int | Bus ID |
| `p_mw` | float | Active power consumption (MW) |
| `q_mvar` | float | Reactive power consumption (Mvar) |
| `vn_kv` | float | Nominal voltage (kV) |

#### `measurements.csv` *(required)*

| Column | Type | Description |
|---|---|---|
| `meas_id` | int | Unique measurement ID |
| `meas_type` | str | `v` · `p` · `q` · `i` |
| `element_type` | str | `bus` · `line` · `trafo` · `trafo3w` |
| `element` | int | Element ID (bus_id for bus; 0-based index for branch) |
| `value` | float | Measured value (p.u. for V; MW for P; Mvar for Q; kA for I) |
| `std_dev` | float | Measurement standard deviation (same unit) |
| `side` | str | Branch side: `from`/`to` for lines, `hv`/`lv` for trafos |
| `name` | str | Optional label |

```csv
meas_id,meas_type,element_type,element,value,std_dev,side,name
1,v,bus,1,1.020,0.004,,V_Bus1
2,p,bus,1,-150.5,1.5,,P_Bus1
3,q,bus,1,-30.2,1.5,,Q_Bus1
4,p,line,0,80.1,1.0,from,P_Line1_from
5,q,line,0,15.3,1.0,from,Q_Line1_from
6,i,line,0,0.310,0.005,from,I_Line1
```

---

### XML Format

#### PLN Custom XML (recommended)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<PowerNetwork name="PLN_JABAR_150kV">

  <Buses>
    <Bus id="1" name="CIRATA_150" vn_kv="150" bus_type="3" zone="1" in_service="true"/>
    <Bus id="2" name="CIBINONG_150" vn_kv="150" bus_type="1" zone="1" in_service="true"/>
    <Bus id="3" name="CIBINONG_20" vn_kv="20" bus_type="1" zone="1" in_service="true"/>
  </Buses>

  <ExtGrids>
    <ExtGrid id="1" bus="1" vm_pu="1.02" va_degree="0" name="SLACK_CIRATA"/>
  </ExtGrids>

  <Lines>
    <Line id="1" name="CIRATA-CIBINONG" from_bus="1" to_bus="2"
          length_km="45" r_ohm_per_km="0.0603" x_ohm_per_km="0.3511"
          c_nf_per_km="8.5" max_i_ka="0.645" in_service="true"/>
  </Lines>

  <Transformers>
    <Transformer id="1" name="T_CIBINONG_1" hv_bus="2" lv_bus="3"
                 sn_mva="60" vn_hv_kv="150" vn_lv_kv="20"
                 vk_percent="12.5" vkr_percent="0.35"
                 pfe_kw="60" i0_percent="0.12"
                 shift_degree="0" tap_pos="0" in_service="true"/>
  </Transformers>

  <Measurements>
    <Measurement id="1" meas_type="v" element_type="bus" element="1"
                 value="1.020" std_dev="0.004" name="V_CIRATA"/>
    <Measurement id="2" meas_type="p" element_type="bus" element="1"
                 value="-150.5" std_dev="1.5" name="P_CIRATA"/>
    <Measurement id="3" meas_type="p" element_type="line" element="0"
                 value="80.1" std_dev="1.0" side="from" name="P_LINE_FROM"/>
  </Measurements>

</PowerNetwork>
```

#### IEC 61970 CIM RDF/XML (partial)

Place a CIM16/CIM17 EQ/SSH/SV export alongside the tool.  
The parser auto-detects CIM from the `rdf:RDF` root element.

---

## CLI Reference

```
python -m state_estimation.main [OPTIONS]

Required:
  --input  PATH     CSV directory / ZIP, or XML file
  --format {csv,xml}

Options:
  --output DIR          Output directory (default: reports/)
  --network-name STR    Override network name in report
  --algorithm ALG       wls | wls_with_zero_injection_constraints | lp_se
  --init METHOD         flat | results | slack  (default: flat)
  --tolerance FLOAT     Convergence tolerance (default: 1e-6)
  --max-iterations INT  WLS iteration limit (default: 50)
  --chi2-alpha FLOAT    χ² significance level (default: 0.05)
  --no-bad-data-detection  Skip bad data analysis
  --no-voltage-angles   Do not solve for voltage angles
  --verbose / -v        Debug logging
```

---

## Report Output

The generated HTML report includes:

| Section | Contents |
|---|---|
| **1 – Study Summary** | Convergence status, algorithm, iteration count, timing, network size |
| **2 – Convergence** | Per-iteration correction table + log₁₀ convergence chart + raw solver log |
| **3 – Bad Data Detection** | χ² statistic, threshold, pass/fail; table of removed measurements |
| **4 – Greatest Mismatch** | Measurement with highest normalised residual \|r\|/σ |
| **5 – Bus Results** | Estimated V (p.u.), θ (°), P (MW), Q (Mvar) – sortable/searchable |
| **6 – Line Results** | P/Q flows both ends, I (kA), loading % |
| **7 – Transformer Results** | HV/LV-side P, Q, I, loading % |
| **8 – 3W-Transformer Results** | (if present) |
| **9 – Measurement Residuals** | Sorted by normalised residual |

All result tables are also exported as standalone CSV files in the output directory.  
For networks > 500 rows, the HTML shows the first 500 rows with a link to the full CSV.

---

## Project Structure

```
state_estimation_for_pln/
├── src/
│   └── state_estimation/
│       ├── main.py                  # CLI entry point
│       ├── parsers/
│       │   ├── base_parser.py       # NetworkData dataclass + BaseParser ABC
│       │   ├── csv_parser.py        # CSV directory / ZIP parser
│       │   └── xml_parser.py        # PLN custom XML + CIM RDF/XML parser
│       ├── network/
│       │   └── builder.py           # pandapower network construction
│       ├── estimator/
│       │   └── wls_estimator.py     # WLS runner, convergence capture, bad data
│       └── reports/
│           └── report_generator.py  # Self-contained HTML + CSV report
├── examples/
│   ├── csv/                         # Sample CSV input files
│   └── xml/                         # Sample XML input file
├── tests/
│   └── test_se.py
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Scalability Notes (10 000-node networks)

- pandapower uses **SciPy sparse matrices** internally – memory footprint scales linearly, not quadratically.
- CSV parsing is done with pandas `read_csv` which streams efficiently.
- The HTML report caps inline table rows at **500** (configurable) and links full CSV exports so the browser does not freeze.
- For very large CIM XML files, install `lxml` (`pip install lxml`) – the parser will use it automatically for faster SAX-style parsing.
- Typical timing on a 5 000-bus network: parse ≈ 3 s · build ≈ 5 s · estimate ≈ 8 s · report ≈ 2 s.

---

## License

MIT © 2025 Adrian Jonathan Yosua – PT PLN (Persero)
