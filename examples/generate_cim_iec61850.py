#!/usr/bin/env python3
"""
generate_cim_iec61850.py — PLN Jamali SE network file generator
================================================================
Produces three standards-compliant output files:

  examples/cim/network_EQ.xml
      IEC 61970-552 / CGMES 3.0 Equipment Profile (EQ)
      Substation hierarchy, ConnectivityNodes (node-breaker),
      BusbarSections, Breakers, ACLineSegments,
      PowerTransformers, ExternalNetworkInjection

  examples/cim/network_TP.xml
      IEC 61970-552 / CGMES 3.0 Topology Profile (TP)
      TopologicalNodes + TopologicalIsland
      (depends on EQ profile)

  examples/iec61850/measurements_IEC61850.xml
      Harmonized CIM Measurement + IEC 61850 ACSI addressing
      MMXU / CSWI LNodes, Analog + AnalogValue,
      MeasurementValueSource (IEC 61850-8-1 server)

Run from the project root (state_estimation_for_pln/):
    python examples/generate_cim_iec61850.py

mRID scheme:
    UUID5(RFC-4122-DNS-namespace, "pln.co.id/jamali-se/{tag}")
    → fully deterministic; re-running produces identical files.
"""

from __future__ import annotations
import math
import uuid
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# 1.  Deterministic mRID factory
# ══════════════════════════════════════════════════════════════════════════════
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")   # RFC 4122 DNS namespace


def m(tag: str) -> str:
    """Return a UUID5 mRID string for the given logical tag."""
    return str(uuid.uuid5(_NS, f"pln.co.id/jamali-se/{tag}"))


def uri(tag: str) -> str:
    return f"urn:uuid:{m(tag)}"


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Physical constants (SI)
# ══════════════════════════════════════════════════════════════════════════════
OMEGA = 2.0 * math.pi * 50.0   # 50 Hz angular frequency (rad/s)

# ══════════════════════════════════════════════════════════════════════════════
# 3.  Network data  (mirrors buses/lines/transformers/switches CSV files)
# ══════════════════════════════════════════════════════════════════════════════

# Substations: (code, full_name, iec61850_b1, ied_name)
SUBS = [
    ("CIRATA",   "GI Cirata",   "0ADCIR", "IED_CIR"),
    ("CIBINONG", "GI Cibinong", "0ADCBN", "IED_CBN"),
    ("DEPOK",    "GI Depok",    "0ADDPK", "IED_DPK"),
    ("GANDUL",   "GI Gandul",   "0ADGND", "IED_GND"),
    ("BEKASI",   "GI Bekasi",   "0ADBKS", "IED_BKS"),
    ("BOGOR",    "GI Bogor",    "0ADBGR", "IED_BGR"),
]
SUB_B1:  dict[str, str] = {code: b1  for code, _, b1,  _   in SUBS}   # code→b1
SUB_IED: dict[str, str] = {code: ied for code, _, _,   ied in SUBS}   # code→ied
B1_LD:   dict[str, str] = {b1: f"LD_{b1}" for _, _, b1, _ in SUBS}   # b1→LD name

# Buses: bus_id → (sub_code, vn_kv, name)
BUSES: dict[int, tuple[str, int, str]] = {
     1: ("CIRATA",   150, "CIRATA_150"),
     2: ("CIBINONG", 150, "CIBINONG_150"),
     3: ("DEPOK",    150, "DEPOK_150"),
     4: ("GANDUL",   150, "GANDUL_150"),
     5: ("CIBINONG",  20, "CIBINONG_20"),
     6: ("DEPOK",     20, "DEPOK_20"),
     7: ("BEKASI",   150, "BEKASI_150"),
     8: ("BEKASI",    20, "BEKASI_20"),
     9: ("BOGOR",    150, "BOGOR_150"),
    10: ("BOGOR",     20, "BOGOR_20"),
}

# Lines: (id, name, from_bus, to_bus, length_km, r_Ω/km, x_Ω/km, c_nF/km, parallel)
LINES = [
    (1, "CIRATA-CIBINONG",  1, 2, 45.0,  0.0603, 0.3511, 8.50, 1),
    (2, "CIBINONG-DEPOK",   2, 3, 32.0,  0.0603, 0.3511, 8.50, 2),
    (3, "DEPOK-GANDUL",     3, 4, 18.5,  0.0603, 0.3511, 8.50, 1),
    (4, "CIBINONG-BEKASI",  2, 7, 40.0,  0.0603, 0.3511, 8.50, 1),
    (5, "DEPOK-BOGOR",      3, 9, 28.0,  0.0603, 0.3511, 8.50, 1),
    (6, "GANDUL-BEKASI",    4, 7, 22.0,  0.0603, 0.3511, 8.50, 1),
]

# Transformers: (id, name, hv_bus, lv_bus, sn_MVA, vn_hv_kV, vn_lv_kV,
#                vk_%, vkr_%, pfe_kW, i0_%, shift_deg)
TRAFOS = [
    (1, "T_CIBINONG_1", 2, 5,  60, 150, 20, 12.5, 0.35, 60, 0.12, 0),
    (2, "T_DEPOK_1",    3, 6, 100, 150, 20, 12.5, 0.35, 80, 0.10, 0),
    (3, "T_BEKASI_1",   7, 8,  60, 150, 20, 12.5, 0.35, 60, 0.12, 0),
    (4, "T_BOGOR_1",    9,10,  60, 150, 20, 12.5, 0.35, 60, 0.12, 0),
]

