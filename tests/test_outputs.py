"""Round-trip tests for the GeoTIFF writer (skipped without the [io] extra)."""

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")

from eo_water_volume.outputs import write_geotiff
from eo_water_volume.sources import Raster


def _like(shape=(4, 5)):
    from affine import Affine
    from rasterio.crs import CRS

    transform = Affine(30.0, 0.0, 600000.0, 0.0, -30.0, 4250000.0)  # UTM-ish
    return Raster(np.zeros(shape), transform, CRS.from_epsg(32610))


def test_roundtrip_preserves_data_and_georeferencing(tmp_path):
    like = _like()
    vmap = np.arange(20, dtype="float64").reshape(4, 5) * 1.5
    out = write_geotiff(
        vmap,
        like,
        str(tmp_path / "vol.tif"),
        nodata=-9999.0,
        band_name="water_volume_m3",
        units="m^3",
        tags={"wse_m": 1.85, "invalid_fraction": 0.031},
    )
    with rasterio.open(out) as src:
        assert src.crs == like.crs
        assert src.transform == like.transform
        assert src.nodata == -9999.0
        np.testing.assert_allclose(src.read(1), vmap.astype("float32"))
        assert src.descriptions == ("water_volume_m3",)
        assert src.units == ("m^3",)
        assert src.tags()["wse_m"] == "1.85"
        assert src.tags()["invalid_fraction"] == "0.031"


def test_shape_mismatch_rejected(tmp_path):
    like = _like(shape=(4, 5))
    with pytest.raises(ValueError, match="does not match"):
        write_geotiff(np.zeros((3, 3)), like, str(tmp_path / "bad.tif"))