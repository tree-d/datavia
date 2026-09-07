"""Unit tests for :class:`~datavia.weather.pipeline.WeatherPipeline`.

Covers __call__(), update_data(), get_config(), reconfigure(),
coverage-aware skipping, cds_queue_timeout, and chunk_by config.
"""

from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest


def _insert_weather_layer(
    source_name: str,
    layer_name: str,
    variable: str,
    file_format: str,
    valid_from: str,
    valid_until: str,
    uri: str,
) -> None:
    """Insert a single row into ``weather_layers`` for test setup.

    Parameters
    ----------
    source_name : str
    layer_name : str
    variable : str
    file_format : str
    valid_from : str
        ISO-8601 datetime string.
    valid_until : str
        ISO-8601 datetime string.
    uri : str
        Absolute file path (does not need to exist).
    """
    from sqlalchemy import text

    from datavia.library.database.connection import session_local

    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO weather_layers
                    (layer_name, source_name, variable, file_format,
                     valid_from, valid_until, uri, crs, bbox, metadata)
                VALUES
                    (:layer_name, :source_name, :variable, :file_format,
                     :valid_from, :valid_until, :uri, 'EPSG:4326', NULL, NULL)
                """
            ),
            {
                "layer_name": layer_name,
                "source_name": source_name,
                "variable": variable,
                "file_format": file_format,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "uri": uri,
            },
        )
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------


class TestWeatherPipeline:
    """Tests for WeatherPipeline initialisation and update_data()."""

    def test_call_initialises_components(self) -> None:
        """__call__ creates downloader, saver, and getter instances."""
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe()

        assert pipe.downloader is not None
        assert pipe.saver is not None
        assert pipe.getter is not None

    def test_update_data_splits_paths(self) -> None:
        """update_data() calls saver.save() for each path in the combined string.

        Patches :class:`CoverageManager` to return one missing cell and
        :class:`CompositeWeatherDownloader` so the per-cell downloader returns
        two paths without any network access.
        """
        from datavia.weather.coverage_manager import CoverageCell
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        # Pre-assign components so self() is not invoked during update_data().
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.save.return_value = True
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            result = pipe.update_data()

        assert result is True
        assert pipe.saver.save.call_count == 2
        saved_paths = [call.args[0] for call in pipe.saver.save.call_args_list]
        assert "/tmp/era5.nc" in saved_paths
        assert "/tmp/dwd.parquet" in saved_paths

    def test_update_data_raises_on_download_failure(self) -> None:
        """update_data() raises RuntimeError
        when the per-cell download() returns 'failed'.

        All cells are still attempted before raising so that partial data
        from successful cells remains in the database.
        """
        from datavia.weather.coverage_manager import CoverageCell
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "failed"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            with pytest.raises(RuntimeError, match="failed to download or save"):
                pipe.update_data()

        pipe.saver.save.assert_not_called()

    def test_update_data_raises_on_partial_save_failure(self) -> None:
        """update_data() raises RuntimeError when at least one save() fails.

        The successfully saved file is still processed; only the failure causes
        the exception, raised after the full loop completes.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.save.side_effect = [True, False]
        pipe.saver.list_managed_files.return_value = []
        pipe.getter = MagicMock()
        pipe.getter.get_registered_uris.return_value = set()

        from datavia.weather.coverage_manager import CoverageCell

        fake_cell = CoverageCell(
            bbox=(5.9, 47.3, 15.0, 55.1),
            date_start="2024-01-01",
            date_end="2024-01-31",
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/era5.nc\n/tmp/dwd.parquet"

        with (
            patch("datavia.weather.pipeline.CoverageManager") as mock_cm_cls,
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
            patch.object(pipe, "sync_files_and_database"),
        ):
            mock_cm_cls.return_value.missing_spatiotemporal.return_value = [fake_cell]
            with pytest.raises(RuntimeError, match="failed to download or save"):
                pipe.update_data()

    def test_invalid_variable_for_era5_raises_at_init(self) -> None:
        """Passing a HYRAS-only variable to an ERA5 pipeline
        raises ValueError at __init__.

        ``temperature_2m_max`` and ``temperature_2m_min`` are defined only in
        the HYRAS ``nc_variable_map``.  Constructing a ``WeatherPipeline`` with
        ``source='ERA5_land'`` and either of those variables must raise
        ``ValueError`` immediately, before any network access.

        This prevents the silent failure mode where the CDS API receives an
        unrecognised variable name, the download is skipped, and
        ``get_data()`` later raises ``RuntimeError: No weather files found``.
        """
        from datavia.weather.pipeline import WeatherPipeline

        with pytest.raises(ValueError, match="temperature_2m_max"):
            WeatherPipeline(
                config={
                    "source": "ERA5_land",
                    "variables": ["temperature_2m_max", "temperature_2m_min"],
                    "date_start": "2024-06-01",
                    "date_end": "2024-06-30",
                }
            )

    def test_invalid_variable_error_message_includes_valid_set(self) -> None:
        """The ValueError for an invalid variable lists the valid options.

        Ensures the error message is actionable: the user can read which
        variables are actually supported without consulting the source registry
        manually.
        """
        from datavia.weather.pipeline import WeatherPipeline

        with pytest.raises(ValueError, match="2m_temperature"):
            WeatherPipeline(
                config={
                    "source": "ERA5_land",
                    "variables": ["temperature_2m_max"],
                    "date_start": "2024-06-01",
                    "date_end": "2024-06-30",
                }
            )

    def test_update_data_skips_dwd_when_stations_covered(self) -> None:
        """update_data() returns True without downloading when DWD data is cached.

        When saver.check_data_exists() returns True for the configured station
        set and date range, update_data() must not call the downloader.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "DWD_stations",
                "variables": ["temperature_2m"],
                "date_start": "2024-07-01",
                "date_end": "2024-07-31",
                "dwd_stations": [
                    {"id": "01234", "latitude": 53.5, "longitude": 10.0},
                ],
            }
        )
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.check_data_exists.return_value = True
        pipe.getter = MagicMock()

        with patch.object(pipe, "sync_files_and_database"):
            result = pipe.update_data()

        assert result is True
        pipe.saver.check_data_exists.assert_called_once_with(
            variable="temperature_2m",
            from_dt="2024-07-01",
            to_dt="2024-07-31",
            station_ids=["01234"],
        )
        pipe.downloader.download.assert_not_called()

    def test_update_data_downloads_dwd_when_station_set_differs(self) -> None:
        """update_data() proceeds to download when the requested DWD stations
        are not yet cached.

        When saver.check_data_exists() returns False, update_data() must call
        the downloader exactly once and save the returned file path.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(
            config={
                "source": "DWD_stations",
                "variables": ["temperature_2m"],
                "date_start": "2024-07-01",
                "date_end": "2024-07-31",
                "dwd_stations": [
                    {"id": "99999", "latitude": 48.1, "longitude": 11.6},
                ],
            }
        )
        mock_cell_dl = MagicMock()
        mock_cell_dl.download.return_value = "/tmp/dwd.parquet"
        pipe.downloader = MagicMock()
        pipe.saver = MagicMock()
        pipe.saver.check_data_exists.return_value = False
        pipe.saver.save.return_value = True
        pipe.getter = MagicMock()

        with (
            patch.object(pipe, "sync_files_and_database"),
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader",
                return_value=mock_cell_dl,
            ),
        ):
            result = pipe.update_data()

        assert result is True
        mock_cell_dl.download.assert_called_once()
        pipe.saver.save.assert_called_once_with("/tmp/dwd.parquet")


