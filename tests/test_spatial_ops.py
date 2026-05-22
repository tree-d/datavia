"""Tests for spatial operations library functions."""

from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from datavia.library.spatial_ops import reproject_tiff


class TestReprojectTiff:
    """Tests for the reproject_tiff standalone function."""

    def _create_test_tiff(self, path: str, crs: str = "EPSG:4326") -> None:
        """Write a tiny valid GeoTIFF at *path* in *crs*."""

        data = np.ones((10, 10), dtype=np.int16) * 100
        if crs == "EPSG:4326":
            transform = from_bounds(5.8, 47.2, 15.0, 55.0, 10, 10)
        else:
            transform = from_bounds(200_000, 5_200_000, 900_000, 6_100_000, 10, 10)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=10,
            width=10,
            count=1,
            dtype=data.dtype,
            crs=crs,
            transform=transform,
        ) as dst:
            dst.write(data, 1)

    def test_skips_when_file_already_in_target_crs(self, tmp_path) -> None:
        """reproject_tiff does nothing when the file is already in target_crs."""

        tif = str(tmp_path / "test.tif")
        self._create_test_tiff(tif, crs="EPSG:4326")
        mtime_before = (tmp_path / "test.tif").stat().st_mtime_ns

        reproject_tiff(tif, "EPSG:4326")

        # File should not have been replaced (mtime unchanged)
        with rasterio.open(tif) as src:
            assert src.crs.to_epsg() == 4326
        assert (tmp_path / "test.tif").stat().st_mtime_ns == mtime_before

    def test_reprojects_tiff_in_place(self, tmp_path) -> None:
        """reproject_tiff replaces the file with the reprojected version."""

        tif = str(tmp_path / "test.tif")
        self._create_test_tiff(tif, crs="EPSG:4326")

        reproject_tiff(tif, "EPSG:25832", resolution_m=250)

        with rasterio.open(tif) as src:
            assert src.crs.to_epsg() == 25832
            assert src.res[0] == pytest.approx(250.0, rel=0.01)

    def test_preserves_pixel_values_after_reprojection(self, tmp_path) -> None:
        """The majority of pixel values are preserved across a round-trip reproject."""

        tif = str(tmp_path / "values.tif")
        self._create_test_tiff(tif, crs="EPSG:4326")

        reproject_tiff(tif, "EPSG:25832", resolution_m=1000)

        with rasterio.open(tif) as src:
            data = src.read(1)
        # Most interior pixels should still hold the original value (100);
        # edge pixels may differ due to bilinear resampling and nodata fill.
        assert np.median(data[data > 0]) == pytest.approx(100.0, abs=5)

    def test_original_preserved_on_error(self, tmp_path) -> None:
        """The original file is not corrupted when an error occurs during warp."""

        tif = str(tmp_path / "test.tif")
        self._create_test_tiff(tif, crs="EPSG:4326")
        original_size = (tmp_path / "test.tif").stat().st_size

        with (
            patch(
                "datavia.library.spatial_ops.warp_reproject",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError),
        ):
            reproject_tiff(tif, "EPSG:25832")

        # Original file still intact (temp file was cleaned up)
        assert (tmp_path / "test.tif").stat().st_size == original_size

    def test_raises_when_rasterio_not_available(self, tmp_path) -> None:
        """RuntimeError is raised immediately when rasterio is not available."""

        tif = str(tmp_path / "test.tif")
        self._create_test_tiff(tif)

        with (
            patch("datavia.library.spatial_ops.RASTERIO_AVAILABLE", False),
            pytest.raises(RuntimeError, match="rasterio"),
        ):
            reproject_tiff(tif, "EPSG:25832")
