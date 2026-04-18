"""CSV parser for the PLN State Estimation tool.

Expected files (all optional except buses.csv and measurements.csv):
    buses.csv, lines.csv, transformers.csv, transformers3w.csv,
    switches.csv, ext_grids.csv, shunts.csv, measurements.csv

The *source* argument to parse() should be a **directory** that contains
those files, or a path to a single ZIP archive with the same structure.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from .base_parser import BaseParser, NetworkData

# ---------------------------------------------------------------------------
# Column definitions – maps our canonical key → accepted CSV header aliases
# ---------------------------------------------------------------------------

_BUS_COLS: dict[str, list[str]] = {
    "bus_id": ["bus_id", "id", "bus"],
    "name": ["name", "bus_name"],
    "vn_kv": ["vn_kv", "vnom_kv", "nominal_voltage_kv"],
    "bus_type": ["bus_type", "type"],
    "zone": ["zone"],
    "in_service": ["in_service"],
}

_LINE_COLS: dict[str, list[str]] = {
    "line_id": ["line_id", "id"],
    "name": ["name", "line_name"],
    "from_bus": ["from_bus", "from", "bus_from"],
    "to_bus": ["to_bus", "to", "bus_to"],
    "length_km": ["length_km", "length"],
    "r_ohm_per_km": ["r_ohm_per_km", "r_ohm_km", "r"],
    "x_ohm_per_km": ["x_ohm_per_km", "x_ohm_km", "x"],
    "c_nf_per_km": ["c_nf_per_km", "c_nf_km", "c"],
    "max_i_ka": ["max_i_ka", "imax_ka", "thermal_limit_ka"],
    "parallel": ["parallel"],
    "in_service": ["in_service"],
}

_TRAFO_COLS: dict[str, list[str]] = {
    "trafo_id": ["trafo_id", "id"],
    "name": ["name", "trafo_name"],
    "hv_bus": ["hv_bus", "hv", "bus_hv"],
    "lv_bus": ["lv_bus", "lv", "bus_lv"],
    "sn_mva": ["sn_mva", "sn_kva", "rated_mva"],
    "vn_hv_kv": ["vn_hv_kv", "vnom_hv_kv"],
    "vn_lv_kv": ["vn_lv_kv", "vnom_lv_kv"],
    "vk_percent": ["vk_percent", "vk_pct", "uk_percent"],
    "vkr_percent": ["vkr_percent", "vkr_pct", "ukr_percent"],
    "pfe_kw": ["pfe_kw", "pfe"],
    "i0_percent": ["i0_percent", "i0_pct"],
    "shift_degree": ["shift_degree", "shift_deg"],
    "tap_pos": ["tap_pos", "tap_position"],
    "in_service": ["in_service"],
}

_TRAFO3W_COLS: dict[str, list[str]] = {
    "trafo3w_id": ["trafo3w_id", "id"],
    "name": ["name"],
    "hv_bus": ["hv_bus"],
    "mv_bus": ["mv_bus"],
    "lv_bus": ["lv_bus"],
    "sn_hv_mva": ["sn_hv_mva"],
    "sn_mv_mva": ["sn_mv_mva"],
    "sn_lv_mva": ["sn_lv_mva"],
    "vn_hv_kv": ["vn_hv_kv"],
    "vn_mv_kv": ["vn_mv_kv"],
    "vn_lv_kv": ["vn_lv_kv"],
    "vk_hv_percent": ["vk_hv_percent"],
    "vk_mv_percent": ["vk_mv_percent"],
    "vk_lv_percent": ["vk_lv_percent"],
    "vkr_hv_percent": ["vkr_hv_percent"],
    "vkr_mv_percent": ["vkr_mv_percent"],
    "vkr_lv_percent": ["vkr_lv_percent"],
    "pfe_kw": ["pfe_kw"],
    "i0_percent": ["i0_percent"],
    "in_service": ["in_service"],
}

_SWITCH_COLS: dict[str, list[str]] = {
    "switch_id": ["switch_id", "id"],
    "name": ["name"],
    "bus": ["bus"],
    "element": ["element"],
    "et": ["et", "element_type"],
    "type": ["type", "switch_type"],
    "closed": ["closed", "status"],
}

_EXT_GRID_COLS: dict[str, list[str]] = {
    "ext_grid_id": ["ext_grid_id", "id"],
    "name": ["name"],
    "bus": ["bus"],
    "vm_pu": ["vm_pu", "v_pu"],
    "va_degree": ["va_degree", "angle_deg"],
    "in_service": ["in_service"],
}

_SHUNT_COLS: dict[str, list[str]] = {
    "shunt_id": ["shunt_id", "id"],
    "name": ["name"],
    "bus": ["bus"],
    "p_mw": ["p_mw"],
    "q_mvar": ["q_mvar"],
    "vn_kv": ["vn_kv"],
    "in_service": ["in_service"],
}

_MEAS_COLS: dict[str, list[str]] = {
    "meas_id": ["meas_id", "id"],
    "meas_type": ["meas_type", "type", "measurement_type"],
    "element_type": ["element_type", "elem_type"],
    "element": ["element", "element_id"],
    "value": ["value", "measured_value"],
    "std_dev": ["std_dev", "sigma", "standard_deviation"],
    "side": ["side"],
    "name": ["name"],
}


def _resolve_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """Return the first alias that is an actual column in df (case-insensitive)."""
    lower_cols = {c.lower(): c for c in df.columns}
    for alias in aliases:
        if alias.lower() in lower_cols:
            return lower_cols[alias.lower()]
    return None


def _normalise_df(df: pd.DataFrame, col_map: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Map raw DataFrame columns to canonical names and return list of dicts."""
    rename: dict[str, str] = {}
    for canonical, aliases in col_map.items():
        found = _resolve_col(df, aliases)
        if found and found != canonical:
            rename[found] = canonical
    df = df.rename(columns=rename)
    # Keep only canonical columns that are actually present
    keep = [c for c in col_map if c in df.columns]
    return df[keep].to_dict("records")