# Switches: (id, name, bus_id, element_0idx, et='l'|'t')
SWITCHES = [
    (1, "CB_CIRATA_L1",   1, 0, "l"),
    (2, "CB_CIBINONG_L1", 2, 0, "l"),
    (3, "CB_CIBINONG_L2", 2, 1, "l"),
    (4, "CB_DEPOK_L2",    3, 1, "l"),
    (5, "CB_CIBINONG_T1", 2, 0, "t"),
    (6, "CB_DEPOK_T2",    3, 1, "t"),
]

# SCADA measurements: (b1, b3, signal, value, quality)
MEASUREMENTS = [
    ("0ADCIR", "BUSBAR", "V",  153.00, "act"),
    ("0ADCIR", "BUSBAR", "P", -155.20, "act"),
    ("0ADCIR", "BUSBAR", "Q",  -32.40, "act"),
    ("0ADCIR", "7KSGN1", "P",   82.10, "act"),
    ("0ADCIR", "7KSGN1", "Q",   16.30, "act"),
    ("0ADCIR", "7KSGN1", "I",  314.50, "act"),
    ("0ADCBN", "BUSBAR", "V",  152.03, "act"),
    ("0ADCBN", "BUSBAR", "P",   10.50, "act"),
    ("0ADCBN", "BUSBAR", "Q",    3.20, "act"),
    ("0ADCBN", "7KSGN1", "P",   80.55, "act"),
    ("0ADCBN", "7KSGN1", "Q",   14.90, "act"),
    ("0ADCBN", "7KDPK1", "P",   45.60, "act"),
    ("0ADCBN", "7KDPK1", "Q",    9.80, "act"),
    ("0ADCBN", "7KDPK1", "I",  178.00, "act"),
    ("0ADDPK", "BUSBAR", "V",  151.47, "act"),
    ("0ADDPK", "BUSBAR", "P",    8.30, "act"),
    ("0ADDPK", "BUSBAR", "Q",    2.10, "act"),
    ("0ADDPK", "7KGND1", "P",   18.20, "act"),
    ("0ADDPK", "7KGND1", "Q",    3.90, "act"),
    ("0ADGND", "BUSBAR", "V",  151.07, "act"),
    ("0ADCBN", "7KBKS1", "P",   22.40, "act"),
    ("0ADCBN", "7KBKS1", "Q",    5.60, "act"),
    ("0ADBKS", "BUSBAR", "V",  150.83, "act"),
    ("0ADDPK", "7KBGR1", "P",   12.80, "act"),
    ("0ADDPK", "7KBGR1", "Q",    2.70, "act"),
    ("0ADBGR", "BUSBAR", "V",  150.60, "act"),
    ("0ADCBN", "T1HV",   "P",   41.20, "act"),
    ("0ADCBN", "T1HV",   "Q",    8.40, "act"),
    ("0ADDPK", "T2HV",   "P",   68.90, "act"),
    ("0ADDPK", "T2HV",   "Q",   14.20, "act"),
    ("0ADBKS", "T3HV",   "P",   38.50, "act"),
    ("0ADBKS", "T3HV",   "Q",    7.80, "act"),
]

# ══════════════════════════════════════════════════════════════════════════════
# 4.  Parameter computations — positive-sequence, SI units
# ══════════════════════════════════════════════════════════════════════════════

def line_params(length_km: float, r_opm: float, x_opm: float,
                c_nfpm: float, parallel: int) -> tuple[float, float, float]:
    """Return (R_Ω, X_Ω, Bch_S) for the equivalent π circuit (total).

    For parallel circuits the equivalent series impedance is halved and the
    shunt susceptance is doubled (both circuits combined into one π model).
    """
    r   = r_opm * length_km / parallel
    x   = x_opm * length_km / parallel
    bch = OMEGA * c_nfpm * 1e-9 * length_km * parallel
    return r, x, bch


def trafo_params(sn_mva: float, vn_hv_kv: float, vk_pct: float,
                 vkr_pct: float, pfe_kw: float,
                 i0_pct: float) -> tuple[float, float, float, float]:
    """Return (R_Ω, X_Ω, G_S, B_S) referred to the HV winding (SI, 3-phase).

    G  = iron-core conductance from no-load (iron) loss
    B  = magnetising susceptance from no-load current
    All values referred to the HV side base voltage.
    """
    z_base = (vn_hv_kv * 1e3) ** 2 / (sn_mva * 1e6)   # Ω
    r  = vkr_pct / 100.0 * z_base
    z  = vk_pct  / 100.0 * z_base
    x  = math.sqrt(max(z * z - r * r, 0.0))
    v2 = (vn_hv_kv * 1e3) ** 2                         # V²
    g  = (pfe_kw * 1e3) / v2                           # S
    y0 = (i0_pct / 100.0) * (sn_mva * 1e6) / v2       # S
    b  = math.sqrt(max(y0 * y0 - g * g, 0.0))         # S (positive)
    return r, x, g, b

# ══════════════════════════════════════════════════════════════════════════════
# 5.  Node-breaker topology: terminal → ConnectivityNode routing
#
#     Default: each equipment terminal connects directly to its bus CN.
#     Each defined Breaker inserts a "feeder CN" between the bus CN and the
#     equipment terminal, creating proper node-breaker connectivity.
# ══════════════════════════════════════════════════════════════════════════════
TERM_CN: dict[str, str] = {}
for _lid, _, _fb, _tb, *_ in LINES:
    TERM_CN[f"LINE{_lid}_T1"] = f"CN_BUS{_fb}"
    TERM_CN[f"LINE{_lid}_T2"] = f"CN_BUS{_tb}"
