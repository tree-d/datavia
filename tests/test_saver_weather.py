"""Unit tests for :class:`~datavia.weather.saver_weather.SaverWeather`.

Covers save(), check_data_exists(), list_managed_files(),
delete_registration(), and register_only mode.
"""

from unittest.mock import patch


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


class TestSaverWeather:
    """Tests for SaverWeather.save() and check_data_exists()."""

    def test_save_netcdf_inserts_db_row(self, sqlite_db: None, tmp_path) -> None:
        """A NetCDF file is copied and a DB row is inserted."""
        from datavia.weather.saver_weather import SaverWeather

        from datavia.library.database.query import check_weather_source_exists

        # Create a dummy .nc file — content irrelevant; only the save() path
        # logic is tested here; metadata extraction is mocked out.
        nc_file = tmp_path / "era5_temperature_2m.nc"
        nc_file.write_bytes(b"FAKE_NC_CONTENT")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-01-31T23:00:00",
                "bbox": (
                    "POLYGON ((5.9 47.3, 15.0 47.3, 15.0 55.1, 5.9 55.1, 5.9 47.3))"
                ),
                "crs": "EPSG:4326",
            },
        ):
            result = saver.save(str(nc_file))

        assert result is True
        assert check_weather_source_exists("era5") is True

    def test_save_idempotent(self, sqlite_db: None, tmp_path) -> None:
        """Calling save() twice on the same file does not create duplicate rows."""
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "era5_temperature_2m.nc"
        nc_file.write_bytes(b"FAKE")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        meta = {
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": None,
            "crs": "EPSG:4326",
        }
        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=meta,
        ):
            saver.save(str(nc_file))
            saver.save(str(nc_file))

        session = session_local()
        count = session.execute(
            text("SELECT COUNT(*) FROM weather_layers WHERE source_name='era5'")
        ).fetchone()[0]
        session.close()
        assert count == 1

    def test_check_data_exists_delegates(self, sqlite_db: None, tmp_path) -> None:
        """check_data_exists returns True when a matching row exists."""
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "era5"
        saver.data_dir = str(tmp_path)

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=str(tmp_path / "era5_temperature_2m.nc"),
        )

        assert saver.check_data_exists("temperature_2m") is True
        assert saver.check_data_exists("precipitation") is False

    def test_check_data_exists_with_station_ids(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """check_data_exists with station_ids returns True only when the layer
        covers all requested stations.

        A layer registered with ["A", "B"] satisfies queries for ["A"] and
        ["A", "B"] but not ["C"].  A call without station_ids (legacy callers)
        still returns True.
        """
        import json as _json

        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "DWD_stations"
        saver.data_dir = str(tmp_path)

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
                         :valid_from, :valid_until, :uri, 'EPSG:4326', NULL, :metadata)
                    """
                ),
                {
                    "layer_name": "DWD_stations_2024_a_b",
                    "source_name": "DWD_stations",
                    "variable": "temperature_2m",
                    "file_format": "parquet",
                    "valid_from": "2024-07-01T00:00:00",
                    "valid_until": "2024-07-31T23:00:00",
                    "uri": str(tmp_path / "DWD_stations_2024.parquet"),
                    "metadata": _json.dumps(
                        {"file_format": "parquet", "station_ids": ["A", "B"]}
                    ),
                },
            )
            session.commit()
        finally:
            session.close()

        assert saver.check_data_exists("temperature_2m", station_ids=["A"]) is True
        assert saver.check_data_exists("temperature_2m", station_ids=["A", "B"]) is True
        assert saver.check_data_exists("temperature_2m", station_ids=["C"]) is False
        # Legacy call without station_ids preserves True.
        assert saver.check_data_exists("temperature_2m") is True


# ---------------------------------------------------------------------------
# GetterWeather (requires sqlite_db fixture; interpolation is mocked)

# ---------------------------------------------------------------------------


class TestSaverWeatherExplicitVariable:
    """Tests for the explicit variable parameter added to SaverWeather.save()."""

    def test_explicit_variable_stored_in_db(self, sqlite_db: None, tmp_path) -> None:
        """When variable is passed explicitly it is stored as-is in the DB."""
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "era5_tmpXYZabc.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "weather"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:00:00",
                "bbox": None,
                "crs": "EPSG:4326",
                "variables": ["2m_temperature"],
            },
        ):
            result = saver.save(str(nc_file), variable="2m_temperature")

        assert result is True

        session = session_local()
        row = session.execute(
            text("SELECT variable FROM weather_layers WHERE source_name='weather'")
        ).fetchone()
        session.close()
        assert row is not None
        # The stored variable must be the explicit value, not a random stem fragment.
        assert row[0] == "2m_temperature"

    def test_auto_detect_variables_from_netcdf(self, sqlite_db: None, tmp_path) -> None:
        """When variable=None the variables are read from the NC file metadata."""
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "era5_tmpABC.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "weather"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:00:00",
                "bbox": None,
                "crs": "EPSG:4326",
                "variables": ["2m_temperature", "total_precipitation"],
            },
        ):
            result = saver.save(str(nc_file))

        assert result is True

        session = session_local()
        rows = session.execute(
            text("SELECT variable FROM weather_layers WHERE source_name='weather'")
        ).fetchall()
        session.close()
        stored_vars = {r[0] for r in rows}
        # Both variables from the NC file must have their own DB rows.
        assert "2m_temperature" in stored_vars
        assert "total_precipitation" in stored_vars


# ---------------------------------------------------------------------------
# SaverWeather — content-derived destination naming (BUG-07)

# ---------------------------------------------------------------------------


class TestSaverWeatherDestNaming:
    """Tests for the content-derived destination filename logic in SaverWeather.

    Verifies that :meth:`~datavia.weather.saver_weather.SaverWeather.save`
    copies temp files to descriptive names derived from file content rather
    than from the random ``tmp*`` stem (BUG-07 fix, naming responsibility
    moved from downloader to saver).

    All tests use a mocked
    :func:`~datavia.library.formats.extract_netcdf_layer_metadata`
    so no real NetCDF files are required.
    """

    def test_single_variable_nc_uses_source_variable_year(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """Single-variable NC naming includes source, variable, date-range and bbox tag.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "tmpXXXXXX.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "HYRAS"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-12-31T23:59:59",
                "variables": ["tas"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file))

        # The temp file must NOT appear in the data directory.
        assert not (tmp_path / "HYRAS_tmpXXXXXX.nc").exists()
        # The descriptive name must exist instead.
        assert (tmp_path / "HYRAS_tas_202401_202412_nobbox.nc").exists()

    def test_multi_variable_nc_omits_variable_from_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """Multi-variable NC naming omits the variable token from the stem.

        When more than one data variable is present in the file, encoding all
        of them in the filename would be impractically long.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "tmpMULTI.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-06-01T00:00:00",
                "valid_until": "2024-06-30T23:59:59",
                "variables": ["2m_temperature", "total_precipitation"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file))

        assert (tmp_path / "ERA5_land_202406_202406_nobbox.nc").exists()

    def test_register_only_uses_file_stem_as_layer_name(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """register_only=True uses the file stem directly, no double-prefix.

        Files already in the data directory carry the source_name as part of
        their name (e.g. ``HYRAS_tas_2024.nc``).  Prepending source_name
        again would produce ``HYRAS_HYRAS_tas_2024``.  The layer name must
        match what save() would produce for the same file.

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "HYRAS_tas_2024.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "HYRAS"
        saver.data_dir = str(tmp_path)

        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value={
                "valid_from": "2024-01-01T00:00:00",
                "valid_until": "2024-12-31T23:59:59",
                "variables": ["tas"],
                "bbox": None,
                "crs": "EPSG:4326",
            },
        ):
            saver.save(str(nc_file), register_only=True)

        session = session_local()
        row = session.execute(
            text("SELECT layer_name FROM weather_layers WHERE source_name='HYRAS'")
        ).fetchone()
        session.close()
        assert row is not None
        assert row[0] == "HYRAS_tas_2024", (
            f"Expected layer_name='HYRAS_tas_2024', got '{row[0]}'"
        )

    def test_parquet_uses_datetime_year_not_temp_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """A Parquet station file is copied as
        ``{source}_{year_start}_{year_end}_{hash}.parquet``.

        The year range is derived from the ``datetime`` column and the station
        hash from the sorted ``station_id`` list, so the destination name is
        deterministic and human-readable rather than inheriting the random
        ``tmp*`` temp stem.  Two downloads with the same station set in the same
        year produce the same stem; different station sets produce different
        stems (Bug 6 fix).

        Parameters
        ----------
        sqlite_db : None
            pytest fixture; initialises the SQLite test database.
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import hashlib

        import pandas as pd
        from datavia.weather.saver_weather import SaverWeather

        parquet_file = tmp_path / "tmpABCDEF.parquet"
        df = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2023-01-01", "2023-06-15", "2023-12-31"]),
                "station_id": [1, 1, 1],
                "temperature": [0.5, 20.3, 3.1],
            }
        )
        df.to_parquet(str(parquet_file))

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "DWD"
        saver.data_dir = str(tmp_path)

        saver.save(str(parquet_file))

        station_hash = hashlib.md5(
            str([1]).encode(), usedforsecurity=False
        ).hexdigest()[:6]
        expected_name = f"DWD_2023_2023_{station_hash}.parquet"

        assert not (tmp_path / "DWD_tmpABCDEF.parquet").exists()
        assert (tmp_path / expected_name).exists(), (
            f"Expected '{expected_name}' in {tmp_path}, "
            f"found: {list(tmp_path.glob('*.parquet'))}"
        )

    def test_parquet_different_station_sets_produce_unique_stems(
        self, tmp_path
    ) -> None:
        """Two DWD downloads with different station sets
        in the same year get different stems.

        Before the fix, both UC3 (8 German cities) and UC5 (6 Munich-centric
        cities) produced ``DWD_stations_2024`` — the same filename — because
        the stem was derived from the year alone.  UC5's save then silently
        overwrote UC3's cached file.

        This test verifies that the station hash makes the stems structurally
        distinct so that no two downloads with different station sets can
        collide.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        import pandas as pd
        from datavia.weather.saver_weather import _build_dest_stem

        # UC3 — 8 German cities, July 2024
        df_uc3 = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2024-07-17"] * 8),
                "station_id": [101, 102, 103, 104, 105, 106, 107, 108],
                "temperature_2m": [20.0, 19.5, 21.0, 18.0, 22.0, 17.5, 23.0, 16.0],
            }
        )
        parquet_uc3 = str(tmp_path / "uc3.parquet")
        df_uc3.to_parquet(parquet_uc3)

        # UC5 — 4 Munich-centric cities, August 2024
        df_uc5 = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2024-08-05"] * 4),
                "station_id": [201, 202, 203, 204],
                "temperature_2m": [24.0, 25.0, 23.0, 26.0],
            }
        )
        parquet_uc5 = str(tmp_path / "uc5.parquet")
        df_uc5.to_parquet(parquet_uc5)

        stem_uc3 = _build_dest_stem(parquet_uc3, "parquet", "DWD_stations")
        stem_uc5 = _build_dest_stem(parquet_uc5, "parquet", "DWD_stations")

        assert stem_uc3 != stem_uc5, (
            f"Expected different stems for different station sets; "
            f"UC3: '{stem_uc3}', UC5: '{stem_uc5}'"
        )
        # Both stems must include the year so the name is still human-readable.
        assert "2024" in stem_uc3
        assert "2024" in stem_uc5


