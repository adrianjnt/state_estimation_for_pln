"""
CGMES RDF/XML serializer — IEC 61970-552.

Exports one or more profile containers as standards-compliant CIM RDF/XML
files.  The serialiser builds an rdflib Graph from the in-memory objects and
then uses rdflib's XML serialiser with the canonical CGMES namespace prefixes.

Usage
-----
    from state_estimation.cim.serializer import CgmesSerializer
    from state_estimation.cim.profiles   import StateVariablesProfile

    ser = CgmesSerializer()
    xml_bytes = ser.serialize_sv(sv_profile, tp_model_id="...", ssh_model_id="...")
    Path("network_SV.xml").write_bytes(xml_bytes)
"""

from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

from rdflib import Graph, Literal, Namespace, RDF, URIRef, XSD
from rdflib.namespace import RDFS

from .namespaces import (
    CIM_URI, MD_URI, RDF_URI, RDFS_URI,
    PROFILE_EQ, PROFILE_TP, PROFILE_SSH, PROFILE_SV,
    XML_PREFIXES,
)
from .profiles.eq  import EquipmentProfile
from .profiles.tp  import TopologyProfile
from .profiles.ssh import SteadyStateHypothesisProfile
from .profiles.sv  import StateVariablesProfile
from .model import (
    ConnectivityNode, Terminal, BusbarSection, Breaker, Disconnector,
    LoadBreakSwitch, ACLineSegment, PowerTransformer, PowerTransformerEnd,
    RatioTapChanger, LinearShuntCompensator, EnergyConsumer, SynchronousMachine,
    ExternalNetworkInjection, Analog, AnalogValue,
    TopologicalNode, TopologicalIsland,
    SvVoltage, SvPowerFlow, SvTapStep, SvInjection,
)

CIM = Namespace(CIM_URI)
MD  = Namespace(MD_URI)


def _uri(mrid: str) -> URIRef:
    """Convert an mRID to an rdf:about URI."""
    return URIRef(f"urn:uuid:{mrid}")


def _ref_uri(mrid: str) -> URIRef:
    """Return a reference-target URI (same scheme as _uri)."""
    return _uri(mrid)


def _lit(value, datatype=XSD.string) -> Literal:
    return Literal(str(value), datatype=datatype)


def _lit_float(v: float) -> Literal:
    return Literal(v, datatype=XSD.float)


def _lit_bool(v: bool) -> Literal:
    return Literal("true" if v else "false", datatype=XSD.boolean)


def _lit_int(v: int) -> Literal:
    return Literal(v, datatype=XSD.integer)


def _add_identified(g: Graph, subj: URIRef, obj) -> None:
    """Add IdentifiedObject fields to graph."""
    g.add((subj, CIM.IdentifiedObject.mRID, _lit(obj.mRID)))
    if obj.name:
        g.add((subj, CIM.IdentifiedObject.name, _lit(obj.name)))
    if obj.description:
        g.add((subj, CIM.IdentifiedObject.description, _lit(obj.description)))
    if obj.aliasName:
        g.add((subj, CIM.IdentifiedObject.aliasName, _lit(obj.aliasName)))


def _new_model_id(hint: str = "") -> str:
    return hint if hint else str(uuid.uuid4())


def _build_full_model(g: Graph, model_id: str, profile_uri: str,
                      depends_on: list[str] | None = None) -> None:
    """Add the md:FullModel block required by CGMES."""
    subj = _uri(model_id)
    g.add((subj, RDF.type, MD.FullModel))
    g.add((subj, MD.Model.profile, URIRef(profile_uri)))
    g.add((subj, MD.Model.modelingAuthoritySet, _lit("http://www.pln.co.id/")))
    for dep in (depends_on or []):
        g.add((subj, MD.Model.DependentOn, _uri(dep)))


def _setup_graph() -> Graph:
    g = Graph()
    for prefix, ns_uri in XML_PREFIXES.items():
        g.bind(prefix, Namespace(ns_uri))
    g.bind("md", MD)
    g.bind("xsd", XSD)
    return g


