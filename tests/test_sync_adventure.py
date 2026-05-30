"""Field tests for Pipeline.sync_files_and_database() — the BUG-01 / Option-B refactor.

After the refactor, ``sync_files_and_database`` lives on ``Pipeline`` and
co-ordinates *only* through the established component contracts:

- ``Saver.list_managed_files()``  — disk scan, no DB read.
- ``Getter.get_registered_uris()`` — DB read, no disk access.
- ``Saver.delete_registration(uri)`` — DB DELETE, no filesystem changes.
- ``Saver.save(path, register_only=True)`` — DB INSERT for a file that is
  already on disk, no file copy.

The tests here verify all three pipelines (elevation, soil, weather) under
four entertaining field scenarios:

1. **Happy registration** — a freshly downloaded file ends up in both the
   database and on disk; querying either side returns consistent results.
2. **Ghost in the database** — a file was deleted from disk but its DB row
   survived; ``sync`` must evict the phantom row so future queries don't
   point into the void.
3. **Unregistered survivor on disk** — a file arrived on disk without a DB
   record (e.g. a manual copy or a crashed save run); ``sync`` must adopt it.
4. **Full chaos reconciliation** — both a ghost row *and* an unregistered
   file exist simultaneously; a single ``sync`` call must fix everything in
   one pass.

No network access, no real downloads.  All database I/O uses the
``sqlite_db`` in-memory fixture from ``conftest.py``.
"""

import os
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import numpy as np
import rasterio
from datavia.elevation import ElevationPipeline
from rasterio.transform import from_bounds
from sqlalchemy import text

from datavia.core.getter_tiff import GetterTiff
from datavia.core.saver_tiff import TiffSaver
from datavia.library.database.connection import session_local

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _create_minimal_tiff(path: str) -> None:
    """Write a tiny but valid single-band GeoTIFF to *path*.

    The raster covers a small window over Germany in EPSG:4326.
    All pixel values are 200 (a plausible elevation in metres).

    Parameters
    ----------
    path : str
        Absolute destination path for the ``.tif`` file.
    """
    data = np.full((10, 10), fill_value=200, dtype=np.int16)
    transform = from_bounds(5.8, 47.2, 15.0, 55.0, 10, 10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype=data.dtype,
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data, 1)


def _insert_raster_layer(
    source_name: str,
    layer_name: str,
    uri: str,
) -> None:
    """Insert a bare-minimum row into ``raster_layers`` without touching disk.

    Used to simulate a database record whose backing file no longer exists
    (the *ghost-in-the-database* scenario).

    Parameters
    ----------
    source_name : str
        Source identifier, e.g. ``"elevation"`` or ``"soil"``.
    layer_name : str
        Unique layer identifier for the row.
    uri : str
        Absolute path stored in the ``uri`` column (need not exist on disk).
    """
    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO raster_layers
                    (layer_name, source_name, bbox, resolution_x, resolution_y,
                     crs, uri, acquisition_time)
                VALUES
                    (:layer_name, :source_name,
                     'POLYGON((5 47, 5 55, 15 55, 15 47, 5 47))',
                     0.083, 0.083, 'EPSG:4326', :uri, '2024-01-01T00:00:00')
                """
            ),
            {
                "layer_name": layer_name,
                "source_name": source_name,
                "uri": uri,
            },
        )
        session.commit()
    finally:
        session.close()


def _insert_weather_layer(
    source_name: str,
    layer_name: str,
    variable: str,
    uri: str,
) -> None:
    """Insert a bare-minimum row into ``weather_layers`` without touching disk.

    Parameters
    ----------
    source_name : str
        Source identifier, e.g. ``"era5"`` or ``"hyras"``.
    layer_name : str
        Unique layer identifier for the row.
    variable : str
        Variable name, e.g. ``"temperature_2m"``.
    uri : str
        Absolute path stored in the ``uri`` column (need not exist on disk).
    """
    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO weather_layers
                    (layer_name, source_name, variable, file_format,
                     valid_from, valid_until, uri, crs, bbox, metadata)
                VALUES
                    (:layer_name, :source_name, :variable, 'netcdf',
                     '2024-01-01T00:00:00', '2024-01-31T23:00:00',
                     :uri, 'EPSG:4326', NULL, NULL)
                """
            ),
            {
                "layer_name": layer_name,
                "source_name": source_name,
                "variable": variable,
                "uri": uri,
            },
        )
        session.commit()
    finally:
        session.close()


