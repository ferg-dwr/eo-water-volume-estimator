"""Tests for the pure (rasterio-free) parts of the data source layer."""

import numpy as np

from eo_water_volume.sources import (
    DEFAULT_WATER_FRACTIONS,
    PARTIAL_WATER_FRACTION,
    water_fraction_from_classes,
)


def test_default_class_mapping():
    # not water, open water, partial, invalid(fill)
    wtr = np.array([[0, 1, 2, 253]])
    frac = water_fraction_from_classes(wtr)
    assert frac.tolist() == [[0.0, 1.0, PARTIAL_WATER_FRACTION, 0.0]]


def test_custom_mapping_open_water_only():
    wtr = np.array([[1, 2]])
    frac = water_fraction_from_classes(wtr, {1: 1.0})  # drop the partial class
    assert frac.tolist() == [[1.0, 0.0]]


def test_invalid_classes_are_dry():
    wtr = np.array([[252, 253, 255]])
    assert water_fraction_from_classes(wtr).sum() == 0.0


def test_default_fractions_shape():
    assert DEFAULT_WATER_FRACTIONS[1] == 1.0
    assert 0.0 <= DEFAULT_WATER_FRACTIONS[2] <= 1.0


def test_invalid_mask_flags_unclassifiable_codes():
    from eo_water_volume.sources import invalid_mask_from_classes

    wtr = np.array([[0, 1, 2, 251, 252, 253, 255]])
    assert invalid_mask_from_classes(wtr).tolist() == [
        [False, False, False, False, True, True, True]
    ]
