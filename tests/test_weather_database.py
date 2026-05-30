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
# SaverWeather (requires sqlite_db fixture and a real temp file)
