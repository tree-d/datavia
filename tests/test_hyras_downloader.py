"""Unit tests for HYRASDownloader (no network access).

Covers (all without network access or real files):

- :meth:`~datavia.weather.hyras_downloader.HYRASDownloader._years_in_range` —
  date range expansion to whole calendar years.
- URL construction for each variable/year combination.
- :meth:`~datavia.weather.hyras_downloader.HYRASDownloader._discover_latest_filename`
  — version auto-discovery from a mocked HTML directory listing.
- :meth:`~datavia.weather.hyras_downloader.HYRASDownloader.download` —
  full download loop with mocked ``_discover_latest_filename`` and parent
  ``URLDownloader.download``.
- Validation: unknown variable raises :exc:`ValueError`,
  reversed date range raises :exc:`ValueError`, no HTML match raises
  :exc:`RuntimeError`.
- The derived ``vapour_pressure_deficit``: inputs downloaded and combined
  into a ``vpd`` file (values, grid mapping, stamps), helper files deleted,
  requested inputs kept, failed inputs and mismatched grids handled (small
  synthetic NetCDF files in ``tmp_path``).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from datavia.weather.hyras_downloader import (
    _HYRAS_BASE_URL,
    _VARIABLE_MAP,
    HYRASDownloader,
)

# ---------------------------------------------------------------------------
# _years_in_range
# ---------------------------------------------------------------------------


class TestYearsInRange:
    """Unit tests for the static _years_in_range helper."""

    def test_single_year(self) -> None:
        """Date range within one year returns a single-element list."""
        result = HYRASDownloader._years_in_range("2024-03-01", "2024-11-30")
        assert result == [2024]

    def test_two_years(self) -> None:
        """Date range spanning a year boundary returns both years."""
        result = HYRASDownloader._years_in_range("2024-06-01", "2025-02-28")
        assert result == [2024, 2025]

    def test_multi_year(self) -> None:
        """Date range spanning three full years returns all three."""
        result = HYRASDownloader._years_in_range("2022-01-01", "2024-12-31")
        assert result == [2022, 2023, 2024]

    def test_same_day(self) -> None:
        """A single-day range returns the year of that day."""
        result = HYRASDownloader._years_in_range("2023-07-15", "2023-07-15")
        assert result == [2023]

    def test_end_before_start_raises(self) -> None:
        """Reversed date range raises ValueError."""
        with pytest.raises(ValueError, match="date_end"):
            HYRASDownloader._years_in_range("2024-12-31", "2024-01-01")

    def test_returns_list_of_integers(self) -> None:
        """Every element in the result is a plain Python int.

        Returns:
            None
        """
        result = HYRASDownloader._years_in_range("2021-01-01", "2023-12-31")
        assert all(isinstance(year, int) for year in result)

    def test_years_are_consecutive_no_gaps(self) -> None:
        """Consecutive elements differ by exactly 1 (no gaps, no duplicates).

        Returns:
            None
        """
        result = HYRASDownloader._years_in_range("2019-01-01", "2024-12-31")
        for i in range(len(result) - 1):
            assert result[i + 1] - result[i] == 1, (
                f"Gap between years at index {i}: {result[i]} → {result[i + 1]}"
            )

    def test_years_are_sorted_ascending(self) -> None:
        """Result is in ascending order.

        Returns:
            None
        """
        result = HYRASDownloader._years_in_range("2020-06-01", "2024-03-31")
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestUrlConstruction:
    """Verify that the correct subdirectory URL is assembled for each variable."""

    @pytest.mark.parametrize(
        "variable,expected_subdir",
        [
            ("2m_temperature", "air_temperature_mean"),
            ("temperature_2m_max", "air_temperature_max"),
            ("temperature_2m_min", "air_temperature_min"),
            ("total_precipitation", "precipitation"),
            ("surface_solar_radiation_downwards", "radiation_global"),
            ("relative_humidity_2m", "humidity"),
        ],
    )
    def test_subdir_in_variable_map(self, variable: str, expected_subdir: str) -> None:
        """Each supported variable maps to the correct DWD subdirectory."""
        assert _VARIABLE_MAP[variable]["subdir"] == expected_subdir

    def test_subdir_url_contains_base_url(self) -> None:
        """The assembled subdir URL starts with the DWD base URL."""
        mapping = _VARIABLE_MAP["2m_temperature"]
        subdir_url = f"{_HYRAS_BASE_URL}{mapping['subdir']}/"
        assert subdir_url.startswith(_HYRAS_BASE_URL)
        assert "air_temperature_mean" in subdir_url


# ---------------------------------------------------------------------------
# _discover_latest_filename
# ---------------------------------------------------------------------------


class TestDiscoverLatestFilename:
    """Version auto-discovery from a mocked HTML directory listing."""

    def _make_downloader(self) -> HYRASDownloader:
        """Return a HYRASDownloader without triggering network activity."""
        downloader = HYRASDownloader.__new__(HYRASDownloader)
        downloader.variables = ["2m_temperature"]
        downloader.date_start = "2024-01-01"
        downloader.date_end = "2024-12-31"
        # Replace session with a mock.
        downloader.session = MagicMock()
        downloader.max_retries = 5
        return downloader

    def test_single_version_returned(self) -> None:
        """Returns the only available filename when exactly one version exists."""
        html = (
            "<html><body>"
            '<a href="tas_hyras_1_2024_v6-1_de.nc">tas_hyras_1_2024_v6-1_de.nc</a>'
            "</body></html>"
        )
        downloader = self._make_downloader()
        downloader.session.get.return_value = MagicMock(
            status_code=200, text=html, raise_for_status=MagicMock()
        )

        filename = downloader._discover_latest_filename(
            "https://example.com/air_temperature_mean/", 2024, "tas_hyras_1"
        )
        assert filename == "tas_hyras_1_2024_v6-1_de.nc"

    def test_latest_version_selected(self) -> None:
        """Selects the highest version when multiple versions are listed."""
        html = (
            "<html><body>"
            '<a href="tas_hyras_1_2024_v5-0_de.nc">v5-0</a>'
            '<a href="tas_hyras_1_2024_v6-1_de.nc">v6-1</a>'
            '<a href="tas_hyras_1_2024_v6-2_de.nc">v6-2</a>'
            "</body></html>"
        )
        downloader = self._make_downloader()
        downloader.session.get.return_value = MagicMock(
            status_code=200, text=html, raise_for_status=MagicMock()
        )

        filename = downloader._discover_latest_filename(
            "https://example.com/air_temperature_mean/", 2024, "tas_hyras_1"
        )
        assert filename == "tas_hyras_1_2024_v6-2_de.nc"

    def test_minor_version_tiebreak(self) -> None:
        """Minor version number breaks the tie correctly (6-2 > 6-1)."""
        html = (
            "<html><body>"
            '<a href="tas_hyras_1_2023_v6-1_de.nc">v6-1</a>'
            '<a href="tas_hyras_1_2023_v6-2_de.nc">v6-2</a>'
            "</body></html>"
        )
        downloader = self._make_downloader()
        downloader.session.get.return_value = MagicMock(
            status_code=200, text=html, raise_for_status=MagicMock()
        )

        filename = downloader._discover_latest_filename(
            "https://example.com/air_temperature_mean/", 2023, "tas_hyras_1"
        )
        assert filename == "tas_hyras_1_2023_v6-2_de.nc"

    def test_no_match_raises(self) -> None:
        """RuntimeError raised when no matching filename is found in the listing."""
        html = "<html><body><a href='other_file.nc'>other</a></body></html>"
        downloader = self._make_downloader()
        downloader.session.get.return_value = MagicMock(
            status_code=200, text=html, raise_for_status=MagicMock()
        )

        with pytest.raises(RuntimeError, match="No HYRAS file found"):
            downloader._discover_latest_filename(
                "https://example.com/air_temperature_mean/", 2024, "tas_hyras_1"
            )

    def test_de_suffix_required(self) -> None:
        """Filenames without the _de suffix are not matched."""
        # File without _de — should NOT be matched.
        html = (
            "<html><body>"
            '<a href="tas_hyras_1_2024_v6-1.nc">no _de suffix</a>'
            "</body></html>"
        )
        downloader = self._make_downloader()
        downloader.session.get.return_value = MagicMock(
            status_code=200, text=html, raise_for_status=MagicMock()
        )

        with pytest.raises(RuntimeError):
            downloader._discover_latest_filename(
                "https://example.com/air_temperature_mean/", 2024, "tas_hyras_1"
            )


# ---------------------------------------------------------------------------
# download()
# ---------------------------------------------------------------------------


class TestDownload:
    """Full download loop with mocked network calls."""

    def test_single_variable_single_year(self) -> None:
        """A single (variable, year) pair results in one super().download() call."""
        with (
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                return_value="/tmp/tas_hyras_1_2024.nc",
            ) as mock_parent_dl,
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                return_value="tas_hyras_1_2024_v6-1_de.nc",
            ),
        ):
            downloader = HYRASDownloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
            )
            result = downloader.download()

        assert mock_parent_dl.call_count == 1
        assert "/tmp/tas_hyras_1_2024.nc" in result

    def test_multi_year_calls_parent_once_per_year(self) -> None:
        """A two-year range causes two super().download() calls per variable."""
        with (
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                side_effect=["/tmp/tas_2024.nc", "/tmp/tas_2025.nc"],
            ) as mock_parent_dl,
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                return_value="tas_hyras_1_2024_v6-1_de.nc",
            ),
        ):
            downloader = HYRASDownloader(
                variables=["2m_temperature"],
                date_start="2024-06-01",
                date_end="2025-02-28",
            )
            result = downloader.download()

        assert mock_parent_dl.call_count == 2
        assert "/tmp/tas_2024.nc" in result
        assert "/tmp/tas_2025.nc" in result

    def test_multi_variable_single_year(self) -> None:
        """Each variable triggers one download call when the range is one year."""
        paths = [
            "/tmp/tas_2024.nc",
            "/tmp/pr_2024.nc",
        ]
        with (
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                side_effect=paths,
            ) as mock_parent_dl,
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                return_value="dummy_v6-1_de.nc",
            ),
        ):
            downloader = HYRASDownloader(
                variables=["2m_temperature", "total_precipitation"],
                date_start="2024-01-01",
                date_end="2024-12-31",
            )
            result = downloader.download()

        assert mock_parent_dl.call_count == 2
        for path in paths:
            assert path in result

    def test_failed_download_excluded_from_result(self) -> None:
        """Paths returned as 'failed' are not included in the output string."""
        with (
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                return_value="failed",
            ),
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                return_value="tas_hyras_1_2024_v6-1_de.nc",
            ),
        ):
            downloader = HYRASDownloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
            )
            result = downloader.download()

        assert result == ""

    def test_unknown_variable_raises(self) -> None:
        """ValueError is raised before any network call for unsupported variables."""
        downloader = HYRASDownloader(
            variables=["et0_fao_evapotranspiration"],
            date_start="2024-01-01",
            date_end="2024-12-31",
        )
        with pytest.raises(ValueError, match="not available from HYRAS"):
            downloader.download()

    def test_url_is_set_before_parent_download(self) -> None:
        """self.url contains the full file URL when the parent download is called."""
        captured_urls: list[str] = []

        def capture_url(self_inner: HYRASDownloader) -> str:  # type: ignore[override]
            captured_urls.append(self_inner.url)
            return "/tmp/captured.nc"

        with (
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                return_value="tas_hyras_1_2024_v6-1_de.nc",
            ),
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                autospec=True,
                side_effect=capture_url,
            ),
        ):
            downloader = HYRASDownloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
            )
            downloader.download()

        assert len(captured_urls) == 1
        assert captured_urls[0].endswith("tas_hyras_1_2024_v6-1_de.nc")
        assert captured_urls[0].startswith(_HYRAS_BASE_URL)


# ---------------------------------------------------------------------------
# _get_final_filename — extension normalisation
# ---------------------------------------------------------------------------


class TestGetFinalFilename:
    """Tests for :meth:`HYRASDownloader._get_final_filename`.

    The downloader's sole responsibility is to replace the ``.download``
    temporary extension with ``.nc`` so that the parent
    :class:`~datavia.core.downloader_url.URLDownloader` has a valid local
    path.  The permanent, descriptive filename is assigned later by
    :class:`~datavia.weather.saver_weather.SaverWeather` when it copies the
    file into the data directory.
    """

    def test_replaces_download_extension_with_nc(self) -> None:
        """The ``.download`` extension is replaced by ``.nc``.

        Parameters
        ----------
        None
        """
        from datavia.weather.hyras_downloader import HYRASDownloader

        downloader = HYRASDownloader()
        result = downloader._get_final_filename(
            "/tmp/tmpABCDEF.download", "application/octet-stream"
        )

        assert result == "/tmp/tmpABCDEF.nc"

    def test_preserves_directory_and_stem(self) -> None:
        """Directory and temp stem are kept unchanged; only the extension changes.

        Parameters
        ----------
        None
        """
        from datavia.weather.hyras_downloader import HYRASDownloader

        downloader = HYRASDownloader()
        result = downloader._get_final_filename(
            "/var/tmp/some_temp_file.download", "application/x-netcdf"
        )

        assert result == "/var/tmp/some_temp_file.nc"


# ---------------------------------------------------------------------------
# vapour_pressure_deficit (derived from tasmax, tasmin and hurs)
# ---------------------------------------------------------------------------

#: Synthetic HYRAS inputs: 3 days on a 2 x 3 cell grid.
_DAYS = pd.date_range("2024-07-01", periods=3, freq="D")
_X = np.array([4000500.0, 4001500.0, 4002500.0])
_Y = np.array([3000500.0, 3001500.0])


def _hyras_file(
    path: Path, name: str, value: float, hour: int = 0, x: np.ndarray = _X
) -> str:
    """Write a HYRAS-like file: *name* = *value* everywhere, days at *hour*."""
    data = np.full((len(_DAYS), len(_Y), len(x)), value, dtype=np.float32)
    dataset = xr.Dataset(
        {
            name: (("time", "y", "x"), data, {"grid_mapping": "crs"}),
            "crs": (
                (),
                np.int32(0),
                {"grid_mapping_name": "lambert_azimuthal_equal_area"},
            ),
        },
        coords={"time": _DAYS + pd.Timedelta(hours=hour), "y": _Y, "x": x},
    )
    dataset.to_netcdf(path)
    return str(path)


class TestVapourPressureDeficit:
    """``vapour_pressure_deficit`` is computed from tasmax, tasmin and hurs."""

    @pytest.fixture
    def inputs(self, tmp_path: Path) -> dict[str, str]:
        """Input file per HYRAS prefix; hurs is stamped at noon, the others at 0."""
        return {
            "tasmax_hyras_1": _hyras_file(tmp_path / "tasmax.nc", "tasmax", 20.0),
            "tasmin_hyras_1": _hyras_file(tmp_path / "tasmin.nc", "tasmin", 0.0),
            "hurs_hyras_1": _hyras_file(tmp_path / "hurs.nc", "hurs", 60.0, hour=12),
        }

    @staticmethod
    def _download(
        inputs: dict[str, str], variables: list[str], tmp_path: Path
    ) -> tuple[list[str], MagicMock]:
        """Run download() with the network replaced by *inputs*."""

        def fake_parent_download(self_inner: HYRASDownloader) -> str:
            prefix = self_inner.url.rsplit("/", 1)[1].rsplit("_2024", 1)[0]
            return inputs.get(prefix, "failed")

        with (
            patch.object(
                HYRASDownloader,
                "_discover_latest_filename",
                side_effect=lambda url, year, prefix: f"{prefix}_{year}_v6-1_de.nc",
            ),
            patch(
                "datavia.weather.hyras_downloader.URLDownloader.download",
                autospec=True,
                side_effect=fake_parent_download,
            ) as parent,
            patch("tempfile.tempdir", str(tmp_path)),
        ):
            result = HYRASDownloader(
                variables=variables, date_start="2024-07-01", date_end="2024-07-03"
            ).download()
        return result.splitlines(), parent

    def test_derived_file_alone(self, inputs: dict[str, str], tmp_path: Path) -> None:
        paths, parent = self._download(inputs, ["vapour_pressure_deficit"], tmp_path)
        assert parent.call_count == 3
        assert len(paths) == 1
        with xr.open_dataset(paths[0]) as ds:
            vpd = ds["vpd"].load()
            assert vpd.attrs["units"] == "kPa"
            assert vpd.attrs["grid_mapping"] == "crs"
            assert "crs" in ds
            # Stamps of tasmax (00:00), although hurs is stamped at noon.
            assert list(pd.DatetimeIndex(ds["time"].values)) == list(_DAYS)
        # FAO-56 eq. 19: mean of e(20 degC) and e(0 degC), times 1 - 60 %.
        expected = (2.3383 + 0.6108) / 2 * 0.4
        np.testing.assert_allclose(vpd.values, expected, atol=1e-3)
        # Inputs that were not requested are deleted.
        assert not any(Path(p).exists() for p in inputs.values())

    def test_requested_input_is_kept_and_returned(
        self, inputs: dict[str, str], tmp_path: Path
    ) -> None:
        paths, parent = self._download(
            inputs, ["temperature_2m_max", "vapour_pressure_deficit"], tmp_path
        )
        assert parent.call_count == 3  # tasmax is downloaded once
        assert paths[0] == inputs["tasmax_hyras_1"]
        assert Path(paths[0]).exists()
        assert not Path(inputs["hurs_hyras_1"]).exists()
        with xr.open_dataset(paths[1]) as ds:
            assert "vpd" in ds

    def test_failed_input_skips_the_year(
        self, inputs: dict[str, str], tmp_path: Path
    ) -> None:
        del inputs["hurs_hyras_1"]
        paths, _ = self._download(inputs, ["vapour_pressure_deficit"], tmp_path)
        assert paths == []
        assert not any(Path(p).exists() for p in inputs.values())

    def test_mismatched_grids_raise_and_clean_up(
        self, inputs: dict[str, str], tmp_path: Path
    ) -> None:
        inputs["hurs_hyras_1"] = _hyras_file(
            tmp_path / "hurs_shifted.nc", "hurs", 60.0, x=_X + 1000.0
        )
        with pytest.raises(ValueError, match="differ in grid or days"):
            self._download(inputs, ["vapour_pressure_deficit"], tmp_path)
        assert not any(Path(p).exists() for p in inputs.values())
