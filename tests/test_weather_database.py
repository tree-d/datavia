"""Unit tests for weather database query helpers.

Covers :func:`~datavia.library.database.query.check_weather_source_exists`
and :func:`~datavia.library.database.query.get_weather_paths`.
"""


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


class TestWeatherQueryHelpers:
    """Tests for check_weather_source_exists and get_weather_paths."""

    def test_check_source_empty_db(self, sqlite_db: None) -> None:
        """Returns False when no rows exist for the source."""
        from datavia.library.database.query import check_weather_source_exists

        assert check_weather_source_exists("era5") is False

    def test_check_source_after_insert(self, sqlite_db: None) -> None:
        """Returns True when a matching row exists."""
        from datavia.library.database.query import check_weather_source_exists

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )
        assert check_weather_source_exists("era5") is True
        assert check_weather_source_exists("dwd_stations") is False

    def test_check_source_with_variable_filter(self, sqlite_db: None) -> None:
        """Variable filter narrows the search correctly."""
        from datavia.library.database.query import check_weather_source_exists

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m.nc",
        )
        assert check_weather_source_exists("era5", variable="temperature_2m") is True
        assert check_weather_source_exists("era5", variable="precipitation") is False

    def test_check_source_station_filter(self, sqlite_db: None) -> None:
        """station_ids filter matches only layers that cover all requested stations.

        A layer with stations ["S1", "S2"] satisfies queries for ["S1"],
        ["S1", "S2"], but not ["S3"] or ["S1", "S3"].
        Calling without station_ids preserves the original time-overlap
        behaviour and still returns True.
        """
        import json as _json

        from sqlalchemy import text

        from datavia.library.database.connection import session_local
        from datavia.library.database.query import check_weather_source_exists

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
                    "layer_name": "dwd_2024_s1_s2",
                    "source_name": "DWD_stations",
                    "variable": "temperature_2m",
                    "file_format": "parquet",
                    "valid_from": "2024-07-01T00:00:00",
                    "valid_until": "2024-07-31T23:00:00",
                    "uri": "/data/dwd_2024.parquet",
                    "metadata": _json.dumps(
                        {"file_format": "parquet", "station_ids": ["S1", "S2"]}
                    ),
                },
            )
            session.commit()
        finally:
            session.close()

        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1"],
            )
            is True
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1", "S2"],
            )
            is True
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S3"],
            )
            is False
        )
        assert (
            check_weather_source_exists(
                "DWD_stations",
                variable="temperature_2m",
                station_ids=["S1", "S3"],
            )
            is False
        )
        # Without station_ids the existing check still returns True.
        assert (
            check_weather_source_exists("DWD_stations", variable="temperature_2m")
            is True
        )

    def test_get_weather_paths_overlap(self, sqlite_db: None) -> None:
        """Overlapping layers are returned; non-overlapping layers are excluded."""
        from datavia.library.database.query import get_weather_paths

        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m_jan",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-01-01T00:00:00",
            valid_until="2024-01-31T23:00:00",
            uri="/data/era5_temperature_2m_jan.nc",
        )
        _insert_weather_layer(
            source_name="era5",
            layer_name="era5_temperature_2m_mar",
            variable="temperature_2m",
            file_format="netcdf",
            valid_from="2024-03-01T00:00:00",
            valid_until="2024-03-31T23:00:00",
            uri="/data/era5_temperature_2m_mar.nc",
        )

        # Query for January: only January layer should match.
        paths = get_weather_paths(
            "era5",
            "temperature_2m",
            "2024-01-10T00:00:00",
            "2024-01-20T00:00:00",
        )
        assert len(paths) == 1
        assert "jan" in paths[0]

        # Query spanning February: no match.
        paths_feb = get_weather_paths(
            "era5",
            "temperature_2m",
            "2024-02-01T00:00:00",
            "2024-02-28T00:00:00",
        )
        assert paths_feb == []


# ---------------------------------------------------------------------------
# Raster (non-weather) query helpers — check_source_exists, get_raster_paths,
# get_raster_metadata.  These cover functions absent from the original suite.
# ---------------------------------------------------------------------------


def _insert_raster_layer(
    source_name: str,
    layer_name: str,
    uri: str,
) -> None:
    """Insert a minimal row into the ``raster_layers`` table
    for test setup.

    Parameters
    ----------
    source_name : str
    layer_name : str
    uri : str
        Absolute file path (does not need to exist on disk).
    """
    from sqlalchemy import text

    from datavia.library.database.connection import session_local

    session = session_local()
    try:
        session.execute(
            text(
                """
                INSERT INTO raster_layers
                    (layer_name, source_name, uri, crs)
                VALUES
                    (:layer_name, :source_name, :uri, 'EPSG:4326')
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


class TestCheckSourceExists:
    """Tests for check_source_exists (raster layers table).

    Verifies that the function returns False on an empty database and True
    after a row for the given source has been inserted.
    """

    def test_returns_false_on_empty_database(self, sqlite_db: None) -> None:
        """check_source_exists returns False when no raster rows exist.

        Returns:
            None
        """
        from datavia.library.database.query import check_source_exists

        assert check_source_exists("elevation") is False

    def test_returns_true_after_row_inserted(self, sqlite_db: None) -> None:
        """check_source_exists returns True once a matching row is present.

        Returns:
            None
        """
        from datavia.library.database.query import check_source_exists

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        assert check_source_exists("elevation") is True

    def test_does_not_match_different_source(self, sqlite_db: None) -> None:
        """check_source_exists returns False
        for a source different from the one inserted.

        Returns:
            None
        """
        from datavia.library.database.query import check_source_exists

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        assert check_source_exists("soil") is False


class TestGetRasterPaths:
    """Tests for get_raster_paths (raster layers table).

    Verifies that the function returns a list and that list elements are
    the URIs inserted for a given source name.
    """

    def test_returns_empty_list_for_unknown_source(self, sqlite_db: None) -> None:
        """get_raster_paths returns an empty list when the source is absent.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_paths

        result = get_raster_paths("nonexistent_source")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_returns_uri_after_insert(self, sqlite_db: None) -> None:
        """get_raster_paths returns the correct URI after a row is inserted.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_paths

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        result = get_raster_paths("elevation")
        assert "/data/elevation.tif" in result

    def test_list_elements_are_strings(self, sqlite_db: None) -> None:
        """get_raster_paths returns a list of strings.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_paths

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        result = get_raster_paths("elevation")
        for item in result:
            assert isinstance(item, str)


class TestGetRasterMetadata:
    """Tests for get_raster_metadata (raster layers table).

    Verifies that the function returns a list of dicts with expected keys.
    """

    def test_returns_empty_list_for_unknown_source(self, sqlite_db: None) -> None:
        """get_raster_metadata returns an empty list for an absent source.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_metadata

        result = get_raster_metadata("nonexistent_source")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_returns_list_of_dicts_after_insert(self, sqlite_db: None) -> None:
        """get_raster_metadata returns a list of dicts after a row is inserted.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_metadata

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        result = get_raster_metadata("elevation")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_metadata_dict_contains_uri(self, sqlite_db: None) -> None:
        """Each metadata dict contains the 'uri' key pointing to the inserted path.

        Returns:
            None
        """
        from datavia.library.database.query import get_raster_metadata

        _insert_raster_layer(
            source_name="elevation",
            layer_name="elevation_dem",
            uri="/data/elevation.tif",
        )
        result = get_raster_metadata("elevation")
        assert "uri" in result[0]
        assert result[0]["uri"] == "/data/elevation.tif"


# ---------------------------------------------------------------------------
# SaverWeather (requires sqlite_db fixture and a real temp file)
