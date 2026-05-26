"""
Database query functions for raster metadata and file path retrieval.

Part of the Library component — provides database operations for Core
components.  Uses plain SQL compatible with both SQLite and PostgreSQL:
bbox values are stored and returned as WKT text strings.
"""

import logging
from typing import Any

from sqlalchemy import text

from .connection import session_local

logger = logging.getLogger(__name__)


def get_raster_paths(source_name: str) -> list[str]:
    """
    Get file paths for raster layers filtered by source_name.

    Args:
        source_name: Source identifier to filter layers

    Returns:
        List[str]: List of file paths (URIs) for the source
    """
    session = session_local()
    try:
        result = session.execute(
            text("SELECT uri FROM raster_layers WHERE source_name = :source_name"),
            {"source_name": source_name},
        ).fetchall()

        paths = [row[0] for row in result if row[0]]
        logger.debug(f"Found {len(paths)} raster paths for source: {source_name}")
        return paths

    except Exception as e:
        logger.error(f"Failed to query raster paths for {source_name}: {e}")
        return []
    finally:
        session.close()


def get_raster_metadata(source_name: str) -> list[dict]:
    """
    Get complete metadata for raster layers filtered by source_name.

    Args:
        source_name: Source identifier to filter layers

    Returns:
        List[Dict]: List of metadata dictionaries with layer information
    """
    session = session_local()
    try:
        result = session.execute(
            text(
                """
                SELECT layer_name, uri, crs, resolution_x, resolution_y,
                        bbox as bbox_wkt, acquisition_time
                FROM raster_layers
                WHERE source_name = :source_name
                ORDER BY layer_name
            """
            ),
            {"source_name": source_name},
        ).fetchall()

        metadata_list = []
        for row in result:
            metadata = {
                "layer_name": row[0],
                "uri": row[1],
                "crs": row[2],
                "resolution_x": row[3],
                "resolution_y": row[4],
                "bbox_wkt": row[5],
                "acquisition_time": row[6],
            }
            metadata_list.append(metadata)

        logger.debug(
            "Retrieved metadata for %d layers from source: %s",
            len(metadata_list),
            source_name,
        )
        return metadata_list

    except Exception as e:
        logger.error(f"Failed to query raster metadata for {source_name}: {e}")
        return []
    finally:
        session.close()


def get_band_metadata(source_name: str) -> dict[str, list[dict]]:
    """
    Get band metadata for multi-band rasters filtered by source_name.

    Args:
        source_name: Source identifier to filter layers

    Returns:
        Dict[str, List[Dict]]: Dictionary mapping layer_name to list of band metadata
    """
    session = session_local()
    try:
        result = session.execute(
            text(
                """
                SELECT layer_name, band_index, description
                FROM raster_band_metadata
                WHERE source_name = :source_name
                ORDER BY layer_name, band_index
            """
            ),
            {"source_name": source_name},
        ).fetchall()

        band_metadata: dict[str, list[dict[str, Any]]] = {}
        for row in result:
            layer_name = row[0]
            band_info = {"band_index": row[1], "description": row[2]}

            if layer_name not in band_metadata:
                band_metadata[layer_name] = []
            band_metadata[layer_name].append(band_info)

        logger.debug(
            "Retrieved band metadata for %d layers from source: %s",
            len(band_metadata),
            source_name,
        )
        return band_metadata

    except Exception as e:
        logger.debug(f"No band metadata available for {source_name}: {e}")
        return {}
    finally:
        session.close()


def get_layer_by_name(layer_name: str, source_name: str | None = None) -> dict | None:
    """
    Get metadata for a specific layer, optionally filtered by source.

    Args:
        layer_name: Name of the layer to retrieve
        source_name: Optional source filter

    Returns:
        Optional[Dict]: Layer metadata dictionary or None if not found
    """
    session = session_local()
    try:
        if source_name:
            result = session.execute(
                text(
                    """
                    SELECT layer_name, uri, crs, resolution_x, resolution_y,
                            bbox as bbox_wkt, acquisition_time, source_name
                    FROM raster_layers
                    WHERE layer_name = :layer_name AND source_name = :source_name
                """
                ),
                {"layer_name": layer_name, "source_name": source_name},
            ).fetchone()
        else:
            result = session.execute(
                text(
                    """
                    SELECT layer_name, uri, crs, resolution_x, resolution_y,
                            bbox as bbox_wkt, acquisition_time, source_name
                    FROM raster_layers
                    WHERE layer_name = :layer_name
                """
                ),
                {"layer_name": layer_name},
            ).fetchone()

        if result:
            metadata = {
                "layer_name": result[0],
                "uri": result[1],
                "crs": result[2],
                "resolution_x": result[3],
                "resolution_y": result[4],
                "bbox_wkt": result[5],
                "acquisition_time": result[6],
                "source_name": result[7],
            }
            logger.debug(f"Found layer metadata for: {layer_name}")
            return metadata
        else:
            logger.debug(f"No layer found with name: {layer_name}")
            return None

    except Exception as e:
        logger.error(f"Failed to query layer {layer_name}: {e}")
        return None
    finally:
        session.close()


def check_source_exists(source_name: str) -> bool:
    """
    Check if any raster layers exist for the given source_name.

    Args:
        source_name: Source identifier to check

    Returns:
        bool: True if source has raster layers, False otherwise
    """
    session = session_local()
    try:
        result = session.execute(
            text("SELECT COUNT(*) FROM raster_layers WHERE source_name = :source_name"),
            {"source_name": source_name},
        ).fetchone()

        count = result[0] if result else 0
        exists = count > 0
        logger.debug(f"Source '{source_name}' exists: {exists} ({count} layers)")
        return exists

    except Exception as e:
        logger.error(f"Failed to check source existence for {source_name}: {e}")
        return False
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Weather-specific queries
# ---------------------------------------------------------------------------


