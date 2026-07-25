"""Unit tests for the drift math. Pure, deterministic, no I/O.

Run from analytics-svc/:
    python -m pytest tests/test_psi.py -v
"""

from __future__ import annotations

import math

import pytest

from app.psi import (
    PSI_HIGH_THRESHOLD,
    PSI_MEDIUM_THRESHOLD,
    InsufficientData,
    band_for_psi,
    bin_counts,
    category_counts,
    kl_divergence,
    psi,
    psi_display_percent,
    quantile_edges,
)


# ---------------------------------------------------------------- PSI core ---

def test_identical_distributions_have_zero_psi():
    counts = [10, 20, 30, 40]
    assert psi(counts, counts) == pytest.approx(0.0, abs=1e-12)


def test_psi_grows_as_distributions_diverge():
    baseline = [100, 100, 100, 100]
    mild = [90, 100, 100, 110]
    severe = [10, 20, 30, 340]
    assert psi(baseline, mild) < psi(baseline, severe)


def test_disjoint_distributions_exceed_the_high_band():
    # Baseline spread across the first bins, current piled entirely into the
    # last: the textbook "significant shift" case.
    baseline = [100, 100, 100, 0]
    current = [0, 0, 0, 300]
    assert psi(baseline, current) > PSI_HIGH_THRESHOLD


def test_psi_is_symmetric_in_shape_not_direction():
    # PSI is not formally symmetric, but both directions should agree on "there
    # is substantial drift here", a sanity check that neither direction is
    # accidentally near zero.
    a = [200, 50, 10]
    b = [10, 50, 200]
    assert psi(a, b) > PSI_MEDIUM_THRESHOLD
    assert psi(b, a) > PSI_MEDIUM_THRESHOLD


def test_psi_requires_matching_bin_counts():
    with pytest.raises(ValueError):
        psi([1, 2, 3], [1, 2])


def test_realistic_latency_shift_registers_as_drift():
    baseline = list(range(0, 100))
    edges = quantile_edges(baseline, num_bins=10)
    base_counts = bin_counts(baseline, edges)
    same_counts = bin_counts(list(range(0, 100)), edges)
    shifted_counts = bin_counts([v + 60 for v in baseline], edges)

    assert psi(base_counts, same_counts) == pytest.approx(0.0, abs=1e-12)
    assert psi(base_counts, shifted_counts) > PSI_MEDIUM_THRESHOLD


# ------------------------------------------------------------ KL divergence ---

def test_kl_of_identical_distributions_is_zero():
    counts = [5, 15, 25]
    assert kl_divergence(counts, counts) == pytest.approx(0.0, abs=1e-12)


def test_kl_is_non_negative():
    # Gibbs' inequality: KL(P||Q) >= 0 always.
    p = [100, 10, 1]
    q = [1, 10, 100]
    assert kl_divergence(p, q) >= 0.0
    assert kl_divergence(q, p) >= 0.0


# -------------------------------------------------------------------- bins ---

def test_quantile_edges_are_sorted_and_span_the_data():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    edges = quantile_edges(values, num_bins=5)
    assert edges == sorted(edges)
    assert edges[0] == pytest.approx(1.0)
    assert edges[-1] == pytest.approx(10.0)


def test_bin_counts_account_for_every_value():
    values = list(range(0, 100))
    edges = quantile_edges(values, num_bins=10)
    counts = bin_counts(values, edges)
    assert sum(counts) == len(values)


def test_bin_counts_clamp_values_above_the_last_edge_into_the_last_bin():
    edges = [0.0, 10.0, 20.0]
    counts = bin_counts([5, 15, 1000], edges)
    assert sum(counts) == 3
    assert counts[-1] >= 1  # the 1000 landed in the last bin, not lost


def test_categorical_counts_align_over_the_union_of_labels():
    base = ["db_agent", "db_agent", "api_agent"]
    cur = ["api_agent", "api_agent", "router"]
    base_counts, cur_counts = category_counts(base, cur)
    # union is {api_agent, db_agent, router}; both vectors same length, aligned
    assert len(base_counts) == len(cur_counts) == 3
    assert sum(base_counts) == 3
    assert sum(cur_counts) == 3


def test_categorical_drift_is_detectable():
    base = ["db_agent"] * 90 + ["api_agent"] * 10
    cur = ["db_agent"] * 10 + ["api_agent"] * 90
    base_counts, cur_counts = category_counts(base, cur)
    assert psi(base_counts, cur_counts) > PSI_MEDIUM_THRESHOLD


# --------------------------------------------------------- insufficient data ---

def test_too_few_baseline_values_raises():
    with pytest.raises(InsufficientData):
        quantile_edges([42.0], num_bins=10)


def test_constant_baseline_has_no_spread_to_bin():
    with pytest.raises(InsufficientData):
        quantile_edges([7.0] * 50, num_bins=10)


# --------------------------------------------------------- bands and display ---

def test_band_thresholds_match_the_standard_psi_interpretation():
    assert band_for_psi(0.05) == "low"
    assert band_for_psi(PSI_MEDIUM_THRESHOLD) == "medium"  # 0.10 boundary
    assert band_for_psi(0.15) == "medium"
    assert band_for_psi(PSI_HIGH_THRESHOLD) == "high"  # 0.25 boundary
    assert band_for_psi(0.40) == "high"


def test_display_percent_is_bounded_zero_to_hundred():
    assert psi_display_percent(0.0) == 0
    assert psi_display_percent(0.25) == 25
    assert psi_display_percent(1.0) == 100
    assert psi_display_percent(5.0) == 100  # saturates, never exceeds 100
    assert psi_display_percent(-1.0) == 0  # never negative
