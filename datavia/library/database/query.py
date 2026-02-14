"""
Database query functions for raster metadata and file path retrieval.
Part of the Library component - provides database operations for Core components.
"""

import logging

from sqlalchemy import text

from .connection import SessionLocal

logger = logging.getLogger(__name__)


def get_raster_paths(source_name: str) -> list[str]:
    """
    Get file paths for raster layers filtered by source_name.

    Args:
        source_name: Source identifier to filter layers

    Returns:
        List[str]: List of file paths (URIs) for the source
    """
    session = SessionLocal()
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
    session = SessionLocal()
    try:
        result = session.execute(
            text(
                """
                SELECT layer_name, uri, crs, resolution_x, resolution_y, 
                        ST_AsText(bbox) as bbox_wkt, acquisition_time
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
            f"Retrieved metadata for {len(metadata_list)} layers from source: {source_name}"
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
    session = SessionLocal()
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

        band_metadata = {}
        for row in result:
            layer_name = row[0]
            band_info = {"band_index": row[1], "description": row[2]}

            if layer_name not in band_metadata:
                band_metadata[layer_name] = []
            band_metadata[layer_name].append(band_info)

        logger.debug(
            f"Retrieved band metadata for {len(band_metadata)} layers from source: {source_name}"
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
    session = SessionLocal()
    try:
        if source_name:
            result = session.execute(
                text(
                    """
                    SELECT layer_name, uri, crs, resolution_x, resolution_y, 
                            ST_AsText(bbox) as bbox_wkt, acquisition_time, source_name
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
                            ST_AsText(bbox) as bbox_wkt, acquisition_time, source_name
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
    session = SessionLocal()
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
