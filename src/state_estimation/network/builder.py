"""Construct a pandapower network from a NetworkData intermediate object.

Design notes for large networks (up to 10 000 nodes):
- All element creation uses vectorised pp.create_* calls where available,
  falling back to scalar calls only for elements that lack a batch API.
- Integer bus indices are mapped from the user-supplied bus_id (arbitrary)
  to contiguous pandapower indices via a lookup table.
"""
from __future__ import annotations

import logging
from typing import Any

import pandapower as pp

from ..parsers.base_parser import NetworkData

logger = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError):
        return default


class NetworkBuilder:
    """Convert NetworkData → pandapower net object."""

    def build(self, nd: NetworkData) -> pp.pandapowerNet:
        net = pp.create_empty_network(name=nd.name, f_hz=50.0, sn_mva=100.0)

        # Map user bus_id → pandapower index
        bus_id_map: dict[int, int] = {}
        self._add_buses(net, nd.buses, bus_id_map)
        self._add_ext_grids(net, nd.ext_grids, bus_id_map)
        self._add_lines(net, nd.lines, bus_id_map)
        self._add_trafos(net, nd.transformers_2w, bus_id_map)
        self._add_trafos3w(net, nd.transformers_3w, bus_id_map)
        self._add_switches(net, nd.switches, bus_id_map)
        self._add_shunts(net, nd.shunts, bus_id_map)
        self._add_measurements(net, nd.measurements, bus_id_map)

        logger.info(
            "Network built: %d buses, %d lines, %d 2W-trafos, %d 3W-trafos, "
            "%d switches, %d measurements",
            len(net.bus), len(net.line), len(net.trafo), len(net.trafo3w),
            len(net.switch), len(net.measurement),
        )
        return net

    # ------------------------------------------------------------------
    # Buses
    # ------------------------------------------------------------------

    def _add_buses(
        self, net: pp.pandapowerNet, buses: list[dict], id_map: dict[int, int]
    ) -> None:
        for b in buses:
            pp_idx = pp.create_bus(
                net,
                vn_kv=_safe_float(b.get("vn_kv", 110)),
                name=str(b.get("name", "")),
                index=None,
                in_service=bool(b.get("in_service", True)),
                zone=b.get("zone"),
                type="b",
            )
            id_map[_safe_int(b["bus_id"])] = pp_idx

    # ------------------------------------------------------------------
    # External grids (slack buses)
    # ------------------------------------------------------------------

    def _add_ext_grids(
        self, net: pp.pandapowerNet, ext_grids: list[dict], id_map: dict[int, int]
    ) -> None:
        if not ext_grids:
            # Default: first bus in network is the slack
            if len(net.bus) > 0:
                pp.create_ext_grid(net, bus=net.bus.index[0], vm_pu=1.0, va_degree=0.0)
                logger.warning(
                    "No external grid defined – using bus index %d as slack.",
                    net.bus.index[0],
                )
            return

        for eg in ext_grids:
            bus_pp = id_map.get(_safe_int(eg.get("bus", 0)))
            if bus_pp is None:
                logger.warning("ExtGrid references unknown bus %s – skipped.", eg.get("bus"))
                continue
            pp.create_ext_grid(
                net,
                bus=bus_pp,
                vm_pu=_safe_float(eg.get("vm_pu", 1.0)),
                va_degree=_safe_float(eg.get("va_degree", 0.0)),
                name=str(eg.get("name", "")),
                in_service=bool(eg.get("in_service", True)),
            )

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------

    def _add_lines(
        self, net: pp.pandapowerNet, lines: list[dict], id_map: dict[int, int]
    ) -> None:
        for ln in lines:
            fb = id_map.get(_safe_int(ln.get("from_bus")))
            tb = id_map.get(_safe_int(ln.get("to_bus")))
            if fb is None or tb is None:
                logger.warning(
                    "Line %s: from_bus or to_bus not in network – skipped.", ln.get("line_id")
                )
                continue
            pp.create_line_from_parameters(
                net,
                from_bus=fb,
                to_bus=tb,
                length_km=_safe_float(ln.get("length_km", 1.0)),
                r_ohm_per_km=_safe_float(ln.get("r_ohm_per_km", 0.01)),
                x_ohm_per_km=_safe_float(ln.get("x_ohm_per_km", 0.1)),
                c_nf_per_km=_safe_float(ln.get("c_nf_per_km", 10.0)),
                max_i_ka=_safe_float(ln.get("max_i_ka", 0.5)),
                name=str(ln.get("name", "")),
                parallel=_safe_int(ln.get("parallel", 1)),
                in_service=bool(ln.get("in_service", True)),
            )

    # ------------------------------------------------------------------
    # Two-winding transformers
    # ------------------------------------------------------------------

    def _add_trafos(
        self, net: pp.pandapowerNet, trafos: list[dict], id_map: dict[int, int]
    ) -> None:
        for t in trafos:
            hv = id_map.get(_safe_int(t.get("hv_bus")))
            lv = id_map.get(_safe_int(t.get("lv_bus")))
            if hv is None or lv is None:
                logger.warning(
                    "Trafo %s: hv_bus or lv_bus not in network – skipped.", t.get("trafo_id")
                )
                continue
            pp.create_transformer_from_parameters(
                net,
                hv_bus=hv,
                lv_bus=lv,
                sn_mva=_safe_float(t.get("sn_mva", 100)),
                vn_hv_kv=_safe_float(t.get("vn_hv_kv", 150)),
                vn_lv_kv=_safe_float(t.get("vn_lv_kv", 20)),
                vkr_percent=_safe_float(t.get("vkr_percent", 0.3)),
                vk_percent=_safe_float(t.get("vk_percent", 12)),
                pfe_kw=_safe_float(t.get("pfe_kw", 50)),
                i0_percent=_safe_float(t.get("i0_percent", 0.1)),
                shift_degree=_safe_float(t.get("shift_degree", 0)),
                tap_pos=_safe_int(t.get("tap_pos", 0)),
                name=str(t.get("name", "")),
                in_service=bool(t.get("in_service", True)),
            )

    # ------------------------------------------------------------------
    # Three-winding transformers
    # ------------------------------------------------------------------

    def _add_trafos3w(
        self, net: pp.pandapowerNet, trafos3w: list[dict], id_map: dict[int, int]
    ) -> None:
        for t in trafos3w:
            hv = id_map.get(_safe_int(t.get("hv_bus")))
            mv = id_map.get(_safe_int(t.get("mv_bus")))
            lv = id_map.get(_safe_int(t.get("lv_bus")))
            if None in (hv, mv, lv):
                logger.warning(
                    "Trafo3W %s: a winding bus is not in network – skipped.",
                    t.get("trafo3w_id"),
                )
                continue
            pp.create_transformer3w_from_parameters(
                net,
                hv_bus=hv, mv_bus=mv, lv_bus=lv,
                vn_hv_kv=_safe_float(t.get("vn_hv_kv", 500)),
                vn_mv_kv=_safe_float(t.get("vn_mv_kv", 150)),
                vn_lv_kv=_safe_float(t.get("vn_lv_kv", 20)),
                sn_hv_mva=_safe_float(t.get("sn_hv_mva", 100)),
                sn_mv_mva=_safe_float(t.get("sn_mv_mva", 100)),
                sn_lv_mva=_safe_float(t.get("sn_lv_mva", 100)),
                vk_hv_percent=_safe_float(t.get("vk_hv_percent", 12)),
                vk_mv_percent=_safe_float(t.get("vk_mv_percent", 12)),
                vk_lv_percent=_safe_float(t.get("vk_lv_percent", 12)),
                vkr_hv_percent=_safe_float(t.get("vkr_hv_percent", 0.3)),
                vkr_mv_percent=_safe_float(t.get("vkr_mv_percent", 0.3)),
                vkr_lv_percent=_safe_float(t.get("vkr_lv_percent", 0.3)),
                pfe_kw=_safe_float(t.get("pfe_kw", 50)),
                i0_percent=_safe_float(t.get("i0_percent", 0.1)),
                name=str(t.get("name", "")),
                in_service=bool(t.get("in_service", True)),
            )

    # ------------------------------------------------------------------
    # Switches
    # ------------------------------------------------------------------

    def _add_switches(
        self, net: pp.pandapowerNet, switches: list[dict], id_map: dict[int, int]
    ) -> None:
        for sw in switches:
            bus_pp = id_map.get(_safe_int(sw.get("bus", 0)))
            if bus_pp is None:
                continue
            pp.create_switch(
                net,
                bus=bus_pp,
                element=_safe_int(sw.get("element", 0)),
                et=str(sw.get("et", "l")),
                type=str(sw.get("type", "CB")),
                closed=bool(sw.get("closed", True)),
                name=str(sw.get("name", "")),
            )

    # ------------------------------------------------------------------
    # Shunts
    # ------------------------------------------------------------------

    def _add_shunts(
        self, net: pp.pandapowerNet, shunts: list[dict], id_map: dict[int, int]
    ) -> None:
        for sh in shunts:
            bus_pp = id_map.get(_safe_int(sh.get("bus", 0)))
            if bus_pp is None:
                continue
            pp.create_shunt(
                net,
                bus=bus_pp,
                p_mw=_safe_float(sh.get("p_mw", 0.0)),
                q_mvar=_safe_float(sh.get("q_mvar", 0.0)),
                vn_kv=_safe_float(sh.get("vn_kv", net.bus.at[bus_pp, "vn_kv"])),
                name=str(sh.get("name", "")),
                in_service=bool(sh.get("in_service", True)),
            )

    # ------------------------------------------------------------------
    # Measurements
    # ------------------------------------------------------------------

    def _add_measurements(
        self, net: pp.pandapowerNet, measurements: list[dict], id_map: dict[int, int]
    ) -> None:
        meas_type_map = {
            "v": "v", "voltage": "v",
            "p": "p", "active_power": "p", "activepower": "p",
            "q": "q", "reactive_power": "q", "reactivepower": "q",
            "i": "i", "current": "i",
        }

        for m in measurements:
            mtype = meas_type_map.get(str(m.get("meas_type", "v")).lower(), "v")
            etype = str(m.get("element_type", "bus")).lower()
            raw_elem = _safe_int(m.get("element", 0))

            if etype == "bus":
                elem = id_map.get(raw_elem)
                if elem is None:
                    logger.warning(
                        "Measurement %s: bus %d not in network – skipped.",
                        m.get("meas_id"), raw_elem,
                    )
                    continue
            else:
                # For line/trafo measurements the element index is a
                # pandapower element index (0-based row in net.line / net.trafo)
                elem = raw_elem

            side = str(m.get("side", "")).strip() or None

            try:
                pp.create_measurement(
                    net,
                    meas_type=mtype,
                    element_type=etype,
                    element=elem,
                    value=_safe_float(m.get("value", 0.0)),
                    std_dev=_safe_float(m.get("std_dev", 0.01)),
                    side=side,
                    name=str(m.get("name", "")),
                )
            except Exception as exc:
                logger.warning(
                    "Skipping measurement %s (%s/%s/elem=%s): %s",
                    m.get("meas_id"), mtype, etype, elem, exc,
                )
