"""XML parser for the PLN State Estimation tool.

Supports two XML schemas:

1. **PLN Custom XML** (recommended for new inputs) – a straightforward
   element-attribute mapping that mirrors the CSV column layout.

2. **IEC 61970 CIM (subset)** – partial support for CIM16/CIM17 RDF/XML
   exports from EMS/SCADA systems (ACLineSegment, PowerTransformer,
   ConnectivityNode, Analog / Measurement classes).

The parser auto-detects the schema from the root element tag.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .base_parser import BaseParser, NetworkData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _attr(el: ET.Element, *keys: str, default: Any = None) -> Any:
    """Return the first matching attribute (case-insensitive)."""
    lc = {k.lower(): v for k, v in el.attrib.items()}
    for key in keys:
        if key.lower() in lc:
            return lc[key.lower()]
    return default


def _text(el: ET.Element, tag: str, default: Any = None) -> Any:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _bool(val: Any) -> bool:
    return str(val).strip().lower() not in {"false", "0", "no", "off", ""}


def _int(val: Any, fallback: int = 0) -> int:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError):
        return fallback


def _float(val: Any, fallback: float = 0.0) -> float:
    try:
        return float(str(val))
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# PLN Custom XML schema
# ---------------------------------------------------------------------------

class _PLNXMLParser:
    """Parse the PLN custom XML format."""

    def parse(self, root: ET.Element) -> NetworkData:
        nd = NetworkData(name=root.get("name", "PLN Network"))
        nd.buses = [self._bus(e) for e in root.findall(".//Bus")]
        nd.lines = [self._line(e) for e in root.findall(".//Line")]
        nd.transformers_2w = [self._trafo(e) for e in root.findall(".//Transformer")]
        nd.transformers_3w = [self._trafo3w(e) for e in root.findall(".//Transformer3W")]
        nd.switches = [self._switch(e) for e in root.findall(".//Switch")]
        nd.ext_grids = [self._ext_grid(e) for e in root.findall(".//ExtGrid")]
        nd.shunts = [self._shunt(e) for e in root.findall(".//Shunt")]
        nd.measurements = [self._meas(e) for e in root.findall(".//Measurement")]
        return nd

    def _bus(self, e: ET.Element) -> dict:
        return {
            "bus_id": _int(_attr(e, "id", "bus_id")),
            "name": _attr(e, "name", default=""),
            "vn_kv": _float(_attr(e, "vn_kv", "vnom_kv", default=0)),
            "bus_type": _int(_attr(e, "bus_type", "type", default=1)),
            "zone": _int(_attr(e, "zone", default=0)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _line(self, e: ET.Element) -> dict:
        return {
            "line_id": _int(_attr(e, "id", "line_id")),
            "name": _attr(e, "name", default=""),
            "from_bus": _int(_attr(e, "from_bus", "from")),
            "to_bus": _int(_attr(e, "to_bus", "to")),
            "length_km": _float(_attr(e, "length_km", "length", default=1)),
            "r_ohm_per_km": _float(_attr(e, "r_ohm_per_km", "r", default=0.01)),
            "x_ohm_per_km": _float(_attr(e, "x_ohm_per_km", "x", default=0.1)),
            "c_nf_per_km": _float(_attr(e, "c_nf_per_km", "c", default=10)),
            "max_i_ka": _float(_attr(e, "max_i_ka", "imax_ka", default=0.5)),
            "parallel": _int(_attr(e, "parallel", default=1)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _trafo(self, e: ET.Element) -> dict:
        return {
            "trafo_id": _int(_attr(e, "id", "trafo_id")),
            "name": _attr(e, "name", default=""),
            "hv_bus": _int(_attr(e, "hv_bus", "hv")),
            "lv_bus": _int(_attr(e, "lv_bus", "lv")),
            "sn_mva": _float(_attr(e, "sn_mva", "sn", default=100)),
            "vn_hv_kv": _float(_attr(e, "vn_hv_kv", default=150)),
            "vn_lv_kv": _float(_attr(e, "vn_lv_kv", default=20)),
            "vk_percent": _float(_attr(e, "vk_percent", "vk_pct", default=12)),
            "vkr_percent": _float(_attr(e, "vkr_percent", "vkr_pct", default=0.3)),
            "pfe_kw": _float(_attr(e, "pfe_kw", default=50)),
            "i0_percent": _float(_attr(e, "i0_percent", default=0.1)),
            "shift_degree": _float(_attr(e, "shift_degree", "shift_deg", default=0)),
            "tap_pos": _int(_attr(e, "tap_pos", default=0)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _trafo3w(self, e: ET.Element) -> dict:
        return {
            "trafo3w_id": _int(_attr(e, "id", "trafo3w_id")),
            "name": _attr(e, "name", default=""),
            "hv_bus": _int(_attr(e, "hv_bus")),
            "mv_bus": _int(_attr(e, "mv_bus")),
            "lv_bus": _int(_attr(e, "lv_bus")),
            "sn_hv_mva": _float(_attr(e, "sn_hv_mva", default=100)),
            "sn_mv_mva": _float(_attr(e, "sn_mv_mva", default=100)),
            "sn_lv_mva": _float(_attr(e, "sn_lv_mva", default=100)),
            "vn_hv_kv": _float(_attr(e, "vn_hv_kv", default=500)),
            "vn_mv_kv": _float(_attr(e, "vn_mv_kv", default=150)),
            "vn_lv_kv": _float(_attr(e, "vn_lv_kv", default=20)),
            "vk_hv_percent": _float(_attr(e, "vk_hv_percent", default=12)),
            "vk_mv_percent": _float(_attr(e, "vk_mv_percent", default=12)),
            "vk_lv_percent": _float(_attr(e, "vk_lv_percent", default=12)),
            "vkr_hv_percent": _float(_attr(e, "vkr_hv_percent", default=0.3)),
            "vkr_mv_percent": _float(_attr(e, "vkr_mv_percent", default=0.3)),
            "vkr_lv_percent": _float(_attr(e, "vkr_lv_percent", default=0.3)),
            "pfe_kw": _float(_attr(e, "pfe_kw", default=50)),
            "i0_percent": _float(_attr(e, "i0_percent", default=0.1)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _switch(self, e: ET.Element) -> dict:
        return {
            "switch_id": _int(_attr(e, "id", "switch_id")),
            "name": _attr(e, "name", default=""),
            "bus": _int(_attr(e, "bus")),
            "element": _int(_attr(e, "element")),
            "et": _attr(e, "et", "element_type", default="l"),
            "type": _attr(e, "type", "switch_type", default="CB"),
            "closed": _bool(_attr(e, "closed", "status", default="true")),
        }

    def _ext_grid(self, e: ET.Element) -> dict:
        return {
            "ext_grid_id": _int(_attr(e, "id", "ext_grid_id")),
            "name": _attr(e, "name", default=""),
            "bus": _int(_attr(e, "bus")),
            "vm_pu": _float(_attr(e, "vm_pu", "v_pu", default=1.0)),
            "va_degree": _float(_attr(e, "va_degree", "angle_deg", default=0.0)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _shunt(self, e: ET.Element) -> dict:
        return {
            "shunt_id": _int(_attr(e, "id", "shunt_id")),
            "name": _attr(e, "name", default=""),
            "bus": _int(_attr(e, "bus")),
            "p_mw": _float(_attr(e, "p_mw", default=0.0)),
            "q_mvar": _float(_attr(e, "q_mvar", default=0.0)),
            "vn_kv": _float(_attr(e, "vn_kv", default=0.0)),
            "in_service": _bool(_attr(e, "in_service", default="true")),
        }

    def _meas(self, e: ET.Element) -> dict:
        return {
            "meas_id": _int(_attr(e, "id", "meas_id")),
            "name": _attr(e, "name", default=""),
            "meas_type": str(_attr(e, "meas_type", "type", default="v")).lower(),
            "element_type": str(_attr(e, "element_type", "elem_type", default="bus")).lower(),
            "element": _int(_attr(e, "element", "element_id")),
            "value": _float(_attr(e, "value", "measured_value")),
            "std_dev": _float(_attr(e, "std_dev", "sigma", default=0.01)),
            "side": _attr(e, "side", default=""),
        }


# ---------------------------------------------------------------------------
# IEC 61970 CIM (subset) parser
# ---------------------------------------------------------------------------

# Namespace map used for CIM RDF/XML files
_CIM_NS = {
    "cim": "http://iec.ch/TC57/2013/CIM-schema-cim16#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "md": "http://iec.ch/TC57/61970-552/ModelDescription/1#",
}


def _cim_id(el: ET.Element) -> str:
    """Extract rdf:ID or rdf:about."""
    rdf_id = el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}ID", "")
    rdf_about = el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
    raw = rdf_id or rdf_about.lstrip("#")
    return re.sub(r"[^A-Za-z0-9_\-]", "", raw)


def _cim_text(el: ET.Element, local_tag: str, ns: str = "cim") -> str:
    ns_uri = _CIM_NS.get(ns, ns)
    child = el.find(f"{{{ns_uri}}}{local_tag}")
    return child.text.strip() if child is not None and child.text else ""


def _cim_ref(el: ET.Element, local_tag: str) -> str:
    ns_uri = _CIM_NS["cim"]
    rdf_uri = _CIM_NS["rdf"]
    child = el.find(f"{{{ns_uri}}}{local_tag}")
    if child is None:
        return ""
    resource = child.get(f"{{{rdf_uri}}}resource", "")
    return resource.lstrip("#")


class _CIMParser:
    """Partial CIM16/CIM17 RDF/XML → NetworkData parser."""

    def parse(self, root: ET.Element) -> NetworkData:
        nd = NetworkData(name="CIM Network")
        cn_ids: dict[str, int] = {}  # CIM rdf:ID → pandapower bus index

        # 1. Buses from ConnectivityNode / TopologicalNode
        bus_idx = 0
        for tag in ("ConnectivityNode", "TopologicalNode"):
            for el in root.iter(f"{{{_CIM_NS['cim']}}}{tag}"):
                cid = _cim_id(el)
                if cid and cid not in cn_ids:
                    vn = _float(_cim_text(el, "BaseVoltage.nominalVoltage") or
                                _cim_text(el, "nominalVoltage"), 110.0)
                    cn_ids[cid] = bus_idx
                    nd.buses.append({
                        "bus_id": bus_idx,
                        "name": _cim_text(el, "IdentifiedObject.name") or cid[:16],
                        "vn_kv": vn,
                        "bus_type": 1,
                        "zone": 0,
                        "in_service": True,
                    })
                    bus_idx += 1

        # Slack bus (EnergySource / ExternalNetworkInjection)
        for tag in ("EnergySource", "ExternalNetworkInjection"):
            for el in root.iter(f"{{{_CIM_NS['cim']}}}{tag}"):
                cid = _cim_ref(el, "Terminal.ConnectivityNode")
                if cid in cn_ids:
                    nd.ext_grids.append({
                        "ext_grid_id": len(nd.ext_grids),
                        "name": _cim_text(el, "IdentifiedObject.name"),
                        "bus": cn_ids[cid],
                        "vm_pu": 1.0,
                        "va_degree": 0.0,
                        "in_service": True,
                    })

        # 2. Lines (ACLineSegment)
        line_idx = 0
        for el in root.iter(f"{{{_CIM_NS['cim']}}}ACLineSegment"):
            terms = list(root.iter(f"{{{_CIM_NS['cim']}}}Terminal"))
            seg_id = _cim_id(el)
            seg_terms = [
                t for t in terms
                if _cim_ref(t, "Terminal.ConductingEquipment").endswith(seg_id)
                or _cim_ref(t, "Terminal.ConductingEquipment") == seg_id
            ]
            if len(seg_terms) < 2:
                continue
            fb = cn_ids.get(_cim_ref(seg_terms[0], "Terminal.ConnectivityNode"))
            tb = cn_ids.get(_cim_ref(seg_terms[1], "Terminal.ConnectivityNode"))
            if fb is None or tb is None:
                continue
            length = _float(_cim_text(el, "Conductor.length") or "1", 1.0)
            r = _float(_cim_text(el, "ACLineSegment.r") or "0.01", 0.01)
            x = _float(_cim_text(el, "ACLineSegment.x") or "0.1", 0.1)
            nd.lines.append({
                "line_id": line_idx,
                "name": _cim_text(el, "IdentifiedObject.name") or seg_id[:16],
                "from_bus": fb,
                "to_bus": tb,
                "length_km": length,
                "r_ohm_per_km": r / max(length, 1e-6),
                "x_ohm_per_km": x / max(length, 1e-6),
                "c_nf_per_km": 10.0,
                "max_i_ka": 0.5,
                "parallel": 1,
                "in_service": True,
            })
            line_idx += 1

        # 3. Transformers (PowerTransformer / PowerTransformerEnd)
        trafo_idx = 0
        for el in root.iter(f"{{{_CIM_NS['cim']}}}PowerTransformer"):
            pt_id = _cim_id(el)
            ends = [
                e for e in root.iter(f"{{{_CIM_NS['cim']}}}PowerTransformerEnd")
                if _cim_ref(e, "PowerTransformerEnd.PowerTransformer") == pt_id
            ]
            if len(ends) < 2:
                continue
            hv_cn = _cim_ref(ends[0], "TransformerEnd.Terminal")
            lv_cn = _cim_ref(ends[1], "TransformerEnd.Terminal")
            hv_bus = cn_ids.get(hv_cn)
            lv_bus = cn_ids.get(lv_cn)
            if hv_bus is None or lv_bus is None:
                continue
            nd.transformers_2w.append({
                "trafo_id": trafo_idx,
                "name": _cim_text(el, "IdentifiedObject.name") or pt_id[:16],
                "hv_bus": hv_bus,
                "lv_bus": lv_bus,
                "sn_mva": _float(_cim_text(ends[0], "PowerTransformerEnd.ratedS"), 100.0),
                "vn_hv_kv": _float(_cim_text(ends[0], "PowerTransformerEnd.ratedU"), 150.0),
                "vn_lv_kv": _float(_cim_text(ends[1], "PowerTransformerEnd.ratedU"), 20.0),
                "vk_percent": 12.0,
                "vkr_percent": 0.3,
                "pfe_kw": 50.0,
                "i0_percent": 0.1,
                "shift_degree": 0.0,
                "tap_pos": 0,
                "in_service": True,
            })
            trafo_idx += 1

        # 4. Measurements (Analog)
        meas_idx = 0
        for el in root.iter(f"{{{_CIM_NS['cim']}}}Analog"):
            mtype_raw = _cim_text(el, "Measurement.measurementType").lower()
            mtype_map = {
                "voltage": "v", "activepowerflow": "p", "reactivepowerflow": "q",
                "current": "i", "activepower": "p", "reactivepower": "q",
                "v": "v", "p": "p", "q": "q", "i": "i",
            }
            mtype = mtype_map.get(mtype_raw, "v")
            cn_ref = _cim_ref(el, "Measurement.Terminal")
            elem_id = cn_ids.get(cn_ref, 0)
            for val_el in root.iter(f"{{{_CIM_NS['cim']}}}AnalogValue"):
                if _cim_ref(val_el, "AnalogValue.Analog") == _cim_id(el):
                    value = _float(_cim_text(val_el, "AnalogValue.value"), 0.0)
                    nd.measurements.append({
                        "meas_id": meas_idx,
                        "name": _cim_text(el, "IdentifiedObject.name"),
                        "meas_type": mtype,
                        "element_type": "bus",
                        "element": elem_id,
                        "value": value,
                        "std_dev": 0.01,
                        "side": "",
                    })
                    meas_idx += 1
                    break

        return nd


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class XMLParser(BaseParser):
    """Auto-detecting XML parser (PLN custom or CIM RDF/XML)."""

    def parse(self, source: str) -> NetworkData:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"XML file not found: {source}")

        tree = ET.parse(str(path))
        root = tree.getroot()

        # Strip namespace for tag comparison
        tag_local = re.sub(r"\{[^}]*\}", "", root.tag).lower()

        if tag_local in ("rdf:rdf", "rdf", "model"):
            return _CIMParser().parse(root)
        # Default: PLN custom XML (root tag = PowerNetwork or similar)
        return _PLNXMLParser().parse(root)
