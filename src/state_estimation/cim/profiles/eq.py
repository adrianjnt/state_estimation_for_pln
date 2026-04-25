"""
Equipment Profile (EQ) — IEC 61970-552 / CGMES.

Stores static network topology and nameplate data.  All objects are indexed by
their mRID so that cross-referencing between profiles is possible without
converting identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from ..model import (
    IdentifiedObject,
    GeographicalRegion,
    SubGeographicalRegion,
    Substation,
    VoltageLevel,
    Bay,
    Line,
    BaseVoltage,
    ConnectivityNode,
    Terminal,
    BusbarSection,
    Junction,
    Breaker,
    Disconnector,
    LoadBreakSwitch,
    Fuse,
    ACLineSegment,
    PowerTransformer,
    PowerTransformerEnd,
    RatioTapChanger,
    LinearShuntCompensator,
    EnergyConsumer,
    GeneratingUnit,
    SynchronousMachine,
    ExternalNetworkInjection,
    Analog,
)
from ..namespaces import PROFILE_EQ

# Type alias for any EQ-resident object
EQObject = Union[
    GeographicalRegion, SubGeographicalRegion, Substation, VoltageLevel, Bay,
    Line, BaseVoltage, ConnectivityNode, Terminal, BusbarSection, Junction,
    Breaker, Disconnector, LoadBreakSwitch, Fuse, ACLineSegment,
    PowerTransformer, PowerTransformerEnd, RatioTapChanger,
    LinearShuntCompensator, EnergyConsumer, GeneratingUnit, SynchronousMachine,
    ExternalNetworkInjection, Analog,
]


@dataclass
class EquipmentProfile:
    """
    Container for all objects belonging to the EQ profile.

    Objects are stored in typed registries *and* in a unified mRID → object
    lookup so that any cross-profile reference can be resolved in O(1).
    """
    model_id:   str = ""    # md:FullModel rdf:about UUID
    description: str = ""
    profile_uri: str = PROFILE_EQ

    # ---- typed registries -----------------------------------------------
    geographical_regions:     Dict[str, GeographicalRegion]     = field(default_factory=dict)
    sub_geographical_regions: Dict[str, SubGeographicalRegion]  = field(default_factory=dict)
    substations:              Dict[str, Substation]              = field(default_factory=dict)
    voltage_levels:           Dict[str, VoltageLevel]            = field(default_factory=dict)
    bays:                     Dict[str, Bay]                     = field(default_factory=dict)
    lines:                    Dict[str, Line]                    = field(default_factory=dict)
    base_voltages:            Dict[str, BaseVoltage]             = field(default_factory=dict)
    connectivity_nodes:       Dict[str, ConnectivityNode]        = field(default_factory=dict)
    terminals:                Dict[str, Terminal]                = field(default_factory=dict)
    busbar_sections:          Dict[str, BusbarSection]           = field(default_factory=dict)
    junctions:                Dict[str, Junction]                = field(default_factory=dict)
    breakers:                 Dict[str, Breaker]                 = field(default_factory=dict)
    disconnectors:            Dict[str, Disconnector]            = field(default_factory=dict)
    load_break_switches:      Dict[str, LoadBreakSwitch]         = field(default_factory=dict)
    fuses:                    Dict[str, Fuse]                    = field(default_factory=dict)
    ac_line_segments:         Dict[str, ACLineSegment]           = field(default_factory=dict)
    power_transformers:       Dict[str, PowerTransformer]        = field(default_factory=dict)
    transformer_ends:         Dict[str, PowerTransformerEnd]     = field(default_factory=dict)
    ratio_tap_changers:       Dict[str, RatioTapChanger]         = field(default_factory=dict)
    shunt_compensators:       Dict[str, LinearShuntCompensator]  = field(default_factory=dict)
    energy_consumers:         Dict[str, EnergyConsumer]          = field(default_factory=dict)
    generating_units:         Dict[str, GeneratingUnit]          = field(default_factory=dict)
    synchronous_machines:     Dict[str, SynchronousMachine]      = field(default_factory=dict)
    ext_net_injections:       Dict[str, ExternalNetworkInjection] = field(default_factory=dict)
    analogs:                  Dict[str, Analog]                  = field(default_factory=dict)

    # unified mRID → object (populated by _rebuild_index)
    _index: Dict[str, IdentifiedObject] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def add(self, obj: EQObject) -> None:
        """Register an object in the appropriate typed registry and index."""
        self._index[obj.mRID] = obj
        registry = self._registry_for(obj)
        if registry is not None:
            registry[obj.mRID] = obj  # type: ignore[index]

    def get(self, mrid: str) -> Optional[IdentifiedObject]:
        """Resolve any mRID to its object, or None if not found."""
        return self._index.get(mrid)

    def _registry_for(self, obj: EQObject):
        """Return the typed dict that should own this object."""
        type_map = {
            GeographicalRegion:      self.geographical_regions,
            SubGeographicalRegion:   self.sub_geographical_regions,
            Substation:              self.substations,
            VoltageLevel:            self.voltage_levels,
            Bay:                     self.bays,
            Line:                    self.lines,
            BaseVoltage:             self.base_voltages,
            ConnectivityNode:        self.connectivity_nodes,
            Terminal:                self.terminals,
            BusbarSection:           self.busbar_sections,
            Junction:                self.junctions,
            Breaker:                 self.breakers,
            Disconnector:            self.disconnectors,
            LoadBreakSwitch:         self.load_break_switches,
            Fuse:                    self.fuses,
            ACLineSegment:           self.ac_line_segments,
            PowerTransformer:        self.power_transformers,
            PowerTransformerEnd:     self.transformer_ends,
            RatioTapChanger:         self.ratio_tap_changers,
            LinearShuntCompensator:  self.shunt_compensators,
            EnergyConsumer:          self.energy_consumers,
            GeneratingUnit:          self.generating_units,
            SynchronousMachine:      self.synchronous_machines,
            ExternalNetworkInjection: self.ext_net_injections,
            Analog:                  self.analogs,
        }
        return type_map.get(type(obj))

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def terminals_of(self, equipment_mrid: str) -> List[Terminal]:
        """Return all Terminals whose conductingEquipment is `equipment_mrid`."""
        return [t for t in self.terminals.values()
                if t.conductingEquipment_mRID == equipment_mrid]

    def terminals_at_cn(self, cn_mrid: str) -> List[Terminal]:
        """Return all Terminals connected to a given ConnectivityNode."""
        return [t for t in self.terminals.values()
                if t.connectivityNode_mRID == cn_mrid]

    def voltage_level_of(self, substation_mrid: str) -> List[VoltageLevel]:
        return [vl for vl in self.voltage_levels.values()
                if vl.substation_mRID == substation_mrid]

    def all_switches(self) -> List[Union[Breaker, Disconnector, LoadBreakSwitch, Fuse]]:
        return (
            list(self.breakers.values())
            + list(self.disconnectors.values())
            + list(self.load_break_switches.values())
            + list(self.fuses.values())
        )

    def all_conducting_equipment(self):
        """Yield every ConductingEquipment regardless of sub-type."""
        for d in (
            self.busbar_sections, self.junctions, self.breakers,
            self.disconnectors, self.load_break_switches, self.fuses,
            self.ac_line_segments, self.power_transformers,
            self.shunt_compensators, self.energy_consumers,
            self.synchronous_machines, self.ext_net_injections,
        ):
            yield from d.values()

    def rebuild_index(self) -> None:
        """Rebuild the unified mRID index from all typed registries."""
        self._index.clear()
        for d in (
            self.geographical_regions, self.sub_geographical_regions,
            self.substations, self.voltage_levels, self.bays, self.lines,
            self.base_voltages, self.connectivity_nodes, self.terminals,
            self.busbar_sections, self.junctions, self.breakers,
            self.disconnectors, self.load_break_switches, self.fuses,
            self.ac_line_segments, self.power_transformers,
            self.transformer_ends, self.ratio_tap_changers,
            self.shunt_compensators, self.energy_consumers,
            self.generating_units, self.synchronous_machines,
            self.ext_net_injections, self.analogs,
        ):
            self._index.update(d)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"EquipmentProfile  model_id={self.model_id}",
            f"  Substations:          {len(self.substations)}",
            f"  VoltageLevels:        {len(self.voltage_levels)}",
            f"  Bays:                 {len(self.bays)}",
            f"  ConnectivityNodes:    {len(self.connectivity_nodes)}",
            f"  Terminals:            {len(self.terminals)}",
            f"  BusbarSections:       {len(self.busbar_sections)}",
            f"  Breakers:             {len(self.breakers)}",
            f"  Disconnectors:        {len(self.disconnectors)}",
            f"  ACLineSegments:       {len(self.ac_line_segments)}",
            f"  PowerTransformers:    {len(self.power_transformers)}",
            f"  TransformerEnds:      {len(self.transformer_ends)}",
            f"  ShuntCompensators:    {len(self.shunt_compensators)}",
            f"  EnergyConsumers:      {len(self.energy_consumers)}",
            f"  SynchronousMachines:  {len(self.synchronous_machines)}",
            f"  ExtNetInjections:     {len(self.ext_net_injections)}",
            f"  Analogs:              {len(self.analogs)}",
        ]
        return "\n".join(lines)