def get_weather_paths(
    source_name: str,
    variable: str,
    from_dt: str,
    to_dt: str,
) -> list[str]:
    """Return file URIs for weather layers that overlap a requested time window.

    A layer is considered relevant when its stored time window overlaps the
    requested range, i.e. ``valid_from <= to_dt AND valid_until >= from_dt``.
    Both boundary comparisons rely on ISO-8601 lexicographic ordering, which
    is valid for both SQLite (text) and PostgreSQL (text / timestamptz).

    Args:
        source_name: Source identifier, e.g. ``"era5"`` or ``"dwd_stations"``.
        variable: Variable name, e.g. ``"temperature_2m"``.
        from_dt: Start of the requested time window (ISO-8601 datetime string).
        to_dt: End of the requested time window (ISO-8601 datetime string).

    Returns:
        List of absolute file paths (URIs) for matching weather layers.
        Returns an empty list when no matching layers are found.
    """
    session = session_local()
    try:
        result = session.execute(
            text(
                """
                SELECT uri FROM weather_layers
                WHERE source_name = :source_name
                  AND variable    = :variable
                  AND valid_from  <= :to_dt
                  AND valid_until >= :from_dt
                ORDER BY valid_from
                """
            ),
            {
                "source_name": source_name,
                "variable": variable,
                "from_dt": from_dt,
                "to_dt": to_dt,
            },
        ).fetchall()

        paths = [row[0] for row in result if row[0]]
        logger.debug(
            "Found %d weather paths for %s/%s in [%s, %s]",
            len(paths),
            source_name,
            variable,
            from_dt,
            to_dt,
        )
        return paths

    except Exception as e:
        logger.error(
            "Failed to query weather paths for %s/%s: %s", source_name, variable, e
        )
        return []
    finally:
        session.close()


def get_weather_metadata(source_name: str, variable: str | None = None) -> list[dict]:
    """Return metadata dicts for weather layers filtered by source and variable.

    Args:
        source_name: Source identifier to filter layers.
        variable: Optional variable filter. When ``None`` all variables for the
            source are returned.

    Returns:
        List of metadata dictionaries, each containing
        ``layer_name``, ``variable``, ``file_format``, ``valid_from``,
        ``valid_until``, ``uri``, ``crs``, ``bbox``, ``acquisition_time``,
        and ``metadata`` (raw JSON string).
        Returns an empty list when no matching layers exist.
    """
    session = session_local()
    try:
        if variable is not None:
            result = session.execute(
                text(
                    """
                    SELECT layer_name, variable, file_format,
                           valid_from, valid_until, uri,
                           crs, bbox, acquisition_time, metadata
                    FROM weather_layers
                    WHERE source_name = :source_name
                      AND variable    = :variable
                    ORDER BY variable, valid_from
                    """
                ),
                {"source_name": source_name, "variable": variable},
            ).fetchall()
        else:
            result = session.execute(
                text(
                    """
                    SELECT layer_name, variable, file_format,
                           valid_from, valid_until, uri,
                           crs, bbox, acquisition_time, metadata
                    FROM weather_layers
                    WHERE source_name = :source_name
                    ORDER BY variable, valid_from
                    """
                ),
                {"source_name": source_name},
            ).fetchall()

        metadata_list = [
            {
                "layer_name": row[0],
                "variable": row[1],
                "file_format": row[2],
                "valid_from": row[3],
                "valid_until": row[4],
                "uri": row[5],
                "crs": row[6],
                "bbox": row[7],
                "acquisition_time": row[8],
                "metadata": row[9],
            }
            for row in result
        ]
        logger.debug(
            "Retrieved %d weather metadata entries for source %s",
            len(metadata_list),
            source_name,
        )
        return metadata_list

    except Exception as e:
        logger.error("Failed to query weather metadata for %s: %s", source_name, e)
        return []
    finally:
        session.close()


def check_weather_source_exists(
    source_name: str,
    variable: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
) -> bool:
    """Check whether weather layers exist for a given source and optional filters.

    When *from_dt* and *to_dt* are supplied the check is narrowed to layers
    whose time window overlaps the requested range (same logic as
    :func:`get_weather_paths`).

    Args:
        source_name: Source identifier to check.
        variable: Optional variable name filter.
        from_dt: Optional start of time window (ISO-8601 datetime string).
        to_dt: Optional end of time window (ISO-8601 datetime string).

    Returns:
        ``True`` if at least one matching weather layer exists, ``False``
        otherwise.
    """
    session = session_local()
    try:
        sql = "SELECT COUNT(*) FROM weather_layers WHERE source_name = :source_name"
        params: dict[str, str] = {"source_name": source_name}

        if variable is not None:
            sql += " AND variable = :variable"
            params["variable"] = variable

        if from_dt is not None and to_dt is not None:
            sql += " AND valid_from <= :to_dt AND valid_until >= :from_dt"
            params["from_dt"] = from_dt
            params["to_dt"] = to_dt

        result = session.execute(text(sql), params).fetchone()
        count = result[0] if result else 0
        exists = count > 0
        logger.debug(
            "Weather source '%s' (variable=%s) exists: %s (%d layers)",
            source_name,
            variable,
            exists,
            count,
        )
        return exists

    except Exception as e:
        logger.error(
            "Failed to check weather source existence for %s: %s", source_name, e
        )
        return False
    finally:
        session.close()