def _build_tiff_pipeline(source_name: str, data_dir: str) -> ElevationPipeline:
    """Build an ``ElevationPipeline`` with its data directory overridden.

    Initialises the pipeline components and then redirects the saver's
    ``data_dir`` to *data_dir* so that all file I/O stays inside the
    test-controlled temporary directory.

    Parameters
    ----------
    source_name : str
        Source name used for both the pipeline and the saver.
    data_dir : str
        Directory that the saver will scan and write to.

    Returns
    -------
    ElevationPipeline
        Initialised pipeline ready for sync testing.
    """
    pipeline = ElevationPipeline(config={"source": source_name})
    pipeline()  # creates saver and getter
    pipeline.saver.data_dir = data_dir  # type: ignore[union-attr]
    return pipeline


# ---------------------------------------------------------------------------
# Elevation pipeline — TIFF-based sync scenarios
# ---------------------------------------------------------------------------


class TestElevationSyncAdventure:
    """Sync scenarios for the elevation pipeline (TiffSaver + GetterTiff).

    Each test starts with a clean in-memory SQLite database and an empty
    temporary directory on disk.  A single ``ElevationPipeline`` instance
    is constructed per test, its saver's ``data_dir`` redirected to the
    test-local ``tmp_path``, and the four sync scenarios are played out.
    """

    def test_happy_registration(self, sqlite_db: None, tmp_path: Path) -> None:
        """A saved TIFF appears in both disk scan and DB query after save().

        Scenario: the downloader writes a file; the saver copies it to the
        data directory and registers it.  Both ``list_managed_files`` and
        ``get_registered_uris`` should agree on the same single file path.
        """
        pipeline = _build_tiff_pipeline("elevation", str(tmp_path))

        tiff_path = str(tmp_path / "elevation_test_dem.tif")
        _create_minimal_tiff(tiff_path)
        assert pipeline.saver.save(tiff_path, register_only=True)  # type: ignore[union-attr]

        disk_files = set(pipeline.saver.list_managed_files())  # type: ignore[union-attr]
        db_uris = pipeline.getter.get_registered_uris()  # type: ignore[union-attr]

        assert tiff_path in disk_files, "The file must be visible on disk."
        assert tiff_path in db_uris, "The file must be registered in the database."
        assert disk_files == db_uris, "Disk and database must agree after a clean save."

    def test_ghost_in_the_database(self, sqlite_db: None, tmp_path: Path) -> None:
        """A deleted file's DB row is purged by sync().

        Scenario: the file was once valid, then someone deleted it.  The
        database still holds the ghost row.  After sync the DB must be clean.
        """
        pipeline = _build_tiff_pipeline("elevation", str(tmp_path))
        phantom_uri = str(tmp_path / "elevation_deleted_dem.tif")

        # Plant the ghost directly in the database — no file on disk.
        _insert_raster_layer("elevation", "elevation_deleted_dem", phantom_uri)

        assert phantom_uri in pipeline.getter.get_registered_uris()  # type: ignore[union-attr]
        assert not os.path.exists(phantom_uri), "The ghost file must not exist."

        pipeline.sync_files_and_database()

        surviving_uris = pipeline.getter.get_registered_uris()  # type: ignore[union-attr]
        assert phantom_uri not in surviving_uris, (
            "sync() must evict the orphan DB row for the missing file."
        )
        assert surviving_uris == set(), (
            "Database must be empty after purging the ghost."
        )

    def test_unregistered_survivor_on_disk(
        self, sqlite_db: None, tmp_path: Path
    ) -> None:
        """An unregistered disk file is adopted into the database by sync().

        Scenario: a TIFF was copied directly into the data directory (e.g.
        by a manual restore or a crashed pipeline run) without going through
        save().  After sync it must appear in the database.
        """
        pipeline = _build_tiff_pipeline("elevation", str(tmp_path))
        orphan_path = str(tmp_path / "elevation_orphan_dem.tif")
        _create_minimal_tiff(orphan_path)

        # Disk file exists, but nothing is registered in the database yet.
        assert os.path.exists(orphan_path)
        assert orphan_path not in pipeline.getter.get_registered_uris()  # type: ignore[union-attr]

        pipeline.sync_files_and_database()

        adopted_uris = pipeline.getter.get_registered_uris()  # type: ignore[union-attr]
        assert orphan_path in adopted_uris, (
            "sync() must register the orphan disk file into the database."
        )

    def test_full_chaos_reconciliation(self, sqlite_db: None, tmp_path: Path) -> None:
        """A ghost row and an unregistered disk file are both fixed in one sync().

        Scenario: chaos reigns — one file was deleted but its DB row remains,
        and a second file was copied to disk without being registered.
        A single call to sync() must restore order to both sides.
        """
        pipeline = _build_tiff_pipeline("elevation", str(tmp_path))

        # The ghost: DB row, no file.
        phantom_uri = str(tmp_path / "elevation_vanished_dem.tif")
        _insert_raster_layer("elevation", "elevation_vanished_dem", phantom_uri)

        # The orphan: file on disk, no DB row.
        orphan_path = str(tmp_path / "elevation_stowaway_dem.tif")
        _create_minimal_tiff(orphan_path)

        assert phantom_uri in pipeline.getter.get_registered_uris()  # type: ignore[union-attr]
        assert orphan_path not in pipeline.getter.get_registered_uris()  # type: ignore[union-attr]

        pipeline.sync_files_and_database()

        final_uris = pipeline.getter.get_registered_uris()  # type: ignore[union-attr]

        assert phantom_uri not in final_uris, "Ghost row must be evicted."
        assert orphan_path in final_uris, "Orphan disk file must be adopted."
        assert final_uris == {orphan_path}, "Only the real file must remain."


