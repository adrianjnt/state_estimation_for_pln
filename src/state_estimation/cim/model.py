"""
CIM data model — IEC 61970-301 CIM100.

Every class derives from IdentifiedObject and carries an immutable mRID (UUID4
string).  References between objects use mRID strings; resolution is done by
the profile containers (EQ, TP, SSH, SV) that own the object registries.

Hierarchy (simplified):
  IdentifiedObject
    PowerSystemResource
      ConnectivityNodeContainer
        EquipmentContainer
          Substation
          VoltageLevel
          Bay
        Line              (long-distance segment container)
      Equipment
        ConductingEquipment
          Connector
            BusbarSection
            Junction
          Switch
            Breaker
            Disconnector
            LoadBreakSwitch
          Conductor
            ACLineSegment
          PowerTransformer
    ACDCTerminal
      Terminal
    ConnectivityNode
    BaseVoltage
    PowerTransformerEnd
    RatioTapChanger

TP-profile additions:
  TopologicalNode
  TopologicalIsland

SV-profile additions:
  StateVariable
    SvVoltage
    SvPowerFlow
    SvTapStep

Measurement classes (SSH / EQ):
  Analog, AnalogValue
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_mrid() -> str:
    """Return a new UUID4 string for use as mRID."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class UnitSymbol(str, Enum):
    """Subset of CIM UnitSymbol relevant to SE."""
    V   = "V"
    A   = "A"
    W   = "W"
    VA  = "VA"
    VAr = "VAr"
    deg = "deg"
    Hz  = "Hz"
    ohm = "ohm"
    S   = "S"
    none = "none"


class UnitMultiplier(str, Enum):
    k = "k"    # kilo
    M = "M"    # mega
    G = "G"    # giga
    m = "m"    # milli
    none = "none"


class PhaseCode(str, Enum):
    A   = "A"
    B   = "B"
    C   = "C"
    ABC = "ABC"
    N   = "N"


class WindingConnection(str, Enum):
    D  = "D"
    Yn = "Yn"
    Y  = "Y"
    Z  = "Z"
    Zn = "Zn"
    A  = "A"
    I  = "I"


class MeasurementType(str, Enum):
    ThreePhaseActivePower   = "ThreePhaseActivePower"
    ThreePhaseReactivePower = "ThreePhaseReactivePower"
    VoltageAngle            = "VoltageAngle"
    VoltageMagnitude        = "VoltageMagnitude"
    CurrentMagnitude        = "CurrentMagnitude"


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------

@dataclass
class IdentifiedObject:
    """Root of the CIM class hierarchy. Every object MUST have an mRID."""
    mRID:        str           = field(default_factory=_new_mrid)
    name:        str           = ""
    description: str           = ""
    aliasName:   str           = ""

    def __post_init__(self) -> None:
        if not self.mRID:
            self.mRID = _new_mrid()

    def __hash__(self) -> int:
        return hash(self.mRID)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IdentifiedObject):
            return NotImplemented
        return self.mRID == other.mRID


@dataclass
class PowerSystemResource(IdentifiedObject):
    """Abstract — a piece of the power system."""
    pass


# ---------------------------------------------------------------------------
# Geographic / containment hierarchy
# ---------------------------------------------------------------------------

@dataclass
class GeographicalRegion(PowerSystemResource):
    """Broadest geographic grouping (country / island)."""
    pass


@dataclass
class SubGeographicalRegion(PowerSystemResource):
    """Province / zone within a GeographicalRegion."""
    region_mRID: str = ""   # → GeographicalRegion.mRID


@dataclass
class ConnectivityNodeContainer(PowerSystemResource):
    """Abstract — can own ConnectivityNodes."""
    pass


@dataclass
class EquipmentContainer(ConnectivityNodeContainer):
    """Abstract — can own Equipment."""
    pass


@dataclass
class Substation(EquipmentContainer):
    """Physical substation site."""
    subGeographicalRegion_mRID: str = ""   # → SubGeographicalRegion.mRID


