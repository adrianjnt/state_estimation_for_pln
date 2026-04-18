"""Command-line entry point for the PLN State Estimation tool.

Usage examples
--------------
# CSV directory input, default output
python -m state_estimation.main --input examples/csv --format csv

# XML file input, custom output directory
python -m state_estimation.main --input examples/xml/network.xml --format xml \\
       --output reports/

# Override WLS settings
python -m state_estimation.main --input examples/csv --format csv \\
       --algorithm wls --tolerance 1e-8 --max-iterations 100 \\
       --no-bad-data-detection

# ZIP of CSVs
python -m state_estimation.main --input data.zip --format csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandapower as pp

from .parsers import CSVParser, XMLParser
from .network import NetworkBuilder
from .estimator import WLSEstimator
from .reports import ReportGenerator

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
        datefmt="%H:%M:%S",
    )


logger = logging.getLogger("se_pln")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="state_estimation",
        description="PLN State Estimation – pandapower WLS engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input / output
    p.add_argument(
        "--input", "-i", required=True,
        help="Path to CSV directory / ZIP archive, or XML file.",
    )
    p.add_argument(
        "--format", "-f", choices=["csv", "xml"], default="csv",
        help="Input format (default: csv).",
    )
    p.add_argument(
        "--output", "-o", default="reports",
        help="Output directory for the report and CSV results (default: reports/).",
    )
    p.add_argument(
        "--network-name", default=None,
        help="Override the network name shown in the report.",
    )

    # Estimator settings
    p.add_argument(
        "--algorithm", choices=["wls", "wls_with_zero_injection_constraints", "lp_se"],
        default="wls",
        help="SE algorithm (default: wls).",
    )
    p.add_argument(
        "--init", choices=["flat", "results", "slack"], default="flat",
        help="Initialisation method (default: flat).",
    )
    p.add_argument(
        "--tolerance", type=float, default=1e-6,
        help="Convergence tolerance (default: 1e-6).",
    )
    p.add_argument(
        "--max-iterations", type=int, default=50,
        help="Maximum WLS iterations (default: 50).",
    )
    p.add_argument(
        "--no-bad-data-detection", action="store_true",
        help="Skip chi-squared / normalised residual bad-data analysis.",
    )
    p.add_argument(
        "--chi2-alpha", type=float, default=0.05,
        help="Significance level for chi-squared test (default: 0.05).",
    )
    p.add_argument(
        "--no-voltage-angles", action="store_true",
        help="Do not calculate voltage angles.",
    )

    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")

    return p


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    _configure_logging(args.verbose)

    # --- 1. Parse input ---
    logger.info("Parsing input: %s (format=%s)", args.input, args.format)
    try:
        if args.format == "csv":
            parser = CSVParser()
        else:
            parser = XMLParser()
        network_data = parser.parse(args.input)
    except FileNotFoundError as exc:
        logger.error("Input not found: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Parsing failed: %s", exc)
        return 1

    # Validation
    errors, warnings = network_data.validate()
    for w in warnings:
        logger.warning("Validation warning: %s", w)
    if errors:
        for e in errors:
            logger.error("Validation: %s", e)
        return 1

    # IEC 61850 / SCADA metadata summary
    scada_info = network_data.scada_summary()
    if scada_info["is_scada"]:
        logger.info(
            "IEC 61850 SCADA input detected: %d substation(s) [%s], "
            "voltage levels %s kV, %d equipment codes",
            len(scada_info["substations"]),
            ", ".join(scada_info["substations"]),
            "/".join(str(int(v)) for v in scada_info["voltage_levels_kv"]),
            len(scada_info["equipment_codes"]),
        )
        if scada_info["timestamps"]:
            logger.info("Measurement timestamps: %s", " → ".join(
                [scada_info["timestamps"][0], scada_info["timestamps"][-1]]
                if len(scada_info["timestamps"]) > 1
                else scada_info["timestamps"]
            ))
        if scada_info["suspect_measurements"]:
            logger.warning(
                "%d measurement(s) have suspect quality flags (accepted).",
                scada_info["suspect_measurements"],
            )

    network_name = args.network_name or network_data.name
    logger.info(
        "Parsed network '%s': %d buses, %d lines, %d 2W-trafos, %d measurements",
        network_name,
        len(network_data.buses),
        len(network_data.lines),
        len(network_data.transformers_2w),
        len(network_data.measurements),
    )

    # --- 2. Build pandapower network ---
    logger.info("Building pandapower network model…")
    try:
        builder = NetworkBuilder()
        net = builder.build(network_data)
    except Exception as exc:
        logger.error("Network build failed: %s", exc)
        return 1

    net_summary = {
        "buses": len(net.bus),
        "lines": len(net.line),
        "trafos": len(net.trafo) + len(net.trafo3w),
        "measurements": len(net.measurement),
    }

    # --- 3. Run state estimation ---
    logger.info(
        "Running %s state estimation (tol=%.0e, max_iter=%d)…",
        args.algorithm, args.tolerance, args.max_iterations,
    )
    estimator = WLSEstimator(
        algorithm=args.algorithm,
        init=args.init,
        tolerance=args.tolerance,
        maximum_iterations=args.max_iterations,
        calculate_voltage_angles=not args.no_voltage_angles,
        chi2_alpha=args.chi2_alpha,
        run_bad_data_detection=not args.no_bad_data_detection,
    )
    result = estimator.run(net)

    # Log key outcome
    if result.converged:
        logger.info(
            "Converged in %d iterations (final corr = %.3e, time = %.2f s)",
            result.iterations, result.final_tolerance, result.computation_time_s,
        )
    else:
        logger.warning("State estimation DID NOT CONVERGE after %d iterations.", result.iterations)

    if result.bad_data_detected:
        logger.warning(
            "Bad data detected – %d measurement(s) removed.",
            len(result.removed_measurements),
        )

    if result.greatest_mismatch:
        gm = result.greatest_mismatch
        logger.info(
            "Greatest mismatch: meas #%s (%s/%s) – normalised residual = %.4f",
            gm.get("meas_id"), gm.get("meas_type"), gm.get("element_type"),
            gm.get("normalized_residual", 0),
        )

    # --- 4. Generate report ---
    logger.info("Generating report in '%s'…", args.output)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reporter = ReportGenerator()
    html_path = reporter.generate(
        result,
        output_dir=args.output,
        network_name=network_name,
        run_timestamp=ts,
        net_summary=net_summary,
        scada_summary=scada_info,
    )
    logger.info("Report written to: %s", html_path.resolve())

    # Final summary to stdout
    print("\n" + "=" * 60)
    print(f"  State Estimation Complete – {network_name}")
    print("=" * 60)
    print(f"  Status       : {'CONVERGED' if result.converged else 'NOT CONVERGED'}")
    print(f"  Algorithm    : {result.algorithm.upper()}")
    print(f"  Iterations   : {result.iterations}")
    if result.max_corrections:
        print(f"  Final Δ      : {result.final_tolerance:.3e}")
    print(f"  Buses        : {net_summary['buses']:,}")
    print(f"  Bad data     : {'YES (' + str(len(result.removed_measurements)) + ' removed)' if result.bad_data_detected else 'None detected'}")
    if result.greatest_mismatch:
        gm = result.greatest_mismatch
        print(f"  Max residual : meas #{gm.get('meas_id')} → |r|/σ = {gm.get('normalized_residual', 0):.4f}")
    print(f"  Report       : {html_path.resolve()}")
    print("=" * 60 + "\n")

    return 0 if result.converged else 2


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
