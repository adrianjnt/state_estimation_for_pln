"""
SHACL Semantic Validator — IEC 61970-301 structural constraints.

Validates an EQ profile against a set of SHACL shapes (cim_shapes.ttl) to
catch data-quality issues *before* topology processing or state estimation runs.

Examples of rules enforced
--------------------------
* Every Terminal must reference exactly one ConnectivityNode.
* Every Terminal must reference exactly one ConductingEquipment.
* Every ConnectivityNode must be owned by exactly one container
  (Bay, VoltageLevel, or Line).
* No VoltageLevel may be orphaned from a Substation.
* Every island must have exactly one angle-reference node.

Dependencies
------------
``pyshacl`` must be installed (``pip install pyshacl``).  If it is not
available, the validator falls back to a built-in rule engine that checks
the most critical constraints directly against the profile objects.

Usage
-----
    validator = ShaclValidator()
    result = validator.validate(eq, tp)
    if not result.conforms:
        for v in result.violations:
            print(v)
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from rdflib import Graph, Namespace, RDF, URIRef
from rdflib.namespace import RDFS

from ..cim.namespaces import CIM_URI, RDF_URI, XML_PREFIXES
from ..cim.profiles.eq  import EquipmentProfile
from ..cim.profiles.ssh import SteadyStateHypothesisProfile
from ..cim.profiles.tp  import TopologyProfile

log = logging.getLogger(__name__)

CIM = Namespace(CIM_URI)

_SHAPES_PATH = Path(__file__).parent / "rules" / "cim_shapes.ttl"


@dataclass
class ShaclViolation:
    """One SHACL constraint violation."""
    severity:    str = "Violation"    # Violation | Warning | Info
    message:     str = ""
    focus_node:  str = ""            # mRID of the offending object
    source_shape: str = ""


@dataclass
class ShaclResult:
    """Aggregated result of a SHACL validation run."""
    conforms:   bool                  = True
    violations: List[ShaclViolation]  = field(default_factory=list)
    warnings:   List[ShaclViolation]  = field(default_factory=list)

    def summary(self) -> str:
        status = "CONFORMS" if self.conforms else "DOES NOT CONFORM"
        lines = [
            f"SHACL Validation: {status}",
            f"  Violations: {len(self.violations)}",
            f"  Warnings:   {len(self.warnings)}",
        ]
        for v in self.violations[:10]:
            lines.append(f"  [VIOLATION] {v.focus_node}: {v.message}")
        if len(self.violations) > 10:
            lines.append(f"  … and {len(self.violations)-10} more violations")
        return "\n".join(lines)


class ShaclValidator:
    """
    Validates CIM profile data against SHACL shapes.

    Tries pyshacl first; falls back to a built-in Python rule engine if
    pyshacl is not installed.
    """

    def __init__(self, shapes_path: Optional[Path] = None) -> None:
        self._shapes_path = shapes_path or _SHAPES_PATH
        self._pyshacl_available = importlib.util.find_spec("pyshacl") is not None
        if not self._pyshacl_available:
            log.warning(
                "pyshacl not found — using built-in rule engine.  "
                "Install with: pip install pyshacl"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cross-profile referential integrity (Task 2a)
    # ------------------------------------------------------------------

    def validate_cross_profile(self,
                                eq:  EquipmentProfile,
                                ssh: SteadyStateHypothesisProfile) -> ShaclResult:
        """
        Verify that every mRID referenced in the SSH profile resolves to a
        known object in the EQ profile's unified index.

        Checks performed
        ----------------
        • SwitchState.switch_mRID         → must be a Switch in EQ
        • TapStep.tapChanger_mRID          → must be a RatioTapChanger in EQ
        • MachineSetpoint.equipment_mRID   → must be a SynchronousMachine or
                                             ExternalNetworkInjection in EQ
        • LoadSetpoint.consumer_mRID       → must be an EnergyConsumer in EQ
        • AnalogValue.analog_mRID          → must be an Analog in EQ

        Returns a ShaclResult using the same violation/warning schema as
        ``validate()`` so callers can aggregate results uniformly.
        """
        from ..cim.model import (
            Breaker, Disconnector, LoadBreakSwitch, Fuse,
            RatioTapChanger, SynchronousMachine, ExternalNetworkInjection,
            EnergyConsumer, Analog,
        )

        result = ShaclResult(conforms=True)

        def _violation(mrid: str, msg: str, shape: str = "") -> None:
            result.conforms = False
            result.violations.append(
                ShaclViolation(message=msg, focus_node=mrid, source_shape=shape)
            )

        def _warning(mrid: str, msg: str, shape: str = "") -> None:
            result.warnings.append(
                ShaclViolation(severity="Warning", message=msg,
                               focus_node=mrid, source_shape=shape)
            )

        _SWITCH_TYPES = (Breaker, Disconnector, LoadBreakSwitch, Fuse)

        # ---- SwitchState.switch_mRID ----------------------------------------
        for sw_mrid, state in ssh.switch_states.items():
            eq_obj = eq.get(sw_mrid)
            if eq_obj is None:
                _violation(
                    sw_mrid,
                    f"SSH SwitchState references mRID {sw_mrid[:8]}… which does "
                    "not exist in the EQ profile.",
                    "SSHSwitchMRIDExistsInEQ",
                )
            elif not isinstance(eq_obj, _SWITCH_TYPES):
                _warning(
                    sw_mrid,
                    f"SSH SwitchState mRID {sw_mrid[:8]}… resolves to "
                    f"{type(eq_obj).__name__}, not a Switch sub-type.",
                    "SSHSwitchMRIDIsSwitch",
                )

        # ---- TapStep.tapChanger_mRID ----------------------------------------
        for tc_mrid, step in ssh.tap_steps.items():
            eq_obj = eq.get(tc_mrid)
            if eq_obj is None:
                _violation(
                    tc_mrid,
                    f"SSH TapStep references mRID {tc_mrid[:8]}… which does "
                    "not exist in the EQ profile.",
                    "SSHTapStepMRIDExistsInEQ",
                )
            elif not isinstance(eq_obj, RatioTapChanger):
                _warning(
                    tc_mrid,
                    f"SSH TapStep mRID {tc_mrid[:8]}… resolves to "
                    f"{type(eq_obj).__name__}, not a RatioTapChanger.",
                    "SSHTapStepMRIDIsRatioTapChanger",
                )

        # ---- MachineSetpoint.equipment_mRID ---------------------------------
        for eq_mrid, sp in ssh.machine_setpoints.items():
            eq_obj = eq.get(eq_mrid)
            if eq_obj is None:
                _violation(
                    eq_mrid,
                    f"SSH MachineSetpoint references mRID {eq_mrid[:8]}… which "
                    "does not exist in the EQ profile.",
                    "SSHMachineSetpointMRIDExistsInEQ",
                )
            elif not isinstance(eq_obj, (SynchronousMachine, ExternalNetworkInjection)):
                _warning(
                    eq_mrid,
                    f"SSH MachineSetpoint mRID {eq_mrid[:8]}… resolves to "
                    f"{type(eq_obj).__name__}; expected SynchronousMachine or "
                    "ExternalNetworkInjection.",
                    "SSHMachineSetpointMRIDType",
                )

        # ---- LoadSetpoint.consumer_mRID -------------------------------------
        for cons_mrid, sp in ssh.load_setpoints.items():
            eq_obj = eq.get(cons_mrid)
            if eq_obj is None:
                _violation(
                    cons_mrid,
                    f"SSH LoadSetpoint references mRID {cons_mrid[:8]}… which "
                    "does not exist in the EQ profile.",
                    "SSHLoadSetpointMRIDExistsInEQ",
                )
            elif not isinstance(eq_obj, EnergyConsumer):
                _warning(
                    cons_mrid,
                    f"SSH LoadSetpoint mRID {cons_mrid[:8]}… resolves to "
                    f"{type(eq_obj).__name__}, not an EnergyConsumer.",
                    "SSHLoadSetpointMRIDIsEnergyConsumer",
                )

        # ---- AnalogValue.analog_mRID ----------------------------------------
        orphan_count = 0
        for av_mrid, av in ssh.analog_values.items():
            if not av.analog_mRID:
                _warning(av_mrid,
                         "AnalogValue has no analog_mRID reference.",
                         "AnalogValueHasAnalog")
                continue
            eq_obj = eq.get(av.analog_mRID)
            if eq_obj is None:
                orphan_count += 1
                if orphan_count <= 10:   # cap individual violations to avoid noise
                    _violation(
                        av_mrid,
                        f"SSH AnalogValue references Analog {av.analog_mRID[:8]}… "
                        "which does not exist in the EQ profile.",
                        "SSHAnalogValueMRIDExistsInEQ",
                    )
            elif not isinstance(eq_obj, Analog):
                _warning(
                    av_mrid,
                    f"AnalogValue.analog_mRID {av.analog_mRID[:8]}… resolves to "
                    f"{type(eq_obj).__name__}, not an Analog.",
                    "SSHAnalogValueMRIDIsAnalog",
                )

        if orphan_count > 10:
            result.violations.append(ShaclViolation(
                message=(
                    f"… and {orphan_count - 10} more AnalogValues reference "
                    "unknown Analogs (suppressed)."
                ),
                source_shape="SSHAnalogValueMRIDExistsInEQ",
            ))

        total_mismatches = (
            sum(1 for v in result.violations)
            + sum(1 for w in result.warnings)
        )
        log.info(
            "Cross-profile integrity: %d violation(s), %d warning(s) "
            "across %d SSH objects",
            len(result.violations),
            len(result.warnings),
            (len(ssh.switch_states) + len(ssh.tap_steps)
             + len(ssh.machine_setpoints) + len(ssh.load_setpoints)
             + len(ssh.analog_values)),
        )
        return result

    # ------------------------------------------------------------------
    # Primary structural validation
    # ------------------------------------------------------------------

    def validate(self,
                 eq: EquipmentProfile,
                 tp: Optional[TopologyProfile] = None) -> ShaclResult:
        """
        Validate the EQ (and optionally TP) profile.

        If pyshacl is available, builds an RDF graph from the profiles and
        validates it against the shapes file.  Otherwise, runs the built-in
        Python rule engine.
        """
        if self._pyshacl_available and self._shapes_path.exists():
            return self._validate_with_pyshacl(eq, tp)
        return self._validate_builtin(eq, tp)

    # ------------------------------------------------------------------
    # pyshacl path
    # ------------------------------------------------------------------

    def _validate_with_pyshacl(self,
                                eq: EquipmentProfile,
                                tp: Optional[TopologyProfile]) -> ShaclResult:
        import pyshacl
        from ..cim.serializer import CgmesSerializer

        # Build a minimal RDF graph from the EQ profile to validate
        ser = CgmesSerializer()
        xml_bytes = ser.serialize_eq(eq)

        data_g = Graph()
        data_g.parse(data=xml_bytes, format="xml")

        shapes_g = Graph()
        shapes_g.parse(str(self._shapes_path), format="turtle")

        conforms, results_g, results_text = pyshacl.validate(
            data_g,
            shacl_graph=shapes_g,
            inference="none",
            abort_on_first=False,
        )
        return self._parse_pyshacl_results(conforms, results_g, results_text)

    def _parse_pyshacl_results(self,
                                conforms: bool,
                                results_g: Graph,
                                results_text: str) -> ShaclResult:
        SH = Namespace("http://www.w3.org/ns/shacl#")
        result = ShaclResult(conforms=conforms)

        for vr in results_g.subjects(RDF.type, SH.ValidationResult):
            severity_node = results_g.value(vr, SH.resultSeverity)
            severity = (
                "Violation" if severity_node == SH.Violation
                else "Warning"  if severity_node == SH.Warning
                else "Info"
            )
            message    = str(results_g.value(vr, SH.resultMessage)    or "")
            focus_node = str(results_g.value(vr, SH.focusNode)        or "")
            source     = str(results_g.value(vr, SH.sourceShape)      or "")

            v = ShaclViolation(severity=severity, message=message,
                               focus_node=focus_node, source_shape=source)
            if severity == "Violation":
                result.violations.append(v)
            else:
                result.warnings.append(v)

        return result

    # ------------------------------------------------------------------
    # Built-in rule engine (pyshacl fallback)
    # ------------------------------------------------------------------

    def _validate_builtin(self,
                           eq: EquipmentProfile,
                           tp: Optional[TopologyProfile]) -> ShaclResult:
        """Run Python-native CIM constraint checks."""
        result = ShaclResult(conforms=True)

        def _violation(mrid: str, msg: str, shape: str = "") -> None:
            result.conforms = False
            result.violations.append(
                ShaclViolation(message=msg, focus_node=mrid, source_shape=shape)
            )

        def _warning(mrid: str, msg: str, shape: str = "") -> None:
            result.warnings.append(
                ShaclViolation(severity="Warning", message=msg, focus_node=mrid,
                               source_shape=shape)
            )

        # ---- Terminal constraints ----
        for t in eq.terminals.values():
            if not t.connectivityNode_mRID:
                _violation(t.mRID,
                           "Terminal has no ConnectivityNode reference.",
                           "TerminalHasConnectivityNode")
            elif t.connectivityNode_mRID not in eq.connectivity_nodes:
                _violation(t.mRID,
                           f"Terminal references unknown CN "
                           f"{t.connectivityNode_mRID[:8]}…",
                           "TerminalConnectivityNodeExists")

            if not t.conductingEquipment_mRID:
                _violation(t.mRID,
                           "Terminal has no ConductingEquipment reference.",
                           "TerminalHasConductingEquipment")
            else:
                eq_obj = eq.get(t.conductingEquipment_mRID)
                if eq_obj is None:
                    _violation(t.mRID,
                               f"Terminal references unknown equipment "
                               f"{t.conductingEquipment_mRID[:8]}…",
                               "TerminalEquipmentExists")

        # ---- ConnectivityNode constraints ----
        for cn in eq.connectivity_nodes.values():
            if not cn.connectivityNodeContainer_mRID:
                _violation(cn.mRID,
                           "ConnectivityNode has no container reference.",
                           "ConnectivityNodeHasContainer")
            else:
                container = eq.get(cn.connectivityNodeContainer_mRID)
                if container is None:
                    _violation(cn.mRID,
                               "ConnectivityNode references an unknown container.",
                               "ConnectivityNodeContainerExists")

        # ---- VoltageLevel constraints ----
        for vl in eq.voltage_levels.values():
            if not vl.substation_mRID:
                _violation(vl.mRID,
                           "VoltageLevel is not linked to any Substation.",
                           "VoltageLevelHasSubstation")
            elif vl.substation_mRID not in eq.substations:
                _violation(vl.mRID,
                           "VoltageLevel references an unknown Substation.",
                           "VoltageLevelSubstationExists")

            if not vl.baseVoltage_mRID:
                _warning(vl.mRID,
                         "VoltageLevel has no BaseVoltage — nominal kV unknown.",
                         "VoltageLevelHasBaseVoltage")

        # ---- PowerTransformerEnd constraints ----
        for end in eq.transformer_ends.values():
            if not end.powerTransformer_mRID:
                _violation(end.mRID,
                           "PowerTransformerEnd not linked to a PowerTransformer.",
                           "TransformerEndHasTransformer")
            if not end.terminal_mRID:
                _violation(end.mRID,
                           "PowerTransformerEnd has no Terminal.",
                           "TransformerEndHasTerminal")

        # ---- Two-terminal equipment must have exactly 2 terminals ----
        from ..cim.model import ACLineSegment, PowerTransformer
        for acls in eq.ac_line_segments.values():
            terms = eq.terminals_of(acls.mRID)
            if len(terms) != 2:
                _warning(acls.mRID,
                         f"ACLineSegment has {len(terms)} terminal(s); expected 2.",
                         "ACLineSegmentTerminalCount")

        for pt in eq.power_transformers.values():
            ends_for_pt = [e for e in eq.transformer_ends.values()
                           if e.powerTransformer_mRID == pt.mRID]
            if len(ends_for_pt) < 2:
                _violation(pt.mRID,
                           f"PowerTransformer has {len(ends_for_pt)} end(s); "
                           "expected ≥ 2.",
                           "PowerTransformerEndsCount")

        # ---- Analog constraints ----
        for analog in eq.analogs.values():
            if not analog.terminal_mRID and not analog.powerSystemResource_mRID:
                _warning(analog.mRID,
                         "Analog measurement is not linked to a Terminal or PSR.",
                         "AnalogLinkedToAsset")

        # ---- TP island reference constraint ----
        if tp is not None:
            for ti in tp.topological_islands.values():
                if not ti.angleRefTopologicalNode_mRID:
                    _violation(ti.mRID,
                               "TopologicalIsland has no angle-reference node.",
                               "IslandHasAngleReference")
                elif ti.angleRefTopologicalNode_mRID not in tp.topological_nodes:
                    _violation(ti.mRID,
                               "TopologicalIsland angle-reference references unknown TN.",
                               "IslandAngleReferenceExists")
                if not ti.topologicalNodes:
                    _violation(ti.mRID,
                               "TopologicalIsland contains no nodes.",
                               "IslandNotEmpty")

            # Every TN must belong to exactly one island
            for tn in tp.topological_nodes.values():
                if not tn.topologicalIsland_mRID:
                    _warning(tn.mRID,
                             "TopologicalNode not assigned to any island.",
                             "TopologicalNodeHasIsland")

        return result