# ---------------------------------------------------------------------------
# ERA5Downloader._build_request_date_fields


def _insert_coverage_cell(
    source_name: str,
    variable: str,
    bbox: tuple,
    date_start: str,
    date_end: str,
) -> None:
    """Insert a ``weather_layers`` row with a WKT POLYGON bbox for coverage tests.

    Parameters
    ----------
    source_name : str
    variable : str
    bbox : tuple[float, float, float, float]
        ``(west, south, east, north)`` in EPSG:4326 degrees.
    date_start : str
        ISO date string ``YYYY-MM-DD``; used as ``valid_from``.
    date_end : str
        ISO date string ``YYYY-MM-DD``; used as ``valid_until`` (with
        ``T23:59:59`` appended to match the saver convention).
    """
    from sqlalchemy import text

    from datavia.library.database.connection import session_local

    w, s, e, n = bbox
    bbox_wkt = f"POLYGON (({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"

    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO weather_layers
                    (layer_name, source_name, variable, file_format,
                     valid_from, valid_until, uri, crs, bbox, metadata)
                VALUES
                    (:layer_name, :source_name, :variable, :file_format,
                     :valid_from, :valid_until, :uri, 'EPSG:4326', :bbox, NULL)
                """
            ),
            {
                "layer_name": f"{source_name}_{variable}_{date_start}",
                "source_name": source_name,
                "variable": variable,
                "file_format": "netcdf",
                "valid_from": date_start,
                "valid_until": f"{date_end}T23:59:59",
                "uri": f"/fake/{source_name}_{variable}_{date_start}.nc",
                "bbox": bbox_wkt,
            },
        )
        session.commit()
    finally:
        session.close()


class TestCoverageManager:
    """Tests for :class:`~datavia.weather.coverage_manager.CoverageManager`.

    All tests use the ``sqlite_db`` fixture so they run against an in-memory
    database with no network access.  The 2-D subtraction algorithm is
    exercised through five scenarios that collectively cover all branches of
    :func:`~datavia.weather.coverage_manager._compute_missing`.
    """

    # Germany bounding box used as a representative large extent.
    _GERMANY: ClassVar[tuple[float, float, float, float]] = (5.9, 47.3, 15.0, 55.1)
    # Berlin inner-city approximate bounding box (fully inside Germany).
    _BERLIN: ClassVar[tuple[float, float, float, float]] = (13.1, 52.3, 13.8, 52.7)
    # East Germany bounding box (contains Berlin, inside Germany).
    _EAST_GERMANY: ClassVar[tuple[float, float, float, float]] = (
        10.0,
        50.0,
        15.0,
        55.0,
    )

    def test_fully_covered_returns_empty(self, sqlite_db) -> None:
        """Requesting a bbox and date range already in the DB returns an empty list.

        Verifies the happy-path where all data is already registered and no
        download is triggered.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._GERMANY, "2024-01-01", "2024-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2024-01-01", "2024-12-31")

        assert result == [], (
            f"Expected no missing cells when DB fully covers the request; got {result}"
        )

    def test_wider_time_range_returns_date_gaps(self, sqlite_db) -> None:
        """A request wider in time than what is in the DB returns before/after gaps.

        Existing: Germany 2024.  Requested: Germany 2023-2025.
        Expected: two cells - one for 2023 and one for 2025.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._GERMANY, "2024-01-01", "2024-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2023-01-01", "2025-12-31")

        assert len(result) == 2, (
            f"Expected 2 temporal gap cells; got {len(result)}: {result}"
        )

        date_starts = {c.date_start for c in result}
        date_ends = {c.date_end for c in result}
        assert "2023-01-01" in date_starts, "Expected a cell starting 2023-01-01."
        assert "2025-12-31" in date_ends, "Expected a cell ending 2025-12-31."
        assert "2024-01-01" not in date_starts, (
            "Cell starting on 2024-01-01 should not be missing."
        )
        # Both gap cells must span the full Germany bbox.
        for cell in result:
            assert cell.bbox == self._GERMANY, (
                f"Temporal gap cell has unexpected bbox {cell.bbox!r}; "
                f"expected {self._GERMANY!r}."
            )

    def test_wider_bbox_returns_spatial_strips(self, sqlite_db) -> None:
        """A request wider in space than what is in the DB returns spatial strips.

        Existing: western half of Germany for 2024.
        Requested: full Germany for 2024.
        Expected: the eastern strip is returned as a missing cell.
        """
        from datavia.weather.coverage_manager import CoverageManager

        west_half = (5.9, 47.3, 10.45, 55.1)  # western half
        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", west_half, "2024-01-01", "2024-12-31"
        )

        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(self._GERMANY, "2024-01-01", "2024-12-31")

        # The right strip (east of the existing cell) must appear.
        assert len(result) >= 1, "Expected at least one spatial strip."
        # No cell should cover the western half (5.9 to 10.45 longitude).
        for cell in result:
            w, _s, e, _n = cell.bbox
            assert not (w <= 8.0 <= e), (
                f"Cell {cell} covers longitude 8.0° which is in the already-registered "
                f"western half and should not be re-fetched."
            )

    def test_2d_overlap_berlin_east_germany(self, sqlite_db) -> None:
        """2-D overlap: Berlin 1990-2000 on disk, East Germany 1980-2010 requested.

        The existing Berlin/1990-2000 cell splits the request into:

        - 1 temporal piece before 1990 (full East Germany bbox)
        - 4 spatial strips during 1990-2000 (East Germany minus Berlin)
        - 1 temporal piece after 2000 (full East Germany bbox)

        Total: 6 cells.  Crucially, the Berlin area must not appear in any of
        the 4 spatial strips that cover the 1990-2000 overlap period.
        """
        from datavia.weather.coverage_manager import CoverageManager

        _insert_coverage_cell(
            "ERA5_land", "2m_temperature", self._BERLIN, "1990-01-01", "2000-12-31"
        )
        mgr = CoverageManager("ERA5_land", ["2m_temperature"])
        result = mgr.missing_spatiotemporal(
            self._EAST_GERMANY, "1980-01-01", "2010-12-31"
        )

        assert len(result) == 6, (
            f"Expected 6 cells (2 temporal + 4 spatial strips); got {len(result)}: "
            f"{result}"
        )

        # The two temporal pieces must have the full East Germany bbox.
        temporal_cells = [
            c
            for c in result
            if c.date_end < "1990-01-01" or c.date_start > "2000-12-31"
        ]
        assert len(temporal_cells) == 2, (
            "Expected 2 temporal-only cells; "
            f"got {len(temporal_cells)}: {temporal_cells}"
        )
        for cell in temporal_cells:
            assert cell.bbox == self._EAST_GERMANY, (
                f"Temporal cell has unexpected bbox {cell.bbox!r}."
            )

        # The 4 spatial strips must all lie in the 1990-2000 overlap period
        # and none of them must cover the Berlin centroid (13.45, 52.5).
        spatial_cells = [
            c for c in result if "1990-01-01" <= c.date_start <= "2000-12-31"
        ]
        assert len(spatial_cells) == 4, (
            f"Expected 4 spatial strip cells during 1990-2000; "
            f"got {len(spatial_cells)}: {spatial_cells}"
        )
        berlin_lon, berlin_lat = 13.45, 52.5
        for cell in spatial_cells:
            w, s, e, n = cell.bbox
            assert not (w <= berlin_lon <= e and s <= berlin_lat <= n), (
                f"Spatial strip {cell} covers the Berlin centroid ({berlin_lon}, "
                f"{berlin_lat}) — Berlin/1990-2000 must not be re-fetched."
            )

    def test_invalid_date_order_raises(self) -> None:
        """date_start > date_end raises :exc:`ValueError`.

        The error is raised before any DB access so no ``sqlite_db`` fixture
        is needed.
        """
        from datavia.weather.coverage_manager import CoverageManager

        with pytest.raises(ValueError, match="date_start"):
            mgr = CoverageManager.__new__(CoverageManager)
            mgr._source_name = "ERA5_land"
            mgr._variables = ["2m_temperature"]
            mgr._cells_per_variable = {"2m_temperature": []}
            mgr.missing_spatiotemporal(self._GERMANY, "2024-12-31", "2024-01-01")

    def test_update_data_skips_download_when_fully_covered(self, sqlite_db) -> None:
        """``update_data()`` issues no downloader calls when data is already registered.

        Pre-populates the DB with a cell that fully covers the pipeline's
        request, then asserts that :class:`CompositeWeatherDownloader` is
        never instantiated.
        """
        from datavia.weather.pipeline import WeatherPipeline

        # Pre-populate DB: Germany for all of 2024, both variables.
        for var in ["2m_temperature"]:
            _insert_coverage_cell(
                "ERA5_land", var, self._GERMANY, "2024-01-01", "2024-12-31"
            )

        pipe = WeatherPipeline(
            config={
                "source": "ERA5_land",
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-12-31",
                "era5_bbox": [55.1, 5.9, 47.3, 15.0],  # [north, west, south, east]
            }
        )
        pipe()  # Initialise components.

        with (
            patch(
                "datavia.weather.pipeline.CompositeWeatherDownloader"
            ) as mock_downloader_cls,
            patch.object(pipe, "sync_files_and_database"),
        ):
            result = pipe.update_data()

        mock_downloader_cls.assert_not_called()
        assert result is True


class TestWeatherPipelineLifecycle:
    """Tests for WeatherPipeline lifecycle methods: get_config and reconfigure.

    All tests are isolated and do not touch the database because ``get_config``
    and ``reconfigure`` are pure in-memory operations on the stored config
    dict.
    """

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_get_config_returns_copy(self) -> None:
        """get_config returns a copy; mutating it does not affect the pipeline.

        Ensures callers cannot accidentally corrupt the internal config by
        modifying the returned dict.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        cfg = pipe.get_config()
        cfg["date_start"] = "2023-01-01"

        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "Mutating the returned config must not change "
            "the pipeline's internal state."
        )

    def test_get_config_matches_init_config(self) -> None:
        """get_config returns all keys supplied at construction."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "era5_bbox": [55.1, 5.9, 47.3, 15.0]}
        pipe = WeatherPipeline(config=config)

        assert pipe.get_config() == config

    def test_reconfigure_merge_updates_dates(self) -> None:
        """reconfigure in merge mode applies only the provided delta.

        Only ``date_start`` and ``date_end`` are updated; all other keys
        keep their original values.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        pipe.reconfigure({"date_start": "2023-06-01", "date_end": "2023-12-31"})

        cfg = pipe.get_config()
        assert cfg["date_start"] == "2023-06-01"
        assert cfg["date_end"] == "2023-12-31"
        assert cfg["source"] == "ERA5_land", "Unchanged keys must be preserved."
        assert cfg["variables"] == ["2m_temperature"], (
            "Unchanged keys must be preserved."
        )

    def test_reconfigure_replace_sets_entire_config(self) -> None:
        """reconfigure with replace=True swaps the full config.

        The new config contains only the required keys.  Optional keys from
        the original config (e.g. ``era5_bbox``) must not be present.
        """
        from datavia.weather.pipeline import WeatherPipeline

        original = {**self._BASE_CONFIG, "era5_bbox": [55.1, 5.9, 47.3, 15.0]}
        pipe = WeatherPipeline(config=original)

        new_config = {
            "source": "ERA5_land",
            "variables": ["total_precipitation"],
            "date_start": "2020-01-01",
            "date_end": "2020-12-31",
        }
        pipe.reconfigure(new_config, replace=True)

        cfg = pipe.get_config()
        assert cfg == new_config
        assert "era5_bbox" not in cfg, (
            "replace=True must remove keys absent from the new config."
        )

    def test_reconfigure_rebuilds_downloader(self) -> None:
        """reconfigure re-creates the downloader with the new config.

        After reconfiguration the pipeline's ``downloader`` attribute must be
        a fresh :class:`CompositeWeatherDownloader` instance, not the old one.
        """
        from unittest.mock import patch

        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)
        pipe()  # Instantiate components.

        original_downloader = pipe.downloader

        with patch("datavia.weather.pipeline.GetterWeather"):
            pipe.reconfigure({"date_end": "2025-12-31"})

        assert pipe.downloader is not original_downloader, (
            "reconfigure must rebuild the downloader instance."
        )

    def test_reconfigure_source_change_raises(self) -> None:
        """reconfigure raises ValueError when 'source' would change.

        The source name is immutable after construction because it is baked
        into the pipeline name and all database rows.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)

        with pytest.raises(ValueError, match="source"):
            pipe.reconfigure({"source": "HYRAS"})

    def test_reconfigure_missing_required_key_raises(self) -> None:
        """reconfigure with replace=True raises ValueError when required key is absent.

        Supplying an incomplete replacement config must fail validation before
        any state is mutated.
        """
        from datavia.weather.pipeline import WeatherPipeline

        pipe = WeatherPipeline(config=self._BASE_CONFIG)

        incomplete = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            # date_start and date_end intentionally omitted
        }

        with pytest.raises(ValueError):
            pipe.reconfigure(incomplete, replace=True)

        # Original config must be unchanged after failed reconfigure.
        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "A failed reconfigure must not partially mutate the pipeline config."
        )

    def test_replace_false_at_init_stores_copy(self) -> None:
        """WeatherPipeline(config, replace=False) stores a copy of the config.

        Mutating the original dict after construction must not affect the
        pipeline's internal config.
        """
        from datavia.weather.pipeline import WeatherPipeline

        config = dict(self._BASE_CONFIG)
        pipe = WeatherPipeline(config=config, replace=False)

        config["date_start"] = "2000-01-01"
        assert pipe.get_config()["date_start"] == "2024-01-01", (
            "WeatherPipeline must store a copy; mutating the caller's dict "
            "must not change the pipeline state."
        )


# ---------------------------------------------------------------------------
# ERA5Downloader — monthly chunking (Step 9)

# ---------------------------------------------------------------------------


class TestWeatherPipelineCdsQueueTimeout:
    """Tests that ``cds_queue_timeout`` is accepted as a valid config key."""

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_cds_queue_timeout_accepted_in_config(self) -> None:
        """WeatherPipeline accepts ``cds_queue_timeout`` without raising ValueError."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "cds_queue_timeout": 1800}
        # Must not raise.
        pipe = WeatherPipeline(config=config)
        assert pipe.get_config()["cds_queue_timeout"] == 1800

    def test_cds_queue_timeout_forwarded_to_composite(self) -> None:
        """``cds_queue_timeout`` is forwarded to ``CompositeWeatherDownloader``."""
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        config = {
            "source": "ERA5_land",
            "variables": ["2m_temperature"],
            "date_start": "2024-01-01",
            "date_end": "2024-01-31",
            "cds_queue_timeout": 600,
        }

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_class.return_value = MagicMock()
            mock_registry.return_value = mock_grid_class

            CompositeWeatherDownloader(config=config)

        # The grid class must have been called with cds_queue_timeout=600.
        call_kwargs = mock_grid_class.call_args[1]
        assert call_kwargs.get("cds_queue_timeout") == 600, (
            f"Expected cds_queue_timeout=600 forwarded to grid downloader, "
            f"got {call_kwargs}"
        )


