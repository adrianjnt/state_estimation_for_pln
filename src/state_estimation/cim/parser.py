"""
CGMES RDF/XML parser — IEC 61970-552.

Reads one or more CIM RDF/XML files (EQ / TP / SSH / SV profiles) and
populates the corresponding profile containers.

Usage
-----
    from state_estimation.cim.parser import CgmesParser

    parser = CgmesParser()
    parser.parse_file("network_EQ.xml")
    parser.parse_file("network_SSH.xml")

    eq  = parser.eq
    tp  = parser.tp
    ssh = parser.ssh
    sv  = parser.sv

Design notes
------------
* rdflib is used to parse arbitrary RDF/XML serialisations without needing to
  hand-code namespace-aware element tree walking.
* Each RDF subject is dispatched to a handler keyed by its rdf:type URI
  (``cim:Breaker``, ``cim:ACLineSegment`` etc.).
* mRID is taken from ``cim:IdentifiedObject.mRID`` when present; otherwise the
  local fragment of ``rdf:about`` / ``rdf:ID`` is used as the mRID (the CIM
  spec allows this for backwards compatibility with CIM14/CIM16 files).
* Multi-file loading (e.g. a full CGMES boundary + IGM set) is supported by
  calling ``parse_file()`` repeatedly; all profiles accumulate.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import urldefrag

from rdflib import Graph, Namespace, RDF, URIRef, Literal
from rdflib.namespace import RDFS

from .namespaces import (
    CIM_URI, MD_URI,
    PROFILE_EQ, PROFILE_TP, PROFILE_SSH, PROFILE_SV,
    PROFILE_LABELS,
    LEGACY_NS_ALIASES, PREDICATE_RENAMES,
)
from .model import (
    GeographicalRegion, SubGeographicalRegion, Substation, VoltageLevel, Bay,
    Line, BaseVoltage, ConnectivityNode, Terminal, BusbarSection, Junction,
    Breaker, Disconnector, LoadBreakSwitch, Fuse, ACLineSegment,
    PowerTransformer, PowerTransformerEnd, RatioTapChanger,
    LinearShuntCompensator, EnergyConsumer, GeneratingUnit, SynchronousMachine,
    ExternalNetworkInjection, Analog, AnalogValue, PhaseCode, WindingConnection,
    TopologicalNode, TopologicalIsland, SvVoltage, SvPowerFlow, SvTapStep,
    SvInjection,
)
from .profiles.eq  import EquipmentProfile
from .profiles.tp  import TopologyProfile
from .profiles.ssh import SteadyStateHypothesisProfile, SwitchState, TapStep, MachineSetpoint, LoadSetpoint
from .profiles.sv  import StateVariablesProfile

log = logging.getLogger(__name__)

CIM  = Namespace(CIM_URI)
MD   = Namespace(MD_URI)

# CIM14 windingType enum → sequenceNumber integer
_CIM14_WINDING_TYPE_TO_SEQ: dict[str, int] = {
    "primary":   1,
    "secondary": 2,
    "tertiary":  3,
}


def _normalize_to_cim100(g: Graph) -> Graph:
    """
    Pre-process a parsed RDF graph to normalise CIM14 / CIM16 predicate and
    type URIs to CIM100.

    Strategy
    --------
    1. Scan all predicates (and rdf:type objects) for legacy namespace prefixes.
    2. If none found, return the graph unchanged (zero cost for CIM100 files).
    3. Otherwise, rebuild a new Graph replacing every legacy URI with its CIM100
       equivalent, applying predicate local-name renames where necessary.
    """
    # Quick scan — avoid full rebuild for native CIM100 files
    needs_norm = False
    for legacy_ns in LEGACY_NS_ALIASES:
        for _, p, _ in g:
            if str(p).startswith(legacy_ns):
                needs_norm = True
                break
        if needs_norm:
            break

    if not needs_norm:
        return g

    # Detect which legacy namespace is dominant (for rename table lookup)
    dominant_ns = ""
    for legacy_ns in LEGACY_NS_ALIASES:
        for _, p, _ in g:
            if str(p).startswith(legacy_ns):
                dominant_ns = legacy_ns
                break
        if dominant_ns:
            break

    log.info(
        "CgmesParser: legacy CIM namespace detected (%s…) — normalising to CIM100",
        dominant_ns[:40],
    )

    target_ns = LEGACY_NS_ALIASES.get(dominant_ns, CIM_URI)
    new_g = Graph()

    for s, p, o in g:
        new_p = _remap_predicate(p, dominant_ns, target_ns)
        # rdf:type objects may also be CIM class URIs
        new_o = _remap_predicate(o, dominant_ns, target_ns) if isinstance(o, URIRef) else o
        new_s = _remap_predicate(s, dominant_ns, target_ns) if isinstance(s, URIRef) else s
        new_g.add((new_s, new_p, new_o))

    return new_g


def _remap_predicate(uri: URIRef, legacy_ns: str, target_ns: str) -> URIRef:
    """
    Replace a single URI's namespace prefix.

    First checks PREDICATE_RENAMES for an explicit local-name change, then
    falls back to a simple string prefix substitution.
    """
    uri_str = str(uri)
    if not uri_str.startswith(legacy_ns):
        return uri

    local_name = uri_str[len(legacy_ns):]
    # Look up explicit rename
    new_local = PREDICATE_RENAMES.get((legacy_ns, local_name), local_name)
    return URIRef(target_ns + new_local)


# Regex to extract a bare UUID (with or without urn:uuid: prefix)
_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _mrid_from_uri(uri: URIRef) -> str:
    """
    Extract the mRID from an rdf:about / rdf:ID URI.

    CGMES files may use:
      * ``rdf:about="urn:uuid:...``   → extract the UUID
      * ``rdf:about="#someLocalID"``  → use the fragment as-is
      * ``rdf:ID="someLocalID"``      → use as-is
    """
    s = str(uri)
    m = _UUID_RE.search(s)
    if m:
        return m.group(1).lower()
    _, frag = urldefrag(s)
    return frag if frag else s


def _str(g: Graph, subj: URIRef, pred: URIRef, default: str = "") -> str:
    val = g.value(subj, pred)
    return str(val) if val is not None else default


def _float(g: Graph, subj: URIRef, pred: URIRef, default: float = 0.0) -> float:
    val = g.value(subj, pred)
    if val is None:
        return default
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return default


def _int(g: Graph, subj: URIRef, pred: URIRef, default: int = 0) -> int:
    val = g.value(subj, pred)
    if val is None:
        return default
    try:
        return int(str(val))
    except (ValueError, TypeError):
        return default


def _bool(g: Graph, subj: URIRef, pred: URIRef, default: bool = False) -> bool:
    val = g.value(subj, pred)
    if val is None:
        return default
    s = str(val).strip().lower()
    return s in ("true", "1", "yes")


def _ref(g: Graph, subj: URIRef, pred: URIRef) -> str:
    """Return mRID of a referenced object (object property)."""
    obj = g.value(subj, pred)
    if obj is None:
        return ""
    return _mrid_from_uri(obj)


def _identified(g: Graph, subj: URIRef, mrid_override: str = "") -> dict:
    """Return IdentifiedObject field dict for subj."""
    mrid = _str(g, subj, CIM.IdentifiedObject.mRID) or mrid_override or _mrid_from_uri(subj)
    return {
        "mRID":        mrid,
        "name":        _str(g, subj, CIM.IdentifiedObject.name),
        "description": _str(g, subj, CIM.IdentifiedObject.description),
        "aliasName":   _str(g, subj, CIM.IdentifiedObject.aliasName),
    }


class CgmesParser:
    """
    Multi-profile CGMES RDF/XML parser.

    After parsing, access `.eq`, `.tp`, `.ssh`, `.sv` for the results.
    """

    def __init__(self) -> None:
        self.eq  = EquipmentProfile()
        self.tp  = TopologyProfile()
        self.ssh = SteadyStateHypothesisProfile()
        self.sv  = StateVariablesProfile()
        self._g  = Graph()   # accumulated RDF graph across all files

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, path: str | Path) -> None:
        """Parse a single CGMES RDF/XML file and merge into the graph."""
        path = Path(path)
        log.info("Parsing %s", path.name)
        sub_g = Graph()
        sub_g.parse(str(path), format="xml")
        sub_g = _normalize_to_cim100(sub_g)          # Task 4: CIM version aliasing
        profile = self._detect_profile(sub_g, path.stem)
        log.info("  → detected profile: %s", profile)
        self._g += sub_g
        self._dispatch_graph(sub_g, profile)

    def parse_string(self, xml: str, profile_hint: str = "EQ") -> None:
        """Parse XML from a string."""
        sub_g = Graph()
        sub_g.parse(data=xml, format="xml")
        sub_g = _normalize_to_cim100(sub_g)          # Task 4: CIM version aliasing
        profile = profile_hint.upper()
        self._g += sub_g
        self._dispatch_graph(sub_g, profile)

    # ------------------------------------------------------------------
    # Profile detection
    # ------------------------------------------------------------------

    def _detect_profile(self, g: Graph, stem: str) -> str:
        """
        Infer profile from md:Model.profile triples or filename suffix.
        Returns one of: EQ, TP, SSH, SV.
        """
        for _, _, obj in g.triples((None, MD.Model.profile, None)):
            label = PROFILE_LABELS.get(str(obj))
            if label:
                return label
        # Fallback: filename contains the profile label
        stem_up = stem.upper()
        for label in ("EQ", "TP", "SSH", "SV"):
            if label in stem_up:
                return label
        return "EQ"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch_graph(self, g: Graph, profile: str) -> None:
        dispatch: Dict[str, Callable] = {}
        if profile == "EQ":
            dispatch = self._eq_handlers()
        elif profile == "TP":
            dispatch = self._tp_handlers()
        elif profile == "SSH":
            dispatch = self._ssh_handlers()
        elif profile == "SV":
            dispatch = self._sv_handlers()

        for subj, _, type_uri in g.triples((None, RDF.type, None)):
            handler = dispatch.get(str(type_uri))
            if handler:
                try:
                    handler(g, subj)
                except Exception as exc:
                    log.warning("Error parsing %s %s: %s", type_uri, subj, exc)

    # ------------------------------------------------------------------
    # EQ handlers
    # ------------------------------------------------------------------

    def _eq_handlers(self) -> Dict[str, Callable]:
        return {
            str(CIM.GeographicalRegion):      self._parse_geographical_region,
            str(CIM.SubGeographicalRegion):   self._parse_sub_geographical_region,
            str(CIM.Substation):              self._parse_substation,
            str(CIM.VoltageLevel):            self._parse_voltage_level,
            str(CIM.Bay):                     self._parse_bay,
            str(CIM.Line):                    self._parse_line,
            str(CIM.BaseVoltage):             self._parse_base_voltage,
            str(CIM.ConnectivityNode):        self._parse_connectivity_node,
            str(CIM.Terminal):                self._parse_terminal,
            str(CIM.BusbarSection):           self._parse_busbar_section,
            str(CIM.Junction):                self._parse_junction,
            str(CIM.Breaker):                 self._parse_breaker,
            str(CIM.Disconnector):            self._parse_disconnector,
            str(CIM.LoadBreakSwitch):         self._parse_load_break_switch,
            str(CIM.Fuse):                    self._parse_fuse,
            str(CIM.ACLineSegment):           self._parse_ac_line_segment,
            str(CIM.PowerTransformer):        self._parse_power_transformer,
            str(CIM.PowerTransformerEnd):     self._parse_transformer_end,
            str(CIM.RatioTapChanger):         self._parse_ratio_tap_changer,
            str(CIM.LinearShuntCompensator):  self._parse_shunt_compensator,
            str(CIM.EnergyConsumer):          self._parse_energy_consumer,
            str(CIM.GeneratingUnit):          self._parse_generating_unit,
            str(CIM.SynchronousMachine):      self._parse_synchronous_machine,
            str(CIM.ExternalNetworkInjection): self._parse_ext_net_injection,
            str(CIM.Analog):                  self._parse_analog,
        }

    def _parse_geographical_region(self, g: Graph, s: URIRef) -> None:
        obj = GeographicalRegion(**_identified(g, s))
        self.eq.add(obj)

    def _parse_sub_geographical_region(self, g: Graph, s: URIRef) -> None:
        obj = SubGeographicalRegion(
            **_identified(g, s),
            region_mRID=_ref(g, s, CIM.SubGeographicalRegion.Region),
        )
        self.eq.add(obj)

    def _parse_substation(self, g: Graph, s: URIRef) -> None:
        obj = Substation(
            **_identified(g, s),
            subGeographicalRegion_mRID=_ref(g, s, CIM.Substation.Region),
        )
        self.eq.add(obj)

    def _parse_voltage_level(self, g: Graph, s: URIRef) -> None:
        obj = VoltageLevel(
            **_identified(g, s),
            substation_mRID=_ref(g, s, CIM.VoltageLevel.Substation),
            baseVoltage_mRID=_ref(g, s, CIM.VoltageLevel.BaseVoltage),
            highVoltageLimit=_float(g, s, CIM.VoltageLevel.highVoltageLimit),
            lowVoltageLimit=_float(g, s, CIM.VoltageLevel.lowVoltageLimit),
        )
        self.eq.add(obj)

    def _parse_bay(self, g: Graph, s: URIRef) -> None:
        obj = Bay(
            **_identified(g, s),
            voltageLevel_mRID=_ref(g, s, CIM.Bay.VoltageLevel),
        )
        self.eq.add(obj)

    def _parse_line(self, g: Graph, s: URIRef) -> None:
        obj = Line(
            **_identified(g, s),
            region_mRID=_ref(g, s, CIM.Line.Region),
        )
        self.eq.add(obj)

    def _parse_base_voltage(self, g: Graph, s: URIRef) -> None:
        obj = BaseVoltage(
            **_identified(g, s),
            nominalVoltage=_float(g, s, CIM.BaseVoltage.nominalVoltage),
        )
        self.eq.add(obj)

    def _parse_connectivity_node(self, g: Graph, s: URIRef) -> None:
        obj = ConnectivityNode(
            **_identified(g, s),
            connectivityNodeContainer_mRID=_ref(
                g, s, CIM.ConnectivityNode.ConnectivityNodeContainer
            ),
        )
        self.eq.add(obj)

    def _parse_terminal(self, g: Graph, s: URIRef) -> None:
        obj = Terminal(
            **_identified(g, s),
            conductingEquipment_mRID=_ref(g, s, CIM.Terminal.ConductingEquipment),
            connectivityNode_mRID=_ref(g, s, CIM.Terminal.ConnectivityNode),
            sequenceNumber=_int(g, s, CIM.ACDCTerminal.sequenceNumber, 1),
            connected=_bool(g, s, CIM.ACDCTerminal.connected, True),
        )
        self.eq.add(obj)

    def _parse_busbar_section(self, g: Graph, s: URIRef) -> None:
        obj = BusbarSection(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            baseVoltage_mRID=_ref(g, s, CIM.ConductingEquipment.BaseVoltage),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            ipMax=_float(g, s, CIM.BusbarSection.ipMax),
        )
        self.eq.add(obj)

    def _parse_junction(self, g: Graph, s: URIRef) -> None:
        obj = Junction(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
        )
        self.eq.add(obj)

    def _parse_switch_base(self, g: Graph, s: URIRef) -> dict:
        return dict(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            baseVoltage_mRID=_ref(g, s, CIM.ConductingEquipment.BaseVoltage),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            normalOpen=_bool(g, s, CIM.Switch.normalOpen, False),
            open=_bool(g, s, CIM.Switch.open, False),
            retained=_bool(g, s, CIM.Switch.retained, False),
            ratedCurrent=_float(g, s, CIM.Switch.ratedCurrent),
        )

    def _parse_breaker(self, g: Graph, s: URIRef) -> None:
        self.eq.add(Breaker(**self._parse_switch_base(g, s)))

    def _parse_disconnector(self, g: Graph, s: URIRef) -> None:
        self.eq.add(Disconnector(**self._parse_switch_base(g, s)))

    def _parse_load_break_switch(self, g: Graph, s: URIRef) -> None:
        self.eq.add(LoadBreakSwitch(**self._parse_switch_base(g, s)))

    def _parse_fuse(self, g: Graph, s: URIRef) -> None:
        self.eq.add(Fuse(**self._parse_switch_base(g, s)))

    def _parse_ac_line_segment(self, g: Graph, s: URIRef) -> None:
        obj = ACLineSegment(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            baseVoltage_mRID=_ref(g, s, CIM.ConductingEquipment.BaseVoltage),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            length=_float(g, s, CIM.Conductor.length),
            r=_float(g, s, CIM.ACLineSegment.r),
            x=_float(g, s, CIM.ACLineSegment.x),
            bch=_float(g, s, CIM.ACLineSegment.bch),
            gch=_float(g, s, CIM.ACLineSegment.gch),
            r0=_float(g, s, CIM.ACLineSegment.r0),
            x0=_float(g, s, CIM.ACLineSegment.x0),
        )
        self.eq.add(obj)

    def _parse_power_transformer(self, g: Graph, s: URIRef) -> None:
        obj = PowerTransformer(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            vectorGroup=_str(g, s, CIM.PowerTransformer.vectorGroup),
        )
        self.eq.add(obj)

    def _parse_transformer_end(self, g: Graph, s: URIRef) -> None:
        wc_str = _str(g, s, CIM.PowerTransformerEnd.connectionKind, "Yn")
        try:
            wc = WindingConnection(wc_str)
        except ValueError:
            wc = WindingConnection.Yn
        obj = PowerTransformerEnd(
            **_identified(g, s),
            powerTransformer_mRID=_ref(g, s, CIM.PowerTransformerEnd.PowerTransformer),
            terminal_mRID=_ref(g, s, CIM.TransformerEnd.Terminal),
            baseVoltage_mRID=_ref(g, s, CIM.TransformerEnd.BaseVoltage),
            sequenceNumber=_int(g, s, CIM.TransformerEnd.endNumber, 1),
            ratedS=_float(g, s, CIM.PowerTransformerEnd.ratedS),
            ratedU=_float(g, s, CIM.PowerTransformerEnd.ratedU),
            r=_float(g, s, CIM.PowerTransformerEnd.r),
            x=_float(g, s, CIM.PowerTransformerEnd.x),
            g=_float(g, s, CIM.PowerTransformerEnd.g),
            b=_float(g, s, CIM.PowerTransformerEnd.b),
            r0=_float(g, s, CIM.PowerTransformerEnd.r0),
            x0=_float(g, s, CIM.PowerTransformerEnd.x0),
            phaseAngleClock=_int(g, s, CIM.PowerTransformerEnd.phaseAngleClock),
            connectionKind=wc,
            grounded=_bool(g, s, CIM.TransformerEnd.grounded, True),
        )
        self.eq.add(obj)

    def _parse_ratio_tap_changer(self, g: Graph, s: URIRef) -> None:
        obj = RatioTapChanger(
            **_identified(g, s),
            transformerEnd_mRID=_ref(g, s, CIM.RatioTapChanger.TransformerEnd),
            lowStep=_int(g, s, CIM.TapChanger.lowStep, -10),
            highStep=_int(g, s, CIM.TapChanger.highStep, 10),
            neutralStep=_int(g, s, CIM.TapChanger.neutralStep, 0),
            normalStep=_int(g, s, CIM.TapChanger.normalStep, 0),
            neutralU=_float(g, s, CIM.TapChanger.neutralU),
            stepVoltageIncrement=_float(g, s, CIM.RatioTapChanger.stepVoltageIncrement),
            ltcFlag=_bool(g, s, CIM.TapChanger.ltcFlag),
        )
        self.eq.add(obj)

    def _parse_shunt_compensator(self, g: Graph, s: URIRef) -> None:
        obj = LinearShuntCompensator(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            baseVoltage_mRID=_ref(g, s, CIM.ConductingEquipment.BaseVoltage),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            nomU=_float(g, s, CIM.ShuntCompensator.nomU),
            maximumSections=_int(g, s, CIM.ShuntCompensator.maximumSections, 1),
            normalSections=_int(g, s, CIM.ShuntCompensator.normalSections, 1),
            bPerSection=_float(g, s, CIM.LinearShuntCompensator.bPerSection),
            gPerSection=_float(g, s, CIM.LinearShuntCompensator.gPerSection),
        )
        self.eq.add(obj)

    def _parse_energy_consumer(self, g: Graph, s: URIRef) -> None:
        obj = EnergyConsumer(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            inService=_bool(g, s, CIM.Equipment.inService, True),
        )
        self.eq.add(obj)

    def _parse_generating_unit(self, g: Graph, s: URIRef) -> None:
        obj = GeneratingUnit(
            **_identified(g, s),
            nominalP=_float(g, s, CIM.GeneratingUnit.nominalP),
            maxOperatingP=_float(g, s, CIM.GeneratingUnit.maxOperatingP),
            minOperatingP=_float(g, s, CIM.GeneratingUnit.minOperatingP),
        )
        self.eq.add(obj)

    def _parse_synchronous_machine(self, g: Graph, s: URIRef) -> None:
        obj = SynchronousMachine(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            generatingUnit_mRID=_ref(g, s, CIM.SynchronousMachine.GeneratingUnit),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            ratedS=_float(g, s, CIM.SynchronousMachine.ratedS),
            ratedU=_float(g, s, CIM.SynchronousMachine.ratedU),
            qMin=_float(g, s, CIM.SynchronousMachine.minQ),
            qMax=_float(g, s, CIM.SynchronousMachine.maxQ),
            referencePriority=_int(g, s, CIM.SynchronousMachine.referencePriority),
        )
        self.eq.add(obj)

    def _parse_ext_net_injection(self, g: Graph, s: URIRef) -> None:
        obj = ExternalNetworkInjection(
            **_identified(g, s),
            equipmentContainer_mRID=_ref(g, s, CIM.Equipment.EquipmentContainer),
            inService=_bool(g, s, CIM.Equipment.inService, True),
            referencePriority=_int(g, s, CIM.ExternalNetworkInjection.referencePriority, 1),
        )
        self.eq.add(obj)

    def _parse_analog(self, g: Graph, s: URIRef) -> None:
        obj = Analog(
            **_identified(g, s),
            powerSystemResource_mRID=_ref(g, s, CIM.Measurement.PowerSystemResource),
            terminal_mRID=_ref(g, s, CIM.Measurement.Terminal),
            measurementType=_str(g, s, CIM.Measurement.measurementType),
            positiveFlowIn=_bool(g, s, CIM.Analog.positiveFlowIn, True),
        )
        self.eq.add(obj)

    # ------------------------------------------------------------------
    # TP handlers
    # ------------------------------------------------------------------

    def _tp_handlers(self) -> Dict[str, Callable]:
        return {
            str(CIM.TopologicalNode):  self._parse_topological_node,
            str(CIM.TopologicalIsland): self._parse_topological_island,
        }

    def _parse_topological_node(self, g: Graph, s: URIRef) -> None:
        obj = TopologicalNode(
            **_identified(g, s),
            baseVoltage_mRID=_ref(g, s, CIM.TopologicalNode.BaseVoltage),
            connectivityNodeContainer_mRID=_ref(
                g, s, CIM.TopologicalNode.ConnectivityNodeContainer
            ),
        )
        self.tp.add_node(obj)

    def _parse_topological_island(self, g: Graph, s: URIRef) -> None:
        # Collect all member nodes
        tn_mrids = [
            _mrid_from_uri(o)
            for o in g.objects(s, CIM.TopologicalIsland.TopologicalNodes)
        ]
        obj = TopologicalIsland(
            **_identified(g, s),
            topologicalNodes=tn_mrids,
            angleRefTopologicalNode_mRID=_ref(
                g, s, CIM.TopologicalIsland.AngleRefTopologicalNode
            ),
        )
        self.tp.add_island(obj)

    # ------------------------------------------------------------------
    # SSH handlers
    # ------------------------------------------------------------------

    def _ssh_handlers(self) -> Dict[str, Callable]:
        return {
            str(CIM.Breaker):                  self._parse_ssh_switch,
            str(CIM.Disconnector):             self._parse_ssh_switch,
            str(CIM.LoadBreakSwitch):          self._parse_ssh_switch,
            str(CIM.Fuse):                     self._parse_ssh_switch,
            str(CIM.RatioTapChanger):          self._parse_ssh_tap,
            str(CIM.SynchronousMachine):       self._parse_ssh_machine,
            str(CIM.ExternalNetworkInjection): self._parse_ssh_machine,
            str(CIM.EnergyConsumer):           self._parse_ssh_load,
            str(CIM.AnalogValue):              self._parse_analog_value,
        }

    def _parse_ssh_switch(self, g: Graph, s: URIRef) -> None:
        mrid = _str(g, s, CIM.IdentifiedObject.mRID) or _mrid_from_uri(s)
        state = SwitchState(
            switch_mRID=mrid,
            open=_bool(g, s, CIM.Switch.open, False),
        )
        self.ssh.set_switch(state)

    def _parse_ssh_tap(self, g: Graph, s: URIRef) -> None:
        mrid = _str(g, s, CIM.IdentifiedObject.mRID) or _mrid_from_uri(s)
        step = TapStep(
            tapChanger_mRID=mrid,
            step=_float(g, s, CIM.TapChanger.step),
        )
        self.ssh.set_tap(step)

    def _parse_ssh_machine(self, g: Graph, s: URIRef) -> None:
        mrid = _str(g, s, CIM.IdentifiedObject.mRID) or _mrid_from_uri(s)
        sp = MachineSetpoint(
            equipment_mRID=mrid,
            p=_float(g, s, CIM.SynchronousMachine.p) or _float(g, s, CIM.ExternalNetworkInjection.p),
            q=_float(g, s, CIM.SynchronousMachine.q) or _float(g, s, CIM.ExternalNetworkInjection.q),
            referencePriority=_int(g, s, CIM.SynchronousMachine.referencePriority)
                or _int(g, s, CIM.ExternalNetworkInjection.referencePriority),
        )
        self.ssh.set_machine(sp)

    def _parse_ssh_load(self, g: Graph, s: URIRef) -> None:
        mrid = _str(g, s, CIM.IdentifiedObject.mRID) or _mrid_from_uri(s)
        sp = LoadSetpoint(
            consumer_mRID=mrid,
            p=_float(g, s, CIM.EnergyConsumer.p),
            q=_float(g, s, CIM.EnergyConsumer.q),
        )
        self.ssh.set_load(sp)

    def _parse_analog_value(self, g: Graph, s: URIRef) -> None:
        av = AnalogValue(
            **_identified(g, s),
            value=_float(g, s, CIM.AnalogValue.value),
            analog_mRID=_ref(g, s, CIM.AnalogValue.Analog),
            quality=_str(g, s, CIM.AnalogValue.quality, "act"),
            stdDev=_float(g, s, CIM.AnalogValue.stdDev, 0.01),
        )
        self.ssh.add_analog_value(av)

    # ------------------------------------------------------------------
    # SV handlers
    # ------------------------------------------------------------------

    def _sv_handlers(self) -> Dict[str, Callable]:
        return {
            str(CIM.SvVoltage):   self._parse_sv_voltage,
            str(CIM.SvPowerFlow): self._parse_sv_power_flow,
            str(CIM.SvTapStep):   self._parse_sv_tap_step,
            str(CIM.SvInjection): self._parse_sv_injection,
        }

    def _parse_sv_voltage(self, g: Graph, s: URIRef) -> None:
        sv = SvVoltage(
            **_identified(g, s),
            v=_float(g, s, CIM.SvVoltage.v),
            angle=_float(g, s, CIM.SvVoltage.angle),
            topologicalNode_mRID=_ref(g, s, CIM.SvVoltage.TopologicalNode),
        )
        self.sv.add_voltage(sv)

    def _parse_sv_power_flow(self, g: Graph, s: URIRef) -> None:
        sv = SvPowerFlow(
            **_identified(g, s),
            p=_float(g, s, CIM.SvPowerFlow.p),
            q=_float(g, s, CIM.SvPowerFlow.q),
            terminal_mRID=_ref(g, s, CIM.SvPowerFlow.Terminal),
        )
        self.sv.add_power_flow(sv)

    def _parse_sv_tap_step(self, g: Graph, s: URIRef) -> None:
        sv = SvTapStep(
            **_identified(g, s),
            position=_float(g, s, CIM.SvTapStep.position),
            tapChanger_mRID=_ref(g, s, CIM.SvTapStep.TapChanger),
        )
        self.sv.add_tap_step(sv)

    def _parse_sv_injection(self, g: Graph, s: URIRef) -> None:
        sv = SvInjection(
            **_identified(g, s),
            pInjection=_float(g, s, CIM.SvInjection.pInjection),
            qInjection=_float(g, s, CIM.SvInjection.qInjection),
            topologicalNode_mRID=_ref(g, s, CIM.SvInjection.TopologicalNode),
        )
        self.sv.add_injection(sv)
