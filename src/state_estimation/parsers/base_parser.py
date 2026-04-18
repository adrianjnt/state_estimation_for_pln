"""Base parser defining the contract all input parsers must fulfil.

Measurement record schema
-------------------------
Every entry in NetworkData.measurements must carry these canonical keys:

  Core (required by builder):
    meas_id       int    Unique measurement identifier
    name          str    Human-readable label – for SCADA inputs this is the
                         IEC 61850 canonical tag: "{B1}/{B2}kV/{B3}/{Signal}"
    meas_type     str    'v' | 'p' | 'q' | 'i'
    element_type  str    'bus' | 'line' | 'trafo' | 'trafo3w'
    element       int    pandapower element index
    value         float  Measurement value (p.u. for V; MW for P; Mvar for Q; kA for I)
    std_dev       float  Measurement standard deviation (same unit)
    side          str    '' | 'from' | 'to' | 'hv' | 'lv' | 'mv'

  IEC 61850 metadata (optional, prefixed with '_'; ignored by builder):
    _b1           str    Substation code   (B1 level)
    _b2_kv        float  Voltage level kV  (B2 level)
    _b3           str    Bay/Equipment code (B3 level)
    _timestamp    str    ISO-format timestamp from SCADA
    _quality      str    Raw quality flag from SCADA (e.g. 'act', 'man')
    _suspect      bool   True if quality was flagged as suspect but accepted
"""
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
        suspect_count = 0
        for m in self.measurements:
            if str(m.get("meas_type", "")).lower() not in valid_meas_types:
                errors.append(
                    f"Measurement {m.get('meas_id')}: unknown meas_type '{m.get('meas_type')}'."
                )
            if str(m.get("element_type", "")).lower() not in valid_elem_types:
                errors.append(
                    f"Measurement {m.get('meas_id')}: unknown element_type '{m.get('element_type')}'."
                )
            if m.get("_suspect"):
                suspect_count += 1

        if suspect_count:
            # Not an error – surfaced as an informational warning in the report
            errors.append(
                f"__WARN__: {suspect_count} measurement(s) have suspect quality flags "
                "(accepted but flagged)."
            )

        return [e for e in errors if not e.startswith("__WARN__")], \
               [e[8:] for e in errors if e.startswith("__WARN__")]

    def validate_strict(self) -> list[str]:
        """Return all errors (and warnings as errors). Convenience for tests."""
        errs, warns = self.validate()
        return errs + warns

    # ---------- IEC 61850 helpers ----------

    def scada_summary(self) -> dict[str, Any]:
        """Return aggregated IEC 61850 metadata about the loaded measurements."""
        substations: set[str] = set()
        voltage_levels: set[float] = set()
        equipment_codes: set[str] = set()
        suspect_count = 0
        timestamps: list[str] = []

        for m in self.measurements:
            if m.get("_b1"):
                substations.add(m["_b1"])
            if m.get("_b2_kv"):
                voltage_levels.add(m["_b2_kv"])
            if m.get("_b3"):
                equipment_codes.add(m["_b3"])
            if m.get("_suspect"):
                suspect_count += 1
            if m.get("_timestamp"):
                timestamps.append(m["_timestamp"])

        return {
            "is_scada": bool(substations),
            "substations": sorted(substations),
            "voltage_levels_kv": sorted(voltage_levels),
            "equipment_codes": sorted(equipment_codes),
            "suspect_measurements": suspect_count,
            "timestamps": sorted(set(timestamps)),
        }


class BaseParser(ABC):
    """Abstract parser – subclasses implement parse_file / parse_string."""

    @abstractmethod
    def parse(self, source: str) -> NetworkData:
        """Parse *source* (file path or raw string) into a NetworkData object."""
        ...
