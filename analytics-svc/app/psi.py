"""Distribution-drift math.

Pure functions, no I/O. Everything here takes plain numbers and returns plain
numbers so the drift computation can be unit-tested in isolation from the
telemetry backend and the HTTP layer.

The core measure is PSI (Population Stability Index), the industry-standard
drift metric. It compares a baseline distribution against a current one and
returns a single magnitude with well-established interpretation bands:

    PSI < 0.10   no significant shift        -> "low"
    0.10 - 0.25  moderate shift, watch it    -> "medium"
    PSI > 0.25   significant shift, act       -> "high"

KL divergence is offered alongside for the cases where a directional
information-gain reading is clearer than PSI's symmetric-ish magnitude.

Both work from per-bin *counts* rather than proportions, because turning counts
into a proper probability distribution requires smoothing away empty bins, and
that is easiest to do correctly in one place.
"""

from __future__ import annotations

import math
from typing import Sequence

# Standard PSI interpretation thresholds. Kept here, next to the math, because
# they are intrinsic to PSI itself, not to any particular tile or signal.
PSI_MEDIUM_THRESHOLD = 0.10
PSI_HIGH_THRESHOLD = 0.25

# Laplace (additive) smoothing weight. Added to every bin's count before
# proportions are taken, so no bin is ever exactly zero. Without this, a single
# empty bin sends ln(a/e) to +/- infinity and the whole PSI blows up. 0.5 is a
# conventional, gentle choice.
_SMOOTHING_ALPHA = 0.5


class InsufficientData(Exception):
    """Raised when there is not enough data to compute drift meaningfully.

    The caller is expected to translate this into an ``unavailable`` tile
    rather than a hard failure: a missing drift number is a gray gauge, never a
    500. This keeps the whole feature fail-open, matching the observability
    plane's own discipline.
    """


def quantile_edges(values: Sequence[float], num_bins: int = 10) -> list[float]:
    """Bin edges placed at evenly spaced quantiles of the baseline values.

    Quantile (equal-frequency) bins are used rather than equal-width ones so a
    heavy-tailed signal like latency does not dump almost everything into one
    bin. The edges are derived from the *baseline* and then reused for the
    current window, so both distributions are measured on the same ruler.
    """
    clean = sorted(float(v) for v in values if v is not None and not math.isnan(float(v)))
    if len(clean) < 2:
        raise InsufficientData("need at least two baseline values to form bins")

    edges: list[float] = []
    for i in range(num_bins + 1):
        q = i / num_bins
        pos = q * (len(clean) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        interp = clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)
        edges.append(interp)

    # Collapse duplicate edges (a signal with little spread can produce repeated
    # quantiles). Fewer than two distinct edges means the baseline is
    # effectively constant and drift cannot be measured on it.
    unique = sorted(set(edges))
    if len(unique) < 2:
        raise InsufficientData("baseline values have no spread to bin on")
    return unique


def bin_counts(values: Sequence[float], edges: Sequence[float]) -> list[int]:
    """Count how many values fall in each bin defined by ``edges``.

    Values below the first edge are clamped into the first bin and values above
    the last edge into the last bin, so the current window never loses mass just
    because it ranges wider than the baseline did.
    """
    if len(edges) < 2:
        raise InsufficientData("need at least two edges to bin")
    counts = [0] * (len(edges) - 1)
    for raw in values:
        if raw is None:
            continue
        v = float(raw)
        if math.isnan(v):
            continue
        placed = False
        for i in range(len(edges) - 1):
            # Last bin is closed on the right so the maximum value lands inside.
            upper_closed = i == len(edges) - 2
            if v < edges[i + 1] or (upper_closed and v <= edges[i + 1]):
                if v >= edges[i] or i == 0:
                    counts[i] += 1
                    placed = True
                    break
        if not placed:
            # Above the last edge -> clamp into the last bin.
            counts[-1] += 1
    return counts


def category_counts(
    baseline: Sequence[str], current: Sequence[str]
) -> tuple[list[int], list[int]]:
    """Aligned per-category counts over the union of categories seen in either
    window. Used for categorical signals such as which agent handled a request
    or which tool was chosen, where "bins" are the discrete labels themselves.
    """
    categories = sorted(set(baseline) | set(current))
    if not categories:
        raise InsufficientData("no categories observed in either window")
    base_map = {c: 0 for c in categories}
    cur_map = {c: 0 for c in categories}
    for c in baseline:
        base_map[c] += 1
    for c in current:
        cur_map[c] += 1
    return [base_map[c] for c in categories], [cur_map[c] for c in categories]


def _proportions(counts: Sequence[int]) -> list[float]:
    """Laplace-smoothed proportions: a proper probability distribution with no
    zero entries, so the logarithms in PSI and KL are always finite."""
    total = sum(counts) + _SMOOTHING_ALPHA * len(counts)
    if total <= 0:
        raise InsufficientData("no observations to form a distribution")
    return [(c + _SMOOTHING_ALPHA) / total for c in counts]


def psi(baseline_counts: Sequence[int], current_counts: Sequence[int]) -> float:
    """Population Stability Index between two aligned count vectors.

    PSI = sum over bins of (current_i - baseline_i) * ln(current_i / baseline_i)

    Both inputs must be aligned to the same bins. Returns 0.0 for identical
    distributions and grows without bound as they diverge.
    """
    if len(baseline_counts) != len(current_counts):
        raise ValueError("baseline and current must have the same number of bins")
    if len(baseline_counts) == 0:
        raise InsufficientData("no bins to compare")
    expected = _proportions(baseline_counts)
    actual = _proportions(current_counts)
    return sum((a - e) * math.log(a / e) for e, a in zip(expected, actual))


def kl_divergence(p_counts: Sequence[int], q_counts: Sequence[int]) -> float:
    """Kullback-Leibler divergence KL(P || Q) in nats.

    Directional: the information lost when Q (current) is used to approximate P
    (baseline). Not symmetric, unlike PSI. Provided for signals where that
    directionality reads more naturally.
    """
    if len(p_counts) != len(q_counts):
        raise ValueError("p and q must have the same number of bins")
    if len(p_counts) == 0:
        raise InsufficientData("no bins to compare")
    p = _proportions(p_counts)
    q = _proportions(q_counts)
    return sum(pi * math.log(pi / qi) for pi, qi in zip(p, q))


def band_for_psi(value: float) -> str:
    """Map a raw PSI value onto the UI's low/medium/high band using the standard
    interpretation thresholds."""
    if value < PSI_MEDIUM_THRESHOLD:
        return "low"
    if value < PSI_HIGH_THRESHOLD:
        return "medium"
    return "high"


def psi_display_percent(value: float) -> int:
    """Scale a raw PSI into the 0-100 magnitude the gauge renders.

    A PSI of 1.0 or more reads as a fully saturated gauge; below that it scales
    linearly. This is a display convenience only; banding is done on the raw
    value so the colour never disagrees with the standard thresholds.
    """
    return int(round(min(max(value, 0.0), 1.0) * 100))
