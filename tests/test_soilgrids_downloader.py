"""Unit tests for the SoilGrids WCS downloader.

Covers:
- :class:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader` ``__init__``
  defaults and custom configuration.
- :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader.get_coverage_ids`
  coverage ID format and Cartesian-product count.
- :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader._download_single_coverage`
  property-alias mapping (``"carbon"`` → ``"soc"``, ``"ph"`` → ``"phh2o"``),
  empty-file guard and exception handling.
- :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader.download_coverages`
  full success, partial failures and empty-input behaviour.
- :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader._get_coverage_data`
  input validation: invalid bounding box, missing EPSG:4326 pixel dimensions,
  unsupported CRS and missing ``.tif`` extension.
- :meth:`~datavia.soil.soilgrids_downloader.SoilGridsDownloader.download`
  interface-contract fallback (no active network call).
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# The module imports `from soilgrids import SoilGrids` at the top level.
# We patch that class every time we instantiate SoilGridsDownloader in
# order to avoid network calls and the need for a live service.
_SOILGRIDS_CLASS_PATH = "datavia.soil.soilgrids_downloader.SoilGrids"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def downloader():
    """Return a SoilGridsDownloader initialised with default configuration.

    The ``SoilGrids`` client is replaced by a :class:`~unittest.mock.MagicMock`
    so no network requests are made during ``__init__``.
    """
    with patch(_SOILGRIDS_CLASS_PATH):
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        return SoilGridsDownloader({})


@pytest.fixture()
def downloader_custom():
    """Return a SoilGridsDownloader with an explicit two-property configuration.

    Uses ``["clay", "ph"]`` with depth ``["0-5cm"]`` and statistic
    ``"Q0.05"`` to verify that non-default values are stored unchanged.
    """
    with patch(_SOILGRIDS_CLASS_PATH):
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        return SoilGridsDownloader(
            {
                "properties": ["clay", "ph"],
                "depths": ["0-5cm"],
                "statistic": "Q0.05",
            }
        )


# ---------------------------------------------------------------------------
# __init__ defaults and custom configuration
# ---------------------------------------------------------------------------


class TestSoilGridsDownloaderInit:
    """Tests for SoilGridsDownloader __init__ configuration storage."""

    def test_default_properties(self, downloader) -> None:
        """Five priority soil properties are configured by default."""
        assert set(downloader.priority_properties) == {
            "clay",
            "sand",
            "silt",
            "ph",
            "carbon",
        }

    def test_default_depths(self, downloader) -> None:
        """Default depth list contains the two standard shallow layers."""
        assert downloader.priority_depths == ["0-5cm", "5-15cm"]

    def test_default_statistic(self, downloader) -> None:
        """Default statistic is 'mean'."""
        assert downloader.priority_statistic == "mean"

    def test_fixed_resolution(self, downloader) -> None:
        """Spatial resolution is always 250 metres."""
        assert downloader.resolution == 250

    def test_germany_bbox_has_four_keys(self, downloader) -> None:
        """Germany bounding box dictionary exposes west, south, east and north."""
        assert {"west", "south", "east", "north"} <= set(downloader.germany_bbox.keys())

    def test_germany_bbox_values_are_finite_floats(self, downloader) -> None:
        """All bounding-box values are finite floating-point numbers."""
        for key, val in downloader.germany_bbox.items():
            assert isinstance(val, float), (
                f"Expected float for key '{key}', got {type(val)}"
            )

    def test_custom_properties_stored(self, downloader_custom) -> None:
        """Custom property list is stored unchanged."""
        assert downloader_custom.priority_properties == ["clay", "ph"]

    def test_custom_depth_stored(self, downloader_custom) -> None:
        """Custom depth list is stored unchanged."""
        assert downloader_custom.priority_depths == ["0-5cm"]

    def test_custom_statistic_stored(self, downloader_custom) -> None:
        """Custom statistic token is stored unchanged."""
        assert downloader_custom.priority_statistic == "Q0.05"


# ---------------------------------------------------------------------------
# get_coverage_ids
# ---------------------------------------------------------------------------


class TestSoilGridsDownloaderGetCoverageIds:
    """Tests for SoilGridsDownloader.get_coverage_ids output format and count."""

    def test_single_property_single_depth_format(self, downloader) -> None:
        """Single combination yields exactly one ID in the expected format."""
        ids = downloader.get_coverage_ids(
            properties=["clay"], depths=["0-5cm"], statistic="mean"
        )
        assert ids == ["clay_0-5cm_mean"]

    def test_count_matches_cartesian_product(self, downloader) -> None:
        """Number of IDs equals len(properties) x len(depths)."""
        ids = downloader.get_coverage_ids(
            properties=["clay", "sand", "silt"],
            depths=["0-5cm", "5-15cm"],
            statistic="mean",
        )
        assert len(ids) == 3 * 2

    def test_no_arguments_applies_instance_defaults(self, downloader) -> None:
        """Calling with no arguments reproduces the instance-level defaults."""
        expected = downloader.get_coverage_ids(
            properties=downloader.priority_properties,
            depths=downloader.priority_depths,
            statistic=downloader.priority_statistic,
        )
        assert downloader.get_coverage_ids() == expected

    def test_quantile_statistic_embedded(self, downloader) -> None:
        """Non-mean statistics are embedded verbatim in the coverage ID."""
        ids = downloader.get_coverage_ids(
            properties=["clay"], depths=["0-5cm"], statistic="Q0.95"
        )
        assert "clay_0-5cm_Q0.95" in ids

    def test_all_ids_follow_three_token_pattern(self, downloader) -> None:
        """Every generated ID contains at least two underscores."""
        for cid in downloader.get_coverage_ids():
            parts = cid.split("_")
            assert len(parts) >= 3, f"Unexpected coverage ID format: '{cid}'"


# ---------------------------------------------------------------------------
# _download_single_coverage helpers and alias mapping
# ---------------------------------------------------------------------------


class TestSoilGridsDownloaderSingleCoverage:
    """Tests for the _download_single_coverage internal helper.

    ``_get_coverage_data`` is replaced by a side-effect that writes a small
    binary placeholder so that the file-existence guard is satisfied.
    """

    def _build_downloader_with_fake_wcs(self, tif_content: bytes = b"\x00" * 256):
        """Return a downloader whose WCS call writes *tif_content* to disk."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        def _write_fake_tiff(service_id, coverage_id, output, **kwargs):
            with open(output, "wb") as fh:
                fh.write(tif_content)

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        dl._get_coverage_data = MagicMock(side_effect=_write_fake_tiff)
        return dl

    def test_successful_download_returns_tif_path(self) -> None:
        """A successful download returns a path ending with the coverage ID."""
        dl = self._build_downloader_with_fake_wcs()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = dl._download_single_coverage("clay_0-5cm_mean", temp_dir)
        assert result != "failed"
        assert result.endswith("clay_0-5cm_mean.tif")

    def test_carbon_is_aliased_to_soc_in_api_call(self) -> None:
        """Coverage ID 'carbon_*' passes 'soc' as the service_id to the API."""
        dl = self._build_downloader_with_fake_wcs()
        with tempfile.TemporaryDirectory() as temp_dir:
            dl._download_single_coverage("carbon_0-5cm_mean", temp_dir)
        call_args = dl._get_coverage_data.call_args
        # service_id may be passed as positional or keyword argument.
        service_id = (
            call_args.kwargs.get("service_id")
            if call_args.kwargs.get("service_id")
            else call_args.args[0]
        )
        assert service_id == "soc"

    def test_ph_is_aliased_to_phh2o_in_api_call(self) -> None:
        """Coverage ID 'ph_*' passes 'phh2o' as the service_id to the API."""
        dl = self._build_downloader_with_fake_wcs()
        with tempfile.TemporaryDirectory() as temp_dir:
            dl._download_single_coverage("ph_0-5cm_mean", temp_dir)
        call_args = dl._get_coverage_data.call_args
        service_id = (
            call_args.kwargs.get("service_id")
            if call_args.kwargs.get("service_id")
            else call_args.args[0]
        )
        assert service_id == "phh2o"

    def test_properties_without_alias_passed_unchanged(self) -> None:
        """Standard properties like 'clay' are passed to the API without translation."""
        dl = self._build_downloader_with_fake_wcs()
        with tempfile.TemporaryDirectory() as temp_dir:
            dl._download_single_coverage("clay_0-5cm_mean", temp_dir)
        call_args = dl._get_coverage_data.call_args
        service_id = (
            call_args.kwargs.get("service_id")
            if call_args.kwargs.get("service_id")
            else call_args.args[0]
        )
        assert service_id == "clay"

    def test_empty_output_file_returns_failed(self) -> None:
        """An empty output file is treated as a failed download."""
        dl = self._build_downloader_with_fake_wcs(tif_content=b"")
        with tempfile.TemporaryDirectory() as temp_dir:
            result = dl._download_single_coverage("clay_0-5cm_mean", temp_dir)
        assert result == "failed"

    def test_wcs_exception_returns_failed(self) -> None:
        """Any exception raised by the WCS call returns 'failed'."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        dl._get_coverage_data = MagicMock(side_effect=ConnectionError("timeout"))
        with tempfile.TemporaryDirectory() as temp_dir:
            result = dl._download_single_coverage("clay_0-5cm_mean", temp_dir)
        assert result == "failed"


# ---------------------------------------------------------------------------
# download_coverages batch orchestration
# ---------------------------------------------------------------------------


class TestSoilGridsDownloaderDownloadCoverages:
    """Tests for download_coverages batch download and failure handling."""

    def _build_downloader_with_selective_success(self, failing_ids: set[str]):
        """Return a downloader that fails for coverage IDs in *failing_ids*."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        def selective_download(coverage_id: str, temp_dir: str) -> str:
            if coverage_id in failing_ids:
                return "failed"
            path = os.path.join(temp_dir, f"{coverage_id}.tif")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 64)
            return path

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        dl._download_single_coverage = MagicMock(side_effect=selective_download)
        return dl

    def test_all_successful_downloads_in_result(self) -> None:
        """All succeeded coverages appear as tuples in the result list."""
        dl = self._build_downloader_with_selective_success(failing_ids=set())
        ids = ["clay_0-5cm_mean", "sand_5-15cm_mean"]
        with tempfile.TemporaryDirectory() as temp_dir:
            results = dl.download_coverages(ids, temp_dir)
        assert len(results) == 2
        returned_ids = {cid for _, cid in results}
        assert returned_ids == set(ids)

    def test_failed_coverages_excluded_from_result(self) -> None:
        """Coverage IDs that fail are omitted from the result list."""
        dl = self._build_downloader_with_selective_success(
            failing_ids={"sand_5-15cm_mean"}
        )
        ids = ["clay_0-5cm_mean", "sand_5-15cm_mean"]
        with tempfile.TemporaryDirectory() as temp_dir:
            results = dl.download_coverages(ids, temp_dir)
        assert len(results) == 1
        assert results[0][1] == "clay_0-5cm_mean"

    def test_empty_input_returns_empty_list(self) -> None:
        """An empty coverage ID list returns an empty list without errors."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        with tempfile.TemporaryDirectory() as temp_dir:
            results = dl.download_coverages([], temp_dir)
        assert results == []

    def test_result_tuples_have_path_and_coverage_id(self) -> None:
        """Each result tuple is (path, coverage_id) in that order."""
        dl = self._build_downloader_with_selective_success(failing_ids=set())
        with tempfile.TemporaryDirectory() as temp_dir:
            results = dl.download_coverages(["clay_0-5cm_mean"], temp_dir)
        path, cid = results[0]
        assert os.path.isabs(path)
        assert cid == "clay_0-5cm_mean"


# ---------------------------------------------------------------------------
# _get_coverage_data input validation
# ---------------------------------------------------------------------------


class TestSoilGridsGetCoverageDataValidation:
    """Tests for _get_coverage_data input validation.

    All assertions are about ValueError being raised before any actual
    WCS network request is attempted. The WCS service layer is fully
    mocked so no network calls are made.
    """

    def _build_downloader_with_mock_wcs(
        self, supported_crs_urn: str = "urn:ogc:def:crs:EPSG::4326"
    ):
        """Return a downloader with a WCS layer that advertises *supported_crs_urn*."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        mock_crs_obj = MagicMock()
        mock_crs_obj.getcodeurn.return_value = supported_crs_urn

        mock_coverage_obj = MagicMock()
        mock_coverage_obj.supportedCRS = [mock_crs_obj]

        mock_sg = MagicMock()
        mock_sg._get_service_and_coverage_list.return_value = (MagicMock(), [])
        mock_sg._get_coverage_obj.return_value = mock_coverage_obj

        with patch(_SOILGRIDS_CLASS_PATH, return_value=mock_sg):
            dl = SoilGridsDownloader({})
        dl.sg = mock_sg
        return dl

    def test_invalid_bbox_west_greater_than_east_raises(self) -> None:
        """west > east triggers a ValueError before any WCS request."""
        dl = self._build_downloader_with_mock_wcs()
        with pytest.raises(ValueError, match="Invalid bounding box"):
            dl._get_coverage_data(
                service_id="clay",
                coverage_id="clay_0-5cm_mean",
                crs="urn:ogc:def:crs:EPSG::4326",
                west=15.0,
                south=47.0,
                east=5.0,
                north=55.0,
                output="/tmp/test.tif",
                width=250,
                height=250,
            )

    def test_invalid_bbox_south_greater_than_north_raises(self) -> None:
        """south > north triggers a ValueError before any WCS request."""
        dl = self._build_downloader_with_mock_wcs()
        with pytest.raises(ValueError, match="Invalid bounding box"):
            dl._get_coverage_data(
                service_id="clay",
                coverage_id="clay_0-5cm_mean",
                crs="urn:ogc:def:crs:EPSG::4326",
                west=5.0,
                south=55.0,
                east=15.0,
                north=47.0,
                output="/tmp/test.tif",
                width=250,
                height=250,
            )

    def test_output_without_tif_extension_raises(self) -> None:
        """An output path not ending in '.tif' raises a ValueError."""
        dl = self._build_downloader_with_mock_wcs()
        with pytest.raises(ValueError, match=r"\.tif"):
            dl._get_coverage_data(
                service_id="clay",
                coverage_id="clay_0-5cm_mean",
                crs="urn:ogc:def:crs:EPSG::4326",
                west=5.0,
                south=47.0,
                east=15.0,
                north=55.0,
                output="/tmp/test.geotiff",
                width=250,
                height=250,
            )

    def test_missing_width_height_for_epsg4326_raises(self) -> None:
        """EPSG:4326 requests without pixel dimensions raise a ValueError."""
        dl = self._build_downloader_with_mock_wcs()
        with pytest.raises(ValueError, match="width and height"):
            dl._get_coverage_data(
                service_id="clay",
                coverage_id="clay_0-5cm_mean",
                crs="urn:ogc:def:crs:EPSG::4326",
                west=5.0,
                south=47.0,
                east=15.0,
                north=55.0,
                output="/tmp/test.tif",
            )

    def test_unsupported_crs_raises(self) -> None:
        """Requesting a CRS absent from ``supportedCRS`` raises a ValueError."""
        dl = self._build_downloader_with_mock_wcs(
            supported_crs_urn="urn:ogc:def:crs:EPSG::4326"
        )
        with pytest.raises(ValueError, match="not supported"):
            dl._get_coverage_data(
                service_id="clay",
                coverage_id="clay_0-5cm_mean",
                crs="urn:ogc:def:crs:EPSG::25832",
                west=5.0,
                south=47.0,
                east=15.0,
                north=55.0,
                output="/tmp/test.tif",
                width=250,
                height=250,
            )