# ---------------------------------------------------------------------------
# ERA5Downloader — quarterly chunking

# ---------------------------------------------------------------------------


class TestWeatherPipelineChunkByConfig:
    """Tests that ``chunk_by`` is accepted as a valid WeatherPipeline config key."""

    _BASE_CONFIG: ClassVar[dict] = {
        "source": "ERA5_land",
        "variables": ["2m_temperature"],
        "date_start": "2024-01-01",
        "date_end": "2024-12-31",
    }

    def test_chunk_by_accepted_in_pipeline_config(self) -> None:
        """WeatherPipeline accepts chunk_by without raising ValueError."""
        from datavia.weather.pipeline import WeatherPipeline

        config = {**self._BASE_CONFIG, "chunk_by": "quarterly"}
        pipe = WeatherPipeline(config=config)
        assert pipe.get_config()["chunk_by"] == "quarterly"

    def test_chunk_by_all_valid_values_accepted(self) -> None:
        """WeatherPipeline accepts all four valid chunk_by values."""
        from datavia.weather.pipeline import WeatherPipeline

        for value in ("monthly", "quarterly", "yearly", "none"):
            config = {**self._BASE_CONFIG, "chunk_by": value}
            # Must not raise.
            pipe = WeatherPipeline(config=config)
            assert pipe.get_config()["chunk_by"] == value


# ---------------------------------------------------------------------------
# SaverWeather — delete_registration()
