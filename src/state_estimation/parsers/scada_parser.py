"""SCADA measurement parser following IEC 61850 B1/B2/B3 naming convention.

Input format (semicolon-delimited, whitespace-tolerant):
    Substation ; Voltage_kV ; Equipment ; Signal ; Timestamp ; Value ; Quality

Example rows:
    0ADPL7 ; 500 ; 7KSGN1 ; I ; 06.03.2026 00:00 ; 620.00 ; act
    0ADPL7 ; 500 ; 7KSGN1 ; P ; 06.03.2026 00:00 ; 552.00 ; act
    0ADPL7 ; 500 ; 7KSGN1 ; Q ; 06.03.2026 00:00 ;   2.00 ; act
    0ADPL7 ; 500 ; 7KSGN1 ; V ; 06.03.2026 00:00 ; 511.00 ; act

IEC 61850 tag hierarchy:
    B1 = Substation/Main Area  (e.g. 0ADPL7)
    B2 = Voltage Level in kV   (e.g. 500)
    B3 = Bay / Equipment       (e.g. 7KSGN1)
    Signal = I | P | Q | V

Canonical tag format used as measurement name:
    "{B1}/{B2}kV/{B3}/{Signal}"  →  "0ADPL7/500kV/7KSGN1/I"

Unit conventions applied automatically:
    V  – input in kV  → converted to p.u.  using B2 as base (V_pu = V_kV / B2_kV)
    I  – input in A   → converted to kA    (I_kA = I_A / 1000)
    P  – input in MW  → kept as MW
    Q  – input in Mvar→ kept as Mvar

Quality flag definitions (PLN SCADA):
    act  – actual      : paired with field metering, actively updating          → ACCEPTED
    cal  – calculated  : derived from formulation of ≥1 measurements            → ACCEPTED
    blo  – blocked     : field metering present but frozen/blocked              → REJECTED
    not  – not renew   : field metering present but stopped updating            → REJECTED
    exi  – exist       : not paired with field metering, no formula assigned    → REJECTED
    inv  – invalid     : field metering present but returning an error message  → REJECTED
    sub  – substitute  : operator has manually inserted a substitute value      → REJECTED

    Only 'act' and 'cal' are used in state estimation. All other flags cause
    the measurement to be dropped before the WLS solver is invoked.

Element mapping:
    A companion CSV (element_mapping.csv) links each B1/B2/B3 tag to a
    pandapower element so the estimator knows which network node the
    measurement belongs to.

    Columns:
        b1, b2, b3, element_type, element_id, side

    element_type : bus | line | trafo | trafo3w
    element_id   : bus_id for buses; 0-based row index for branches
    side         : "" for buses; "from"/"to" for lines; "hv"/"lv" for trafos

    If element_mapping.csv is absent, measurements are tagged with
    element_type="bus" and element_id=0 as placeholders; a warning is emitted.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality classification
# ---------------------------------------------------------------------------

# PLN SCADA quality flag definitions:
#   act – actual      : field metering active and updating               → use
#   cal – calculated  : derived from a measurement formula               → use
#   blo – blocked     : field metering frozen/blocked                    → reject
#   not – not renew   : field metering stopped updating                  → reject
#   exi – exist       : tag exists but has no field source or formula    → reject
#   inv – invalid     : field metering returning an error message        → reject
#   sub – substitute  : operator-inserted substitute value               → reject

_QUALITY_ACCEPT = {"act", "cal"}

_QUALITY_REJECT = {"blo", "not", "exi", "inv", "sub"}


def _classify_quality(raw: str) -> tuple[bool, bool]:
    """Return (accepted, flagged_as_suspect).

    Only 'act' and 'cal' are accepted for state estimation.
    Any unrecognised flag is rejected and flagged so the operator is informed.
    """
    q = raw.strip().lower()
    if q in _QUALITY_ACCEPT:
        return True, False
    if q in _QUALITY_REJECT:
        return False, True
    # Unknown flag – reject and flag; operator should investigate
    return False, True


# ---------------------------------------------------------------------------
# Default measurement standard deviations (when not provided by SCADA)
# ---------------------------------------------------------------------------

_DEFAULT_STD_DEV = {
    "v": 0.004,    # p.u.  (≈ 0.4% of nominal)
    "p": 1.0,      # MW
    "q": 1.0,      # Mvar
    "i": 0.001,    # kA  (≈ 1 A on a 1 kA base)
}


# ---------------------------------------------------------------------------
# IEC 61850 tag builder
# ---------------------------------------------------------------------------

def build_iec61850_tag(b1: str, b2: str, b3: str, signal: str) -> str:
    """Return canonical IEC 61850 tag:  B1/B2kV/B3/Signal."""
    return f"{b1.strip()}/{b2.strip()}kV/{b3.strip()}/{signal.strip().upper()}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScadaRow:
    """Single SCADA telemetry record, parsed and unit-converted."""

    b1: str          # Substation
    b2_kv: float     # Voltage level in kV
    b3: str          # Bay / Equipment
    signal: str      # V | P | Q | I
    timestamp: str   # raw string from file
    raw_value: float # value as received
    value: float     # unit-converted value
    quality_raw: str
    accepted: bool
    suspect: bool
    tag: str         # IEC 61850 canonical tag


@dataclass
class ScadaMeasurement:
    """Measurement record ready for pandapower (mirrors NetworkData.measurements)."""

    meas_id: int
    name: str           # IEC 61850 tag
    meas_type: str      # v | p | q | i
    element_type: str   # bus | line | trafo | trafo3w
    element: int        # pandapower element index
    value: float
    std_dev: float
    side: str           # "" | from | to | hv | lv
    timestamp: str
    quality_raw: str
    suspect: bool
    b1: str
    b2_kv: float
    b3: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "meas_id": self.meas_id,
            "name": self.name,
            "meas_type": self.meas_type,
            "element_type": self.element_type,
            "element": self.element,
            "value": self.value,
            "std_dev": self.std_dev,
            "side": self.side,
            # Extra IEC 61850 metadata (stored for reporting; ignored by builder)
            "_timestamp": self.timestamp,
            "_quality": self.quality_raw,
            "_suspect": self.suspect,
            "_b1": self.b1,
            "_b2_kv": self.b2_kv,
            "_b3": self.b3,
        }


# ---------------------------------------------------------------------------
# Element mapping loader
# ---------------------------------------------------------------------------

@dataclass
class ElementMapping:
    """Lookup: (b1, b2_str, b3) → (element_type, element_id, side)."""

    _table: dict[tuple[str, str, str], tuple[str, int, str]] = field(
        default_factory=dict
    )

    @classmethod
    def from_csv(cls, path: Path) -> "ElementMapping":
        obj = cls()
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(
                (line for line in fh if not line.startswith("#")),
                skipinitialspace=True,
            )
            for row in reader:
                b1 = row.get("b1", "").strip()
                b2 = row.get("b2", "").strip()
                b3 = row.get("b3", "").strip()
                etype = row.get("element_type", "bus").strip().lower()
                try:
                    eid = int(float(row.get("element_id", "0")))
                except ValueError:
                    eid = 0
                side = row.get("side", "").strip().lower()
                obj._table[(b1, b2, b3)] = (etype, eid, side)
        logger.info("Loaded %d element mappings from %s", len(obj._table), path)
        return obj

    def lookup(
        self, b1: str, b2: str, b3: str
    ) -> tuple[str, int, str] | None:
        """Return (element_type, element_id, side) or None."""
        return self._table.get((b1.strip(), b2.strip(), b3.strip()))

    def __len__(self) -> int:
        return len(self._table)


# ---------------------------------------------------------------------------
# SCADA file parser
# ---------------------------------------------------------------------------

_DATETIME_FMTS = [
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M",
]


def _parse_ts(raw: str) -> str:
    """Normalise timestamp string; return ISO-like string."""
    raw = raw.strip()
    for fmt in _DATETIME_FMTS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return raw  # pass through unparseable strings


def _convert_value(signal: str, raw: float, b2_kv: float) -> float:
    """Apply unit conversion for the given signal type."""
    s = signal.upper()
    if s == "V":
        # kV → p.u.  (base = B2 voltage level)
        return raw / b2_kv if b2_kv > 0 else raw
    if s == "I":
        # A → kA
        return raw / 1000.0
    # P and Q: MW / Mvar – no conversion
    return raw


class SCADAParser:
    """Parse a SCADA semicolon-delimited measurement file.

    Only measurements with quality flag 'act' (actual) or 'cal' (calculated)
    are passed to the state estimator. All others are dropped:

        act – actual      : field metering active and updating          → accepted
        cal – calculated  : derived from ≥1 measurement formula         → accepted
        blo – blocked     : metering frozen/blocked                     → rejected
        not – not renew   : metering stopped updating                   → rejected
        exi – exist       : tag has no field source or formula          → rejected
        inv – invalid     : metering returning error message            → rejected
        sub – substitute  : operator-inserted substitute value          → rejected

    Parameters
    ----------
    delimiter : str
        Field separator (default ";").
    encoding : str
        File encoding (default "utf-8"; try "latin-1" for ABB SCADA exports).
    reject_bad_quality : bool
        If True (default), non-act/cal rows are dropped before SE.
        Set to False only for diagnostics – SE results will be unreliable.
    std_dev_overrides : dict
        Override default σ values per signal type, e.g. {"v": 0.003, "p": 0.5}.
    """

    def __init__(
        self,
        delimiter: str = ";",
        encoding: str = "utf-8",
        reject_bad_quality: bool = True,
        std_dev_overrides: dict[str, float] | None = None,
        last_timestamp_only: bool = True,
    ) -> None:
        self.delimiter = delimiter
        self.encoding = encoding
        self.reject_bad_quality = reject_bad_quality
        self.last_timestamp_only = last_timestamp_only
        self._std_dev = {**_DEFAULT_STD_DEV, **(std_dev_overrides or {})}

    # ------------------------------------------------------------------

    def parse_file(
        self,
        measurement_file: str | Path,
        mapping: ElementMapping | None = None,
    ) -> list[dict[str, Any]]:
        """Parse *measurement_file* and return list of measurement dicts."""
        path = Path(measurement_file)
        rows = self._read_rows(path)
        return self._to_measurement_dicts(rows, mapping)

    # ------------------------------------------------------------------

    def _read_rows(self, path: Path) -> list[ScadaRow]:
        rows: list[ScadaRow] = []
        skipped_quality = 0
        parse_errors = 0

        try:
            lines = path.read_text(encoding=self.encoding, errors="replace").splitlines()
        except FileNotFoundError:
            raise FileNotFoundError(f"SCADA measurement file not found: {path}")

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.lower().startswith("substation"):
                continue  # skip blanks, comments, header

            parts = [p.strip() for p in line.split(self.delimiter)]
            if len(parts) < 6:
                logger.debug("Line %d: too few fields (%d) – skipped", lineno, len(parts))
                parse_errors += 1
                continue

            b1 = parts[0]
            b2_raw = parts[1]
            b3 = parts[2]
            signal_raw = parts[3].upper()
            timestamp_raw = parts[4] if len(parts) > 4 else ""
            value_raw = parts[5] if len(parts) > 5 else ""
            quality_raw = parts[6].strip() if len(parts) > 6 else "act"

            # Validate signal type
            if signal_raw not in {"V", "P", "Q", "I"}:
                logger.debug("Line %d: unknown signal '%s' – skipped", lineno, signal_raw)
                parse_errors += 1
                continue

            # Parse voltage level
            try:
                b2_kv = float(b2_raw)
            except ValueError:
                logger.debug("Line %d: cannot parse voltage level '%s'", lineno, b2_raw)
                parse_errors += 1
                continue

            # Parse measurement value
            try:
                raw_value = float(value_raw.replace(",", "."))
            except ValueError:
                logger.debug("Line %d: cannot parse value '%s'", lineno, value_raw)
                parse_errors += 1
                continue

            # Quality check – only 'act' and 'cal' are accepted
            accepted, suspect = _classify_quality(quality_raw)
            if not accepted and self.reject_bad_quality:
                skipped_quality += 1
                logger.debug(
                    "Line %d: quality '%s' rejected for tag %s "
                    "(only 'act' and 'cal' are accepted for SE)",
                    lineno, quality_raw, build_iec61850_tag(b1, b2_raw, b3, signal_raw),
                )
                continue

            converted_value = _convert_value(signal_raw, raw_value, b2_kv)

            rows.append(ScadaRow(
                b1=b1,
                b2_kv=b2_kv,
                b3=b3,
                signal=signal_raw,
                timestamp=_parse_ts(timestamp_raw),
                raw_value=raw_value,
                value=converted_value,
                quality_raw=quality_raw,
                accepted=accepted,
                suspect=suspect,
                tag=build_iec61850_tag(b1, b2_raw, b3, signal_raw),
            ))

        logger.info(
            "SCADA parse: %d measurements read, %d quality-rejected, %d parse errors",
            len(rows), skipped_quality, parse_errors,
        )
        if skipped_quality:
            logger.warning(
                "%d measurement(s) dropped – quality flag was not 'act' or 'cal'. "
                "Rejected flags: blo (blocked), not (not renew), exi (exist), "
                "inv (invalid), sub (substitute).",
                skipped_quality,
            )

        if self.last_timestamp_only and rows:
            last_ts = max(r.timestamp for r in rows)
            before = len(rows)
            rows = [r for r in rows if r.timestamp == last_ts]
            if len(rows) < before:
                logger.info(
                    "last_timestamp_only=True: retained %d of %d measurements "
                    "from timestamp '%s' (dropped %d earlier snapshots).",
                    len(rows), before, last_ts, before - len(rows),
                )
            else:
                logger.info("Using timestamp '%s' (%d measurements).", last_ts, len(rows))

        return rows

    def _to_measurement_dicts(
        self, rows: list[ScadaRow], mapping: ElementMapping | None
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        unmapped: set[str] = set()
        meas_id = 0

        for row in rows:
            b2_str = str(int(row.b2_kv)) if row.b2_kv == int(row.b2_kv) else str(row.b2_kv)

            # Resolve element from mapping
            if mapping:
                mapped = mapping.lookup(row.b1, b2_str, row.b3)
            else:
                mapped = None

            if mapped is None:
                tag_key = f"{row.b1}/{b2_str}/{row.b3}"
                if tag_key not in unmapped:
                    unmapped.add(tag_key)
                    logger.warning(
                        "No element mapping for tag '%s' – defaulting to bus/0. "
                        "Provide element_mapping.csv for correct association.",
                        tag_key,
                    )
                element_type, element_id, side = "bus", 0, ""
            else:
                element_type, element_id, side = mapped

            signal_lower = row.signal.lower()
            std_dev = self._std_dev.get(signal_lower, 0.01)

            m = ScadaMeasurement(
                meas_id=meas_id,
                name=row.tag,
                meas_type=signal_lower,
                element_type=element_type,
                element=element_id,
                value=row.value,
                std_dev=std_dev,
                side=side,
                timestamp=row.timestamp,
                quality_raw=row.quality_raw,
                suspect=row.suspect,
                b1=row.b1,
                b2_kv=row.b2_kv,
                b3=row.b3,
            )
            results.append(m.to_dict())
            meas_id += 1

        if unmapped:
            logger.warning(
                "%d unique tag(s) had no element mapping. Results may be incorrect.",
                len(unmapped),
            )

        return results


# ---------------------------------------------------------------------------
# Convenience: load mapping from same directory as measurement file
# ---------------------------------------------------------------------------

def load_mapping_from_dir(directory: Path) -> ElementMapping | None:
    """Look for element_mapping.csv in *directory*; return None if absent."""
    candidates = [
        directory / "element_mapping.csv",
        directory / "mapping.csv",
        directory / "tag_mapping.csv",
    ]
    for p in candidates:
        if p.exists():
            return ElementMapping.from_csv(p)
    return None