# ---------------------------------------------------------------------------
# download interface-contract fallback
# ---------------------------------------------------------------------------


class TestSoilGridsDownloaderDownloadFallback:
    """Tests for the download() method (interface-contract fallback)."""

    def test_returns_string_result(self) -> None:
        """download() returns a string (path or 'failed')."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})

        # Patch download_coverages so no real download occurs.
        dl.download_coverages = MagicMock(return_value=[])
        result = dl.download()
        assert isinstance(result, str)

    def test_returns_failed_when_no_coverages_downloaded(self) -> None:
        """download() returns 'failed' if download_coverages returns an empty list."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        dl.download_coverages = MagicMock(return_value=[])
        assert dl.download() == "failed"

    def test_returns_first_path_when_download_succeeds(self) -> None:
        """download() returns the path of the first successful coverage."""
        from datavia.soil.soilgrids_downloader import (  # noqa: PLC0415
            SoilGridsDownloader,
        )

        with patch(_SOILGRIDS_CLASS_PATH):
            dl = SoilGridsDownloader({})
        dl.download_coverages = MagicMock(
            return_value=[("/some/path/clay_0-5cm_mean.tif", "clay_0-5cm_mean")]
        )
        assert dl.download() == "/some/path/clay_0-5cm_mean.tif"
