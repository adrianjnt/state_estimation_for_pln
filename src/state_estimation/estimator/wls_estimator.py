"""Weighted Least Squares state estimator wrapper.

Wraps pandapower's estimation engine and captures:
  - Per-iteration convergence corrections
  - Chi-squared bad-data test results
  - Normalised residuals and removed measurements
  - Greatest mismatch bus / branch
  - Computation timing
"""
from __future__ import annotations

import io
import logging
import re
import sys
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pandapower as pp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class EstimationResult:
    converged: bool = False
    algorithm: str = "wls"
    iterations: int = 0
    max_corrections: list[float] = field(default_factory=list)   # one per iteration
    final_tolerance: float = float("nan")
    convergence_log: list[str] = field(default_factory=list)      # raw lines from verbose

    bad_data_detected: bool = False
    chi2_test_passed: bool = False
    chi2_statistic: float = float("nan")
    chi2_threshold: float = float("nan")
    removed_measurements: list[dict] = field(default_factory=list)
    normalized_residuals: pd.DataFrame = field(default_factory=pd.DataFrame)

    greatest_mismatch: dict = field(default_factory=dict)

    res_bus: pd.DataFrame = field(default_factory=pd.DataFrame)
    res_line: pd.DataFrame = field(default_factory=pd.DataFrame)
    res_trafo: pd.DataFrame = field(default_factory=pd.DataFrame)
    res_trafo3w: pd.DataFrame = field(default_factory=pd.DataFrame)

    computation_time_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ITER_RE = re.compile(
    r"(?:iteration|iter)[^\d]*(\d+)[^\d]+"
    r"(?:max(?:imum)?\s*correction|delta|residual|mismatch)[^\d]*([0-9]+(?:\.[0-9]+)?(?:[eE][+\-]?\d+)?)",
    re.IGNORECASE,
)

_CONV_RE = re.compile(
    r"converged\s+in\s+(\d+)\s+iteration",
    re.IGNORECASE,
)

_MAXCORR_RE = re.compile(
    r"(?:max(?:imum)?\s*(?:correction|mismatch|delta)[^\d]*)([0-9]+(?:\.[0-9]+)?(?:[eE][+\-]?\d+)?)",
    re.IGNORECASE,
)


