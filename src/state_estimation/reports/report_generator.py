"""HTML report generator for WLS state estimation results.

Produces a self-contained HTML file (no external dependencies) containing:
  1. Run summary (status, algorithm, timing, network size)
  2. IEC 61850 / SCADA metadata (when applicable)
  3. WLS convergence chart + per-iteration table
  4. Bad-data detection results
  5. Bus voltage / power results
  6. Line power-flow results
  7. Transformer results
  8. Measurement normalised residuals

Companion CSV files (bus_results, line_results, trafo_results, residuals)
are written alongside the HTML for downstream processing.
"""
from __future__ import annotations

import base64
import html as _html_lib
import io
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TABLE_ROW_LIMIT = 500

# ---------------------------------------------------------------------------
# Inline CSS
# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;font-size:13px;background:#f0f2f5;color:#222}
.page{max-width:1280px;margin:0 auto;background:#fff;padding:28px 36px 56px}
h1{font-size:1.65em;color:#0d2a4a;margin-bottom:4px}
h2{font-size:1.05em;color:#0d2a4a;margin:28px 0 8px;
   border-bottom:2px solid #0d2a4a;padding-bottom:5px}
h3{font-size:0.9em;color:#0d2a4a;margin:14px 0 5px}
.subtitle{color:#666;font-size:0.88em;margin-bottom:28px}
.badge{display:inline-block;padding:2px 11px;border-radius:11px;
       font-weight:700;font-size:0.82em;letter-spacing:.3px}
.green{background:#d1f5dc;color:#155724}
.red{background:#fde0e0;color:#7a1010}
.yellow{background:#fff4cc;color:#7a5800}
table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:6px}
th{background:#0d2a4a;color:#fff;padding:6px 9px;text-align:left;
   font-weight:600;white-space:nowrap}
td{padding:5px 9px;border-bottom:1px solid #ececec;vertical-align:top}
tr:nth-child(even) td{background:#f7f8fa}
tr:hover td{background:#eaf0fb}
.kv td:first-child{font-weight:600;width:230px;color:#444;white-space:nowrap}
.kv td:last-child{color:#222}
.truncated{color:#888;font-style:italic;font-size:0.82em;margin:4px 0 8px}
.no-data{color:#999;font-style:italic;padding:6px 0}
.warn{color:#7a5800}
.chart{max-width:720px;margin:10px 0;display:block;border:1px solid #e0e0e0}
footer{margin-top:48px;color:#bbb;font-size:0.78em;
       border-top:1px solid #eee;padding-top:10px}
section{margin-bottom:4px}
"""

# ---------------------------------------------------------------------------
# Markup helper
# ---------------------------------------------------------------------------

class _Safe:
    """Wrapper: mark a string as already-escaped / raw HTML."""
    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s


def _e(v: Any) -> str:
    """Escape plain value to HTML-safe string."""
    return _html_lib.escape(str(v))


def _cell(v: Any) -> str:
    """Render a table cell value – preserve _Safe, escape everything else."""
    if isinstance(v, _Safe):
        return v.s
    return _e(v)


def _badge(text: str, colour: str) -> _Safe:
    return _Safe(f'<span class="badge {colour}">{_e(text)}</span>')


def _fmt_float(v: float) -> str:
    if math.isnan(v):
        return "—"
    if abs(v) >= 10_000 or (abs(v) < 0.0001 and v != 0.0):
        return f"{v:.4e}"
    return f"{v:.4f}"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return _fmt_float(v)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return str(v)


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def _kv_table(rows: list[tuple[str, Any]]) -> str:
    cells = "\n".join(
        f"<tr><td>{_e(k)}</td><td>{_cell(v)}</td></tr>"
        for k, v in rows
    )
    return f'<table class="kv"><tbody>{cells}</tbody></table>'


def _plain_table(headers: list[str], rows: list[list[Any]]) -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _df_table(df: pd.DataFrame, highlight_index: Any = None) -> str:
    th = "".join(f"<th>{_e(c)}</th>" for c in df.columns)
    rows_html: list[str] = []
    for idx, row in df.iterrows():
        hl = ' style="background:#fff4cc"' if highlight_index is not None and idx == highlight_index else ""
        cells = "".join(f"<td>{_e(_fmt(v))}</td>" for v in row)
        rows_html.append(f"<tr{hl}>{cells}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"


def _limit(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    if len(df) > _TABLE_ROW_LIMIT:
        note = (f'<p class="truncated">Showing first {_TABLE_ROW_LIMIT} of {len(df)} rows '
                f'— see companion CSV for full data.</p>')
        return df.iloc[:_TABLE_ROW_LIMIT], note
    return df, ""


# ---------------------------------------------------------------------------
# Convergence chart (matplotlib → base64 PNG)
# ---------------------------------------------------------------------------

def _convergence_chart(corrections: list[float]) -> str:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        iters = list(range(1, len(corrections) + 1))
        log_corr = [math.log10(max(c, 1e-16)) for c in corrections]

        fig, ax = plt.subplots(figsize=(7.2, 3.6))
        ax.plot(iters, log_corr, "o-", color="#0d2a4a", linewidth=1.8, markersize=5,
                markerfacecolor="#3a7ebf")
        ax.set_xlabel("Iteration", fontsize=10)
        ax.set_ylabel("log₁₀(max correction)", fontsize=10)
        ax.set_title("WLS Convergence", fontsize=11, fontweight="bold")
        ax.set_xticks(iters)
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.tick_params(labelsize=9)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f'<img class="chart" src="data:image/png;base64,{b64}" alt="Convergence chart">'
    except Exception as exc:
        logger.warning("Convergence chart unavailable: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generate a self-contained HTML report from an EstimationResult."""

    def generate(
        self,
        result: Any,
        output_dir: str | Path = "reports",
        network_name: str = "PLN Network",
        run_timestamp: str = "",
        net_summary: dict | None = None,
        scada_summary: dict | None = None,
    ) -> Path:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = out / f"se_report_{ts_tag}.html"

        self._write_csvs(result, out, ts_tag)

        sections: list[str] = [
            self._summary(result, net_summary or {}, scada_summary or {}),
        ]
        if scada_summary and scada_summary.get("is_scada"):
            sections.append(self._scada_meta(scada_summary))
        sections += [
            self._convergence(result),
            self._bad_data(result),
            self._bus_results(result),
            self._line_results(result),
            self._trafo_results(result),
            self._residuals(result),
        ]

        try:
            from .. import __version__ as _ver
        except Exception:
            _ver = ""

        if not run_timestamp:
            run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f"<title>SE Report \u2013 {_e(network_name)}</title>\n"
            f"<style>{_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            '<div class="page">\n'
            f"<h1>State Estimation Report</h1>\n"
            f'<p class="subtitle">{_e(network_name)}&nbsp;&middot;&nbsp;{_e(run_timestamp)}</p>\n'
            + "\n".join(sections)
            + f'\n<footer>Generated by state_estimation_for_pln {_e(_ver)}'
            f"&nbsp;&middot;&nbsp;{_e(run_timestamp)}</footer>\n"
            "</div>\n"
            "</body>\n"
            "</html>"
        )

        html_path.write_text(html, encoding="utf-8")
        logger.info("Report written: %s", html_path)
        return html_path

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _summary(self, result: Any, ns: dict, scada: dict) -> str:
        converged: bool = getattr(result, "converged", False)
        status_badge = _badge("CONVERGED", "green") if converged else _badge("NOT CONVERGED", "red")

        ft = getattr(result, "final_tolerance", float("nan"))
        final_delta = _fmt_float(ft) if not math.isnan(ft) else "—"

        removed = getattr(result, "removed_measurements", [])
        bad_cell: Any
        if getattr(result, "bad_data_detected", False):
            bad_cell = _Safe(
                _badge(f"{len(removed)} measurement(s) removed", "yellow").s
            )
        else:
            bad_cell = "None detected"

        rows: list[tuple[str, Any]] = [
            ("Status", status_badge),
            ("Algorithm", getattr(result, "algorithm", "—").upper()),
            ("Iterations", getattr(result, "iterations", "—")),
            ("Final \u0394 (max correction)", final_delta),
            ("Computation time", f"{getattr(result, 'computation_time_s', 0.0):.2f} s"),
            ("Buses", ns.get("buses", "—")),
            ("Lines", ns.get("lines", "—")),
            ("Transformers", ns.get("trafos", "—")),
            ("Measurements", ns.get("measurements", "—")),
            ("Bad data", bad_cell),
        ]

        warnings = getattr(result, "warnings", [])
        if warnings:
            rows.append(("Warnings", _Safe(
                "<br>".join(f'<span class="warn">{_e(w)}</span>' for w in warnings)
            )))

        gm = getattr(result, "greatest_mismatch", {})
        if gm:
            rows.append((
                "Greatest mismatch",
                _Safe(
                    f'Meas&nbsp;#{_e(gm.get("meas_id", "?"))} '
                    f'({_e(gm.get("name", ""))})'
                    f'&nbsp;&mdash;&nbsp;|r|/&sigma;&nbsp;=&nbsp;'
                    f'<strong>{_e(_fmt_float(gm.get("normalized_residual", float("nan"))))}</strong>'
                ),
            ))

        return f'<section id="summary"><h2>Summary</h2>{_kv_table(rows)}</section>'

    def _scada_meta(self, scada: dict) -> str:
        ts_list: list[str] = scada.get("timestamps", [])
        used_ts = ts_list[-1] if ts_list else "—"

        if len(ts_list) <= 6:
            all_ts_str = ", ".join(ts_list) if ts_list else "—"
        else:
            all_ts_str = f"{ts_list[0]} \u2026 {ts_list[-1]} ({len(ts_list)} snapshots)"

        rows: list[tuple[str, Any]] = [
            ("Substations (B1)", ", ".join(scada.get("substations", []))),
            ("Voltage levels (B2, kV)", ", ".join(
                str(int(v)) if v == int(v) else str(v)
                for v in scada.get("voltage_levels_kv", [])
            )),
            ("Equipment codes (B3)", str(len(scada.get("equipment_codes", [])))),
            ("Timestamps in file", all_ts_str),
            ("Timestamp used for SE", _Safe(f"<strong>{_e(used_ts)}</strong>")),
        ]

        suspect = scada.get("suspect_measurements", 0)
        if suspect:
            rows.append(("Suspect quality flags", _Safe(
                f'<span class="warn">{suspect} measurement(s) accepted with warning</span>'
            )))

        return f'<section id="scada"><h2>IEC&nbsp;61850&nbsp;/ SCADA Metadata</h2>{_kv_table(rows)}</section>'

    def _convergence(self, result: Any) -> str:
        corrections: list[float] = getattr(result, "max_corrections", [])

        if not corrections:
            inner = '<p class="no-data">No per-iteration convergence data captured.</p>'
        else:
            chart = _convergence_chart(corrections)
            tbl = _plain_table(
                ["Iteration", "Max Correction"],
                [(i + 1, _fmt_float(c)) for i, c in enumerate(corrections)],
            )
            inner = chart + tbl

        return f'<section id="convergence"><h2>Convergence</h2>{inner}</section>'

    def _bad_data(self, result: Any) -> str:
        chi2_stat = getattr(result, "chi2_statistic", float("nan"))
        chi2_thr = getattr(result, "chi2_threshold", float("nan"))
        passed = getattr(result, "chi2_test_passed", False)
        detected = getattr(result, "bad_data_detected", False)

        if not math.isnan(chi2_stat):
            test_cell: Any = _badge("PASSED", "green") if passed else _badge("FAILED", "red")
        else:
            test_cell = "—"

        removed = getattr(result, "removed_measurements", [])
        rows: list[tuple[str, Any]] = [
            ("\u03c7\u00b2 test result", test_cell),
            ("\u03c7\u00b2 statistic", _fmt_float(chi2_stat)),
            ("\u03c7\u00b2 threshold", _fmt_float(chi2_thr)),
            ("Measurements removed", str(len(removed))),
        ]
        meta = _kv_table(rows)

        removed_tbl = ""
        if removed:
            cols = ["index", "name", "meas_type", "element_type", "element", "value", "std_dev"]
            removed_tbl = (
                "<h3>Removed Measurements</h3>"
                + _plain_table(cols, [[str(m.get(c, "")) for c in cols] for m in removed])
            )

        return f'<section id="bad-data"><h2>Bad Data Detection</h2>{meta}{removed_tbl}</section>'

    def _bus_results(self, result: Any) -> str:
        df: pd.DataFrame = getattr(result, "res_bus", pd.DataFrame())
        if df.empty:
            return '<section id="bus"><h2>Bus Results</h2><p class="no-data">No results.</p></section>'

        want = ["name", "vn_kv", "vm_pu", "va_degree", "p_mw", "q_mvar"]
        cols = [c for c in want if c in df.columns]
        df2, note = _limit(df[cols])
        return f'<section id="bus"><h2>Bus Results</h2>{_df_table(df2)}{note}</section>'

    def _line_results(self, result: Any) -> str:
        df: pd.DataFrame = getattr(result, "res_line", pd.DataFrame())
        if df.empty:
            return '<section id="line"><h2>Line Results</h2><p class="no-data">No results.</p></section>'

        want = ["name", "from_bus", "to_bus",
                "p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar",
                "i_from_ka", "loading_percent"]
        cols = [c for c in want if c in df.columns]
        df2, note = _limit(df[cols])
        return f'<section id="line"><h2>Line Results</h2>{_df_table(df2)}{note}</section>'

    def _trafo_results(self, result: Any) -> str:
        parts: list[str] = []

        df2w: pd.DataFrame = getattr(result, "res_trafo", pd.DataFrame())
        if not df2w.empty:
            want = ["name", "hv_bus", "lv_bus",
                    "p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "loading_percent"]
            cols = [c for c in want if c in df2w.columns]
            d, note = _limit(df2w[cols])
            parts.append(_df_table(d) + note)

        df3w: pd.DataFrame = getattr(result, "res_trafo3w", pd.DataFrame())
        if not df3w.empty:
            want3 = ["name", "p_hv_mw", "q_hv_mvar",
                     "p_mv_mw", "q_mv_mvar", "p_lv_mw", "q_lv_mvar"]
            cols3 = [c for c in want3 if c in df3w.columns] or list(df3w.columns)
            d3, note3 = _limit(df3w[cols3])
            parts.append("<h3>3-Winding Transformers</h3>" + _df_table(d3) + note3)

        if not parts:
            parts = ['<p class="no-data">No transformer results.</p>']

        return f'<section id="trafo"><h2>Transformer Results</h2>{"".join(parts)}</section>'

    def _residuals(self, result: Any) -> str:
        df: pd.DataFrame = getattr(result, "normalized_residuals", pd.DataFrame())
        if df.empty:
            return (
                '<section id="residuals"><h2>Measurement Residuals</h2>'
                '<p class="no-data">No residual data.</p></section>'
            )

        want = ["name", "meas_type", "element_type", "element",
                "measured", "estimated", "residual", "normalized_residual"]
        cols = [c for c in want if c in df.columns]
        d, note = _limit(df[cols])

        gm = getattr(result, "greatest_mismatch", {})
        hl_idx = gm.get("meas_id") if gm else None

        return (
            f'<section id="residuals"><h2>Measurement Residuals</h2>'
            f'{_df_table(d, highlight_index=hl_idx)}{note}</section>'
        )

    # ------------------------------------------------------------------
    # CSV companion files
    # ------------------------------------------------------------------

    def _write_csvs(self, result: Any, out: Path, ts_tag: str) -> None:
        exports = [
            ("bus_results", getattr(result, "res_bus", pd.DataFrame())),
            ("line_results", getattr(result, "res_line", pd.DataFrame())),
            ("trafo_results", getattr(result, "res_trafo", pd.DataFrame())),
            ("residuals", getattr(result, "normalized_residuals", pd.DataFrame())),
        ]
        for name, df in exports:
            if not df.empty:
                p = out / f"{name}_{ts_tag}.csv"
                df.to_csv(p, index=True)
                logger.debug("CSV written: %s", p)
