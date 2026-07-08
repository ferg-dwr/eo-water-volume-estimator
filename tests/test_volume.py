"""Validate the volume core against a shape with a known analytical volume."""

import numpy as np

from eo_water_volume import estimate_volume, summarize, wse_from_perimeter


def _paraboloid_bowl(px=30.0, r_target=3000.0, z0=1.0, h=5.0, n=401):
    """Bed z = z0 + k r^2 filled to level h. Analytical water volume:
    V = pi (h - z0)^2 / (2k). Sized so the pool spans many pixels."""
    k = (h - z0) / r_target**2
    xs = (np.arange(n) - n // 2) * px
    X, Y = np.meshgrid(xs, xs)
    bed = z0 + k * (X**2 + Y**2)
    water = (bed < h).astype("float64")
    v_true = np.pi * (h - z0) ** 2 / (2 * k)
    return bed, water, h, v_true, px


def test_volume_matches_analytical():
    bed, water, h, v_true, px = _paraboloid_bowl()
    v_est = estimate_volume(bed, water, wse=h, pixel_area=px * px)
    assert abs(v_est - v_true) / v_true < 0.01


def test_perimeter_wse_recovers_fill_level():
    bed, water, h, v_true, px = _paraboloid_bowl()
    wse_hat = wse_from_perimeter(bed, water)
    assert abs(wse_hat - h) < 0.1
    v_blind = estimate_volume(bed, water, wse=wse_hat, pixel_area=px * px)
    assert abs(v_blind - v_true) / v_true < 0.05


def test_dry_and_nodata_contribute_nothing():
    bed = np.array([[0.0, 10.0], [np.nan, 2.0]])
    water = np.array([[1.0, 1.0], [1.0, 0.0]])  # dry, high, nodata, unmasked
    assert estimate_volume(bed, water, wse=5.0, pixel_area=1.0) == 5.0


def test_summary_units():
    bed, water, h, v_true, px = _paraboloid_bowl()
    s = summarize(bed, water, h, px * px)
    assert s["volume_m3"] > 0
    assert abs(s["volume_acre_ft"] - s["volume_m3"] / 1233.4818375475) < 1e-6
    assert 0 < s["mean_depth_m"] <= s["max_depth_m"]


def test_fractional_water_scales_volume():
    # Flat bed at 0, wse=2 -> uniform depth 2. Fractional coverage should scale
    # volume linearly: V = area * depth * sum(fractions).
    bed = np.zeros((1, 4))
    water = np.array([[0.0, 0.25, 0.5, 1.0]])
    area = 100.0
    v = estimate_volume(bed, water, wse=2.0, pixel_area=area)
    assert abs(v - area * 2.0 * (0.0 + 0.25 + 0.5 + 1.0)) < 1e-9


def test_fraction_out_of_range_is_clipped():
    bed = np.zeros((1, 2))
    water = np.array([[1.5, -0.3]])  # clipped to 1.0 and 0.0
    v = estimate_volume(bed, water, wse=1.0, pixel_area=1.0)
    assert v == 1.0  # only pixel 0 contributes: depth 1 * frac 1 * area 1


def test_summarize_reports_invalid_fraction():
    bed = np.zeros((2, 2))
    water = np.array([[1.0, 0.0], [0.0, 0.0]])
    invalid = np.array([[False, True], [True, False]])  # 2 of 4 pixels
    s = summarize(bed, water, wse=1.0, pixel_area=1.0, invalid=invalid)
    assert s["invalid_fraction"] == 0.5


def test_summarize_without_invalid_is_none_not_zero():
    bed = np.zeros((1, 1))
    water = np.ones((1, 1))
    s = summarize(bed, water, wse=1.0, pixel_area=1.0)
    assert s["invalid_fraction"] is None  # "not assessed" != "assessed clean"


def test_volume_map_sums_to_estimate():
    from eo_water_volume import volume_map

    bed, water, h, v_true, px = _paraboloid_bowl()
    vmap = volume_map(bed, water, wse=h, pixel_area=px * px)
    assert vmap.shape == bed.shape
    assert (
        abs(vmap.sum() - estimate_volume(bed, water, wse=h, pixel_area=px * px)) < 1e-6
    )


def test_volume_map_per_pixel_values():
    from eo_water_volume import volume_map

    bed = np.array([[0.0, 10.0], [np.nan, 1.0]])
    water = np.array([[1.0, 1.0], [1.0, 0.5]])
    vmap = volume_map(bed, water, wse=2.0, pixel_area=10.0)
    # wet+shallow: 10*1*2=20 | dry (bed above wse): 0 | nodata: 0 | half-wet: 10*0.5*1=5
    assert vmap.tolist() == [[20.0, 0.0], [0.0, 5.0]]


def test_wse_grid_tilted_plane_analytical():
    # Tilted surface over a flat bed: V = pixel_area * sum(depth) exactly, and
    # summarize() must accept the grid (reporting mean/min/max WSE).
    bed = np.zeros((50, 100))
    water = np.ones_like(bed)
    wse = np.linspace(2.0, 4.0, 100)[None, :].repeat(50, axis=0)
    v = estimate_volume(bed, water, wse, pixel_area=900.0)
    assert abs(v - 900.0 * bed.size * 3.0) < 1e-6
    s = summarize(bed, water, wse, 900.0)
    assert abs(s["wse_m"] - 3.0) < 1e-12
    assert s["volume_m3"] == v