@dataclass
class VoltageLevel(EquipmentContainer):
    """A set of equipment at the same nominal voltage inside a Substation."""
    substation_mRID:  str   = ""   # → Substation.mRID
    baseVoltage_mRID: str   = ""   # → BaseVoltage.mRID
    highVoltageLimit: float = 0.0  # kV
    lowVoltageLimit:  float = 0.0  # kV


@dataclass
class Bay(EquipmentContainer):
    """Sectional grouping within a VoltageLevel (panel / feeder bay)."""
    voltageLevel_mRID: str = ""    # → VoltageLevel.mRID


@dataclass
class Line(EquipmentContainer):
    """Container for ACLineSegments forming a named line."""
    region_mRID: str = ""


@dataclass
class BaseVoltage(IdentifiedObject):
    """Nominal voltage for a VoltageLevel."""
    nominalVoltage: float = 0.0    # kV


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@dataclass
class ConnectivityNode(IdentifiedObject):
    """
    Node in the node-breaker topology.  Physically represents a single
    equipotential point in the as-built network.
    """
    connectivityNodeContainer_mRID: str = ""   # → Bay / VoltageLevel / Line
    topologicalNode_mRID:           str = ""   # set by TP processor


@dataclass
class ACDCTerminal(IdentifiedObject):
    """Abstract terminal (DC not needed for SE, kept for completeness)."""
    sequenceNumber: int  = 1
    connected:      bool = True


@dataclass
class Terminal(ACDCTerminal):
    """
    Connection point of ConductingEquipment to a ConnectivityNode.

    `sequenceNumber` distinguishes sides (1 = from-side, 2 = to-side for a
    two-terminal device).
    """
    conductingEquipment_mRID: str = ""   # → ConductingEquipment.mRID
    connectivityNode_mRID:    str = ""   # → ConnectivityNode.mRID
    topologicalNode_mRID:     str = ""   # set by TP processor (via CN → TN)
    phases:                   PhaseCode = PhaseCode.ABC


# ---------------------------------------------------------------------------
# Equipment hierarchy
# ---------------------------------------------------------------------------

@dataclass
class Equipment(PowerSystemResource):
    """Abstract — a single piece of equipment."""
    equipmentContainer_mRID: str  = ""    # → EquipmentContainer.mRID
    inService:               bool = True
    normallyInService:       bool = True


@dataclass
class ConductingEquipment(Equipment):
    """Abstract — equipment that carries current."""
    baseVoltage_mRID: str = ""   # → BaseVoltage.mRID


# ---- Connectors -----------------------------------------------------------

@dataclass
class Connector(ConductingEquipment):
    """Abstract — equipment that just connects conductors."""
    pass


@dataclass
class BusbarSection(Connector):
    """
    A length of busbar in a substation.  The canonical anchor point for a
    CIM equipotential area — every bay's connectivity node connects here.
    """
    ipMax: float = 0.0   # maximum allowable peak short-circuit current (kA)


@dataclass
class Junction(Connector):
    """A point where multiple conductors meet without switching."""
    pass


# ---- Switches -------------------------------------------------------------

@dataclass
class Switch(ConductingEquipment):
    """Abstract — a device that can open or close a circuit."""
    normalOpen: bool  = False
    open:       bool  = False
    retained:   bool  = False   # true → retained in topology reduction
    ratedCurrent: float = 0.0   # A


@dataclass
class ProtectedSwitch(Switch):
    """Abstract — a switch with protection relay."""
    breakingCapacity: float = 0.0   # MVA


@dataclass
class Breaker(ProtectedSwitch):
    """Circuit breaker — primary switching device used in topology processing."""
    pass


@dataclass
class Disconnector(Switch):
    """Isolator — open only when de-energised."""
    pass


@dataclass
class LoadBreakSwitch(ProtectedSwitch):
    """Can interrupt load current."""
    pass


@dataclass
class Fuse(Switch):
    """One-shot protective switch."""
    ratingCurrentValue: float = 0.0


# ---- Conductors -----------------------------------------------------------

@dataclass
class Conductor(ConductingEquipment):
    """Abstract — a length of conductor."""
    length: float = 0.0   # km