for _tid, _, _hb, _lb, *_ in TRAFOS:
    TERM_CN[f"TRAFO{_tid}_HV"] = f"CN_BUS{_hb}"
    TERM_CN[f"TRAFO{_tid}_LV"] = f"CN_BUS{_lb}"
for _bid in BUSES:
    TERM_CN[f"BB{_bid}_T1"] = f"CN_BUS{_bid}"
TERM_CN["ENI_T1"] = "CN_BUS1"

# Build feeder CNs and reroute equipment terminals
FEEDER_CN_INFO: list[tuple[str, str, int]] = []  # (cn_tag, name, bus_id)
for _sw_id, _sw_name, _bus_id, _e0, _et in SWITCHES:
    if _et == "l":
        _lid = _e0 + 1
        _, _, _fb, _tb, *_ = LINES[_e0]
        _tkey = f"LINE{_lid}_T1" if _bus_id == _fb else f"LINE{_lid}_T2"
    else:
        _tid = _e0 + 1
        _, _, _hb, _lb, *_ = TRAFOS[_e0]
        _tkey = f"TRAFO{_tid}_HV" if _bus_id == _hb else f"TRAFO{_tid}_LV"
    _fc = f"CN_FEEDER_{_sw_name}"
    FEEDER_CN_INFO.append((_fc, f"Feeder CN {_sw_name}", _bus_id))
    TERM_CN[_tkey] = _fc   # reroute: equipment connects to feeder CN, not bus CN

# ══════════════════════════════════════════════════════════════════════════════
# 6.  VoltageLevel map  (one per substation × nominal voltage)
# ══════════════════════════════════════════════════════════════════════════════
VL_MAP: dict[str, tuple[str, int]] = {}
for _bid, (_sc, _kv, _) in BUSES.items():
    VL_MAP.setdefault(f"VL_{_sc}_{_kv}", (_sc, _kv))

# ══════════════════════════════════════════════════════════════════════════════
# 7.  IEC 61850 routing tables
# ══════════════════════════════════════════════════════════════════════════════

# (b1, b3) → CIM Terminal key (mRID tag in the EQ profile)
MEAS_TERM: dict[tuple[str, str], str] = {
    ("0ADCIR", "BUSBAR"): "BB1_T1",      # Cirata  150 kV busbar
    ("0ADCIR", "7KSGN1"): "LINE1_T1",    # Cirata  → Cibinong, sending end
    ("0ADCBN", "BUSBAR"): "BB2_T1",      # Cibinong 150 kV busbar
    ("0ADCBN", "7KSGN1"): "LINE1_T2",    # Cibinong ← Cirata,  receiving end
    ("0ADCBN", "7KDPK1"): "LINE2_T1",    # Cibinong → Depok,   sending end
    ("0ADCBN", "7KBKS1"): "LINE4_T1",    # Cibinong → Bekasi,  sending end
    ("0ADCBN", "T1HV"):   "TRAFO1_HV",   # T_CIBINONG_1 HV terminal
    ("0ADDPK", "BUSBAR"): "BB3_T1",      # Depok   150 kV busbar
    ("0ADDPK", "7KGND1"): "LINE3_T1",    # Depok   → Gandul,   sending end
    ("0ADDPK", "7KBGR1"): "LINE5_T1",    # Depok   → Bogor,    sending end
    ("0ADDPK", "T2HV"):   "TRAFO2_HV",   # T_DEPOK_1    HV terminal
    ("0ADGND", "BUSBAR"): "BB4_T1",      # Gandul  150 kV busbar
    ("0ADBKS", "BUSBAR"): "BB7_T1",      # Bekasi  150 kV busbar
    ("0ADBKS", "T3HV"):   "TRAFO3_HV",   # T_BEKASI_1   HV terminal
    ("0ADBGR", "BUSBAR"): "BB9_T1",      # Bogor   150 kV busbar
}
# Cirata BUSBAR P/Q belongs to the ExternalNetworkInjection, not the busbar
BUSBAR_PQ_ENI = {"0ADCIR"}

# (b1, b3) → MMXU instance number within the substation Logical Device
MMXU_INST: dict[tuple[str, str], int] = {
    ("0ADCIR", "BUSBAR"): 1, ("0ADCIR", "7KSGN1"): 2,
    ("0ADCBN", "BUSBAR"): 1, ("0ADCBN", "7KSGN1"): 2,
    ("0ADCBN", "7KDPK1"): 3, ("0ADCBN", "7KBKS1"): 4, ("0ADCBN", "T1HV"): 5,
    ("0ADDPK", "BUSBAR"): 1, ("0ADDPK", "7KGND1"): 2,
    ("0ADDPK", "7KBGR1"): 3, ("0ADDPK", "T2HV"):   4,
    ("0ADGND", "BUSBAR"): 1,
    ("0ADBKS", "BUSBAR"): 1, ("0ADBKS", "T3HV"):   2,
    ("0ADBGR", "BUSBAR"): 1,
}

# signal → (IEC61850 DataObject, CIM measurementType, unitSymbol URI, unitMultiplier URI)
CIM_NS = "http://iec.ch/TC57/CIM100#"
SIG_MAP: dict[str, tuple[str, str, str, str]] = {
    "V": ("PhV",    "V", f"{CIM_NS}UnitSymbol.V",   f"{CIM_NS}UnitMultiplier.k"),
    "P": ("TotW",   "P", f"{CIM_NS}UnitSymbol.W",   f"{CIM_NS}UnitMultiplier.M"),
    "Q": ("TotVAr", "Q", f"{CIM_NS}UnitSymbol.VAr", f"{CIM_NS}UnitMultiplier.M"),
    "I": ("A",      "I", f"{CIM_NS}UnitSymbol.A",   f"{CIM_NS}UnitMultiplier.none"),
}