class CgmesSerializer:
    """Serializes profile containers to CGMES RDF/XML."""

    # ------------------------------------------------------------------
    # EQ export
    # ------------------------------------------------------------------

    def serialize_eq(self, eq: EquipmentProfile,
                     model_id: Optional[str] = None) -> bytes:
        model_id = _new_model_id(model_id or eq.model_id)
        g = _setup_graph()
        _build_full_model(g, model_id, PROFILE_EQ)

        for obj in eq.connectivity_nodes.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.ConnectivityNode))
            _add_identified(g, s, obj)
            if obj.connectivityNodeContainer_mRID:
                g.add((s, CIM.ConnectivityNode.ConnectivityNodeContainer,
                        _ref_uri(obj.connectivityNodeContainer_mRID)))

        for obj in eq.terminals.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.Terminal))
            _add_identified(g, s, obj)
            g.add((s, CIM.ACDCTerminal.sequenceNumber, _lit_int(obj.sequenceNumber)))
            g.add((s, CIM.ACDCTerminal.connected, _lit_bool(obj.connected)))
            if obj.conductingEquipment_mRID:
                g.add((s, CIM.Terminal.ConductingEquipment,
                        _ref_uri(obj.conductingEquipment_mRID)))
            if obj.connectivityNode_mRID:
                g.add((s, CIM.Terminal.ConnectivityNode,
                        _ref_uri(obj.connectivityNode_mRID)))

        for obj in eq.busbar_sections.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.BusbarSection))
            _add_identified(g, s, obj)
            self._add_equipment(g, s, obj)

        for obj in eq.breakers.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.Breaker))
            _add_identified(g, s, obj)
            self._add_switch(g, s, obj)

        for obj in eq.disconnectors.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.Disconnector))
            _add_identified(g, s, obj)
            self._add_switch(g, s, obj)

        for obj in eq.load_break_switches.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.LoadBreakSwitch))
            _add_identified(g, s, obj)
            self._add_switch(g, s, obj)

        for obj in eq.ac_line_segments.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.ACLineSegment))
            _add_identified(g, s, obj)
            self._add_equipment(g, s, obj)
            g.add((s, CIM.Conductor.length, _lit_float(obj.length)))
            g.add((s, CIM.ACLineSegment.r, _lit_float(obj.r)))
            g.add((s, CIM.ACLineSegment.x, _lit_float(obj.x)))
            g.add((s, CIM.ACLineSegment.bch, _lit_float(obj.bch)))
            g.add((s, CIM.ACLineSegment.gch, _lit_float(obj.gch)))

        for obj in eq.power_transformers.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.PowerTransformer))
            _add_identified(g, s, obj)
            self._add_equipment(g, s, obj)
            if obj.vectorGroup:
                g.add((s, CIM.PowerTransformer.vectorGroup, _lit(obj.vectorGroup)))

        for obj in eq.transformer_ends.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.PowerTransformerEnd))
            _add_identified(g, s, obj)
            g.add((s, CIM.TransformerEnd.endNumber, _lit_int(obj.sequenceNumber)))
            g.add((s, CIM.PowerTransformerEnd.ratedS, _lit_float(obj.ratedS)))
            g.add((s, CIM.PowerTransformerEnd.ratedU, _lit_float(obj.ratedU)))
            g.add((s, CIM.PowerTransformerEnd.r, _lit_float(obj.r)))
            g.add((s, CIM.PowerTransformerEnd.x, _lit_float(obj.x)))
            g.add((s, CIM.PowerTransformerEnd.g, _lit_float(obj.g)))
            g.add((s, CIM.PowerTransformerEnd.b, _lit_float(obj.b)))
            if obj.powerTransformer_mRID:
                g.add((s, CIM.PowerTransformerEnd.PowerTransformer,
                        _ref_uri(obj.powerTransformer_mRID)))
            if obj.terminal_mRID:
                g.add((s, CIM.TransformerEnd.Terminal,
                        _ref_uri(obj.terminal_mRID)))

        for obj in eq.analogs.values():
            s = _uri(obj.mRID)
            g.add((s, RDF.type, CIM.Analog))
            _add_identified(g, s, obj)
            if obj.measurementType:
                g.add((s, CIM.Measurement.measurementType, _lit(obj.measurementType)))
            g.add((s, CIM.Analog.positiveFlowIn, _lit_bool(obj.positiveFlowIn)))
            if obj.terminal_mRID:
                g.add((s, CIM.Measurement.Terminal, _ref_uri(obj.terminal_mRID)))

        return self._to_bytes(g)

    # ------------------------------------------------------------------
    # TP export
    # ------------------------------------------------------------------

    def serialize_tp(self, tp: TopologyProfile,
                     eq_model_id: str,
                     model_id: Optional[str] = None) -> bytes:
        model_id = _new_model_id(model_id or tp.model_id)
        g = _setup_graph()
        _build_full_model(g, model_id, PROFILE_TP, depends_on=[eq_model_id])

        for tn in tp.topological_nodes.values():
            s = _uri(tn.mRID)
            g.add((s, RDF.type, CIM.TopologicalNode))
            _add_identified(g, s, tn)
            if tn.baseVoltage_mRID:
                g.add((s, CIM.TopologicalNode.BaseVoltage,
                        _ref_uri(tn.baseVoltage_mRID)))
            if tn.connectivityNodeContainer_mRID:
                g.add((s, CIM.TopologicalNode.ConnectivityNodeContainer,
                        _ref_uri(tn.connectivityNodeContainer_mRID)))

        for ti in tp.topological_islands.values():
            s = _uri(ti.mRID)
            g.add((s, RDF.type, CIM.TopologicalIsland))
            _add_identified(g, s, ti)
            if ti.angleRefTopologicalNode_mRID:
                g.add((s, CIM.TopologicalIsland.AngleRefTopologicalNode,
                        _ref_uri(ti.angleRefTopologicalNode_mRID)))
            for tn_mrid in ti.topologicalNodes:
                g.add((s, CIM.TopologicalIsland.TopologicalNodes,
                        _ref_uri(tn_mrid)))

        return self._to_bytes(g)

    # ------------------------------------------------------------------
    # SV export (primary SE output)
    # ------------------------------------------------------------------

    def serialize_sv(self, sv: StateVariablesProfile,
                     tp_model_id: str,
                     ssh_model_id: str,
                     model_id: Optional[str] = None) -> bytes:
        model_id = _new_model_id(model_id or sv.model_id)
        g = _setup_graph()
        _build_full_model(g, model_id, PROFILE_SV,
                          depends_on=[tp_model_id, ssh_model_id])

        for sv_v in sv.sv_voltages.values():
            s = _uri(sv_v.mRID)
            g.add((s, RDF.type, CIM.SvVoltage))
            _add_identified(g, s, sv_v)
            g.add((s, CIM.SvVoltage.v,     _lit_float(sv_v.v)))
            g.add((s, CIM.SvVoltage.angle, _lit_float(sv_v.angle)))
            if sv_v.topologicalNode_mRID:
                g.add((s, CIM.SvVoltage.TopologicalNode,
                        _ref_uri(sv_v.topologicalNode_mRID)))

        for sv_p in sv.sv_power_flows.values():
            s = _uri(sv_p.mRID)
            g.add((s, RDF.type, CIM.SvPowerFlow))
            _add_identified(g, s, sv_p)
            g.add((s, CIM.SvPowerFlow.p, _lit_float(sv_p.p)))
            g.add((s, CIM.SvPowerFlow.q, _lit_float(sv_p.q)))
            if sv_p.terminal_mRID:
                g.add((s, CIM.SvPowerFlow.Terminal, _ref_uri(sv_p.terminal_mRID)))

        for sv_t in sv.sv_tap_steps.values():
            s = _uri(sv_t.mRID)
            g.add((s, RDF.type, CIM.SvTapStep))
            _add_identified(g, s, sv_t)
            g.add((s, CIM.SvTapStep.position, _lit_float(sv_t.position)))
            if sv_t.tapChanger_mRID:
                g.add((s, CIM.SvTapStep.TapChanger, _ref_uri(sv_t.tapChanger_mRID)))

        for sv_i in sv.sv_injections.values():
            s = _uri(sv_i.mRID)
            g.add((s, RDF.type, CIM.SvInjection))
            _add_identified(g, s, sv_i)
            g.add((s, CIM.SvInjection.pInjection, _lit_float(sv_i.pInjection)))
            g.add((s, CIM.SvInjection.qInjection, _lit_float(sv_i.qInjection)))
            if sv_i.topologicalNode_mRID:
                g.add((s, CIM.SvInjection.TopologicalNode,
                        _ref_uri(sv_i.topologicalNode_mRID)))

        return self._to_bytes(g)

    # ------------------------------------------------------------------
    # SSH export
    # ------------------------------------------------------------------

    def serialize_ssh(self, ssh: SteadyStateHypothesisProfile,
                      eq_model_id: str,
                      model_id: Optional[str] = None) -> bytes:
        model_id = _new_model_id(model_id or ssh.model_id)
        g = _setup_graph()
        _build_full_model(g, model_id, PROFILE_SSH, depends_on=[eq_model_id])

        for state in ssh.switch_states.values():
            s = _uri(state.switch_mRID)
            g.add((s, CIM.Switch.open, _lit_bool(state.open)))

        for step in ssh.tap_steps.values():
            s = _uri(step.tapChanger_mRID)
            g.add((s, CIM.TapChanger.step, _lit_float(step.step)))

        for sp in ssh.machine_setpoints.values():
            s = _uri(sp.equipment_mRID)
            g.add((s, CIM.SynchronousMachine.p, _lit_float(sp.p)))
            g.add((s, CIM.SynchronousMachine.q, _lit_float(sp.q)))

        for sp in ssh.load_setpoints.values():
            s = _uri(sp.consumer_mRID)
            g.add((s, CIM.EnergyConsumer.p, _lit_float(sp.p)))
            g.add((s, CIM.EnergyConsumer.q, _lit_float(sp.q)))

        for av in ssh.analog_values.values():
            s = _uri(av.mRID)
            g.add((s, RDF.type, CIM.AnalogValue))
            _add_identified(g, s, av)
            g.add((s, CIM.AnalogValue.value, _lit_float(av.value)))
            if av.analog_mRID:
                g.add((s, CIM.AnalogValue.Analog, _ref_uri(av.analog_mRID)))
            if av.quality:
                g.add((s, CIM.AnalogValue.quality, _lit(av.quality)))

        return self._to_bytes(g)

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _add_equipment(self, g: Graph, s: URIRef, obj) -> None:
        g.add((s, CIM.Equipment.inService, _lit_bool(obj.inService)))
        if obj.equipmentContainer_mRID:
            g.add((s, CIM.Equipment.EquipmentContainer,
                    _ref_uri(obj.equipmentContainer_mRID)))

    def _add_switch(self, g: Graph, s: URIRef, obj) -> None:
        self._add_equipment(g, s, obj)
        g.add((s, CIM.Switch.open,       _lit_bool(obj.open)))
        g.add((s, CIM.Switch.normalOpen, _lit_bool(obj.normalOpen)))
        g.add((s, CIM.Switch.retained,   _lit_bool(obj.retained)))

    def _to_bytes(self, g: Graph) -> bytes:
        buf = BytesIO()
        g.serialize(destination=buf, format="xml")
        return buf.getvalue()

    def write(self, data: bytes, path: str | Path) -> None:
        Path(path).write_bytes(data)