@dataclass
class ACLineSegment(Conductor):
    """A single homogeneous segment of an AC transmission line."""
    r:          float = 0.0   # Ω/km at 20°C
    x:          float = 0.0   # Ω/km
    bch:        float = 0.0   # S/km (total charging susceptance)
    gch:        float = 0.0   # S/km (total conductance)
    r0:         float = 0.0   # zero-sequence R (Ω/km)
    x0:         float = 0.0   # zero-sequence X (Ω/km)
    b0ch:       float = 0.0
    g0ch:       float = 0.0
    shortCircuitEndTemperature: float = 0.0  # °C


# ---- Transformers ---------------------------------------------------------

@dataclass
class PowerTransformer(ConductingEquipment):
    """
    A set of windings coupled through electromagnetic induction.
    Ends (windings) are modelled separately as PowerTransformerEnd.
    """
    vectorGroup:           str   = ""
    isPartOfGeneratorUnit: bool  = False
    beforeShCircuitHighestOperatingCurrent: float = 0.0


@dataclass
class TapChanger(PowerSystemResource):
    """Abstract — controls voltage by varying transformer turns ratio."""
    lowStep:           int   = -10
    highStep:          int   =  10
    neutralStep:       int   =   0
    normalStep:        int   =   0
    neutralU:          float = 0.0   # kV at neutral tap
    ltcFlag:           bool  = False
    controlEnabled:    bool  = True
    step:              float = 0.0   # current position (SSH value)


@dataclass
class RatioTapChanger(TapChanger):
    """Tap changer that varies turns ratio."""
    transformerEnd_mRID:   str   = ""
    stepVoltageIncrement:  float = 0.0   # % of ratedU per step
    tculControlMode:       str   = "volt"


@dataclass
class PowerTransformerEnd(IdentifiedObject):
    """
    One winding of a PowerTransformer.
    sequenceNumber=1 → HV winding; =2 → LV winding; =3 → tertiary.
    """
    powerTransformer_mRID: str            = ""
    terminal_mRID:         str            = ""
    baseVoltage_mRID:      str            = ""
    sequenceNumber:        int            = 1
    ratedS:                float          = 0.0    # MVA
    ratedU:                float          = 0.0    # kV
    r:                     float          = 0.0    # Ω (referred to ratedU)
    x:                     float          = 0.0    # Ω
    r0:                    float          = 0.0
    x0:                    float          = 0.0
    g:                     float          = 0.0    # S  (core loss conductance)
    b:                     float          = 0.0    # S  (magnetising susceptance)
    g0:                    float          = 0.0
    b0:                    float          = 0.0
    phaseAngleClock:       int            = 0
    connectionKind:        WindingConnection = WindingConnection.Yn
    grounded:              bool           = True
    xground:               float          = 0.0


# ---- Shunt ----------------------------------------------------------------

@dataclass
class ShuntCompensator(ConductingEquipment):
    """Fixed or switchable shunt capacitor / reactor bank."""
    nomU:               float = 0.0   # kV
    maximumSections:    int   = 1
    normalSections:     int   = 1
    bPerSection:        float = 0.0   # S per section
    gPerSection:        float = 0.0
    b0PerSection:       float = 0.0
    g0PerSection:       float = 0.0
    voltageSensitivity: float = 0.0


@dataclass
class LinearShuntCompensator(ShuntCompensator):
    pass


# ---- Generators & Loads ---------------------------------------------------

@dataclass
class EnergyConsumer(ConductingEquipment):
    """Load — P and Q set in SSH profile."""
    p:             float = 0.0   # MW  (SSH)
    q:             float = 0.0   # Mvar (SSH)
    pfixed:        float = 0.0
    qfixed:        float = 0.0
    grounded:      bool  = False
    phaseConnection: str = "D"


@dataclass
class GeneratingUnit(PowerSystemResource):
    """A single generating unit."""
    nominalP:      float = 0.0   # MW
    maxOperatingP: float = 0.0   # MW
    minOperatingP: float = 0.0   # MW
    normalPF:      float = 1.0


