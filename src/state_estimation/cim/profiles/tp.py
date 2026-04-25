"""
Topology Profile (TP) — IEC 61970-552 / CGMES.

Contains the results of topology processing: TopologicalNodes (buses) and
TopologicalIslands.  Objects here are derived from the EQ profile by the
Topology Processor and must never be hand-edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..model import TopologicalNode, TopologicalIsland
from ..namespaces import PROFILE_TP


@dataclass
class TopologyProfile:
    """Container for TP-profile objects."""
    model_id:   str = ""
    profile_uri: str = PROFILE_TP
    # mRID of the EQ FullModel this TP augments
    eq_model_id: str = ""

    topological_nodes:   Dict[str, TopologicalNode]  = field(default_factory=dict)
    topological_islands: Dict[str, TopologicalIsland] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_node(self, tn: TopologicalNode) -> None:
        self.topological_nodes[tn.mRID] = tn

    def add_island(self, ti: TopologicalIsland) -> None:
        self.topological_islands[ti.mRID] = ti

    def get_node(self, mrid: str) -> Optional[TopologicalNode]:
        return self.topological_nodes.get(mrid)

    def get_island(self, mrid: str) -> Optional[TopologicalIsland]:
        return self.topological_islands.get(mrid)

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    def nodes_in_island(self, island_mrid: str) -> List[TopologicalNode]:
        ti = self.topological_islands.get(island_mrid)
        if ti is None:
            return []
        return [self.topological_nodes[m] for m in ti.topologicalNodes
                if m in self.topological_nodes]

    def island_of_node(self, tn_mrid: str) -> Optional[TopologicalIsland]:
        tn = self.topological_nodes.get(tn_mrid)
        if tn is None:
            return None
        return self.topological_islands.get(tn.topologicalIsland_mRID)

    def reference_node_of_island(self, island_mrid: str) -> Optional[TopologicalNode]:
        ti = self.topological_islands.get(island_mrid)
        if ti is None or not ti.angleRefTopologicalNode_mRID:
            return None
        return self.topological_nodes.get(ti.angleRefTopologicalNode_mRID)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"TopologyProfile  model_id={self.model_id}  (EQ: {self.eq_model_id})",
            f"  TopologicalNodes:   {len(self.topological_nodes)}",
            f"  TopologicalIslands: {len(self.topological_islands)}",
        ]
        for ti in self.topological_islands.values():
            ref = ti.angleRefTopologicalNode_mRID or "(none)"
            lines.append(
                f"    Island '{ti.name}': {len(ti.topologicalNodes)} nodes, "
                f"ref={ref}"
            )
        return "\n".join(lines)