# ══════════════════════════════════════════════════════════════════════════════
# 8.  XML helpers
# ══════════════════════════════════════════════════════════════════════════════
FOOTER = "\n</rdf:RDF>\n"

_RDF_NS = (
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
    '  xmlns:cim="http://iec.ch/TC57/CIM100#"\n'
    '  xmlns:md="http://iec.ch/TC57/61970-552/ModelDescription/1#"\n'
    '  xmlns:xsd="http://www.w3.org/2001/XMLSchema#"'
)


def res(cls: str, tag: str, name: str, body: str = "") -> str:
    """Emit a complete CIM resource block with mRID and name."""
    mid = m(tag)
    return (
        f"\n  <cim:{cls} rdf:about=\"urn:uuid:{mid}\">"
        f"\n    <cim:IdentifiedObject.mRID>{mid}</cim:IdentifiedObject.mRID>"
        f"\n    <cim:IdentifiedObject.name>{name}</cim:IdentifiedObject.name>"
        f"{body}"
        f"\n  </cim:{cls}>\n"
    )


def ref(pred: str, tag: str) -> str:
    """Emit an rdf:resource reference triple."""
    return f'\n    <cim:{pred} rdf:resource="{uri(tag)}"/>'


def lit(pred: str, val, dtype: str = "string") -> str:
    """Emit a typed literal triple."""
    if dtype == "string":
        return f"\n    <cim:{pred}>{val}</cim:{pred}>"
    xsd = f"http://www.w3.org/2001/XMLSchema#{dtype}"
    return f'\n    <cim:{pred} rdf:datatype="{xsd}">{val}</cim:{pred}>'


def raw_ref(pred: str, full_uri: str) -> str:
    """Emit an rdf:resource reference to an arbitrary URI (for CIM enums)."""
    return f'\n    <cim:{pred} rdf:resource="{full_uri}"/>'


# ══════════════════════════════════════════════════════════════════════════════
# 9.  EQ Profile
# ══════════════════════════════════════════════════════════════════════════════

