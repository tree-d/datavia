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
    """Saver for TIFF files with SQLite metadata and multi-band support."""

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
        register_only: bool = False,
    ) -> bool:
        """Save a TIFF file and register it in the metadata database.

        Handles both single-band and multi-band TIFF files. When *reproject*
        is ``True`` the file is reprojected in-place to ``self.target_crs``
        (derived from ``get_config().default_crs``) after being copied to the
        data directory but before metadata is registered in the database.

        When *register_only* is ``True`` the file-copy step is skipped and
        only the database registration is performed.  Use this when the file
        is already in the data directory, e.g. when re-registering an orphan
        file discovered by
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`.

        Parameters
        ----------
        data_path : str
            Path to the source TIFF file to save.  When *register_only* is
            ``True`` this must already be the absolute path inside the data
            directory.
        reproject : bool, optional
            Reproject the saved file to ``self.target_crs`` before registering
            metadata. Skipped silently when the file is already in the target
            CRS. Ignored when *register_only* is ``True``. Defaults to
            ``False``.
        resolution_m : int, optional
            Target pixel resolution in metres for reprojection. Only applied
            for projected (metric) CRSs; unused for geographic CRSs where
            rasterio derives the resolution automatically. Defaults to
            ``None``.
        register_only : bool, optional
            When ``True`` skip the file-copy step and only register metadata.
            Defaults to ``False``.

        Returns
        -------
        bool
            ``True`` if the operation completed successfully.
        """
        try:
            stem = os.path.splitext(os.path.basename(data_path))[0]
            layer_name = f"{self.source_name}_{stem}"

            if register_only:
                dest_path = data_path
            else:
                dest_path = os.path.join(self.data_dir, layer_name + ".tif")
                shutil.copy2(data_path, dest_path)
                logger.info("Copied TIFF file to %s", dest_path)
                if reproject:
                    reproject_tiff(
                        dest_path, self.target_crs, resolution_m=resolution_m
                    )

            # Import metadata to the SQLite database with multi-band support
            self._import_raster_metadata_with_bands(dest_path, layer_name)
            logger.info("Imported metadata for layer %s", layer_name)

            return True

        except Exception as e:
            logger.error("Failed to save TIFF file %s: %s", data_path, e)
            return False

    def list_managed_files(self) -> list[str]:
        """Return absolute paths of all TIFF files written by this saver.

        Scans the data directory for files matching
        ``<source_name>_*.tif``.  This is a pure filesystem operation and
        must not access the database.

        Returns
        -------
        list[str]
            Absolute paths to every matching ``.tif`` file currently on
            disk.  Returns an empty list when no files are found or the
            data directory does not exist.
        """
        result: list[str] = []
        try:
            for fname in os.listdir(self.data_dir):
                if fname.lower().endswith(".tif") and fname.startswith(
                    self.source_name + "_"
                ):
                    result.append(os.path.join(self.data_dir, fname))
        except FileNotFoundError:
            logger.warning("Data directory not found: %s", self.data_dir)
        return result

    def delete_registration(self, uri: str) -> None:
        """Remove all database registrations for the given file URI.

        Derives the ``layer_name`` from the file path and deletes the
        corresponding rows from both ``raster_layers`` and
        ``raster_band_metadata``.  The file itself is not touched.

        Parameters
        ----------
        uri : str
            Absolute path to the TIFF file whose registrations should be
            removed.
        """
        stem = os.path.splitext(os.path.basename(uri))[0]
        layer_name = (
            f"{self.source_name}_{stem}"
            if not stem.startswith(self.source_name + "_")
            else stem
        )
        self._delete_layer_metadata(layer_name)
        self._delete_band_metadata(layer_name)
        logger.info("Removed database registration for URI: %s", uri)

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

                src_crs_str = src_crs.to_string() if src_crs else "EPSG:4326"

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
                    "SELECT 1 FROM raster_layers "
                    "WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            ).fetchone()

            if existing:
                self._delete_layer_metadata(layer_name, session)

            session.execute(
                text(
                    """
                    INSERT INTO raster_layers
                    (
                        layer_name, source_name, bbox, resolution_x,
                        resolution_y, crs, uri, acquisition_time
                    )
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
        """Import raster metadata and per-band descriptions for multi-band TIFFs."""
        should_close_session = session is None
        if session is None:
            session = session_local()

        try:
            with rasterio.open(filepath) as src:
                band_count = src.count

            self._import_raster_metadata(filepath, layer_name, session)

            if band_count > 1:
                logger.info(
                    "Detected multi-band TIFF (%d bands): %s", band_count, filepath
                )
                try:
                    self._import_multiband_metadata(filepath, layer_name, session)
                except Exception as e:
                    logger.warning(
                        "Could not import per-band metadata for %s: %s", layer_name, e
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
        """Extract and store per-band descriptions in ``raster_band_metadata``."""
        try:
            with rasterio.open(filepath) as src:
                band_count = src.count
                src_descriptions = getattr(src, "descriptions", None)
                descriptions: list[tuple[int, str]] = []

                for i in range(1, band_count + 1):
                    src_description: str | None = None

                    try:
                        if (
                            src_descriptions
                            and len(src_descriptions) >= i
                            and src_descriptions[i - 1]
                        ):
                            src_description = src_descriptions[i - 1]
                    except Exception:  # nosec B110
                        pass

                    if not src_description:
                        with contextlib.suppress(Exception):
                            src_description = src.get_band_description(i)

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
                    "DELETE FROM raster_band_metadata "
                    "WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            )

            for band_index, band_description in descriptions:
                session.execute(
                    text(
                        """
                        INSERT INTO raster_band_metadata (
                            layer_name, source_name, band_index, description
                        )
                        VALUES (:layer_name, :source_name, :band_index, :description)
                    """
                    ),
                    {
                        "layer_name": layer_name,
                        "source_name": self.source_name,
                        "band_index": band_index,
                        "description": band_description,
                    },
                )

            logger.info(
                "Imported %d band metadata entries for %s",
                len(descriptions),
                layer_name,
            )

        except Exception as e:
            logger.debug("Could not store band metadata for %s: %s", layer_name, e)

    def _delete_layer_metadata(self, layer_name: str, session: Any = None) -> None:
        """Delete layer metadata from the database."""
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
        """Delete band metadata from the database."""
        should_close_session = session is None
        if session is None:
            session = session_local()

        try:
            session.execute(
                text(
                    "DELETE FROM raster_band_metadata "
                    "WHERE layer_name = :layer_name AND source_name = :source_name"
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