# ---------------------------------------------------------------------------
# Soil pipeline — multi-coverage TIFF sync scenarios
# ---------------------------------------------------------------------------


class TestSoilSyncAdventure:
    """Sync scenarios for the soil pipeline (TiffSaver + GetterTiff, multi-coverage).

    The soil source registers many TIFFs — one per coverage-ID — all under
    the same ``source_name``.  These tests verify that sync only touches the
    files belonging to the soil source and leaves unrelated sources untouched.
    """

    def test_happy_registration_multi_coverage(
        self, sqlite_db: None, tmp_path: Path
    ) -> None:
        """Multiple coverage TIFFs are each registered with their own layer name.

        Scenario: two soil coverages (clay and sand) are downloaded and
        saved.  Both should appear in the database under distinct layer names
        and the getter must return both URIs.
        """
        saver = TiffSaver.__new__(TiffSaver)
        saver.source_name = "soil"
        saver.data_dir = str(tmp_path)
        saver.target_crs = "EPSG:4326"

        getter = GetterTiff("soil")

        clay_path = str(tmp_path / "soil_clay_0-5cm_mean.tif")
        sand_path = str(tmp_path / "soil_sand_0-5cm_mean.tif")
        _create_minimal_tiff(clay_path)
        _create_minimal_tiff(sand_path)

        assert saver.save(clay_path, register_only=True)
        assert saver.save(sand_path, register_only=True)

        registered = getter.get_registered_uris()
        assert clay_path in registered, "Clay coverage must be registered."
        assert sand_path in registered, "Sand coverage must be registered."
        assert len(registered) == 2, "Exactly two coverages must be registered."

    def test_source_isolation(self, sqlite_db: None, tmp_path: Path) -> None:
        """Soil sync does not disturb elevation DB rows and vice versa.

        Scenario: both soil and elevation have one registered file each.
        A soil ghost row is planted.  Soil sync must remove the soil ghost
        but the elevation row must remain untouched throughout.
        """
        # Register a real elevation file.
        elev_tiff = str(tmp_path / "elevation_dem200.tif")
        _create_minimal_tiff(elev_tiff)
        elev_saver = TiffSaver.__new__(TiffSaver)
        elev_saver.source_name = "elevation"
        elev_saver.data_dir = str(tmp_path)
        elev_saver.target_crs = "EPSG:4326"
        elev_saver.save(elev_tiff, register_only=True)

        # Plant a soil ghost row (no file on disk).
        phantom_soil_uri = str(tmp_path / "soil_phantom_clay.tif")
        _insert_raster_layer("soil", "soil_phantom_clay", phantom_soil_uri)

        # Run sync only for the soil pipeline.
        soil_pipeline = _build_tiff_pipeline("soil", str(tmp_path))
        soil_pipeline.sync_files_and_database()

        # Soil ghost must be gone.
        soil_getter = GetterTiff("soil")
        assert phantom_soil_uri not in soil_getter.get_registered_uris(), (
            "Soil ghost row must be purged by soil sync."
        )

        # Elevation row must be unaffected.
        elev_getter = GetterTiff("elevation")
        assert elev_tiff in elev_getter.get_registered_uris(), (
            "Elevation registration must survive a soil sync."
        )


# ---------------------------------------------------------------------------
# Weather pipeline — NetCDF / Parquet sync scenarios
# ---------------------------------------------------------------------------