def build_eq() -> str:
    blks: list[str] = []
    blks.append(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  PLN Jamali State Estimation — CIM Equipment Profile (EQ)
  IEC 61970-552 / CGMES 3.0  |  Modelling Authority: PT PLN (Persero)
  Network: Jamali 150/20 kV region (Cirata – Cibinong – Depok – Gandul –
           Bekasi – Bogor), 10 buses, 6 lines, 4 transformers
  Generated by: generate_cim_iec61850.py
  mRID scheme:  UUID5(DNS-NS, "pln.co.id/jamali-se/{{tag}}")
-->
<rdf:RDF {_RDF_NS}>

  <md:FullModel rdf:about="{uri("MODEL_EQ_2026")}">
    <md:Model.profile>http://iec.ch/TC57/ns/CIM/CoreEquipment-EU/2.0</md:Model.profile>
    <md:Model.modelingAuthoritySet>http://www.pln.co.id/</md:Model.modelingAuthoritySet>
    <md:Model.description>PLN Jamali 150/20 kV WLS state estimation test network</md:Model.description>
    <md:Model.version>2.0</md:Model.version>
  </md:FullModel>
""")

    # ── BaseVoltages ─────────────────────────────────────────────────────────
    blks.append("  <!-- ═══ BaseVoltages ═══════════════════════════════════════════ -->")
    for kv in (150, 20):
        blks.append(res("BaseVoltage", f"BV_{kv}kV", f"{kv} kV",
                        lit("BaseVoltage.nominalVoltage",
                            float(kv * 1000), "float")))

    # ── Substations ──────────────────────────────────────────────────────────
    blks.append("\n  <!-- ═══ Substations ═════════════════════════════════════════════ -->")
    for code, full_name, b1, ied in SUBS:
        blks.append(res("Substation", f"SUB_{code}", full_name,
                        lit("IdentifiedObject.description",
                            f"IEC 61850 B1={b1}  IED={ied}")))

    # ── VoltageLevels ─────────────────────────────────────────────────────────
    blks.append("\n  <!-- ═══ VoltageLevels ════════════════════════════════════════════ -->")
    for vl_tag, (sc, kv) in VL_MAP.items():
        blks.append(res("VoltageLevel", vl_tag, f"{sc} {kv} kV",
                        ref("VoltageLevel.Substation", f"SUB_{sc}") +
                        ref("VoltageLevel.BaseVoltage", f"BV_{kv}kV")))

    # ── ConnectivityNodes — bus nodes ─────────────────────────────────────────
    blks.append("\n  <!-- ═══ ConnectivityNodes (bus) ══════════════════════════════════ -->")
    for bid, (sc, kv, bname) in BUSES.items():
        blks.append(res("ConnectivityNode", f"CN_BUS{bid}", bname,
                        ref("ConnectivityNode.ConnectivityNodeContainer",
                            f"VL_{sc}_{kv}")))

    # ── ConnectivityNodes — feeder nodes (between busbar and breaker) ─────────
    blks.append("\n  <!-- ═══ ConnectivityNodes (feeder, breaker-inserted) ═════════════ -->")
    for fc_tag, fc_name, bus_id in FEEDER_CN_INFO:
        sc, kv, _ = BUSES[bus_id]
        blks.append(res("ConnectivityNode", fc_tag, fc_name,
                        ref("ConnectivityNode.ConnectivityNodeContainer",
                            f"VL_{sc}_{kv}")))

    # ── BusbarSections + Terminals ────────────────────────────────────────────
    blks.append("\n  <!-- ═══ BusbarSections ═══════════════════════════════════════════ -->")
    for bid, (sc, kv, bname) in BUSES.items():
        blks.append(res("BusbarSection", f"BB{bid}", f"BB_{bname}",
                        ref("Equipment.EquipmentContainer", f"VL_{sc}_{kv}") +
                        lit("Equipment.inService", "true", "boolean")))
        tkey = f"BB{bid}_T1"
        blks.append(res("Terminal", tkey, f"T_BB{bid}_1",
                        ref("Terminal.ConductingEquipment", f"BB{bid}") +
                        ref("Terminal.ConnectivityNode", TERM_CN[tkey]) +
                        lit("ACDCTerminal.sequenceNumber", 1, "integer") +
                        lit("ACDCTerminal.connected", "true", "boolean")))

    # ── ExternalNetworkInjection — slack bus (Cirata) ─────────────────────────
    blks.append("\n  <!-- ═══ ExternalNetworkInjection (slack bus at Cirata) ═══════════ -->")
    blks.append(res("ExternalNetworkInjection", "ENI_CIRATA", "SLACK_CIRATA",
                    ref("Equipment.EquipmentContainer", "VL_CIRATA_150") +
                    lit("Equipment.inService", "true", "boolean") +
                    lit("ExternalNetworkInjection.p",          -155.20e6, "float") +
                    lit("ExternalNetworkInjection.q",           -32.40e6, "float") +
                    lit("ExternalNetworkInjection.governorSCD",    0.0,   "float")))
    blks.append(res("Terminal", "ENI_T1", "T_ENI_CIRATA_1",
                    ref("Terminal.ConductingEquipment", "ENI_CIRATA") +
                    ref("Terminal.ConnectivityNode", TERM_CN["ENI_T1"]) +
                    lit("ACDCTerminal.sequenceNumber", 1, "integer") +
                    lit("ACDCTerminal.connected", "true", "boolean")))

    # ── Breakers + Terminals ──────────────────────────────────────────────────
    blks.append("\n  <!-- ═══ Breakers (Circuit Breakers) ═════════════════════════════ -->")
    for sw_id, sw_name, bus_id, e0, et in SWITCHES:
        sc, kv, _ = BUSES[bus_id]
        blks.append(res("Breaker", sw_name, sw_name,
                        ref("Equipment.EquipmentContainer", f"VL_{sc}_{kv}") +
                        lit("Equipment.inService",  "true",  "boolean") +
                        lit("Switch.open",         "false",  "boolean") +
                        lit("Switch.normalOpen",   "false",  "boolean") +
                        lit("Switch.retained",     "true",   "boolean")))
        # T1 → bus CN (busbar side)
        blks.append(res("Terminal", f"CB{sw_id}_T1", f"T_{sw_name}_1",
                        ref("Terminal.ConductingEquipment", sw_name) +
                        ref("Terminal.ConnectivityNode", f"CN_BUS{bus_id}") +
                        lit("ACDCTerminal.sequenceNumber", 1, "integer") +
                        lit("ACDCTerminal.connected", "true", "boolean")))
        # T2 → feeder CN (equipment side)
        blks.append(res("Terminal", f"CB{sw_id}_T2", f"T_{sw_name}_2",
                        ref("Terminal.ConductingEquipment", sw_name) +
                        ref("Terminal.ConnectivityNode", f"CN_FEEDER_{sw_name}") +
                        lit("ACDCTerminal.sequenceNumber", 2, "integer") +
                        lit("ACDCTerminal.connected", "true", "boolean")))

    # ── ACLineSegments + Terminals ────────────────────────────────────────────
    blks.append("\n  <!-- ═══ ACLineSegments ═══════════════════════════════════════════ -->")
    for lid, lname, fb, tb, length_km, r_opm, x_opm, c_nfpm, par in LINES:
        r, x, bch = line_params(length_km, r_opm, x_opm, c_nfpm, par)
        sc_from, _, _ = BUSES[fb]
        blks.append(res("ACLineSegment", f"LINE{lid}", lname,
                        ref("Equipment.EquipmentContainer", f"VL_{sc_from}_150") +
                        lit("Equipment.inService",      "true",            "boolean") +
                        lit("Conductor.length",         length_km * 1000.0,"float") +
                        lit("ACLineSegment.r",           round(r,   6),     "float") +
                        lit("ACLineSegment.x",           round(x,   6),     "float") +
                        lit("ACLineSegment.bch",         round(bch, 9),     "float") +
                        lit("ACLineSegment.gch",         0.0,               "float")))
        for seq, tkey in ((1, f"LINE{lid}_T1"), (2, f"LINE{lid}_T2")):
            blks.append(res("Terminal", tkey, f"T_{lname}_{seq}",
                            ref("Terminal.ConductingEquipment", f"LINE{lid}") +
                            ref("Terminal.ConnectivityNode", TERM_CN[tkey]) +
                            lit("ACDCTerminal.sequenceNumber", seq,   "integer") +
                            lit("ACDCTerminal.connected", "true", "boolean")))

    # ── PowerTransformers + Ends + RatioTapChangers + Terminals ──────────────
    blks.append("\n  <!-- ═══ PowerTransformers ════════════════════════════════════════ -->")
    for tid, tname, hb, lb, sn, vn_hv, vn_lv, vk, vkr, pfe, i0, shift in TRAFOS:
        r_hv, x_hv, g_hv, b_hv = trafo_params(sn, vn_hv, vk, vkr, pfe, i0)
        sc_hv, _, _ = BUSES[hb]

        blks.append(res("PowerTransformer", f"TRAFO{tid}", tname,
                        ref("Equipment.EquipmentContainer", f"VL_{sc_hv}_150") +
                        lit("Equipment.inService", "true", "boolean") +
                        lit("PowerTransformer.isPartOfGeneratorUnit", "false", "boolean")))

        # HV end — carries all series impedance and shunt admittance
        blks.append(res("PowerTransformerEnd", f"TRAFO{tid}_HV_END", f"{tname}_HV",
                        ref("PowerTransformerEnd.PowerTransformer", f"TRAFO{tid}") +
                        ref("TransformerEnd.Terminal", f"TRAFO{tid}_HV") +
                        ref("TransformerEnd.BaseVoltage", "BV_150kV") +
                        lit("TransformerEnd.endNumber",           1,              "integer") +
                        lit("PowerTransformerEnd.ratedS",         sn * 1e6,       "float") +
                        lit("PowerTransformerEnd.ratedU",         vn_hv * 1e3,    "float") +
                        lit("PowerTransformerEnd.r",              round(r_hv, 6), "float") +
                        lit("PowerTransformerEnd.x",              round(x_hv, 6), "float") +
                        lit("PowerTransformerEnd.g",              round(g_hv,10), "float") +
                        lit("PowerTransformerEnd.b",              round(b_hv,10), "float") +
                        lit("PowerTransformerEnd.phaseAngleClock", shift,         "integer")))

        # LV end — zero impedance (all referred to HV side)
        blks.append(res("PowerTransformerEnd", f"TRAFO{tid}_LV_END", f"{tname}_LV",
                        ref("PowerTransformerEnd.PowerTransformer", f"TRAFO{tid}") +
                        ref("TransformerEnd.Terminal", f"TRAFO{tid}_LV") +
                        ref("TransformerEnd.BaseVoltage", "BV_20kV") +
                        lit("TransformerEnd.endNumber",           2,           "integer") +
                        lit("PowerTransformerEnd.ratedS",         sn * 1e6,    "float") +
                        lit("PowerTransformerEnd.ratedU",         vn_lv * 1e3, "float") +
                        lit("PowerTransformerEnd.r",              0.0,         "float") +
                        lit("PowerTransformerEnd.x",              0.0,         "float") +
                        lit("PowerTransformerEnd.g",              0.0,         "float") +
                        lit("PowerTransformerEnd.b",              0.0,         "float") +
                        lit("PowerTransformerEnd.phaseAngleClock", 0,          "integer")))

        # On-load tap changer on HV end
        blks.append(res("RatioTapChanger", f"RTC{tid}", f"RTC_{tname}",
                        ref("TapChanger.TransformerEnd", f"TRAFO{tid}_HV_END") +
                        lit("TapChanger.neutralStep",             0,    "integer") +
                        lit("TapChanger.normalStep",              0,    "integer") +
                        lit("TapChanger.lowStep",                -2,    "integer") +
                        lit("TapChanger.highStep",                2,    "integer") +
                        lit("TapChanger.step",                    0.0,  "float") +
                        lit("RatioTapChanger.stepVoltageIncrement", 1.25, "float")))

        # Terminals for HV and LV ends
        for side, seq in (("HV", 1), ("LV", 2)):
            tkey = f"TRAFO{tid}_{side}"
            blks.append(res("Terminal", tkey, f"T_{tname}_{side}",
                            ref("Terminal.ConductingEquipment", f"TRAFO{tid}") +
                            ref("Terminal.ConnectivityNode", TERM_CN[tkey]) +
                            lit("ACDCTerminal.sequenceNumber", seq,   "integer") +
                            lit("ACDCTerminal.connected", "true", "boolean")))

    blks.append(FOOTER)
    return "".join(blks)


# ══════════════════════════════════════════════════════════════════════════════
# 10. TP Profile
# ══════════════════════════════════════════════════════════════════════════════

def build_tp() -> str:
    blks: list[str] = []
    blks.append(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  PLN Jamali State Estimation — CIM Topology Profile (TP)
  IEC 61970-552 / CGMES 3.0  |  Depends on: EQ profile
  Generated by: generate_cim_iec61850.py

  Topology rule: all feeder ConnectivityNodes belong to the same
  TopologicalNode as their parent bus (breakers are closed / in service).
  Bus 1 (CIRATA_150) is the angle-reference node.
-->
<rdf:RDF {_RDF_NS}>

  <md:FullModel rdf:about="{uri("MODEL_TP_2026")}">
    <md:Model.profile>http://iec.ch/TC57/ns/CIM/Topology-EU/2.0</md:Model.profile>
    <md:Model.modelingAuthoritySet>http://www.pln.co.id/</md:Model.modelingAuthoritySet>
    <md:Model.DependentOn rdf:resource="{uri("MODEL_EQ_2026")}"/>
  </md:FullModel>
""")

    # ── TopologicalNodes ─────────────────────────────────────────────────────
    blks.append("  <!-- ═══ TopologicalNodes (one per bus) ══════════════════════════ -->")
    for bid, (sc, kv, bname) in BUSES.items():
        blks.append(res("TopologicalNode", f"TN_BUS{bid}", bname,
                        ref("TopologicalNode.BaseVoltage", f"BV_{kv}kV") +
                        ref("TopologicalNode.ConnectivityNodeContainer",
                            f"VL_{sc}_{kv}")))

    # ── CN → TN membership (bus ConnectivityNodes) ───────────────────────────
    blks.append("\n  <!-- ═══ ConnectivityNode.TopologicalNode associations (bus CNs) ═ -->")
    for bid in BUSES:
        blks.append(
            f'\n  <cim:ConnectivityNode rdf:about="{uri(f"CN_BUS{bid}")}">'
            f'\n    <cim:ConnectivityNode.TopologicalNode'
            f' rdf:resource="{uri(f"TN_BUS{bid}")}"/>'
            f'\n  </cim:ConnectivityNode>\n'
        )

    # ── CN → TN membership (feeder CNs — same TN as parent bus) ─────────────
    blks.append("\n  <!-- ═══ ConnectivityNode.TopologicalNode associations (feeder CNs) -->")
    for fc_tag, _, bus_id in FEEDER_CN_INFO:
        blks.append(
            f'\n  <cim:ConnectivityNode rdf:about="{uri(fc_tag)}">'
            f'\n    <cim:ConnectivityNode.TopologicalNode'
            f' rdf:resource="{uri(f"TN_BUS{bus_id}")}"/>'
            f'\n  </cim:ConnectivityNode>\n'
        )

    # ── TopologicalIsland ────────────────────────────────────────────────────
    blks.append("\n  <!-- ═══ TopologicalIsland ═══════════════════════════════════════ -->")
    ti_body = ref("TopologicalIsland.AngleRefTopologicalNode", "TN_BUS1")
    for bid in BUSES:
        ti_body += ref("TopologicalIsland.TopologicalNodes", f"TN_BUS{bid}")
    blks.append(res("TopologicalIsland", "TI_JAMALI", "JAMALI 150 kV Island", ti_body))

    blks.append(FOOTER)
    return "".join(blks)


