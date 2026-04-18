"""Smoke tests for the state estimation pipeline."""
import sys
import os
from pathlib import Path

# Allow importing src/ without an install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
import pandapower as pp

from state_estimation.parsers.csv_parser import CSVParser
from state_estimation.parsers.xml_parser import XMLParser
from state_estimation.network.builder import NetworkBuilder
from state_estimation.estimator.wls_estimator import WLSEstimator

EXAMPLES = Path(__file__).parent.parent / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_net() -> pp.pandapowerNet:
    """Build the pandapower example SE network directly for unit tests."""
    net = pp.create_empty_network()
    b1 = pp.create_bus(net, vn_kv=1, name="Bus 1")
    b2 = pp.create_bus(net, vn_kv=1, name="Bus 2")
    b3 = pp.create_bus(net, vn_kv=1, name="Bus 3")
    pp.create_ext_grid(net, bus=b1)
    pp.create_line_from_parameters(net, from_bus=b1, to_bus=b2,
                                   length_km=1, r_ohm_per_km=1,
                                   x_ohm_per_km=0.5, c_nf_per_km=0, max_i_ka=1)
    pp.create_line_from_parameters(net, from_bus=b1, to_bus=b3,
                                   length_km=1, r_ohm_per_km=1,
                                   x_ohm_per_km=0.5, c_nf_per_km=0, max_i_ka=1)
    pp.create_line_from_parameters(net, from_bus=b2, to_bus=b3,
                                   length_km=1, r_ohm_per_km=1,
                                   x_ohm_per_km=0.5, c_nf_per_km=0, max_i_ka=1)

    pp.create_measurement(net, "v", "bus", 1.006, 0.004, b1)
    pp.create_measurement(net, "v", "bus", 0.968, 0.004, b2)
    pp.create_measurement(net, "v", "bus", 0.980, 0.004, b3)
    pp.create_measurement(net, "p", "bus", -501, 10, b1)
    pp.create_measurement(net, "q", "bus", -286, 10, b1)
    pp.create_measurement(net, "p", "line", 888, 8, 0, side="from")
    pp.create_measurement(net, "q", "line", 568, 8, 0, side="from")
    pp.create_measurement(net, "p", "line", 1173, 8, 1, side="from")
    pp.create_measurement(net, "q", "line", 568, 8, 1, side="from")
    pp.create_measurement(net, "p", "bus", 450, 10, b2)
    pp.create_measurement(net, "q", "bus", 568, 10, b2)

    return net


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestMinimalEstimation:
    def test_converges(self):
        net = _minimal_net()
        est = WLSEstimator(algorithm="wls", tolerance=1e-6, maximum_iterations=20,
                           run_bad_data_detection=False)
        result = est.run(net)
        assert result.converged, "WLS should converge on the minimal 3-bus network"

    def test_iterations_positive(self):
        net = _minimal_net()
        est = WLSEstimator(run_bad_data_detection=False)
        result = est.run(net)
        assert result.iterations >= 1

    def test_bus_results_populated(self):
        net = _minimal_net()
        est = WLSEstimator(run_bad_data_detection=False)
        result = est.run(net)
        assert not result.res_bus.empty
        assert "vm_pu" in result.res_bus.columns or "v_pu" in result.res_bus.columns


class TestCSVParser:
    def test_parse_example(self):
        parser = CSVParser()
        nd = parser.parse(str(EXAMPLES / "csv"))
        assert len(nd.buses) == 10
        assert len(nd.lines) == 6
        assert len(nd.transformers_2w) == 4
        assert len(nd.measurements) > 0

    def test_validation_passes(self):
        parser = CSVParser()
        nd = parser.parse(str(EXAMPLES / "csv"))
        errors, warnings = nd.validate()
        assert errors == [], f"Validation errors: {errors}"

    def test_iec61850_tags_in_names(self):
        """SCADA measurements should carry IEC 61850 canonical tag names."""
        parser = CSVParser()
        nd = parser.parse(str(EXAMPLES / "csv"))
        # At least one measurement name should contain the B1/B2kV/B3/Signal pattern
        names = [m.get("name", "") for m in nd.measurements]
        assert any("kV" in n and "/" in n for n in names), \
            f"No IEC 61850 tags found in measurement names: {names[:5]}"