def _parse_verbose(log_text: str) -> tuple[list[float], int]:
    """Extract (per-iteration max-corrections, final iteration count) from verbose log."""
    corrections: list[float] = []
    for line in log_text.splitlines():
        m = _ITER_RE.search(line)
        if m:
            corrections.append(float(m.group(2)))
            continue
        m2 = _MAXCORR_RE.search(line)
        if m2 and ("iteration" in line.lower() or "iter" in line.lower()):
            corrections.append(float(m2.group(1)))

    m_conv = _CONV_RE.search(log_text)
    final_iter = int(m_conv.group(1)) if m_conv else len(corrections)
    return corrections, final_iter


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class WLSEstimator:
    """Run pandapower WLS state estimation with detailed logging."""

    def __init__(
        self,
        algorithm: str = "wls",
        init: str = "flat",
        tolerance: float = 1e-6,
        maximum_iterations: int = 50,
        calculate_voltage_angles: bool = True,
        zero_injection: str | None = "auto",
        chi2_alpha: float = 0.05,
        run_bad_data_detection: bool = True,
    ) -> None:
        self.algorithm = algorithm
        self.init = init
        self.tolerance = tolerance
        self.maximum_iterations = maximum_iterations
        self.calculate_voltage_angles = calculate_voltage_angles
        self.zero_injection = zero_injection
        self.chi2_alpha = chi2_alpha
        self.run_bad_data_detection = run_bad_data_detection

    # ------------------------------------------------------------------

    def run(self, net: pp.pandapowerNet) -> EstimationResult:
        result = EstimationResult(algorithm=self.algorithm)
        t0 = time.perf_counter()

        # --- Step 1: Initial WLS run with verbose capture ---
        verbose_log = self._run_estimation(net, result)
        result.convergence_log = verbose_log.splitlines()
        corrections, n_iter = _parse_verbose(verbose_log)
        result.max_corrections = corrections
        result.iterations = n_iter or result.iterations
        result.final_tolerance = corrections[-1] if corrections else float("nan")

        if not result.converged:
            result.computation_time_s = time.perf_counter() - t0
            result.warnings.append("State estimation did not converge.")
            return result

        # --- Step 2: Bad data detection ---
        if self.run_bad_data_detection:
            self._bad_data_detection(net, result)

        # --- Step 3: Extract results ---
        self._extract_results(net, result)
        self._compute_greatest_mismatch(net, result)

        result.computation_time_s = time.perf_counter() - t0
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_estimation(self, net: pp.pandapowerNet, result: EstimationResult) -> str:
        """Run estimation, capture stdout, return raw log string."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            kwargs: dict = {
                "algorithm": self.algorithm,
                "init": self.init,
                "tolerance": self.tolerance,
                "maximum_iterations": self.maximum_iterations,
                "calculate_voltage_angles": self.calculate_voltage_angles,
            }
            if self.zero_injection is not None:
                kwargs["zero_injection"] = self.zero_injection

            converged = pp.estimation.estimate(net, **kwargs)
            result.converged = bool(converged)
            result.iterations = self.maximum_iterations  # fallback; overridden by log parse
        except Exception as exc:
            result.converged = False
            result.warnings.append(f"Estimation error: {exc}")
            logger.exception("Estimation failed")
        finally:
            sys.stdout = old_stdout

        return buf.getvalue()

    def _bad_data_detection(self, net: pp.pandapowerNet, result: EstimationResult) -> None:
        try:
            from pandapower.estimation import chi2_analysis, remove_bad_data

            bad_data_flag = chi2_analysis(net, alpha=self.chi2_alpha)
            result.bad_data_detected = bool(bad_data_flag)

            # Capture chi2 statistic from net if available
            if hasattr(net, "_chi2") and net._chi2 is not None:
                result.chi2_statistic = float(net._chi2.get("chi2", float("nan")))
                result.chi2_threshold = float(net._chi2.get("threshold", float("nan")))

            result.chi2_test_passed = not bad_data_flag

            if bad_data_flag:
                logger.info("Bad data detected – running normalised residual test.")
                removed_indices: list[int] = []
                buf = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = buf
                    converged_clean = remove_bad_data(
                        net,
                        init=self.init,
                        tolerance=self.tolerance,
                        maximum_iterations=self.maximum_iterations,
                        chi2_prob_false=self.chi2_alpha,
                        rn_max_threshold=3.0,
                    )
                    result.converged = bool(converged_clean)
                except Exception as exc:
                    result.warnings.append(f"Bad data removal error: {exc}")
                    logger.warning("Bad data removal failed: %s", exc)
                finally:
                    sys.stdout = old_stdout

                # Find which measurements were removed (marked as excluded)
                if "excluded" in net.measurement.columns:
                    excluded = net.measurement[net.measurement["excluded"] == True]
                    for idx, row in excluded.iterrows():
                        result.removed_measurements.append({
                            "index": idx,
                            "name": row.get("name", ""),
                            "meas_type": row.get("measurement_type", ""),
                            "element_type": row.get("element_type", ""),
                            "element": row.get("element", ""),
                            "value": row.get("value", ""),
                            "std_dev": row.get("std_dev", ""),
                        })

        except ImportError:
            result.warnings.append("chi2_analysis not available in this pandapower version.")
        except Exception as exc:
            result.warnings.append(f"Bad data detection error: {exc}")
            logger.warning("Bad data detection error: %s", exc)

    def _extract_results(self, net: pp.pandapowerNet, result: EstimationResult) -> None:
        bus_res_attr = "res_bus_est" if hasattr(net, "res_bus_est") and not net.res_bus_est.empty else "res_bus"
        result.res_bus = getattr(net, bus_res_attr, pd.DataFrame()).copy()
        # Add bus name column
        if not result.res_bus.empty and "name" not in result.res_bus.columns:
            result.res_bus = result.res_bus.join(net.bus[["name", "vn_kv"]], how="left")

        line_res_attr = "res_line_est" if hasattr(net, "res_line_est") and not net.res_line_est.empty else "res_line"
        result.res_line = getattr(net, line_res_attr, pd.DataFrame()).copy()
        if not result.res_line.empty and "name" not in result.res_line.columns:
            result.res_line = result.res_line.join(net.line[["name", "from_bus", "to_bus"]], how="left")

        trafo_res_attr = "res_trafo_est" if hasattr(net, "res_trafo_est") and not net.res_trafo_est.empty else "res_trafo"
        result.res_trafo = getattr(net, trafo_res_attr, pd.DataFrame()).copy()
        if not result.res_trafo.empty and "name" not in result.res_trafo.columns:
            result.res_trafo = result.res_trafo.join(net.trafo[["name", "hv_bus", "lv_bus"]], how="left")

        trafo3w_res_attr = "res_trafo3w_est" if hasattr(net, "res_trafo3w_est") and not net.res_trafo3w_est.empty else "res_trafo3w"
        result.res_trafo3w = getattr(net, trafo3w_res_attr, pd.DataFrame()).copy()

    def _compute_greatest_mismatch(
        self, net: pp.pandapowerNet, result: EstimationResult
    ) -> None:
        """Find the measurement with the highest normalised residual."""
        if result.res_bus.empty:
            return

        meas = net.measurement.copy()
        if meas.empty:
            return

        mismatches: list[dict] = []

        for idx, row in meas.iterrows():
            mtype = str(row.get("measurement_type", "")).lower()
            etype = str(row.get("element_type", "")).lower()
            elem = row.get("element", 0)
            measured = float(row.get("value", 0.0))
            std_dev = float(row.get("std_dev", 0.01)) or 0.01

            estimated = self._get_estimated_value(net, result, mtype, etype, elem, row)
            if estimated is None:
                continue

            residual = abs(measured - estimated)
            norm_residual = residual / std_dev
            mismatches.append({
                "meas_id": idx,
                "name": row.get("name", ""),
                "meas_type": mtype,
                "element_type": etype,
                "element": elem,
                "measured": round(measured, 6),
                "estimated": round(estimated, 6),
                "residual": round(residual, 6),
                "normalized_residual": round(norm_residual, 4),
                "std_dev": std_dev,
            })

        if mismatches:
            result.normalized_residuals = pd.DataFrame(mismatches).sort_values(
                "normalized_residual", ascending=False
            )
            result.greatest_mismatch = mismatches[
                max(range(len(mismatches)), key=lambda i: mismatches[i]["normalized_residual"])
            ]

    def _get_estimated_value(
        self, net: pp.pandapowerNet, result: EstimationResult,
        mtype: str, etype: str, elem: int, row: pd.Series,
    ) -> float | None:
        try:
            if etype == "bus":
                if mtype == "v":
                    return float(result.res_bus.at[elem, "vm_pu"])
                elif mtype == "p":
                    return float(result.res_bus.at[elem, "p_mw"])
                elif mtype == "q":
                    return float(result.res_bus.at[elem, "q_mvar"])
            elif etype == "line" and not result.res_line.empty:
                side = str(row.get("side", "from")).lower()
                if mtype == "p":
                    col = "p_from_mw" if side in ("from", "") else "p_to_mw"
                    return float(result.res_line.at[elem, col])
                elif mtype == "q":
                    col = "q_from_mvar" if side in ("from", "") else "q_to_mvar"
                    return float(result.res_line.at[elem, col])
                elif mtype == "i":
                    return float(result.res_line.at[elem, "i_from_ka"])
            elif etype == "trafo" and not result.res_trafo.empty:
                side = str(row.get("side", "hv")).lower()
                if mtype == "p":
                    col = "p_hv_mw" if side in ("hv", "") else "p_lv_mw"
                    return float(result.res_trafo.at[elem, col])
                elif mtype == "q":
                    col = "q_hv_mvar" if side in ("hv", "") else "q_lv_mvar"
                    return float(result.res_trafo.at[elem, col])
        except (KeyError, IndexError):
            return None
        return None