class TestWeatherSyncAdventure:
    """Sync scenarios for the weather pipeline (SaverWeather + GetterWeather).

    Weather files carry per-variable DB rows: a single NetCDF can produce
    multiple ``weather_layers`` entries (one per variable).  The sync must
    handle those multi-row cases correctly — deleting *all* rows for a
    missing file, and re-registering an orphan file for *all* its variables.

    The source name ``"ERA5_land"`` is used because it is a registered key in
    :data:`~datavia.weather.source_registry.SOURCE_REGISTRY`.  Files on disk
    must match the ``ERA5_land_*.nc`` prefix for
    :meth:`~datavia.weather.saver_weather.SaverWeather.list_managed_files`
    to discover them.
    """

    #: Valid source name registered in SOURCE_REGISTRY.
    _SOURCE: str = "ERA5_land"

    # Fake NC metadata returned whenever extract_netcdf_layer_metadata is
    # called during a save() — avoids opening a real NetCDF file.
    _FAKE_NC_META: ClassVar[dict] = {
        "valid_from": "2024-01-01T00:00:00",
        "valid_until": "2024-01-31T23:00:00",
        "bbox": "POLYGON ((5.9 47.3, 15.0 47.3, 15.0 55.1, 5.9 55.1, 5.9 47.3))",
        "crs": "EPSG:4326",
        "variables": ["2m_temperature"],
    }

    def _build_weather_pipeline(self, data_dir: str) -> tuple:
        """Wire SaverWeather and GetterWeather directly, bypassing pipeline().

        Avoids triggering the ``CompositeWeatherDownloader`` constructor (and
        its ``SOURCE_REGISTRY`` validation) when only the sync path is under
        test.  A dummy ``WeatherPipeline`` instance has its components injected
        manually so that ``sync_files_and_database()`` can be called normally.

        Parameters
        ----------
        data_dir : str
            Directory used as the saver's data directory.

        Returns
        -------
        tuple[WeatherPipeline, SaverWeather, GetterWeather]
            The pipeline (with saver and getter pre-wired), plus the
            individual components for direct assertions.
        """
        from datavia.weather.getter_weather import GetterWeather
        from datavia.weather.pipeline import WeatherPipeline
        from datavia.weather.saver_weather import SaverWeather

        pipeline = WeatherPipeline(
            config={
                "source": self._SOURCE,
                "variables": ["2m_temperature"],
                "date_start": "2024-01-01",
                "date_end": "2024-01-31",
            }
        )
        # Inject components directly so that pipeline() (which builds the
        # downloader and validates SOURCE_REGISTRY) is never called.
        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = self._SOURCE
        saver.data_dir = data_dir

        getter = GetterWeather(self._SOURCE)

        pipeline.saver = saver
        pipeline.getter = getter
        return pipeline, saver, getter

    def test_happy_registration(self, sqlite_db: None, tmp_path: Path) -> None:
        """A saved NetCDF file appears in both disk scan and DB query after save().

        Scenario: a downloader writes a temporary file; the saver copies it to
        the data directory with a content-derived name
        ``{source}_{nc_variable}_{year}.nc`` (BUG-07 fix).  Both
        ``list_managed_files`` and ``get_registered_uris`` must agree.
        """
        _, saver, getter = self._build_weather_pipeline(str(tmp_path))

        # Source file has a random temp stem; the saver reads content to build
        # the deterministic destination name (BUG-07 fix).
        src_file = tmp_path / "temperature_jan.nc"
        src_file.write_bytes(b"FAKE_NC_CONTENT")
        # _FAKE_NC_META: variable=2m_temperature, Jan 2024, Germany bbox
        # → _build_dest_stem produces ERA5_land_2m_temperature_202401_202401_11dae5.nc
        expected_dest = str(
            tmp_path / f"{self._SOURCE}_2m_temperature_202401_202401_11dae5.nc"
        )

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=self._FAKE_NC_META,
        ):
            # Full save (not register_only) so that the copy is made and the
            # registered URI is the destination path.
            assert saver.save(str(src_file), variable="2m_temperature")

        disk_files = set(saver.list_managed_files())
        db_uris = getter.get_registered_uris()

        assert expected_dest in disk_files, "Copied NetCDF must be visible on disk."
        assert expected_dest in db_uris, (
            "Copied NetCDF must be registered in the database."
        )
        assert disk_files == db_uris, "Disk and database must agree after a clean save."

    def test_ghost_in_the_database(self, sqlite_db: None, tmp_path: Path) -> None:
        """A deleted NetCDF's DB row is purged by sync().

        Scenario: the file was present, then deleted.  The ``weather_layers``
        row for its variable is a ghost.  After sync the table must be clean.
        """
        pipeline, _, getter = self._build_weather_pipeline(str(tmp_path))

        phantom_uri = str(tmp_path / f"{self._SOURCE}_vanished_jan.nc")
        _insert_weather_layer(
            self._SOURCE, f"{self._SOURCE}_vanished_jan", "2m_temperature", phantom_uri
        )

        assert phantom_uri in getter.get_registered_uris()
        assert not os.path.exists(phantom_uri), "Ghost file must not exist."

        pipeline.sync_files_and_database()

        assert phantom_uri not in getter.get_registered_uris(), (
            "sync() must remove the weather ghost row."
        )

    def test_unregistered_nc_is_adopted(self, sqlite_db: None, tmp_path: Path) -> None:
        """An unregistered NetCDF on disk is adopted into the database by sync().

        Scenario: an ERA5 file was copied manually into the data directory
        without going through save().  sync() must register it.
        """
        pipeline, _, getter = self._build_weather_pipeline(str(tmp_path))

        # Orphan file must carry the source prefix so list_managed_files() finds it.
        orphan_nc = tmp_path / f"{self._SOURCE}_orphan_jan.nc"
        orphan_nc.write_bytes(b"FAKE_NC_CONTENT")

        assert str(orphan_nc) not in getter.get_registered_uris()

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=self._FAKE_NC_META,
        ):
            pipeline.sync_files_and_database()

        assert str(orphan_nc) in getter.get_registered_uris(), (
            "sync() must adopt the orphan NetCDF into the database."
        )

    def test_full_chaos_reconciliation(self, sqlite_db: None, tmp_path: Path) -> None:
        """Ghost row and unregistered file are both resolved in one sync() pass.

        Scenario: worst case — a file disappeared leaving a ghost row, AND
        another file was dropped onto disk without registration.  One call
        to sync() must clean the ghost and adopt the stowaway.
        """
        pipeline, _, getter = self._build_weather_pipeline(str(tmp_path))

        # The ghost: DB row, file deleted.
        phantom_uri = str(tmp_path / f"{self._SOURCE}_vanished_feb.nc")
        _insert_weather_layer(
            self._SOURCE, f"{self._SOURCE}_vanished_feb", "2m_temperature", phantom_uri
        )

        # The orphan: file on disk, no DB row.
        orphan_nc = tmp_path / f"{self._SOURCE}_stowaway_mar.nc"
        orphan_nc.write_bytes(b"FAKE_NC_CONTENT")

        assert phantom_uri in getter.get_registered_uris()
        assert str(orphan_nc) not in getter.get_registered_uris()

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=self._FAKE_NC_META,
        ):
            pipeline.sync_files_and_database()

        final_uris = getter.get_registered_uris()

        assert phantom_uri not in final_uris, "Ghost row must be evicted."
        assert str(orphan_nc) in final_uris, "Orphan file must be adopted."
        assert final_uris == {str(orphan_nc)}, "Only the real file must remain."


