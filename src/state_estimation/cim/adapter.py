"""
CimToNetworkDataAdapter — bridges the CIM/CGMES compliance layer with the
pandapower computation layer.

Converts:
  TopologyProfile   → NetworkData.buses
  EquipmentProfile  → NetworkData.lines / transformers_2w / transformers_3w /
                       shunts / ext_grids
  SteadyStateHypothesisProfile.analog_values
                    → NetworkData.measurements

All element indices in NetworkData are 0-based pandapower indices derived from
the enumeration order of the TopologyProfile and the insertion order of each
equipment registry.  The mapping is recorded in `adapter.bus_map`,
`adapter.line_map`, and `adapter.trafo_map` so that downstream code (e.g. the
SV-profile assembler) can translate pandapower result indices back to CIM mRIDs.

Unit conventions
----------------
  CIM stores impedances in Ω (total, not per-km), lengths in km,
  susceptances in S (total line or winding admittance).
  pandapower expects:
    - r_ohm_per_km, x_ohm_per_km, c_nf_per_km  (per-km values)
    - vk_percent, vkr_percent   (short-circuit impedance as % of Z_base)
    - voltage measurements in p.u.
    - current measurements in kA
    - power measurements in MW / Mvar

Grid frequency
--------------
PLN Java system operates at 50 Hz; `_OMEGA` is fixed.  Change only when
targeting a 60 Hz grid.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..parsers.base_parser import NetworkData
from .model import (
    ACLineSegment,
    BusbarSection,
    EnergyConsumer,
    ExternalNetworkInjection,
    Junction,
    LinearShuntCompensator,
    PowerTransformer,
    PowerTransformerEnd,
    SynchronousMachine,
)
from .profiles.eq  import EquipmentProfile
from .profiles.ssh import SteadyStateHypothesisProfile
from .profiles.tp  import TopologyProfile

log = logging.getLogger(__name__)

_OMEGA: float = 2.0 * math.pi * 50.0  # rad/s — 50 Hz

# Maps CIM measurementType strings → pandapower meas_type codes
_MTYPE_MAP: Dict[str, str] = {
    "VoltageMagnitude":       "v",
    "V":                      "v",
    "ThreePhaseActivePower":  "p",
    "P":                      "p",
    "ThreePhaseReactivePower": "q",
    "Q":                      "q",
    "CurrentMagnitude":       "i",
    "I":                      "i",
}

# ConductingEquipment types that map to bus-level measurements
_BUS_CE_TYPES = (
    BusbarSection,
    Junction,
    EnergyConsumer,
    SynchronousMachine,
    ExternalNetworkInjection,
    LinearShuntCompensator,
)


@dataclass
class AdapterMaps:
    """
    Index mappings produced by the adapter so downstream code can translate
    pandapower integer indices back to CIM mRIDs.
    """
    # tn_mrid → pandapower bus index (0-based)
    bus_map:   Dict[str, int] = field(default_factory=dict)
    # acls_mrid → pandapower line index (0-based)
    line_map:  Dict[str, int] = field(default_factory=dict)
    # pt_mrid → pandapower trafo index (0-based, 2W and 3W tracked separately)
    trafo2w_map: Dict[str, int] = field(default_factory=dict)
    trafo3w_map: Dict[str, int] = field(default_factory=dict)
    # bus_id (1-based, as stored in NetworkData.buses) → tn_mrid
    bus_id_to_tn: Dict[int, str] = field(default_factory=dict)


class CimToNetworkDataAdapter:
    """
    Bridges the CIM/CGMES compliance layer to the pandapower computation layer.

    Parameters
    ----------
    eq  : EquipmentProfile   — post-parse, mRID index populated
    tp  : TopologyProfile    — post-topology-processing (TNs assigned)
    ssh : SteadyStateHypothesisProfile  — current operating snapshot
    """

    def __init__(self,
                 eq:  EquipmentProfile,
                 tp:  TopologyProfile,
                 ssh: SteadyStateHypothesisProfile) -> None:
        self.eq  = eq
        self.tp  = tp
        self.ssh = ssh
        self.maps = AdapterMaps()

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def convert(self) -> NetworkData:
        """
        Build and return a fully-populated NetworkData from the three profiles.

        Raises
        ------
        ValueError
            If the TopologyProfile is empty (topology processing not run).
        """
        if not self.tp.topological_nodes:
            raise ValueError(
                "TopologyProfile is empty — run TopologyProcessor before the adapter."
            )

        nd = NetworkData(name="PLN CIM Network")

        self._build_bus_index()
        self._slack_tn_mrids = self._find_slack_tn_mrids()
        self._pv_tn_mrids    = self._find_pv_tn_mrids()

        self._add_buses(nd)
        self._add_lines(nd)
        self._add_transformers(nd)
        self._add_shunts(nd)
        self._add_ext_grids(nd)
        self._add_measurements(nd)

        log.info(
            "CimToNetworkDataAdapter: %d buses, %d lines, %d 2W trafos, "
            "%d 3W trafos, %d shunts, %d ext_grids, %d measurements",
            len(nd.buses), len(nd.lines), len(nd.transformers_2w),
            len(nd.transformers_3w), len(nd.shunts),
            len(nd.ext_grids), len(nd.measurements),
        )
        return nd

    # ------------------------------------------------------------------
    # Step 0 — build TN → 0-based pandapower index (bus_map)
    # ------------------------------------------------------------------

    def _build_bus_index(self) -> None:
        for idx, tn_mrid in enumerate(self.tp.topological_nodes):
            self.maps.bus_map[tn_mrid] = idx
            self.maps.bus_id_to_tn[idx + 1] = tn_mrid   # NetworkData buses use 1-based ids

    # ------------------------------------------------------------------
    # Slack and PV classification
    # ------------------------------------------------------------------

    def _find_slack_tn_mrids(self) -> Set[str]:
        """Collect TN mRIDs that are angle-reference nodes."""
        slack: Set[str] = set()
        for ti in self.tp.topological_islands.values():
            if ti.angleRefTopologicalNode_mRID:
                slack.add(ti.angleRefTopologicalNode_mRID)
        for ext in self.eq.ext_net_injections.values():
            if ext.referencePriority > 0:
                for t in self.eq.terminals_of(ext.mRID):
                    if t.topologicalNode_mRID:
                        slack.add(t.topologicalNode_mRID)
        return slack

    def _find_pv_tn_mrids(self) -> Set[str]:
        """Collect TN mRIDs of non-slack generator buses."""
        pv: Set[str] = set()
        for sm in self.eq.synchronous_machines.values():
            if sm.referencePriority > 0:
                continue          # it's the slack, skip
            for t in self.eq.terminals_of(sm.mRID):
                tn = t.topologicalNode_mRID
                if tn and tn not in self._slack_tn_mrids:
                    pv.add(tn)
        return pv

    # ------------------------------------------------------------------
    # Step 1 — buses
    # ------------------------------------------------------------------

    def _add_buses(self, nd: NetworkData) -> None:
        for tn_mrid, tn in self.tp.topological_nodes.items():
            bus_id = self.maps.bus_map[tn_mrid] + 1   # 1-based for NetworkData

            bv     = self.eq.base_voltages.get(tn.baseVoltage_mRID)
            vn_kv  = bv.nominalVoltage if bv else 0.0

            if tn_mrid in self._slack_tn_mrids:
                bus_type = 3
            elif tn_mrid in self._pv_tn_mrids:
                bus_type = 2
            else:
                bus_type = 1

            nd.buses.append({
                "bus_id":     bus_id,
                "name":       tn.name or f"TN_{tn_mrid[:8]}",
                "vn_kv":      vn_kv,
                "bus_type":   bus_type,
                "zone":       1,
                "in_service": True,
                # Preserve mRID for traceability
                "_tn_mrid":   tn_mrid,
            })

    # ------------------------------------------------------------------
    # Step 2 — lines (ACLineSegment)
    # ------------------------------------------------------------------

    def _add_lines(self, nd: NetworkData) -> None:
        line_idx = 0
        for acls_mrid, acls in self.eq.ac_line_segments.items():
            row = self._convert_acls(acls, line_idx)
            if row is None:
                continue
            nd.lines.append(row)
            self.maps.line_map[acls_mrid] = line_idx
            line_idx += 1

    def _convert_acls(self, acls: ACLineSegment, idx: int) -> Optional[dict]:
        terms = sorted(
            self.eq.terminals_of(acls.mRID),
            key=lambda t: t.sequenceNumber,
        )
        if len(terms) < 2:
            log.debug("ACLineSegment %s: fewer than 2 terminals — skipped", acls.mRID)
            return None

        from_tn = terms[0].topologicalNode_mRID
        to_tn   = terms[1].topologicalNode_mRID

        if not from_tn or not to_tn:
            log.debug("ACLineSegment %s: terminal(s) have no TN — skipped", acls.mRID)
            return None

        from_pp = self.maps.bus_map.get(from_tn)
        to_pp   = self.maps.bus_map.get(to_tn)

        if from_pp is None or to_pp is None:
            log.debug("ACLineSegment %s: TN not in bus_map — skipped", acls.mRID)
            return None

        if from_pp == to_pp:
            # Topology processor merged both ends into the same bus — zero-impedance
            log.debug(
                "ACLineSegment %s: both ends on same TN after topology merge — skipped",
                acls.mRID,
            )
            return None

        length_km  = max(acls.length, 0.001)       # guard against zero-length
        r_per_km   = acls.r                         # Ω/km (CIM stores per-unit-length)
        x_per_km   = max(acls.x, 1e-6)             # guard against zero reactance
        # bch is total susceptance S/km; convert to total line capacitance nF/km
        c_nf_per_km = acls.bch / _OMEGA * 1e9 if acls.bch else 0.0

        return {
            "line_id":       idx,
            "name":          acls.name or acls.mRID[:8],
            "from_bus":      from_pp + 1,   # NetworkData buses are 1-based
            "to_bus":        to_pp + 1,
            "length_km":     length_km,
            "r_ohm_per_km":  r_per_km,
            "x_ohm_per_km":  x_per_km,
            "c_nf_per_km":   c_nf_per_km,
            "max_i_ka":      1.0,           # OperationalLimits not parsed; set to 1 kA
            "parallel":      1,
            "in_service":    acls.inService,
            "_acls_mrid":    acls.mRID,
        }

    # ------------------------------------------------------------------
    # Step 3 — transformers
    # ------------------------------------------------------------------

    def _add_transformers(self, nd: NetworkData) -> None:
        idx_2w = 0
        idx_3w = 0
        for pt_mrid, pt in self.eq.power_transformers.items():
            ends = sorted(
                [e for e in self.eq.transformer_ends.values()
                 if e.powerTransformer_mRID == pt_mrid],
                key=lambda e: e.sequenceNumber,
            )
            if len(ends) == 2:
                row = self._convert_trafo_2w(pt, ends, idx_2w)
                if row:
                    nd.transformers_2w.append(row)
                    self.maps.trafo2w_map[pt_mrid] = idx_2w
                    idx_2w += 1
            elif len(ends) >= 3:
                row = self._convert_trafo_3w(pt, ends[:3], idx_3w)
                if row:
                    nd.transformers_3w.append(row)
                    self.maps.trafo3w_map[pt_mrid] = idx_3w
                    idx_3w += 1
            else:
                log.debug("PowerTransformer %s: %d ends — skipped", pt_mrid, len(ends))

    def _convert_trafo_2w(self,
                           pt: PowerTransformer,
                           ends: List[PowerTransformerEnd],
                           idx: int) -> Optional[dict]:
        end_hv, end_lv = ends[0], ends[1]

        hv_t = self.eq.terminals.get(end_hv.terminal_mRID)
        lv_t = self.eq.terminals.get(end_lv.terminal_mRID)
        if not hv_t or not lv_t:
            log.debug("Transformer %s: missing terminal on an end — skipped", pt.mRID)
            return None

        hv_tn = hv_t.topologicalNode_mRID
        lv_tn = lv_t.topologicalNode_mRID
        if not hv_tn or not lv_tn:
            return None

        hv_pp = self.maps.bus_map.get(hv_tn)
        lv_pp = self.maps.bus_map.get(lv_tn)
        if hv_pp is None or lv_pp is None:
            return None

        sn_mva   = end_hv.ratedS or end_lv.ratedS or 1.0
        vn_hv_kv = end_hv.ratedU or 1.0
        vn_lv_kv = end_lv.ratedU or 1.0

        # Z_base (Ω) = kV² / MVA  (valid because 1 kV²/MVA = 1 Ω)
        z_base = vn_hv_kv ** 2 / sn_mva if sn_mva > 0 else 1.0

        vk_percent  = math.sqrt(end_hv.r ** 2 + end_hv.x ** 2) / z_base * 100
        vkr_percent = end_hv.r / z_base * 100

        # Iron losses: P_fe = g [S] × (ratedU [kV] × 1000)² [W] → kW
        pfe_kw = end_hv.g * vn_hv_kv ** 2 * 1_000 if end_hv.g else 0.0

        # No-load current: i0 = y0 / Y_base × 100 %
        y0 = math.sqrt(end_hv.g ** 2 + end_hv.b ** 2) if (end_hv.g or end_hv.b) else 0.0
        y_base     = sn_mva / vn_hv_kv ** 2 if vn_hv_kv > 0 else 1.0
        i0_percent = y0 / y_base * 100 if y_base > 0 else 0.0

        # Phase shift: CIM phaseAngleClock × 30° (Yd11 → 330°, Dy1 → 30°, etc.)
        shift_degree = end_lv.phaseAngleClock * 30.0

        # Tap position from SSH (fallback: EQ normalStep)
        tap_pos = 0
        for rtc in self.eq.ratio_tap_changers.values():
            if rtc.transformerEnd_mRID == end_hv.mRID:
                tap_pos = int(self.ssh.tap_position(rtc.mRID, float(rtc.normalStep)))
                break

        return {
            "trafo_id":     idx,
            "name":         pt.name or pt.mRID[:8],
            "hv_bus":       hv_pp + 1,
            "lv_bus":       lv_pp + 1,
            "sn_mva":       sn_mva,
            "vn_hv_kv":     vn_hv_kv,
            "vn_lv_kv":     vn_lv_kv,
            "vk_percent":   max(vk_percent, 0.01),
            "vkr_percent":  max(vkr_percent, 0.0),
            "pfe_kw":       pfe_kw,
            "i0_percent":   i0_percent,
            "shift_degree": shift_degree,
            "tap_pos":      tap_pos,
            "in_service":   pt.inService,
            "_pt_mrid":     pt.mRID,
        }

    def _convert_trafo_3w(self,
                           pt: PowerTransformer,
                           ends: List[PowerTransformerEnd],
                           idx: int) -> Optional[dict]:
        """Three-winding transformer — pandapower trafo3w schema."""
        e_hv, e_mv, e_lv = ends[0], ends[1], ends[2]

        def _term_bus(end: PowerTransformerEnd) -> Optional[int]:
            t = self.eq.terminals.get(end.terminal_mRID)
            if not t or not t.topologicalNode_mRID:
                return None
            pp = self.maps.bus_map.get(t.topologicalNode_mRID)
            return None if pp is None else pp + 1

        hv_bus = _term_bus(e_hv)
        mv_bus = _term_bus(e_mv)
        lv_bus = _term_bus(e_lv)

        if None in (hv_bus, mv_bus, lv_bus):
            log.debug("Transformer3W %s: missing bus — skipped", pt.mRID)
            return None

        def _vk(end: PowerTransformerEnd) -> float:
            z_b = end.ratedU ** 2 / end.ratedS if end.ratedS else 1.0
            return math.sqrt(end.r ** 2 + end.x ** 2) / z_b * 100

        def _vkr(end: PowerTransformerEnd) -> float:
            z_b = end.ratedU ** 2 / end.ratedS if end.ratedS else 1.0
            return end.r / z_b * 100

        return {
            "trafo_id":      idx,
            "name":          pt.name or pt.mRID[:8],
            "hv_bus":        hv_bus,
            "mv_bus":        mv_bus,
            "lv_bus":        lv_bus,
            "sn_hv_mva":     e_hv.ratedS or 1.0,
            "sn_mv_mva":     e_mv.ratedS or 1.0,
            "sn_lv_mva":     e_lv.ratedS or 1.0,
            "vn_hv_kv":      e_hv.ratedU or 1.0,
            "vn_mv_kv":      e_mv.ratedU or 1.0,
            "vn_lv_kv":      e_lv.ratedU or 1.0,
            "vk_hv_percent": max(_vk(e_hv), 0.01),
            "vk_mv_percent": max(_vk(e_mv), 0.01),
            "vk_lv_percent": max(_vk(e_lv), 0.01),
            "vkr_hv_percent": max(_vkr(e_hv), 0.0),
            "vkr_mv_percent": max(_vkr(e_mv), 0.0),
            "vkr_lv_percent": max(_vkr(e_lv), 0.0),
            "pfe_kw":         e_hv.g * e_hv.ratedU ** 2 * 1_000 if e_hv.g else 0.0,
            "i0_percent":     0.0,
            "shift_mv_degree": e_mv.phaseAngleClock * 30.0,
            "shift_lv_degree": e_lv.phaseAngleClock * 30.0,
            "in_service":      pt.inService,
            "_pt_mrid":        pt.mRID,
        }

    # ------------------------------------------------------------------
    # Step 4 — shunts
    # ------------------------------------------------------------------

    def _add_shunts(self, nd: NetworkData) -> None:
        for shunt in self.eq.shunt_compensators.values():
            row = self._convert_shunt(shunt)
            if row:
                nd.shunts.append(row)

    def _convert_shunt(self, shunt: LinearShuntCompensator) -> Optional[dict]:
        terms = self.eq.terminals_of(shunt.mRID)
        if not terms:
            return None
        tn_mrid = terms[0].topologicalNode_mRID
        if not tn_mrid:
            return None
        bus_pp = self.maps.bus_map.get(tn_mrid)
        if bus_pp is None:
            return None

        sections = self.ssh.shunt_sections.get(shunt.mRID)
        n_sec = sections.sections if sections is not None else shunt.normalSections

        # Pandapower convention: q_mvar > 0 = inductive (absorbing),
        # q_mvar < 0 = capacitive (generating).  CIM bPerSection > 0 = capacitive.
        # Therefore q_mvar = -bPerSection × n_sec × nomU²
        q_mvar = -(shunt.bPerSection * n_sec * shunt.nomU ** 2) if shunt.bPerSection else 0.0
        p_mw   =   shunt.gPerSection * n_sec * shunt.nomU ** 2  if shunt.gPerSection else 0.0

        return {
            "bus":        bus_pp + 1,
            "q_mvar":     q_mvar,
            "p_mw":       p_mw,
            "in_service": shunt.inService,
        }

    # ------------------------------------------------------------------
    # Step 5 — external grids (slack / injection)
    # ------------------------------------------------------------------

    def _add_ext_grids(self, nd: NetworkData) -> None:
        for ext in self.eq.ext_net_injections.values():
            row = self._convert_ext_grid(ext)
            if row:
                nd.ext_grids.append(row)

    def _convert_ext_grid(self, ext: ExternalNetworkInjection) -> Optional[dict]:
        terms = self.eq.terminals_of(ext.mRID)
        if not terms:
            return None
        tn_mrid = terms[0].topologicalNode_mRID
        if not tn_mrid:
            return None
        bus_pp = self.maps.bus_map.get(tn_mrid)
        if bus_pp is None:
            return None

        sp = self.ssh.machine_setpoints.get(ext.mRID)
        p  = sp.p if sp else 0.0
        q  = sp.q if sp else 0.0

        return {
            "bus":        bus_pp + 1,
            "vm_pu":      1.0,    # flat-start; updated by SE
            "va_degree":  0.0,
            "p_mw":       p,
            "q_mvar":     q,
            "in_service": ext.inService,
        }

    # ------------------------------------------------------------------
    # Step 6 — measurements
    # ------------------------------------------------------------------

    def _add_measurements(self, nd: NetworkData) -> None:
        meas_id = 1
        for av in self.ssh.analog_values.values():
            if av.suspect:
                continue
            row = self._convert_measurement(av, meas_id)
            if row:
                nd.measurements.append(row)
                meas_id += 1

    def _convert_measurement(self, av, meas_id: int) -> Optional[dict]:
        analog = self.eq.analogs.get(av.analog_mRID)
        if analog is None:
            return None

        mtype = _MTYPE_MAP.get(analog.measurementType, "")
        if not mtype:
            log.debug("AnalogValue %s: unknown type '%s' — skipped",
                      av.mRID, analog.measurementType)
            return None

        # ---- resolve element via Terminal chain -------------------------
        terminal = self.eq.terminals.get(analog.terminal_mRID)

        if terminal is None:
            # Fall back: PSR-level measurement (no terminal)
            return self._convert_psr_measurement(av, analog, mtype, meas_id)

        ce = self.eq.get(terminal.conductingEquipment_mRID)
        if ce is None:
            return None

        tn_mrid = terminal.topologicalNode_mRID

        # ---- classify element ------------------------------------------
        if isinstance(ce, ACLineSegment):
            line_idx = self.maps.line_map.get(ce.mRID)
            if line_idx is None:
                return None
            side = "from" if terminal.sequenceNumber == 1 else "to"
            element_type = "line"
            element_idx  = line_idx

        elif isinstance(ce, PowerTransformer):
            trafo_idx = self.maps.trafo2w_map.get(ce.mRID) \
                     or self.maps.trafo3w_map.get(ce.mRID)
            if trafo_idx is None:
                return None
            side = self._trafo_side(ce.mRID, terminal.mRID)
            element_type = "trafo" if ce.mRID in self.maps.trafo2w_map else "trafo3w"
            element_idx  = trafo_idx

        elif isinstance(ce, _BUS_CE_TYPES):
            bus_pp = self.maps.bus_map.get(tn_mrid) if tn_mrid else None
            if bus_pp is None:
                return None
            side = ""
            element_type = "bus"
            element_idx  = bus_pp

        else:
            return None

        scale = self._value_scale(mtype, tn_mrid)

        return {
            "meas_id":      meas_id,
            "name":         analog.name or f"{mtype.upper()}_{meas_id}",
            "meas_type":    mtype,
            "element_type": element_type,
            "element":      element_idx,
            "value":        av.value * scale,
            "std_dev":      max(abs(av.stdDev * scale), 1e-6),
            "side":         side,
            "_quality":     av.quality,
            "_suspect":     av.suspect,
            "_timestamp":   av.timeStamp,
        }

    def _convert_psr_measurement(self, av, analog, mtype: str,
                                  meas_id: int) -> Optional[dict]:
        """Handle measurements attached to a PSR directly (no terminal)."""
        psr_mrid = analog.powerSystemResource_mRID
        if not psr_mrid:
            return None
        terms = self.eq.terminals_of(psr_mrid)
        if not terms:
            return None
        tn_mrid = terms[0].topologicalNode_mRID
        bus_pp  = self.maps.bus_map.get(tn_mrid) if tn_mrid else None
        if bus_pp is None:
            return None
        scale = self._value_scale(mtype, tn_mrid)
        return {
            "meas_id":      meas_id,
            "name":         analog.name or f"{mtype.upper()}_{meas_id}",
            "meas_type":    mtype,
            "element_type": "bus",
            "element":      bus_pp,
            "value":        av.value * scale,
            "std_dev":      max(abs(av.stdDev * scale), 1e-6),
            "side":         "",
            "_quality":     av.quality,
            "_suspect":     av.suspect,
            "_timestamp":   av.timeStamp,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trafo_side(self, pt_mrid: str, terminal_mrid: str) -> str:
        """Map a terminal to 'hv', 'lv', or 'mv' by matching PowerTransformerEnd."""
        for end in self.eq.transformer_ends.values():
            if end.powerTransformer_mRID == pt_mrid and end.terminal_mRID == terminal_mrid:
                return {1: "hv", 2: "lv", 3: "mv"}.get(end.sequenceNumber, "hv")
        return "hv"

    def _value_scale(self, mtype: str, tn_mrid: str) -> float:
        """
        Return a multiplier to convert the CIM unit to the pandapower unit.
          v : kV → p.u.   (divide by nominalVoltage)
          i : A  → kA     (divide by 1 000)
          p : MW → MW     (no-op)
          q : Mvar → Mvar (no-op)
        """
        if mtype == "v":
            tn = self.tp.topological_nodes.get(tn_mrid) if tn_mrid else None
            if tn:
                bv = self.eq.base_voltages.get(tn.baseVoltage_mRID)
                if bv and bv.nominalVoltage > 0:
                    return 1.0 / bv.nominalVoltage
            return 1.0
        if mtype == "i":
            return 1.0 / 1_000.0
        return 1.0   # p, q already in MW / Mvar