# ---------------------------------------------------------------------------
# extract_netcdf_layer_metadata — year-end timestamp boundary (BUG-06)

# ---------------------------------------------------------------------------


class TestSaverWeatherDeleteRegistration:
    """Tests for :meth:`SaverWeather.delete_registration`.

    Verifies that rows are removed from ``weather_layers`` by URI, that the
    deletion is scoped to the saver's ``source_name``, and that all rows for
    a given URI (e.g. multi-variable NetCDF) are cleaned up together.
    """

    def test_delete_registration_removes_row(self, sqlite_db: None, tmp_path) -> None:
        """A registered layer is absent from the DB after delete_registration.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory (provides a realistic URI path).
        """
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        uri = str(tmp_path / "ERA5_land_2m_temperature_202401.nc")
        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_2m_temperature_202401",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=uri,
        )

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        saver.delete_registration(uri)

        session = session_local()
        count = session.execute(
            text("SELECT COUNT(*) FROM weather_layers WHERE uri = :uri"),
            {"uri": uri},
        ).fetchone()[0]
        session.close()
        assert count == 0

    def test_delete_registration_no_op_for_unknown_uri(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """delete_registration does not raise when the URI has no matching rows.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        # Must not raise even when no row exists for this URI.
        saver.delete_registration("/nonexistent/path.nc")

    def test_delete_registration_is_source_scoped(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """delete_registration only removes rows for self.source_name.

        A URI registered under two different sources must retain the row for
        the other source after deletion.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        shared_uri = "/data/shared_temperature.nc"
        _insert_weather_layer(
            source_name="ERA5_land",
            layer_name="ERA5_land_shared",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=shared_uri,
        )
        _insert_weather_layer(
            source_name="HYRAS",
            layer_name="HYRAS_shared",
            variable="2m_temperature",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri=shared_uri,
        )

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)
        saver.delete_registration(shared_uri)

        session = session_local()
        remaining_sources = [
            row[0]
            for row in session.execute(
                text("SELECT source_name FROM weather_layers WHERE uri = :uri"),
                {"uri": shared_uri},
            ).fetchall()
        ]
        session.close()

        assert "ERA5_land" not in remaining_sources, (
            "ERA5_land row should have been deleted."
        )
        assert "HYRAS" in remaining_sources, (
            "HYRAS row must not be affected by an ERA5_land deletion."
        )

    def test_delete_registration_removes_all_variable_rows_for_uri(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """All rows for a URI are removed even when a file has multiple variables.

        A multi-variable ERA5 NetCDF is registered once per variable; all
        those rows must be gone after a single delete_registration call.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        uri = "/data/ERA5_land_multi_202401.nc"
        for variable in ("2m_temperature", "total_precipitation"):
            _insert_weather_layer(
                source_name="ERA5_land",
                layer_name=f"ERA5_land_multi_202401_{variable}",
                variable=variable,
                file_format="netcdf",
                valid_from="2024-01-01T00:00:00",
                valid_until="2024-01-31T23:00:00",
                uri=uri,
            )

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)
        saver.delete_registration(uri)

        session = session_local()
        count = session.execute(
            text(
                "SELECT COUNT(*) FROM weather_layers "
                "WHERE source_name = 'ERA5_land' AND uri = :uri"
            ),
            {"uri": uri},
        ).fetchone()[0]
        session.close()
        assert count == 0, f"Expected 0 rows after delete_registration, found {count}."


# ---------------------------------------------------------------------------
# SaverWeather — list_managed_files()

# ---------------------------------------------------------------------------


class TestSaverWeatherListManagedFiles:
    """Tests for :meth:`SaverWeather.list_managed_files`.

    Verifies that the filesystem scan returns only files whose names start
    with ``{source_name}_`` and end with ``.nc`` or ``.parquet``.
    """

    def test_matching_nc_and_parquet_files_returned(self, tmp_path) -> None:
        """Files prefixed with the source name and using .nc/.parquet are returned.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Temporary directory where files are created.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        (tmp_path / "ERA5_land_2m_temperature_202401.nc").touch()
        (tmp_path / "ERA5_land_precipitation_202401.parquet").touch()

        files = saver.list_managed_files()
        names = {f.split("/")[-1] for f in files}
        assert "ERA5_land_2m_temperature_202401.nc" in names
        assert "ERA5_land_precipitation_202401.parquet" in names

    def test_wrong_source_prefix_excluded(self, tmp_path) -> None:
        """Files belonging to a different source are not returned.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        (tmp_path / "ERA5_land_temperature.nc").touch()
        (tmp_path / "HYRAS_temperature.nc").touch()

        files = saver.list_managed_files()
        names = {f.split("/")[-1] for f in files}
        assert "HYRAS_temperature.nc" not in names
        assert "ERA5_land_temperature.nc" in names

    def test_unsupported_extensions_excluded(self, tmp_path) -> None:
        """Files with extensions other than .nc and .parquet are excluded.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        (tmp_path / "ERA5_land_temperature.nc").touch()
        (tmp_path / "ERA5_land_temperature.txt").touch()
        (tmp_path / "ERA5_land_temperature.json").touch()

        files = saver.list_managed_files()
        names = {f.split("/")[-1] for f in files}
        assert "ERA5_land_temperature.nc" in names
        assert "ERA5_land_temperature.txt" not in names
        assert "ERA5_land_temperature.json" not in names

    def test_nonexistent_data_dir_returns_empty_list(self) -> None:
        """Returns an empty list when the data directory does not exist.

        No exception must be raised; the method gracefully handles a missing
        directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = "/nonexistent/path/that/does/not/exist"

        assert saver.list_managed_files() == []

    def test_empty_directory_returns_empty_list(self, tmp_path) -> None:
        """Returns an empty list when data_dir exists but contains no matching files.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Empty temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        assert saver.list_managed_files() == []


# ---------------------------------------------------------------------------
# GetterWeather — get_registered_uris()

# ---------------------------------------------------------------------------


class TestSaverWeatherSaveRegisterOnly:
    """Tests for :meth:`SaverWeather.save` with ``register_only=True``.

    When ``register_only=True`` the file must NOT be copied and the DB row
    must use the original ``data_path`` as ``uri``.
    """

    def test_register_only_does_not_copy_file(self, sqlite_db: None, tmp_path) -> None:
        """No additional file is created in data_dir when register_only=True.

        The original file remains in place; no copy is written to a
        descriptive destination stem.

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory used as both the source location and data_dir.
        """
        from datavia.weather.saver_weather import SaverWeather

        nc_file = tmp_path / "ERA5_land_2m_temperature_202401.nc"
        nc_file.write_bytes(b"FAKE_NC")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        meta = {
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": None,
            "crs": "EPSG:4326",
            "variables": ["2m_temperature"],
        }
        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=meta,
        ):
            result = saver.save(str(nc_file), register_only=True)

        assert result is True
        # Source file must still exist (not moved or deleted).
        assert nc_file.exists(), "Original file must still exist."
        # Only the original file should be in the directory.
        nc_files = list(tmp_path.glob("*.nc"))
        assert len(nc_files) == 1, (
            f"Expected exactly 1 .nc file (the original); found: {nc_files}"
        )

    def test_register_only_inserts_db_row_with_original_uri(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """The DB row uri equals data_path (no copy means no new path).

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory.
        """
        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "ERA5_land_2m_temperature_202401.nc"
        nc_file.write_bytes(b"FAKE")

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        meta = {
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": None,
            "crs": "EPSG:4326",
            "variables": ["2m_temperature"],
        }
        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=meta,
        ):
            saver.save(str(nc_file), register_only=True)

        session = session_local()
        row = session.execute(
            text(
                "SELECT uri FROM weather_layers WHERE source_name = 'ERA5_land' LIMIT 1"
            )
        ).fetchone()
        session.close()

        assert row is not None, "A DB row must be inserted."
        assert row[0] == str(nc_file), f"Expected URI={str(nc_file)!r}, got {row[0]!r}."

    def test_register_only_layer_name_is_file_stem(
        self, sqlite_db: None, tmp_path
    ) -> None:
        """The layer_name stored equals os.path.splitext(os.path.basename(path))[0].

        Parameters
        ----------
        sqlite_db : None
            In-memory SQLite fixture.
        tmp_path : pathlib.Path
            Temporary directory.
        """
        import os

        from datavia.weather.saver_weather import SaverWeather
        from sqlalchemy import text

        from datavia.library.database.connection import session_local

        nc_file = tmp_path / "ERA5_land_2m_temperature_202401.nc"
        nc_file.write_bytes(b"FAKE")
        expected_layer_name = os.path.splitext(os.path.basename(str(nc_file)))[0]

        saver = SaverWeather.__new__(SaverWeather)
        saver.source_name = "ERA5_land"
        saver.data_dir = str(tmp_path)

        meta = {
            "valid_from": "2024-01-01T00:00:00",
            "valid_until": "2024-01-31T23:00:00",
            "bbox": None,
            "crs": "EPSG:4326",
            "variables": ["2m_temperature"],
        }
        with patch(
            "datavia.weather.saver_weather.extract_netcdf_layer_metadata",
            return_value=meta,
        ):
            saver.save(str(nc_file), register_only=True)

        session = session_local()
        row = session.execute(
            text(
                "SELECT layer_name FROM weather_layers "
                "WHERE source_name = 'ERA5_land' LIMIT 1"
            )
        ).fetchone()
        session.close()

        assert row is not None
        assert row[0] == expected_layer_name, (
            f"Expected layer_name={expected_layer_name!r}, got {row[0]!r}."
        )


# ---------------------------------------------------------------------------
# GetterWeather — apply_conversion applied correctly in get_data()