# ══════════════════════════════════════════════════════════════════════════════
# 11. IEC 61850 Measurement file
# ══════════════════════════════════════════════════════════════════════════════

def build_m61() -> str:
    blks: list[str] = []
    TIMESTAMP = "2026-03-06T00:00:00Z"

    blks.append(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  PLN Jamali State Estimation — Harmonized CIM/IEC 61850 Measurement File
  IEC 61970-301 CIM Measurement model extended with IEC 61850 ACSI addressing

  Key attributes:
    cim:IdentifiedObject.aliasName  = IEC 61850 ACSI path
                                      [LD]/[LN].[DO].mag.f[FC]
                                      e.g. LD_0ADCIR/MMXU2.TotW.mag.f[MX]
    cim:Measurement.Terminal        = urn:uuid of the Terminal in the EQ profile
                                      → ensures lossless equipment linkage
    cim:AnalogValue.MeasurementValueSource = IEC61850 server mRID
    Units: V in kV (UnitMultiplier.k), P/Q in MW/Mvar (UnitMultiplier.M),
           I in A (UnitMultiplier.none)

  SCADA snapshot: {TIMESTAMP}
  Generated by: generate_cim_iec61850.py
-->
<rdf:RDF {_RDF_NS}>
""")

    # ── MeasurementValueSource (IEC 61850 SCADA server) ──────────────────────
    blks.append("  <!-- ═══ MeasurementValueSource (IEC 61850 server) ════════════════ -->")
    blks.append(res("MeasurementValueSource", "MVS_JAMALI_IED",
                    "PLN Jamali IEC 61850 Server",
                    lit("IdentifiedObject.description",
                        "IEC 61850-8-1 MMS/GOOSE aggregator; "
                        "covers IED_CIR IED_CBN IED_DPK IED_GND IED_BKS IED_BGR")))

    # ── MMXU LNodes (one per unique b1/b3 bay in MEASUREMENTS) ───────────────
    blks.append("\n  <!-- ═══ LNodes — MMXU (electrical telemetry per bay) ═══════════ -->")
    seen_lnodes: set[tuple[str, str]] = set()
    for b1, b3, _sig, _val, _qual in MEASUREMENTS:
        key = (b1, b3)
        if key in seen_lnodes:
            continue
        seen_lnodes.add(key)
        inst     = MMXU_INST.get(key, 99)
        ied_name = SUB_IED.get(next(c for c, _, b, _ in SUBS if b == b1), "IED_UNK")
        ld_name  = B1_LD[b1]
        blks.append(res("LNode", f"LNODE_MMXU_{b1}_{b3}",
                        f"{ld_name}/MMXU{inst}",
                        lit("LNode.lnClass",  "MMXU") +
                        lit("LNode.lnInst",   str(inst)) +
                        lit("LNode.iedName",  ied_name) +
                        lit("LNode.ldInst",   ld_name) +
                        lit("LNode.prefix",   "")))

    # ── CSWI LNodes (one per circuit breaker) ────────────────────────────────
    blks.append("\n  <!-- ═══ LNodes — CSWI (switch control) ══════════════════════════ -->")
    for sw_id, sw_name, bus_id, _e0, _et in SWITCHES:
        sc, _, _ = BUSES[bus_id]
        b1_tag   = SUB_B1[sc]
        ied_name = SUB_IED[sc]
        ld_name  = B1_LD[b1_tag]
        blks.append(res("LNode", f"LNODE_CSWI_{sw_name}",
                        f"{ld_name}/CSWI{sw_id}",
                        lit("LNode.lnClass", "CSWI") +
                        lit("LNode.lnInst",  str(sw_id)) +
                        lit("LNode.iedName", ied_name) +
                        lit("LNode.ldInst",  ld_name) +
                        lit("LNode.prefix",  "") +
                        ref("LNode.Equipment", sw_name)))

    # ── Analog + AnalogValue (one pair per SCADA measurement row) ────────────
    blks.append("\n  <!-- ═══ Analog measurements (CIM + IEC 61850 ACSI addressing) ══ -->")
    for b1, b3, sig, val, qual in MEASUREMENTS:
        # Select the Terminal this measurement is physically associated with
        base_key = MEAS_TERM.get((b1, b3), "BB1_T1")
        if b1 in BUSBAR_PQ_ENI and b3 == "BUSBAR" and sig in ("P", "Q"):
            term_key = "ENI_T1"
        else:
            term_key = base_key

        inst     = MMXU_INST.get((b1, b3), 99)
        ld_name  = B1_LD[b1]
        do_name, mtype, unit_uri, mult_uri = SIG_MAP[sig]
        acsi     = f"{ld_name}/MMXU{inst}.{do_name}.mag.f[MX]"

        a_tag  = f"AN_{b1}_{b3}_{sig}".replace("/", "_")
        av_tag = f"AV_{b1}_{b3}_{sig}".replace("/", "_")

        blks.append(res("Analog", a_tag, f"{b1}/{b3}/{sig}",
                        lit("IdentifiedObject.aliasName",   acsi) +
                        lit("Measurement.measurementType",  mtype) +
                        raw_ref("Measurement.unitSymbol",    unit_uri) +
                        raw_ref("Measurement.unitMultiplier", mult_uri) +
                        ref("Measurement.Terminal", term_key) +
                        lit("Analog.positiveFlowIn", "true", "boolean")))

        blks.append(res("AnalogValue", av_tag, f"AV {b1}/{b3}/{sig}",
                        lit("AnalogValue.value",   float(val), "float") +
                        lit("AnalogValue.quality", qual) +
                        f'\n    <cim:AnalogValue.timeStamp'
                        f' rdf:datatype="http://www.w3.org/2001/XMLSchema#dateTime"'
                        f'>{TIMESTAMP}</cim:AnalogValue.timeStamp>' +
                        ref("AnalogValue.Analog", a_tag) +
                        ref("AnalogValue.MeasurementValueSource", "MVS_JAMALI_IED")))

    blks.append(FOOTER)
    return "".join(blks)


# ══════════════════════════════════════════════════════════════════════════════
# 12. Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    base    = Path(__file__).parent
    cim_dir = base / "cim"
    m61_dir = base / "iec61850"
    cim_dir.mkdir(exist_ok=True)
    m61_dir.mkdir(exist_ok=True)

    eq_path  = cim_dir / "network_EQ.xml"
    tp_path  = cim_dir / "network_TP.xml"
    m61_path = m61_dir / "measurements_IEC61850.xml"

    eq_path.write_text(build_eq(),   encoding="utf-8")
    tp_path.write_text(build_tp(),   encoding="utf-8")
    m61_path.write_text(build_m61(), encoding="utf-8")

    print("CIM / IEC 61850 file generation complete")
    print(f"  EQ  -> {eq_path.relative_to(base.parent)}")
    print(f"  TP  -> {tp_path.relative_to(base.parent)}")
    print(f"  M61 -> {m61_path.relative_to(base.parent)}")

    print("\n── Deterministic mRID reference (UUID5) ──────────────────────")
    print(f"  EQ  model : {m('MODEL_EQ_2026')}")
    print(f"  TP  model : {m('MODEL_TP_2026')}")
    print(f"  {'Bus':5s}  {'mRID':36s}  Name")
    for bid, (_, _, bname) in BUSES.items():
        print(f"  BUS{bid:<3d}  {m(f'CN_BUS{bid}')}  {bname}")
    print(f"  {'Line':5s}  {'mRID':36s}  Name")
    for lid, lname, *_ in LINES:
        print(f"  L{lid:<4d}  {m(f'LINE{lid}')}  {lname}")
    print(f"  {'Trafo':5s}  {'mRID':36s}  Name")
    for tid, tname, *_ in TRAFOS:
        print(f"  T{tid:<4d}  {m(f'TRAFO{tid}')}  {tname}")

    # Parameter summary
    print("\n── Line parameters (positive-sequence, equivalent π circuit) ──")
    print(f"  {'Name':20s}  {'R (Ω)':>10s}  {'X (Ω)':>10s}  {'Bch (µS)':>10s}")
    for lid, lname, fb, tb, lkm, ropm, xopm, cnf, par in LINES:
        r, x, bch = line_params(lkm, ropm, xopm, cnf, par)
        print(f"  {lname:20s}  {r:10.4f}  {x:10.4f}  {bch*1e6:10.4f}")

    print("\n── Transformer parameters (referred to HV, SI) ───────────────")
    print(f"  {'Name':16s}  {'R (Ω)':>8s}  {'X (Ω)':>8s}  "
          f"{'G (nS)':>10s}  {'B (nS)':>10s}")
    for tid, tname, hb, lb, sn, vn_hv, vn_lv, vk, vkr, pfe, i0, _ in TRAFOS:
        r, x, g, b = trafo_params(sn, vn_hv, vk, vkr, pfe, i0)
        print(f"  {tname:16s}  {r:8.4f}  {x:8.4f}  "
              f"{g*1e9:10.4f}  {b*1e9:10.4f}")