@dataclass
class SynchronousMachine(ConductingEquipment):
    """Rotating synchronous machine (generator or motor)."""
    generatingUnit_mRID: str   = ""
    ratedS:              float = 0.0   # MVA
    ratedU:              float = 0.0   # kV
    p:                   float = 0.0   # MW  (SSH)
    q:                   float = 0.0   # Mvar (SSH)
    qMin:                float = 0.0
    qMax:                float = 0.0
    referencePriority:   int   = 0     # 0 = not a reference node


@dataclass
class ExternalNetworkInjection(ConductingEquipment):
    """Equivalent injection representing an external network (slack bus)."""
    p:                    float = 0.0   # MW  (SSH)
    q:                    float = 0.0   # Mvar (SSH)
    regulationStatus:     bool  = True
    referencePriority:    int   = 1     # typically 1 for the angle reference


# ---------------------------------------------------------------------------
# TP Profile — results of topology processing
# ---------------------------------------------------------------------------

@dataclass
class TopologicalNode(IdentifiedObject):
    """
    Bus in the bus-branch model.  Created by merging ConnectivityNodes that
    are electrically connected through closed switches.

    Created by the Topology Processor; not present in EQ profile.
    """
    baseVoltage_mRID:      str   = ""
    connectivityNodeContainer_mRID: str = ""   # the VoltageLevel it belongs to
    connectivityNodes:     list[str] = field(default_factory=list)  # CN mRIDs
    topologicalIsland_mRID: str  = ""

    # Computed geometry / display
    angleReferenceMachine_mRID: str = ""


@dataclass
class TopologicalIsland(IdentifiedObject):
    """
    A set of TopologicalNodes connected by closed branches and forming a
    single synchronously-coupled island.  Needs exactly one angle reference.
    """
    topologicalNodes:          list[str] = field(default_factory=list)  # TN mRIDs
    angleRefTopologicalNode_mRID: str    = ""


# ---------------------------------------------------------------------------
# SV Profile — State Variable results
# ---------------------------------------------------------------------------

@dataclass
class StateVariable(IdentifiedObject):
    """Abstract root of SV profile."""
    pass


@dataclass
class SvVoltage(StateVariable):
    """
    Estimated voltage at a TopologicalNode.
    v     → magnitude in kV
    angle → phase angle in degrees (relative to angle-reference node)
    """
    v:                     float = 0.0
    angle:                 float = 0.0
    topologicalNode_mRID:  str   = ""


@dataclass
class SvPowerFlow(StateVariable):
    """Estimated power flow through a Terminal."""
    p:            float = 0.0   # MW  (positive = into equipment)
    q:            float = 0.0   # Mvar
    terminal_mRID: str  = ""


@dataclass
class SvTapStep(StateVariable):
    """Estimated tap changer position."""
    position:         float = 0.0
    tapChanger_mRID:  str   = ""


@dataclass
class SvInjection(StateVariable):
    """Net injection at a TopologicalNode (sum of all connected injections)."""
    pInjection:           float = 0.0   # MW
    qInjection:           float = 0.0   # Mvar
    topologicalNode_mRID: str   = ""


# ---------------------------------------------------------------------------
# Measurement classes (used in SSH / EQ profiles)
# ---------------------------------------------------------------------------

@dataclass
class Measurement(IdentifiedObject):
    """Abstract — a sensor attached to a PowerSystemResource or Terminal."""
    powerSystemResource_mRID: str           = ""
    terminal_mRID:            str           = ""
    measurementType:          str           = ""
    phases:                   PhaseCode     = PhaseCode.ABC
    unitSymbol:               UnitSymbol    = UnitSymbol.none
    unitMultiplier:           UnitMultiplier = UnitMultiplier.none


@dataclass
class Analog(Measurement):
    """A continuous (real-valued) measurement."""
    positiveFlowIn: bool = True   # true → positive value means flow into element


@dataclass
class AnalogValue(IdentifiedObject):
    """A specific reading of an Analog measurement (SSH profile)."""
    value:        float = 0.0
    analog_mRID:  str   = ""
    timeStamp:    str   = ""
    quality:      str   = "act"    # IEC 61850 quality code
    suspect:      bool  = False
    stdDev:       float = 0.0      # measurement uncertainty σ (for WLS weight)
