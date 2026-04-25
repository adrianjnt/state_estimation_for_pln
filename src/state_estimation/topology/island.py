"""
TopologicalIsland detector — IEC 61970-301 §9.

After the Topology Processor has created TopologicalNodes, this module
identifies which nodes form synchronously-coupled islands by walking the
bus-branch graph (branches = closed non-switching conducting equipment).

An island is a maximal connected subgraph of the TN graph.  Every island
needs exactly one angle-reference node (the one with the highest-priority
``referencePriority`` among ExternalNetworkInjection / SynchronousMachine
objects connected to it).

Usage
-----
    detector = IslandDetector(eq, tp)
    tp = detector.detect()   # modifies tp in-place and returns it
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set

from ..cim.model import TopologicalIsland, ExternalNetworkInjection, SynchronousMachine
from ..cim.profiles.eq import EquipmentProfile
from ..cim.profiles.tp import TopologyProfile

log = logging.getLogger(__name__)


class IslandDetector:
    """
    Detects synchronous islands within a populated TopologyProfile.

    Parameters
    ----------
    eq : EquipmentProfile
    tp : TopologyProfile  — must have been populated by TopologyProcessor first
    """

    def __init__(self, eq: EquipmentProfile, tp: TopologyProfile) -> None:
        self.eq = eq
        self.tp = tp

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def detect(self) -> TopologyProfile:
        """
        Group TopologicalNodes into TopologicalIslands.

        Modifies ``self.tp`` in-place:
        - Clears and repopulates ``tp.topological_islands``
        - Sets ``TopologicalNode.topologicalIsland_mRID``
        - Sets ``TopologicalIsland.angleRefTopologicalNode_mRID``

        Returns the modified profile.
        """
        log.info("IslandDetector: building TN adjacency graph")
        adj = self._build_adjacency()

        visited: Set[str] = set()
        self.tp.topological_islands.clear()

        for seed_mrid in self.tp.topological_nodes:
            if seed_mrid in visited:
                continue
            # BFS from seed
            component = self._bfs(seed_mrid, adj)
            visited |= component

            island_mrid = str(uuid.uuid4())
            ref_mrid = self._choose_reference(component)

            island = TopologicalIsland(
                mRID=island_mrid,
                name=f"Island_{island_mrid[:8]}",
                topologicalNodes=sorted(component),
                angleRefTopologicalNode_mRID=ref_mrid or "",
            )
            self.tp.add_island(island)

            for tn_mrid in component:
                tn = self.tp.topological_nodes[tn_mrid]
                tn.topologicalIsland_mRID = island_mrid

        log.info("IslandDetector: %d island(s) detected",
                 len(self.tp.topological_islands))
        for ti in self.tp.topological_islands.values():
            ref = ti.angleRefTopologicalNode_mRID or "(none)"
            log.info("  Island %s: %d nodes, ref=%s",
                     ti.name, len(ti.topologicalNodes), ref)

        return self.tp

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_adjacency(self) -> Dict[str, Set[str]]:
        """
        Build TN–TN adjacency from all non-switching ConductingEquipment
        (ACLineSegment, PowerTransformer, etc.) whose Terminals are connected
        to different TopologicalNodes.

        Switching devices (Breaker, Disconnector, …) are intentionally excluded
        because they define the island *boundaries*, not the connections within.
        """
        from ..cim.model import (
            Breaker, Disconnector, LoadBreakSwitch, Fuse,
            BusbarSection, Junction,
        )
        SWITCHING_TYPES = (Breaker, Disconnector, LoadBreakSwitch, Fuse)
        INTERNAL_TYPES  = (BusbarSection, Junction)

        adj: Dict[str, Set[str]] = defaultdict(set)

        # Index: equipment_mRID → list[terminal]
        eq_terminals: Dict[str, List] = defaultdict(list)
        for t in self.eq.terminals.values():
            eq_terminals[t.conductingEquipment_mRID].append(t)

        for eq_mrid, terminals in eq_terminals.items():
            eq_obj = self.eq.get(eq_mrid)
            if eq_obj is None:
                continue
            # Skip switching devices and internal connectors (already merged)
            if isinstance(eq_obj, (SWITCHING_TYPES + INTERNAL_TYPES)):
                continue
            # Collect unique TN mRIDs from all terminals of this equipment
            tn_mrids = list({
                t.topologicalNode_mRID
                for t in terminals
                if t.topologicalNode_mRID
            })
            # Add edges between every pair of TNs (branch creates connectivity)
            for i in range(len(tn_mrids)):
                for j in range(i + 1, len(tn_mrids)):
                    a, b = tn_mrids[i], tn_mrids[j]
                    adj[a].add(b)
                    adj[b].add(a)

        return adj

    # ------------------------------------------------------------------
    # BFS
    # ------------------------------------------------------------------

    @staticmethod
    def _bfs(start: str, adj: Dict[str, Set[str]]) -> Set[str]:
        visited = {start}
        queue   = deque([start])
        while queue:
            node = queue.popleft()
            for neighbour in adj.get(node, ()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
        return visited

    # ------------------------------------------------------------------
    # Reference node selection
    # ------------------------------------------------------------------

    def _choose_reference(self, tn_mrids: Set[str]) -> Optional[str]:
        """
        Select the angle-reference node for an island.

        Priority:
        1. ExternalNetworkInjection with referencePriority = 1 (slack bus).
        2. ExternalNetworkInjection with any referencePriority > 0.
        3. SynchronousMachine with the highest referencePriority.
        4. If none found, pick the first node alphabetically (fallback).
        """
        candidates: List[tuple] = []   # (priority, tn_mrid, equipment_mRID)

        for eq_obj in (
            list(self.eq.ext_net_injections.values())
            + list(self.eq.synchronous_machines.values())
        ):
            ref_p = getattr(eq_obj, "referencePriority", 0)
            if ref_p <= 0:
                continue
            # Find which TN this machine is in
            for t in self.eq.terminals_of(eq_obj.mRID):
                if t.topologicalNode_mRID in tn_mrids:
                    candidates.append((ref_p, t.topologicalNode_mRID, eq_obj.mRID))

        if candidates:
            # Lowest referencePriority number = highest priority (1 = slack)
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]

        # Fallback — no reference machine found; use first node
        if tn_mrids:
            chosen = sorted(tn_mrids)[0]
            log.warning(
                "IslandDetector: no reference machine found for island containing "
                "%s; defaulting to node %s", chosen, chosen
            )
            return chosen
        return None