# ---------------------------------------------------------------------------
# Cross-pipeline — Datavia controller integration
# ---------------------------------------------------------------------------


class TestDataviaControllerSync:
    """Verify sync_files_and_database() through the Datavia controller.

    These tests do not test the full Datavia initialisation flow (which
    requires real config / pipelines) but confirm that the controller passes
    sync calls through to the correct pipeline instance without corruption.
    """

    def test_sync_called_via_controller(self, sqlite_db: None, tmp_path: Path) -> None:
        """Pipeline sync is reachable through controller pipeline attributes.

        Builds a minimal Datavia instance backed by an ElevationPipeline
        with a temporary data directory and confirms that calling
        ``datavia.elevation.sync_files_and_database()`` resolves a ghost row.
        """
        from datavia.core.datavia import Datavia

        pipeline = _build_tiff_pipeline("elevation", str(tmp_path))
        dv = Datavia(pipelines=[pipeline])()

        phantom_uri = str(tmp_path / "elevation_ghost_dem.tif")
        _insert_raster_layer("elevation", "elevation_ghost_dem", phantom_uri)

        assert phantom_uri in dv.elevation.getter.get_registered_uris()

        dv.elevation.sync_files_and_database()

        assert phantom_uri not in dv.elevation.getter.get_registered_uris(), (
            "Ghost row must be gone after sync called via the controller."
        )
