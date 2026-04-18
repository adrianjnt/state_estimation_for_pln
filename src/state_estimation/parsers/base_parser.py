"""Base parser defining the contract all input parsers must fulfil."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NetworkData:
    """Normalised intermediate representation of a power network."""

    buses: list[dict[str, Any]] = field(default_factory=list)
    lines: list[dict[str, Any]] = field(default_factory=list)
    transformers_2w: list[dict[str, Any]] = field(default_factory=list)
    transformers_3w: list[dict[str, Any]] = field(default_factory=list)
    switches: list[dict[str, Any]] = field(default_factory=list)
    ext_grids: list[dict[str, Any]] = field(default_factory=list)
    shunts: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    name: str = "PLN Network"

    # ---------- validation helpers ----------

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty = OK)."""
        errors: list[str] = []
        if not self.buses:
            errors.append("No buses defined.")
        if not self.measurements:
            errors.append("No measurements provided – SE will not run.")

        bus_ids = {b["bus_id"] for b in self.buses}

        for line in self.lines:
            for end in ("from_bus", "to_bus"):
                if line.get(end) not in bus_ids:
                    errors.append(
                        f"Line {line.get('line_id')}: {end}={line.get(end)} not in bus list."
                    )

        for t in self.transformers_2w:
            for end in ("hv_bus", "lv_bus"):
                if t.get(end) not in bus_ids:
                    errors.append(
                        f"Transformer {t.get('trafo_id')}: {end}={t.get(end)} not in bus list."
                    )

        valid_meas_types = {"v", "p", "q", "i"}
        valid_elem_types = {"bus", "line", "trafo", "trafo3w"}
        for m in self.measurements:
            if str(m.get("meas_type", "")).lower() not in valid_meas_types:
                errors.append(
                    f"Measurement {m.get('meas_id')}: unknown meas_type '{m.get('meas_type')}'."
                )
            if str(m.get("element_type", "")).lower() not in valid_elem_types:
                errors.append(
                    f"Measurement {m.get('meas_id')}: unknown element_type '{m.get('element_type')}'."
                )

        return errors


class BaseParser(ABC):
    """Abstract parser – subclasses implement parse_file / parse_string."""

    @abstractmethod
    def parse(self, source: str) -> NetworkData:
        """Parse *source* (file path or raw string) into a NetworkData object."""
        ...
