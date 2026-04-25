"""
State Variables Profile (SV) — IEC 61970-552 / CGMES.

Carries the output of the State Estimator:
  - SvVoltage  per TopologicalNode  (v in kV, angle in degrees)
  - SvPowerFlow per Terminal         (p in MW, q in Mvar)
  - SvTapStep  per TapChanger        (continuous step position)
  - SvInjection per TopologicalNode  (net injection)

This profile is the primary deliverable written back to the CGMES exchange
point after a successful SE run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..model import SvVoltage, SvPowerFlow, SvTapStep, SvInjection
from ..namespaces import PROFILE_SV


@dataclass
class StateVariablesProfile:
    """Container for SV-profile objects."""
    model_id:   str = ""
    profile_uri: str = PROFILE_SV
    tp_model_id: str = ""   # TP FullModel this SV augments
    ssh_model_id: str = ""  # SSH FullModel this SV references

    # mRID-keyed registries
    sv_voltages:   Dict[str, SvVoltage]   = field(default_factory=dict)
    sv_power_flows: Dict[str, SvPowerFlow] = field(default_factory=dict)
    sv_tap_steps:  Dict[str, SvTapStep]   = field(default_factory=dict)
    sv_injections: Dict[str, SvInjection] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_voltage(self, sv: SvVoltage) -> None:
        self.sv_voltages[sv.mRID] = sv

    def add_power_flow(self, sv: SvPowerFlow) -> None:
        self.sv_power_flows[sv.mRID] = sv

    def add_tap_step(self, sv: SvTapStep) -> None:
        self.sv_tap_steps[sv.mRID] = sv

    def add_injection(self, sv: SvInjection) -> None:
        self.sv_injections[sv.mRID] = sv

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def voltage_at(self, tn_mrid: str) -> Optional[SvVoltage]:
        """Return voltage result for a given TopologicalNode mRID."""
        for sv in self.sv_voltages.values():
            if sv.topologicalNode_mRID == tn_mrid:
                return sv
        return None

    def flows_at_terminal(self, terminal_mrid: str) -> Optional[SvPowerFlow]:
        for sv in self.sv_power_flows.values():
            if sv.terminal_mRID == terminal_mrid:
                return sv
        return None

    def tap_at(self, tap_changer_mrid: str) -> Optional[SvTapStep]:
        for sv in self.sv_tap_steps.values():
            if sv.tapChanger_mRID == tap_changer_mrid:
                return sv
        return None

    def all_voltages_sorted(self) -> List[SvVoltage]:
        """Return SvVoltage list sorted by v descending (highest voltage first)."""
        return sorted(self.sv_voltages.values(), key=lambda sv: sv.v, reverse=True)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"StateVariablesProfile  model_id={self.model_id}",
            f"  SvVoltage:    {len(self.sv_voltages)}",
            f"  SvPowerFlow:  {len(self.sv_power_flows)}",
            f"  SvTapStep:    {len(self.sv_tap_steps)}",
            f"  SvInjection:  {len(self.sv_injections)}",
        ]
        if self.sv_voltages:
            vmax = max(sv.v for sv in self.sv_voltages.values())
            vmin = min(sv.v for sv in self.sv_voltages.values())
            lines.append(f"  Voltage range:  {vmin:.3f} — {vmax:.3f} kV")
        return "\n".join(lines)