def _bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in {"false", "0", "no", "off", ""}


def _coerce_types(records: list[dict], bool_keys: list[str], int_keys: list[str]) -> list[dict]:
    for r in records:
        for k in bool_keys:
            if k in r:
                r[k] = _bool(r[k])
        for k in int_keys:
            if k in r:
                try:
                    r[k] = int(float(str(r[k])))
                except (ValueError, TypeError):
                    pass
    return records


class CSVParser(BaseParser):
    """Parse a directory (or ZIP) of CSV files into a NetworkData object."""

    def parse(self, source: str) -> NetworkData:
        source_path = Path(source)
        if source_path.suffix.lower() == ".zip":
            return self._parse_zip(source_path)
        if source_path.is_dir():
            return self._parse_dir(source_path)
        raise ValueError(f"CSVParser expects a directory or .zip file, got: {source}")

    # ------------------------------------------------------------------

    def _parse_dir(self, directory: Path) -> NetworkData:
        def read(name: str) -> pd.DataFrame | None:
            for fname in (name, name.replace(".csv", "")):
                for p in directory.glob(f"{fname}*.csv"):
                    return pd.read_csv(p, dtype=str, keep_default_na=False)
            return None

        return self._build(read, directory.name)

    def _parse_zip(self, zpath: Path) -> NetworkData:
        with zipfile.ZipFile(zpath) as zf:
            names = {Path(n).stem.lower(): n for n in zf.namelist() if n.endswith(".csv")}

            def read(name: str) -> pd.DataFrame | None:
                key = name.replace(".csv", "").lower()
                if key in names:
                    with zf.open(names[key]) as fh:
                        return pd.read_csv(fh, dtype=str, keep_default_na=False)
                return None

        return self._build(read, zpath.stem)

    def _build(self, read_fn, network_name: str) -> NetworkData:
        nd = NetworkData(name=network_name)

        buses_df = read_fn("buses")
        if buses_df is None:
            raise FileNotFoundError("buses.csv not found in source.")
        nd.buses = _coerce_types(
            _normalise_df(buses_df, _BUS_COLS),
            bool_keys=["in_service"],
            int_keys=["bus_id", "zone"],
        )

        lines_df = read_fn("lines")
        if lines_df is not None:
            nd.lines = _coerce_types(
                _normalise_df(lines_df, _LINE_COLS),
                bool_keys=["in_service"],
                int_keys=["line_id", "from_bus", "to_bus", "parallel"],
            )

        trafo_df = read_fn("transformers")
        if trafo_df is not None:
            nd.transformers_2w = _coerce_types(
                _normalise_df(trafo_df, _TRAFO_COLS),
                bool_keys=["in_service"],
                int_keys=["trafo_id", "hv_bus", "lv_bus"],
            )

        trafo3w_df = read_fn("transformers3w")
        if trafo3w_df is not None:
            nd.transformers_3w = _coerce_types(
                _normalise_df(trafo3w_df, _TRAFO3W_COLS),
                bool_keys=["in_service"],
                int_keys=["trafo3w_id", "hv_bus", "mv_bus", "lv_bus"],
            )

        sw_df = read_fn("switches")
        if sw_df is not None:
            nd.switches = _coerce_types(
                _normalise_df(sw_df, _SWITCH_COLS),
                bool_keys=["closed"],
                int_keys=["switch_id", "bus", "element"],
            )

        eg_df = read_fn("ext_grids")
        if eg_df is not None:
            nd.ext_grids = _coerce_types(
                _normalise_df(eg_df, _EXT_GRID_COLS),
                bool_keys=["in_service"],
                int_keys=["ext_grid_id", "bus"],
            )

        shunt_df = read_fn("shunts")
        if shunt_df is not None:
            nd.shunts = _coerce_types(
                _normalise_df(shunt_df, _SHUNT_COLS),
                bool_keys=["in_service"],
                int_keys=["shunt_id", "bus"],
            )

        meas_df = read_fn("measurements")
        if meas_df is None:
            raise FileNotFoundError("measurements.csv not found in source.")
        nd.measurements = _coerce_types(
            _normalise_df(meas_df, _MEAS_COLS),
            bool_keys=[],
            int_keys=["meas_id", "element"],
        )

        return nd
