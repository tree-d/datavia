"""
TiffSaver — Saver interface implementation for TIFF files.

Handles copying TIFF files to the configured data directory and persisting
raster metadata in the SQLite metadata database.  Multi-band TIFFs are also
supported: per-band descriptions are stored in the ``raster_band_metadata``
table.

The bbox extent is stored as a WKT polygon string in EPSG:4326 — no PostGIS
or geometry column is required.
"""

import contextlib
import datetime
import logging
import os
import shutil
from typing import Any

import rasterio
from sqlalchemy import text

from ..config import get_config
from ..library.coordinate_transforms import transform_bbox
from ..library.database.connection import session_local
from ..library.spatial_ops import reproject_tiff
from .interfaces import Saver

logger = logging.getLogger(__name__)


class TiffSaver(Saver):
    """Saver implementation for TIFF files with PostGIS metadata management and multi-band support."""

    def __init__(self, source_name: str):
        """Initialize TiffSaver with data directory and CRS from config.

        Parameters
        ----------
        source_name : str
            Unique identifier for this data source, used as a filename and
            database layer prefix.
        """
        cfg = get_config()
        self.data_dir = str(cfg.data_directory)
        # target_crs is used both for metadata bbox storage and as the
        # destination CRS when reproject=True is passed to save().
        self.target_crs = cfg.default_crs
        self.source_name = source_name

    def save(
        self,
        data_path: str,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Save a TIFF file to the data directory and register it in PostGIS.

        Handles both single-band and multi-band TIFF files. When *reproject*
        is ``True`` the file is reprojected in-place to ``self.target_crs``
        (derived from ``get_config().default_crs``) after being copied to the
        data directory but before metadata is registered in the database.

        Parameters
        ----------
        data_path : str
            Path to the source TIFF file to save.
        reproject : bool, optional
            Reproject the saved file to ``self.target_crs`` before registering
            metadata. Skipped silently when the file is already in the target
            CRS. Defaults to ``False``.
        resolution_m : int, optional
            Target pixel resolution in metres for reprojection. Only applied
            for projected (metric) CRSs; unused for geographic CRSs where
            rasterio derives the resolution automatically. Defaults to
            ``None``.

        Returns
        -------
        bool
            ``True`` if the file was saved (and optionally reprojected)
            and its metadata was registered successfully.
        """
        try:
            # Derive the layer name from the full file stem so that any filename
            # maps to a unique, unambiguous layer: ``clay_0-5cm_mean.tif`` →
            # ``soil_clay_0-5cm_mean``, ``downloaded_file_1234.tif`` →
            # ``elevation_downloaded_file_1234``.
            stem = os.path.splitext(os.path.basename(data_path))[0]
            layer_name = f"{self.source_name}_{stem}"

            # Destination path in data directory
            dest_path = os.path.join(self.data_dir, layer_name + ".tif")

            # Copy file to data directory
            shutil.copy2(data_path, dest_path)
            logger.info("Copied TIFF file to %s", dest_path)

            # Optionally reproject in-place before registering metadata so
            # the database always records the reprojected extent and CRS.
            if reproject:
                reproject_tiff(dest_path, self.target_crs, resolution_m=resolution_m)

            # Import metadata to PostGIS with multi-band support
            self._import_raster_metadata_with_bands(dest_path, layer_name)
            logger.info("Imported metadata for layer %s", layer_name)

            return True

        except Exception as e:
            logger.error("Failed to save TIFF file %s: %s", data_path, e)
            return False

    def check_data_exists(self) -> tuple[set[Any], set[Any], set[Any]]:
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

            session = session_local()

            logger.info(
                f"Checking data existence for source {self.source_name} in {self.data_dir}"
            )
            logger.info(f"Found {len(tiff_files)} TIFF files in data directory.")

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
            return set(), set(), set()

    def sync_files_and_database(self) -> bool:
        """
        Sync TIFF files in data directory with PostGIS metadata records.
        Enhanced to handle multi-band metadata cleanup and source-specific filtering.

        Returns:
            bool: True if sync operation was successful, False if there is nothing to sync or an error occurred
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
        logger.info(
            f"Sync completed. Found: {len(found_files)}, Missing: {len(missing_files)}, New: {len(new_files)}"
        )
        return bool(found_files or new_files)

    def _import_raster_metadata(
        self, filepath: str, layer_name: str, session: Any = None
    ) -> None:
        """Import raster metadata into the raster_layers table.

        The ``bbox`` extent is always stored in EPSG:4326 as a WKT polygon
        string.  Bounds are reprojected via
        :func:`~datavia.library.coordinate_transforms.transform_bbox` when the
        file's native CRS differs from EPSG:4326.  The file's actual CRS is
        recorded separately in the ``crs`` column.

        Parameters
        ----------
        filepath : str
            Absolute path to the TIFF file.
        layer_name : str
            Unique layer identifier to use in the database record.
        session : optional
            Existing SQLAlchemy session to reuse.  A new session is created
            and closed automatically when ``None`` is passed.
        """
        should_close_session = session is None
        if session is None:
            session = session_local()

        try:
            with rasterio.open(filepath) as src:
                bounds = src.bounds
                resolution = src.res
                src_crs = src.crs

                # Always record the file's native CRS in the crs column.
                src_crs_str = src_crs.to_string() if src_crs else "EPSG:4326"

                # The bbox column is fixed to EPSG:4326 by the DB schema.
                # Always transform bounds to that CRS for metadata storage.
                if src_crs_str != "EPSG:4326" and src_crs:
                    bounds_target = transform_bbox(
                        (bounds.left, bounds.bottom, bounds.right, bounds.top),
                        src_crs_str,
                        "EPSG:4326",
                    )
                else:
                    bounds_target = (
                        bounds.left,
                        bounds.bottom,
                        bounds.right,
                        bounds.top,
                    )

                # bbox is stored as WKT text in EPSG:4326.
                left, bottom, right, top = bounds_target
                bbox_wkt = (
                    f"POLYGON(({left} {bottom}, {left} {top}, "
                    f"{right} {top}, {right} {bottom}, {left} {bottom}))"
                )

            existing = session.execute(
                text(
                    "SELECT 1 FROM raster_layers WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            ).fetchone()

            if existing:
                self._delete_layer_metadata(layer_name, session)

            # Insert new metadata — bbox is stored as WKT text (no PostGIS required).
            session.execute(
                text(
                    """
                    INSERT INTO raster_layers
                    (layer_name, source_name, bbox, resolution_x, resolution_y, crs, uri, acquisition_time)
                    VALUES (:layer_name, :source_name, :bbox_wkt,
                            :res_x, :res_y, :crs, :uri, :acq_time)
                """
                ),
                {
                    "layer_name": layer_name,
                    "source_name": self.source_name,
                    "bbox_wkt": bbox_wkt,
                    "res_x": resolution[0],
                    "res_y": resolution[1],
                    "crs": src_crs_str,
                    "uri": filepath,
                    "acq_time": datetime.datetime.now(datetime.UTC).isoformat(),
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
        self, filepath: str, layer_name: str, session: Any = None
    ) -> None:
        """
        Import raster metadata and handle multi-band TIFF files.
        Determines if file is single-band or multi-band and processes accordingly.
        """
        should_close_session = session is None
        if session is None:
            session = session_local()

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

    def _import_multiband_metadata(
        self, filepath: str, layer_name: str, session: Any
    ) -> None:
        """
        Extract and store band metadata for multi-band TIFF files.
        Stores in raster_band_metadata table if available.
        """
        try:
            with rasterio.open(filepath) as src:
                band_count = src.count
                # src_descriptions holds the rasterio per-band description
                # strings (may be None); descriptions is the accumulator of
                # (band_index, description_str) tuples built below.
                src_descriptions = getattr(src, "descriptions", None)
                descriptions: list[tuple[int, str]] = []

                for i in range(1, band_count + 1):
                    src_description: str | None = None

                    # Try rasterio descriptions array first
                    try:
                        if (
                            src_descriptions
                            and len(src_descriptions) >= i
                            and src_descriptions[i - 1]
                        ):
                            src_description = src_descriptions[i - 1]
                    except Exception:  # nosec B110
                        pass

                    # Fallback to get_band_description
                    if not src_description:
                        with contextlib.suppress(Exception):
                            src_description = src.get_band_description(i)

                    # Final fallback: tags or generated name
                    if not src_description:
                        try:
                            tags = src.tags(i)
                            if tags:
                                src_description = ";".join(
                                    f"{k}={v}" for k, v in tags.items()
                                )
                        except Exception:  # nosec B110
                            pass

                    if not src_description:
                        src_description = f"band_{i}"

                    descriptions.append((i, src_description))

            session.execute(
                text(
                    "DELETE FROM raster_band_metadata WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            )

            # Insert new band metadata - using 'band_index' to match schema
            for band_index, band_description in descriptions:
                session.execute(
                    text(
                        """
                        INSERT INTO raster_band_metadata (layer_name, source_name, band_index, description)
                        VALUES (:layer_name, :source_name, :band_index, :description)
                    """
                    ),
                    {
                        "layer_name": layer_name,
                        "source_name": self.source_name,
                        "band_index": band_index,  # Using band_index to match schema
                        "description": band_description,
                    },
                )

            logger.info(
                f"Imported {len(descriptions)} band metadata entries for {layer_name}"
            )

        except Exception as e:
            # Log as debug to not affect main raster import if band metadata table doesn't exist
            logger.debug(f"Could not store band metadata (table might be missing): {e}")

    def _delete_layer_metadata(self, layer_name: str, session: Any = None) -> None:
        """Delete layer metadata from PostGIS."""
        should_close_session = session is None
        if session is None:
            session = session_local()

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

    def _delete_band_metadata(self, layer_name: str, session: Any = None) -> None:
        """Delete band metadata from PostGIS."""
        should_close_session = session is None
        if session is None:
            session = session_local()

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
