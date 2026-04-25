"""
Topology Processor — ConnectivityNode → TopologicalNode (CN → TN) logic.

Implements the "active topology" processing required by IEC 61970-301 §9 and
the CGMES TP profile.

Algorithm
---------
1.  Start from the EQ profile's ConnectivityNodes and Terminals.
2.  Build an undirected graph where nodes are ConnectivityNode mRIDs and edges
    exist between any two CNs that are connected by a *closed*, *retained*
    switching device (Breaker, Disconnector, LoadBreakSwitch, Fuse).
3.  Run Union-Find (disjoint-set union with path compression) over this graph.
    Each equivalence class becomes one TopologicalNode.
4.  Write back:
    - `ConnectivityNode.topologicalNode_mRID`
    - `Terminal.topologicalNode_mRID`  (derived via its CN)
5.  Populate the TP profile with the resulting TopologicalNode objects.

Switch status is read from the SSH profile if provided; otherwise the EQ
``Switch.open`` / ``Switch.normalOpen`` field is used.

Re-running ``process()`` after a switch-status change replaces the TP profile
contents and updates all back-references in the EQ profile in-place — no
objects are deleted, only reassigned.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Set, Tuple

from ..cim.model import (
    ConnectivityNode, Terminal, TopologicalNode,
    Breaker, Disconnector, LoadBreakSwitch, Fuse, BusbarSection,
)
from ..cim.profiles.eq  import EquipmentProfile
from ..cim.profiles.tp  import TopologyProfile
from ..cim.profiles.ssh import SteadyStateHypothesisProfile

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Union-Find with path compression + union by rank
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}
        self._rank:   Dict[str, int] = {}

    def add(self, x: str) -> None:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x]   = 0

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]   # path halving
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        """Merge sets containing a and b.  Returns True if they were disjoint."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        return True

    def groups(self) -> Dict[str, List[str]]:
        """Return {representative_mRID: [member_mRIDs]} for every group."""
        groups: Dict[str, List[str]] = {}
        for x in self._parent:
            root = self.find(x)
            groups.setdefault(root, []).append(x)
        return groups


# ---------------------------------------------------------------------------
# Topology Processor
# ---------------------------------------------------------------------------

class TopologyProcessor:
    """
    Converts EQ-level node-breaker topology into TP-level bus-branch topology.

    Parameters
    ----------
    eq  : EquipmentProfile
        The parsed EQ profile (modified in-place to write back TN mRIDs).
    ssh : SteadyStateHypothesisProfile, optional
        Current operating state (switch positions).  If None, EQ defaults used.
    """

    def __init__(self,
                 eq:  EquipmentProfile,
                 ssh: Optional[SteadyStateHypothesisProfile] = None) -> None:
        self.eq  = eq
        self.ssh = ssh

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self) -> TopologyProfile:
        """
        Run the full CN → TN aggregation.

        Returns a freshly populated TopologyProfile.  Also updates:
        - ``ConnectivityNode.topologicalNode_mRID``
        - ``Terminal.topologicalNode_mRID``
        """
        log.info("TopologyProcessor: starting CN→TN aggregation")
        tp = TopologyProfile()

        uf = _UnionFind()

        # 1. Seed Union-Find with every CN
        for cn_mrid in self.eq.connectivity_nodes:
            uf.add(cn_mrid)

        # 2. Merge CNs connected through closed switches
        merged = 0
        for sw in self.eq.all_switches():
            if not self._is_closed(sw):
                continue
            terminals = self.eq.terminals_of(sw.mRID)
            if len(terminals) < 2:
                continue
            cn_mrids = [t.connectivityNode_mRID for t in terminals
                        if t.connectivityNode_mRID]
            for i in range(len(cn_mrids) - 1):
                if uf.union(cn_mrids[i], cn_mrids[i + 1]):
                    merged += 1

        log.info("  merged %d CN pairs via closed switches", merged)

        # 3. Create one TopologicalNode per equivalence class
        groups = uf.groups()
        log.info("  %d ConnectivityNodes → %d TopologicalNodes",
                 len(self.eq.connectivity_nodes), len(groups))

        # Map representative CN mRID → TN mRID
        rep_to_tn: Dict[str, str] = {}

        for rep, members in groups.items():
            tn_mrid = str(uuid.uuid4())
            rep_to_tn[rep] = tn_mrid

            # Determine BaseVoltage and container from any member CN
            bv_mrid, container_mrid = self._base_voltage_of_cn_group(members)

            # Name after the busbar in this group (if any) or the first CN
            tn_name = self._name_for_group(members)

            tn = TopologicalNode(
                mRID=tn_mrid,
                name=tn_name,
                baseVoltage_mRID=bv_mrid,
                connectivityNodeContainer_mRID=container_mrid,
                connectivityNodes=members[:],
            )
            tp.add_node(tn)

        # 4. Write back TN mRID to every CN and its Terminals
        for cn_mrid, cn in self.eq.connectivity_nodes.items():
            rep  = uf.find(cn_mrid)
            tn_mrid = rep_to_tn[rep]
            cn.topologicalNode_mRID = tn_mrid

        for terminal in self.eq.terminals.values():
            cn = self.eq.connectivity_nodes.get(terminal.connectivityNode_mRID)
            if cn:
                terminal.topologicalNode_mRID = cn.topologicalNode_mRID

        log.info("TopologyProcessor: done — %d TopologicalNodes created",
                 len(tp.topological_nodes))
        return tp

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_closed(self, sw) -> bool:
        """
        A switch is *closed* (merges topology) when open=False.
        Retained switches (isolators that stay in the reduced model) are also
        treated as closed for topology purposes.
        """
        if self.ssh:
            return not self.ssh.is_switch_open(sw.mRID, eq_default=sw.open)
        return not sw.open

    def _base_voltage_of_cn_group(self,
                                   cn_mrids: List[str]) -> Tuple[str, str]:
        """
        Return (baseVoltage_mRID, container_mRID) for a CN group.

        Strategy: look at every Terminal attached to a member CN; trace its
        ConductingEquipment → its equipmentContainer → its VoltageLevel →
        BaseVoltage.  Use the first valid one found.
        """
        for cn_mrid in cn_mrids:
            for t in self.eq.terminals_at_cn(cn_mrid):
                eq_obj = self.eq.get(t.conductingEquipment_mRID)
                if eq_obj is None:
                    continue
                container_mrid = getattr(eq_obj, "equipmentContainer_mRID", "")
                bv_mrid = getattr(eq_obj, "baseVoltage_mRID", "")
                if not bv_mrid:
                    vl = self.eq.voltage_levels.get(container_mrid)
                    if vl:
                        bv_mrid = vl.baseVoltage_mRID
                if bv_mrid or container_mrid:
                    return bv_mrid, container_mrid
        return "", ""

    def _name_for_group(self, cn_mrids: List[str]) -> str:
        """
        Prefer the name of a BusbarSection connected to this group.
        Fall back to the first CN's name.
        """
        for cn_mrid in cn_mrids:
            for t in self.eq.terminals_at_cn(cn_mrid):
                eq_obj = self.eq.get(t.conductingEquipment_mRID)
                if isinstance(eq_obj, BusbarSection) and eq_obj.name:
                    return eq_obj.name
        # Fall back to first CN name
        first_cn = self.eq.connectivity_nodes.get(cn_mrids[0])
        return first_cn.name if first_cn and first_cn.name else cn_mrids[0][:8]
