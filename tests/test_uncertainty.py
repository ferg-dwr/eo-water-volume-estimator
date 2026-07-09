"""Tests for the uncertainty terms -- every exact bound checked by hand."""

import numpy as np

from eo_water_volume.uncertainty import (
    budget,
    combine,
    invalid_term,
    partial_fraction_term,
    wse_distance_term,
)

AREA = 900.0


def _flat_scene():
    bed = np.zeros((4, 5))
    return bed, 2.0  # uniform 2 m depth


def test_partial_term_exact_bound():
    bed, wse = _flat_scene()
    partial = np.zeros((4, 5), bool)
    partial[0, 0] = partial[1, 1] = True
    t = partial_fraction_term(bed, wse, AREA, partial)
    assert t[0, 0] == 0.5 * AREA * 2.0  # +/-900 m^3 per partial pixel
    assert t.sum() == 2 * 0.5 * AREA * 2.0
    assert (t[~partial] == 0).all()


def test_partial_term_asymmetric_assumption_widens_bound():
    bed, wse = _flat_scene()
    partial = np.zeros((4, 5), bool)
    partial[0, 0] = True
    t = partial_fraction_term(bed, wse, AREA, partial, assumed_fraction=0.2)
    assert t[0, 0] == 0.8 * AREA * 2.0  # worst case is the far side of [0,1]


def test_invalid_term_bounds_full_column_and_zero_on_high_ground():
    bed, wse = _flat_scene()
    bed = bed.copy()
    invalid = np.zeros((4, 5), bool)
    invalid[2, 2] = invalid[3, 4] = True
    bed[3, 4] = 5.0  # blind pixel on ground above the waterline
    t = invalid_term(bed, wse, AREA, invalid)
    assert t[2, 2] == AREA * 2.0  # could hide a full column
    assert t[3, 4] == 0.0  # dry ground hides nothing
    assert (t[~invalid] == 0).all()


def test_distance_term_linear_in_distance_and_fraction():
    water = np.ones((4, 5))
    water[0, 0] = 0.5
    dist = np.full((4, 5), 10_000.0)  # 10 km from the gauge
    t = wse_distance_term(water, AREA, dist, slope_rate_m_per_m=5e-5)
    assert abs(t[1, 1] - AREA * 5e-5 * 10_000) < 1e-9  # 450 m^3
    assert abs(t[0, 0] - 225.0) < 1e-9  # half fraction, half bound
    # doubling either doubles the bound
    assert abs(wse_distance_term(water, AREA, dist * 2)[1, 1] - 900.0) < 1e-9


def test_combine_is_a_plain_sum():
    a = np.full((2, 2), 1.0)
    b = np.full((2, 2), 2.0)
    c = combine(a, b)
    assert (c == 3.0).all()  # sum, deliberately NOT quadrature (sqrt(5))


def test_budget_totals_and_fraction():
    a = np.full((2, 2), 100.0)
    b = np.full((2, 2), 25.0)
    out = budget({"alpha": a, "beta": b}, volume_m3=5_000.0)
    assert out["unc_alpha_m3"] == 400.0
    assert out["unc_beta_m3"] == 100.0
    assert out["unc_total_m3"] == 500.0
    assert out["unc_total_fraction_of_volume"] == 0.1
    assert budget({"a": a}, volume_m3=0.0)["unc_total_fraction_of_volume"] is None
