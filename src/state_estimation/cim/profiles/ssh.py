"""
Steady State Hypothesis Profile (SSH) — IEC 61970-552 / CGMES.

Carries the input assumptions for a power-flow / state-estimation run:
  - Generator target P and machine Q setpoints
  - Load P and Q setpoints
  - Switch open/close status
  - Tap-changer step positions
  - Analog measurement values with uncertainty

All SSH values override the corresponding EQ defaults for a specific snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from ..model import AnalogValue
from ..namespaces import PROFILE_SSH


@dataclass
class SwitchState:
    """SSH override for Switch.open / Switch.retained."""
    switch_mRID: str  = ""
    open:        bool = False


@dataclass
class TapStep:
    """SSH override for TapChanger.step."""
    tapChanger_mRID: str   = ""
    step:            float = 0.0


@dataclass
class MachineSetpoint:
    """SSH P/Q setpoint for SynchronousMachine or ExternalNetworkInjection."""
    equipment_mRID: str   = ""
    p:              float = 0.0   # MW
    q:              float = 0.0   # Mvar
    referencePriority: int = 0   # >0 → angle-reference machine


@dataclass
class LoadSetpoint:
    """SSH P/Q setpoint for EnergyConsumer."""
    consumer_mRID: str   = ""
    p:             float = 0.0   # MW
    q:             float = 0.0   # Mvar


@dataclass
class ShuntSection:
    """SSH section count for LinearShuntCompensator."""
    compensator_mRID: str = ""
    sections:         int = 0


@dataclass
class SteadyStateHypothesisProfile:
    """Container for SSH-profile objects."""
    model_id:   str = ""
    profile_uri: str = PROFILE_SSH
    eq_model_id: str = ""   # EQ FullModel this SSH supplements

    # mRID → SSH object
    switch_states:     Dict[str, SwitchState]     = field(default_factory=dict)
    tap_steps:         Dict[str, TapStep]          = field(default_factory=dict)
    machine_setpoints: Dict[str, MachineSetpoint]  = field(default_factory=dict)
    load_setpoints:    Dict[str, LoadSetpoint]     = field(default_factory=dict)
    shunt_sections:    Dict[str, ShuntSection]     = field(default_factory=dict)
    analog_values:     Dict[str, AnalogValue]      = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def set_switch(self, state: SwitchState) -> None:
        self.switch_states[state.switch_mRID] = state

    def set_tap(self, step: TapStep) -> None:
        self.tap_steps[step.tapChanger_mRID] = step

    def set_machine(self, sp: MachineSetpoint) -> None:
        self.machine_setpoints[sp.equipment_mRID] = sp

    def set_load(self, sp: LoadSetpoint) -> None:
        self.load_setpoints[sp.consumer_mRID] = sp

    def add_analog_value(self, av: AnalogValue) -> None:
        self.analog_values[av.mRID] = av

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def is_switch_open(self, switch_mrid: str, eq_default: bool = False) -> bool:
        """Return open state from SSH, falling back to EQ default."""
        state = self.switch_states.get(switch_mrid)
        return state.open if state is not None else eq_default

    def tap_position(self, tap_changer_mrid: str, eq_default: float = 0.0) -> float:
        step = self.tap_steps.get(tap_changer_mrid)
        return step.step if step is not None else eq_default

    def machine_p(self, mrid: str, default: float = 0.0) -> float:
        sp = self.machine_setpoints.get(mrid)
        return sp.p if sp is not None else default

    def load_p(self, mrid: str, default: float = 0.0) -> float:
        sp = self.load_setpoints.get(mrid)
        return sp.p if sp is not None else default

    def values_for_analog(self, analog_mrid: str) -> list[AnalogValue]:
        return [av for av in self.analog_values.values()
                if av.analog_mRID == analog_mrid]

    def reference_machine(self) -> Optional[MachineSetpoint]:
        """Return the highest-priority angle-reference machine."""
        refs = [m for m in self.machine_setpoints.values()
                if m.referencePriority > 0]
        if not refs:
            return None
        return min(refs, key=lambda m: m.referencePriority)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        return (
            f"SteadyStateHypothesisProfile  model_id={self.model_id}\n"
            f"  SwitchStates:     {len(self.switch_states)}\n"
            f"  TapSteps:         {len(self.tap_steps)}\n"
            f"  MachineSetpoints: {len(self.machine_setpoints)}\n"
            f"  LoadSetpoints:    {len(self.load_setpoints)}\n"
            f"  AnalogValues:     {len(self.analog_values)}"
        )
