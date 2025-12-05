"""
TiffSaver - Clean implementation of Saver interface for TIFF files.
Handles saving TIFF files to designated folder and managing metadata in PostGIS.
Enhanced with multi-band TIFF support.
"""

import os
import shutil
import logging
import datetime
from typing import Optional, List, Tuple
import rasterio
from rasterio.warp import transform_bounds
from sqlalchemy import text

from .interfaces import Saver
from ..library.database.connection import SessionLocal
from ..config import get_config

logger = logging.getLogger(__name__)


class TiffSaver(Saver):
    """Saver implementation for TIFF files with PostGIS metadata management and multi-band support."""

    def __init__(self, source_name: str):
        """Initialize TiffSaver with data directory from config."""
        self.data_dir = str(get_config().data_directory)
        self.target_crs = "EPSG:4326"  # Default CRS for metadata storage
        self.source_name = source_name

    def save(self, data_path: str) -> bool:
        """
        Save TIFF file to data directory and import metadata to PostGIS.
        Handles both single-band and multi-band TIFF files.

        Args:
            data_path: Path to the source TIFF file

        Returns:
            bool: True if save operation was successful
        """
        try:
            # Extract layer name from filename
            filename = os.path.basename(data_path)
            layer_name = (
                self.source_name + "_" + os.path.splitext(filename)[0].split("_")[2]
            )

            # Destination path in data directory
            dest_path = os.path.join(self.data_dir, layer_name + ".tif")

            # Copy file to data directory
            shutil.copy2(data_path, dest_path)
            logger.info(f"Copied TIFF file to {dest_path}")

            # Import metadata to PostGIS with multi-band support
            self._import_raster_metadata_with_bands(dest_path, layer_name)
            logger.info(f"Imported metadata for layer {layer_name}")

            return True

        except Exception as e:
            logger.error(f"Failed to save TIFF file {data_path}: {e}")
            return False

    def check_data_exists(self) -> Tuple[set, set, set]:
        """
        Check if a TIFF file with the given layer name exists in the data directory.

        Args:
            layer_name: Name of the layer (filename without extension)
        Returns:
            Tuple[set, set, set]: (found_files, missing_files, new_files)
        """
        try:
            # Get all .tif files in data directory
            tiff_files = {
                os.path.splitext(f)[0]: f
                for f in os.listdir(self.data_dir)
                if f.lower().endswith(".tif") and f.startswith(self.source_name + "_")
            }

            session = SessionLocal()

            try:
                # Get existing layers from database for this source
                db_layers = {
                    row[0]
                    for row in session.execute(
                        text(
                            "SELECT layer_name FROM raster_layers WHERE source_name = :source_name"
                        ),
                        {"source_name": self.source_name},
                    ).fetchall()
                }
                # Check if any layer exists in both file system and database
                missing_files = db_layers - tiff_files.keys()
                new_files = tiff_files.keys() - db_layers
                found_files = db_layers & tiff_files.keys()
                logger.info(
                    f"Checked data existence for source {self.source_name}: "
                    f"{len(found_files)} found, {len(missing_files)} missing, {len(new_files)} new"
                )
                return found_files, missing_files, new_files
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()

        except Exception as e:
            logger.error(
                f"Failed to find files and database info for source {self.source_name}: {e}"
            )
            return False, False, False

    def sync_files_and_database(self):
        """
        Sync TIFF files in data directory with PostGIS metadata records.
        Enhanced to handle multi-band metadata cleanup and source-specific filtering.

        Returns:
            bool: True if sync operation was successful
        """
        found_files, missing_files, new_files = self.check_data_exists()
        for layer_name in missing_files:
            self._delete_layer_metadata(layer_name)
            self._delete_band_metadata(layer_name)
            logger.info(f"Removed metadata for missing file: {layer_name}")
        for layer_name in new_files:
            file_path = os.path.join(
                self.data_dir,
                layer_name + ".tif",
            )
            self._import_raster_metadata_with_bands(file_path, layer_name)
            logger.info(f"Added metadata for new file: {layer_name}")
        return found_files

    def _import_raster_metadata(self, filepath: str, layer_name: str, session=None):
        """Import raster metadata into PostGIS raster_layers table."""
        should_close_session = session is None
        if session is None:
            session = SessionLocal()

        try:
            with rasterio.open(filepath) as src:
                bounds = src.bounds
                resolution = src.res
                src_crs = src.crs

                # Handle CRS conversion
                src_crs_str = src_crs.to_string() if src_crs else self.target_crs

                # Transform bounds to target CRS if needed
                if src_crs_str != self.target_crs and src_crs:
                    bounds_target = transform_bounds(
                        src_crs,
                        self.target_crs,
                        bounds.left,
                        bounds.bottom,
                        bounds.right,
                        bounds.top,
                    )
                else:
                    bounds_target = (
                        bounds.left,
                        bounds.bottom,
                        bounds.right,
                        bounds.top,
                    )

                # Create bbox WKT
                left, bottom, right, top = bounds_target
                bbox_wkt = (
                    f"POLYGON(({left} {bottom}, {left} {top}, "
                    f"{right} {top}, {right} {bottom}, {left} {bottom}))"
                )

                # Get SRID
                try:
                    srid = int(self.target_crs.split(":")[1])
                except:
                    srid = 4326

            existing = session.execute(
                text(
                    "SELECT 1 FROM raster_layers WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            ).fetchone()

            if existing:
                self._delete_layer_metadata(layer_name, session)

            # Insert new metadata with source_name
            session.execute(
                text("""
                    INSERT INTO raster_layers 
                    (layer_name, source_name, bbox, resolution_x, resolution_y, crs, uri, acquisition_time)
                    VALUES (:layer_name, :source_name, ST_GeomFromText(:bbox_wkt, :srid), 
                            :res_x, :res_y, :crs, :uri, :acq_time)
                """),
                {
                    "layer_name": layer_name,
                    "source_name": self.source_name,
                    "bbox_wkt": bbox_wkt,
                    "srid": srid,
                    "res_x": resolution[0],
                    "res_y": resolution[1],
                    "crs": src_crs_str,
                    "uri": filepath,
                    "acq_time": datetime.datetime.now(datetime.timezone.utc),
                },
            )

            if should_close_session:
                session.commit()

        except Exception as e:
            if should_close_session:
                session.rollback()
            raise e
        finally:
            if should_close_session:
                session.close()

    def _import_raster_metadata_with_bands(
        self, filepath: str, layer_name: str, session=None
    ):
        """
        Import raster metadata and handle multi-band TIFF files.
        Determines if file is single-band or multi-band and processes accordingly.
        """
        should_close_session = session is None
        if session is None:
            session = SessionLocal()

        try:
            # Check band count
            with rasterio.open(filepath) as src:
                band_count = src.count

            # Import main raster metadata
            self._import_raster_metadata(filepath, layer_name, session)

            # If multi-band, import per-band metadata
            if band_count > 1:
                logger.info(
                    f"Detected multi-band TIFF ({band_count} bands): {filepath}"
                )
                try:
                    self._import_multiband_metadata(filepath, layer_name, session)
                except Exception as e:
                    logger.warning(
                        f"Could not import per-band metadata for {layer_name}: {e}"
                    )

            if should_close_session:
                session.commit()

        except Exception as e:
            if should_close_session:
                session.rollback()
            raise e
        finally:
            if should_close_session:
                session.close()

    def _import_multiband_metadata(self, filepath: str, layer_name: str, session):
        """
        Extract and store band metadata for multi-band TIFF files.
        Stores in raster_band_metadata table if available.
        """
        try:
            with rasterio.open(filepath) as src:
                band_count = src.count
                descriptions: List[Tuple[int, str]] = []
                descs = getattr(src, "descriptions", None)

                for i in range(1, band_count + 1):
                    desc = None

                    # Try descriptions array first
                    try:
                        if descs and len(descs) >= i and descs[i - 1]:
                            desc = descs[i - 1]
                    except Exception:
                        pass

                    # Fallback to get_band_description
                    if not desc:
                        try:
                            desc = src.get_band_description(i)
                        except Exception:
                            pass

                    # Final fallback: tags or generated name
                    if not desc:
                        try:
                            tags = src.tags(i)
                            if tags:
                                desc = ";".join(f"{k}={v}" for k, v in tags.items())
                        except Exception:
                            pass

                    if not desc:
                        desc = f"band_{i}"

                    descriptions.append((i, desc))

            session.execute(
                text(
                    "DELETE FROM raster_band_metadata WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            )

            # Insert new band metadata - using 'band_index' to match schema
            for band_index, band_desc in descriptions:
                session.execute(
                    text("""
                        INSERT INTO raster_band_metadata (layer_name, source_name, band_index, description)
                        VALUES (:layer_name, :source_name, :band_index, :description)
                    """),
                    {
                        "layer_name": layer_name,
                        "source_name": self.source_name,
                        "band_index": band_index,  # Using band_index to match schema
                        "description": band_desc,
                    },
                )

            logger.info(
                f"Imported {len(descriptions)} band metadata entries for {layer_name}"
            )

        except Exception as e:
            # Log as debug to not affect main raster import if band metadata table doesn't exist
            logger.debug(f"Could not store band metadata (table might be missing): {e}")

    def _delete_layer_metadata(self, layer_name: str, session=None):
        """Delete layer metadata from PostGIS."""
        should_close_session = session is None
        if session is None:
            session = SessionLocal()

        try:
            session.execute(
                text("DELETE FROM raster_layers WHERE layer_name = :layer_name"),
                {"layer_name": layer_name},
            )

            if should_close_session:
                session.commit()

        except Exception as e:
            if should_close_session:
                session.rollback()
            raise e
        finally:
            if should_close_session:
                session.close()

    def _delete_band_metadata(self, layer_name: str, session=None):
        """Delete band metadata from PostGIS."""
        should_close_session = session is None
        if session is None:
            session = SessionLocal()

        try:
            session.execute(
                text(
                    "DELETE FROM raster_band_metadata WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            )

            if should_close_session:
                session.commit()

        except Exception as e:
            if should_close_session:
                session.rollback()
            # Don't raise exception if table doesn't exist
            logger.debug(f"Could not delete band metadata for {layer_name}: {e}")
        finally:
            if should_close_session:
                session.close()
