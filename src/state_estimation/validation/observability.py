"""
Observability Analysis — pre-estimation gate.

Checks that the available measurement set is sufficient to make the state
estimation problem observable (i.e., every state variable can be uniquely
determined from the measurements).

Theory
------
For a network with ``n`` TopologicalNodes and one angle-reference node, the
state vector has ``2n − 1`` unknowns (n voltage magnitudes + n−1 angles).
A measurement set is *numerically observable* iff the measurement Jacobian H
has full column rank (rank = 2n − 1).

The Jacobian is linearised at the flat-start operating point (|V| = 1 p.u.,
θ = 0).  Its rank is checked with:

  • n ≤ 500  — ``numpy.linalg.matrix_rank`` on a dense matrix.
  • n  > 500  — ``scipy.sparse.lil_matrix`` for construction, converted to CSR
                for a truncated SVD via ``scipy.sparse.linalg.svds``.  This
                keeps peak memory at O(m × k) rather than O(m × 2n) and is
                dramatically faster for large networks (Java 500/150 kV scale).

Additionally, a *graph-based* (topological) observability check identifies
every bus that has no measurement coverage, regardless of Jacobian rank.

Results
-------
``ObservabilityResult.observable``      — True iff both checks pass.
``ObservabilityResult.rank_deficiency`` — number of missing independent measurements.
``ObservabilityResult.unobservable_nodes`` — mRIDs of uncovered TNs.
``ObservabilityResult.pseudo_measurements`` — flat-start suggestions for each gap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Union

import numpy as np
import scipy.sparse
import scipy.sparse.linalg

from ..cim.model import TopologicalNode
from ..cim.profiles.eq  import EquipmentProfile
from ..cim.profiles.tp  import TopologyProfile
from ..cim.profiles.ssh import SteadyStateHypothesisProfile

log = logging.getLogger(__name__)

# Network size above which the sparse Jacobian path is used.
_SPARSE_THRESHOLD = 500


@dataclass
class ObservabilityResult:
    """Result of a pre-estimation observability check."""
    observable:          bool       = False
    n_states:            int        = 0
    n_measurements:      int        = 0
    jacobian_rank:       int        = 0
    rank_deficiency:     int        = 0
    used_sparse:         bool       = False    # True when sparse path was taken
    unobservable_nodes:  List[str]  = field(default_factory=list)  # TN mRIDs
    pseudo_measurements: List[dict] = field(default_factory=list)  # suggestions
    warnings:            List[str]  = field(default_factory=list)

    def summary(self) -> str:
        status  = "OBSERVABLE" if self.observable else "NOT OBSERVABLE"
        backend = "sparse SVD" if self.used_sparse else "dense SVD"
        lines = [
            f"Observability: {status}  [{backend}]",
            f"  States (2n-1):     {self.n_states}",
            f"  Measurements:      {self.n_measurements}",
            f"  Jacobian rank:     {self.jacobian_rank}",
            f"  Rank deficiency:   {self.rank_deficiency}",
        ]
        if self.unobservable_nodes:
            lines.append(f"  Unobservable TNs:  {len(self.unobservable_nodes)}")
            for mrid in self.unobservable_nodes[:5]:
                lines.append(f"    - {mrid}")
            if len(self.unobservable_nodes) > 5:
                lines.append(f"    … and {len(self.unobservable_nodes) - 5} more")
        if self.pseudo_measurements:
            lines.append("  Suggested pseudo-measurements:")
            for pm in self.pseudo_measurements[:5]:
                lines.append(f"    - {pm}")
        return "\n".join(lines)


class ObservabilityAnalyzer:
    """
    Checks measurement observability before the WLS estimator runs.

    Parameters
    ----------
    eq  : EquipmentProfile
    tp  : TopologyProfile  (must be post-topology-processing)
    ssh : SteadyStateHypothesisProfile  (contains AnalogValues)
    sparse_threshold : int
        Networks with more TopologicalNodes than this use the sparse Jacobian
        path.  Default: 500.
    """

    V_TYPES = {"VoltageMagnitude", "V"}
    P_TYPES = {"ThreePhaseActivePower", "P"}
    Q_TYPES = {"ThreePhaseReactivePower", "Q"}
    I_TYPES = {"CurrentMagnitude", "I"}

    def __init__(self,
                 eq:  EquipmentProfile,
                 tp:  TopologyProfile,
                 ssh: SteadyStateHypothesisProfile,
                 sparse_threshold: int = _SPARSE_THRESHOLD) -> None:
        self.eq               = eq
        self.tp               = tp
        self.ssh              = ssh
        self.sparse_threshold = sparse_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> ObservabilityResult:
        """Run the full observability analysis and return the result."""
        result = ObservabilityResult()

        tn_list = list(self.tp.topological_nodes.values())
        n = len(tn_list)
        if n == 0:
            result.warnings.append(
                "No TopologicalNodes found — run TopologyProcessor first."
            )
            return result

        n_states = 2 * n - 1
        result.n_states = n_states

        meas = self._collect_measurements(tn_list)
        result.n_measurements = len(meas)

        if not meas:
            result.warnings.append("No valid measurements found in SSH profile.")
            result.unobservable_nodes = [tn.mRID for tn in tn_list]
            return result

        use_sparse = n > self.sparse_threshold
        result.used_sparse = use_sparse

        if use_sparse:
            H = self._build_jacobian_sparse(tn_list, meas, n_states)
            rank = self._sparse_rank(H, tol=1e-8)
        else:
            H = self._build_jacobian_dense(tn_list, meas, n_states)
            rank = int(np.linalg.matrix_rank(H, tol=1e-8))

        result.jacobian_rank   = rank
        result.rank_deficiency = max(0, n_states - rank)
        result.observable      = (rank >= n_states)

        if not result.observable:
            unobs = self._find_unobservable_nodes(tn_list, meas)
            result.unobservable_nodes  = sorted(unobs)
            result.pseudo_measurements = self._suggest_pseudo_measurements(
                unobs, tn_list
            )
            result.warnings.append(
                f"Rank deficiency {result.rank_deficiency}: "
                f"{len(unobs)} unobservable node(s).  "
                "Add measurements or pseudo-measurements at the suggested buses."
            )

        log.info(result.summary())
        return result

    # ------------------------------------------------------------------
    # Measurement collection
    # ------------------------------------------------------------------

    def _collect_measurements(self, tn_list: List[TopologicalNode]) -> List[dict]:
        """
        Build a list of measurement dicts:
          {type, tn_mrid, terminal_mrid, value, std_dev}

        Only accepted (non-suspect) AnalogValues are included.
        """
        tn_set = {tn.mRID for tn in tn_list}
        meas: List[dict] = []

        for av in self.ssh.analog_values.values():
            if av.suspect:
                continue
            analog = self.eq.analogs.get(av.analog_mRID)
            if analog is None:
                continue
            mtype = (analog.measurementType or "").strip()

            tn_mrid = ""
            t = self.eq.terminals.get(analog.terminal_mRID)
            if t:
                tn_mrid = t.topologicalNode_mRID

            if not tn_mrid or tn_mrid not in tn_set:
                continue

            meas.append({
                "type":          mtype,
                "tn_mrid":       tn_mrid,
                "terminal_mrid": analog.terminal_mRID,
                "value":         av.value,
                "std_dev":       av.stdDev or 0.01,
            })

        return meas

    # ------------------------------------------------------------------
    # Dense Jacobian  (n ≤ sparse_threshold)
    # ------------------------------------------------------------------

    def _build_jacobian_dense(self,
                               tn_list: List[TopologicalNode],
                               meas:    List[dict],
                               n_states: int) -> np.ndarray:
        """
        Build H ∈ ℝ^{m × (2n-1)} as a dense numpy array.

        State ordering: [|V|₁ … |V|ₙ | θ₂ … θₙ]
        Flat-start partials:
          V measurement  → ∂h/∂|V|ᵢ = 1
          P measurement  → ∂h/∂θᵢ = 1  (dominant), ∂h/∂|V|ᵢ = 0.1
          Q measurement  → ∂h/∂|V|ᵢ = 1
          I measurement  → ∂h/∂|V|ᵢ = 1, ∂h/∂θᵢ = 0.5
        """
        n = len(tn_list)
        m = len(meas)
        H = np.zeros((m, n_states), dtype=np.float64)
        tn_index = {tn.mRID: i for i, tn in enumerate(tn_list)}
        self._fill_jacobian_rows(H, meas, tn_index, n)
        return H

    # ------------------------------------------------------------------
    # Sparse Jacobian  (n > sparse_threshold)
    # ------------------------------------------------------------------

    def _build_jacobian_sparse(self,
                                tn_list:  List[TopologicalNode],
                                meas:     List[dict],
                                n_states: int) -> scipy.sparse.csr_matrix:
        """
        Build H ∈ ℝ^{m × (2n-1)} as a scipy CSR sparse matrix.

        Uses lil_matrix for incremental row fill (efficient for insertion),
        then converts to CSR for the SVD solver.  At flat start the Jacobian
        has at most 2 non-zeros per row (voltage/angle columns), so sparsity
        is very high — typically >99% zeros for transmission networks.
        """
        n = len(tn_list)
        m = len(meas)
        H_lil = scipy.sparse.lil_matrix((m, n_states), dtype=np.float64)
        tn_index = {tn.mRID: i for i, tn in enumerate(tn_list)}
        self._fill_jacobian_rows(H_lil, meas, tn_index, n)
        return H_lil.tocsr()

    # ------------------------------------------------------------------
    # Shared row-fill logic (works for both ndarray and lil_matrix)
    # ------------------------------------------------------------------

    def _fill_jacobian_rows(self, H, meas: List[dict],
                             tn_index: Dict[str, int], n: int) -> None:
        for row, m in enumerate(meas):
            mtype   = m["type"]
            tn_mrid = m["tn_mrid"]
            i = tn_index.get(tn_mrid, -1)
            if i < 0:
                continue

            if mtype in self.V_TYPES:
                H[row, i] = 1.0

            elif mtype in self.P_TYPES:
                angle_col = n + (i - 1)
                if angle_col >= 0:
                    H[row, angle_col] = 1.0
                H[row, i] = 0.1

            elif mtype in self.Q_TYPES:
                H[row, i] = 1.0

            elif mtype in self.I_TYPES:
                H[row, i] = 1.0
                angle_col = n + (i - 1)
                if angle_col >= 0:
                    H[row, angle_col] = 0.5

    # ------------------------------------------------------------------
    # Rank computation
    # ------------------------------------------------------------------

    @staticmethod
    def _sparse_rank(H_csr: scipy.sparse.csr_matrix, tol: float = 1e-8) -> int:
        """
        Estimate the numerical rank of a sparse matrix via truncated SVD.

        ``scipy.sparse.linalg.svds`` computes the k largest singular values.
        We request k = min(m, n) − 1 (the maximum allowed by ARPACK).  Any
        singular value above ``tol`` contributes to the rank.

        For very small matrices, falls back to dense SVD via numpy.
        """
        m, n = H_csr.shape
        k = min(m, n) - 1
        if k <= 0:
            return 0

        # Dense fallback for tiny matrices (svds requires k < min(m,n))
        if k < 6 or min(m, n) < 10:
            return int(np.linalg.matrix_rank(H_csr.toarray(), tol=tol))

        try:
            _, singular_values, _ = scipy.sparse.linalg.svds(H_csr, k=k)
            return int(np.sum(singular_values > tol))
        except scipy.sparse.linalg.ArpackNoConvergence as exc:
            # Use whatever converged
            log.warning("svds did not fully converge (%d/%d): %s", len(exc.eigenvalues), k, exc)
            return int(np.sum(exc.eigenvalues > tol))

    # ------------------------------------------------------------------
    # Unobservable node identification
    # ------------------------------------------------------------------

    def _find_unobservable_nodes(self,
                                  tn_list: List[TopologicalNode],
                                  meas:    List[dict]) -> Set[str]:
        """
        Return the set of TN mRIDs that have no measurement coverage.

        A node is covered if:
          (a) at least one measurement directly references it, OR
          (b) at least one branch measurement passes through a terminal on it.
        """
        covered: Set[str] = set()
        for m in meas:
            covered.add(m["tn_mrid"])

        terminal_to_tn: Dict[str, str] = {
            t.mRID: t.topologicalNode_mRID
            for t in self.eq.terminals.values()
            if t.topologicalNode_mRID
        }
        for m in meas:
            if m.get("terminal_mrid"):
                tn = terminal_to_tn.get(m["terminal_mrid"])
                if tn:
                    covered.add(tn)

        return {tn.mRID for tn in tn_list if tn.mRID not in covered}

    # ------------------------------------------------------------------
    # Pseudo-measurement suggestions
    # ------------------------------------------------------------------

    def _suggest_pseudo_measurements(self,
                                      unobs:   Set[str],
                                      tn_list: List[TopologicalNode]) -> List[dict]:
        """
        For each unobservable TN, propose a flat-start voltage pseudo-measurement.

        The suggested value is the nominal voltage and the uncertainty is 5% of
        nominal — a conservative default consistent with IEC 61968-9 practice.
        """
        tn_by_mrid = {tn.mRID: tn for tn in tn_list}
        suggestions = []
        for mrid in sorted(unobs):
            tn   = tn_by_mrid.get(mrid)
            bv   = self.eq.base_voltages.get(tn.baseVoltage_mRID) if tn else None
            v_nom = bv.nominalVoltage if bv else 1.0
            suggestions.append({
                "tn_mrid":  mrid,
                "tn_name":  tn.name if tn else mrid[:8],
                "type":     "VoltageMagnitude",
                "value":    v_nom,
                "std_dev":  v_nom * 0.05,
                "reason":   "unobservable node — no measurement coverage",
            })
        return suggestions