class TestXMLParser:
    def test_parse_example(self):
        parser = XMLParser()
        nd = parser.parse(str(EXAMPLES / "xml" / "network.xml"))
        assert len(nd.buses) == 10
        assert len(nd.lines) == 6
        assert len(nd.measurements) > 0

    def test_validation_passes(self):
        parser = XMLParser()
        nd = parser.parse(str(EXAMPLES / "xml" / "network.xml"))
        errors, warnings = nd.validate()
        assert errors == [], f"Validation errors: {errors}"

    def test_iec61850_tags_in_xml(self):
        """XML measurement names should use IEC 61850 B1/B2kV/B3/Signal format."""
        parser = XMLParser()
        nd = parser.parse(str(EXAMPLES / "xml" / "network.xml"))
        names = [m.get("name", "") for m in nd.measurements]
        assert any("150kV" in n for n in names), \
            f"No IEC 61850 tags (B2=150kV) found in XML measurement names: {names[:5]}"


class TestNetworkBuilder:
    def test_builds_without_error(self):
        parser = CSVParser()
        nd = parser.parse(str(EXAMPLES / "csv"))
        builder = NetworkBuilder()
        net = builder.build(nd)
        assert len(net.bus) == len(nd.buses)
        assert len(net.measurement) > 0

    def test_bus_id_mapping(self):
        parser = CSVParser()
        nd = parser.parse(str(EXAMPLES / "csv"))
        builder = NetworkBuilder()
        net = builder.build(nd)
        # Buses should be contiguous from 0
        assert list(net.bus.index) == list(range(len(net.bus)))


class TestSCADAParser:
    def test_scada_measurements_have_iec61850_names(self):
        """All SCADA-parsed measurements should carry B1/B2kV/B3/Signal tag names."""
        from state_estimation.parsers.scada_parser import SCADAParser, ElementMapping
        sp = SCADAParser()
        mapping = ElementMapping.from_csv(EXAMPLES / "csv" / "element_mapping.csv")
        meas = sp.parse_file(EXAMPLES / "csv" / "measurements.csv", mapping=mapping)
        assert len(meas) > 0
        for m in meas:
            assert "/" in m["name"], f"Not an IEC 61850 tag: {m['name']}"
            assert "kV" in m["name"], f"No voltage level in tag: {m['name']}"

    def test_voltage_converted_to_pu(self):
        """V measurements (kV) must be converted to p.u."""
        from state_estimation.parsers.scada_parser import SCADAParser, ElementMapping
        sp = SCADAParser()
        mapping = ElementMapping.from_csv(EXAMPLES / "csv" / "element_mapping.csv")
        meas = sp.parse_file(EXAMPLES / "csv" / "measurements.csv", mapping=mapping)
        v_meas = [m for m in meas if m["meas_type"] == "v"]
        assert len(v_meas) > 0
        for m in v_meas:
            assert 0.8 < m["value"] < 1.3, \
                f"V not in p.u. range: {m['value']} for {m['name']}"

    def test_current_converted_to_ka(self):
        """I measurements (A) must be converted to kA."""
        from state_estimation.parsers.scada_parser import SCADAParser, ElementMapping
        sp = SCADAParser()
        mapping = ElementMapping.from_csv(EXAMPLES / "csv" / "element_mapping.csv")
        meas = sp.parse_file(EXAMPLES / "csv" / "measurements.csv", mapping=mapping)
        i_meas = [m for m in meas if m["meas_type"] == "i"]
        assert len(i_meas) > 0
        for m in i_meas:
            assert m["value"] < 10, \
                f"I not converted to kA (got {m['value']} for {m['name']})"
