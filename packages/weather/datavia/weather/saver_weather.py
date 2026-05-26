"""
SaverWeather — Saver implementation for NetCDF and Parquet weather files.

Copies downloaded files to the configured data directory, extracts temporal
and spatial metadata, and registers each file as a row in the
``weather_layers`` SQLite table.  The orphan-removal sync follows the same
pattern as :class:`datavia.core.saver_tiff.TiffSaver`.

File format is inferred automatically from the file extension:

- ``.nc``      → ``"netcdf"``
- ``.parquet`` → ``"parquet"``
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil

from sqlalchemy import text

from datavia.config import get_config
from datavia.core.interfaces import Saver
from datavia.library.database.connection import session_local
from datavia.library.database.query import check_weather_source_exists
from datavia.library.formats import extract_netcdf_layer_metadata

logger = logging.getLogger(__name__)


class SaverWeather(Saver):
    """Save NetCDF or Parquet weather files and register them in the database.

    Each call to :meth:`save` copies a single file to the data directory,
    reads its temporal bounds and spatial footprint, and inserts a row into
    the ``weather_layers`` table.  The layer name is derived from the source
    name and the file stem so that multiple files per variable are supported
    (e.g. ERA5 downloaded in monthly chunks).

    Parameters
    ----------
    source_name : str
        Unique identifier for the data source, e.g. ``"era5"`` or
        ``"dwd_stations"``.
    """

    def __init__(self, source_name: str) -> None:
        """Initialise with data directory and source name from config.

        Parameters
        ----------
        source_name : str
            Unique identifier for this data source.
        """
        cfg = get_config()
        self.data_dir: str = str(cfg.data_directory)
        self.source_name: str = source_name

    # ------------------------------------------------------------------
    # Saver interface
    # ------------------------------------------------------------------

    def save(
        self,
        data_path: str,
        reproject: bool = False,
        resolution_m: int | None = None,
    ) -> bool:
        """Copy a weather file to the data directory and register it in the DB.

        The file format is inferred from the extension (``.nc`` → netcdf,
        ``.parquet`` → parquet).  Temporal metadata is extracted from the
        file content:

        - **NetCDF**: reads ``valid_from`` / ``valid_until`` from the ``time``
          dimension via
          :func:`datavia.library.formats.extract_netcdf_layer_metadata`.
        - **Parquet**: infers ``valid_from`` / ``valid_until`` from the
          ``datetime`` column.

        The *reproject* and *resolution_m* parameters are accepted for
        interface compatibility but are not applied to weather files (NetCDF
        and Parquet carry their own coordinate information).

        Parameters
        ----------
        data_path : str
            Absolute path to the downloaded file to save.
        reproject : bool, optional
            Ignored for weather files; present for :class:`~datavia.core.interfaces.Saver`
            interface compatibility. Defaults to ``False``.
        resolution_m : int, optional
            Ignored for weather files. Defaults to ``None``.

        Returns
        -------
        bool
            ``True`` when the file was copied and the DB row was inserted
            successfully, ``False`` on any error.
        """
        try:
            stem = os.path.splitext(os.path.basename(data_path))[0]
            ext = os.path.splitext(data_path)[1].lower()
            file_format = _infer_file_format(ext)
            layer_name = f"{self.source_name}_{stem}"
            dest_path = os.path.join(self.data_dir, f"{layer_name}{ext}")

            shutil.copy2(data_path, dest_path)
            logger.info("Copied weather file to %s", dest_path)

            # Extract variable name from stem: "era5_temperature_2m_2024" → "temperature_2m"
            # Convention: <source_name>_<variable>[_<date_suffix>]
            # The variable is the stem portion after the source_name prefix.
            variable = _extract_variable_from_stem(stem, self.source_name)

            # Extract temporal / spatial metadata.
            valid_from, valid_until, bbox, crs = _read_temporal_metadata(
                dest_path, file_format, variable
            )

            self._insert_weather_layer(
                layer_name=layer_name,
                variable=variable,
                file_format=file_format,
                valid_from=valid_from,
                valid_until=valid_until,
                uri=dest_path,
                bbox=bbox,
                crs=crs,
            )
            logger.info("Registered weather layer '%s' in database.", layer_name)
            return True

        except Exception as exc:
            logger.error("Failed to save weather file %s: %s", data_path, exc)
            return False

    def check_data_exists(
        self,
        variable: str,
        from_dt: str | None = None,
        to_dt: str | None = None,
    ) -> bool:
        """Check whether data for a variable exists in the configured time window.

        Parameters
        ----------
        variable : str
            Variable name to check, e.g. ``"temperature_2m"``.
        from_dt : str, optional
            Start of the time window (ISO-8601 datetime string).
        to_dt : str, optional
            End of the time window (ISO-8601 datetime string).

        Returns
        -------
        bool
            ``True`` when at least one weather layer exists for the requested
            variable and time range.
        """
        return check_weather_source_exists(
            source_name=self.source_name,
            variable=variable,
            from_dt=from_dt,
            to_dt=to_dt,
        )

    def sync_files_and_database(self) -> bool:
        """Reconcile on-disk weather files with database records.

        Scans the data directory for files matching
        ``<source_name>_*.{nc,parquet}`` and performs two-way cleanup:

        1. **Orphan DB rows** — rows whose URI points to a missing file are
           deleted from ``weather_layers``.
        2. **Orphan files** — files on disk without a corresponding DB row
           are re-registered by calling :meth:`save` on each.

        Returns
        -------
        bool
            ``True`` when synchronisation completed without errors,
            ``False`` when an error prevented full reconciliation.
        """
        try:
            disk_files = self._list_weather_files()
            db_rows = self._get_all_db_rows()

            db_uris: set[str] = {row["uri"] for row in db_rows}
            disk_uris: set[str] = set(disk_files)

            # Remove DB rows pointing to missing files.
            orphan_db = db_uris - disk_uris
            if orphan_db:
                logger.info(
                    "Removing %d orphan DB row(s) for source '%s'",
                    len(orphan_db),
                    self.source_name,
                )
                self._delete_db_rows_by_uri(orphan_db)

            # Re-register files that exist on disk but not in the DB.
            orphan_disk = disk_uris - db_uris
            for orphan_path in sorted(orphan_disk):
                logger.info("Re-registering orphan file: %s", orphan_path)
                self.save(orphan_path)

            logger.info(
                "sync_files_and_database complete for source '%s': "
                "%d orphan DB rows removed, %d orphan files re-registered.",
                self.source_name,
                len(orphan_db),
                len(orphan_disk),
            )
            return True

        except Exception as exc:
            logger.error(
                "sync_files_and_database failed for source '%s': %s",
                self.source_name,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_weather_layer(
        self,
        layer_name: str,
        variable: str,
        file_format: str,
        valid_from: str | None,
        valid_until: str | None,
        uri: str,
        bbox: str | None,
        crs: str | None,
    ) -> None:
        """Insert or replace a row in weather_layers for the given file.

        An existing row with the same ``layer_name`` and ``source_name`` is
        deleted before insertion so that re-running save on the same file is
        idempotent.

        Parameters
        ----------
        layer_name : str
            Unique layer identifier, e.g. ``"era5_temperature_2m_2024"``.
        variable : str
            Variable name, e.g. ``"temperature_2m"``.
        file_format : str
            Either ``"netcdf"`` or ``"parquet"``.
        valid_from : str or None
            ISO-8601 start of the time dimension.
        valid_until : str or None
            ISO-8601 end of the time dimension.
        uri : str
            Absolute path to the file on disk.
        bbox : str or None
            WKT POLYGON bounding box in EPSG:4326.
        crs : str or None
            CRS string, e.g. ``"EPSG:4326"``.
        """
        acquisition_time = datetime.datetime.now(datetime.UTC).isoformat()

        session = session_local()
        try:
            # Remove any pre-existing row for this layer so the insert is
            # idempotent (e.g. when the same file is re-saved after a sync).
            session.execute(
                text(
                    "DELETE FROM weather_layers "
                    "WHERE layer_name = :layer_name AND source_name = :source_name"
                ),
                {"layer_name": layer_name, "source_name": self.source_name},
            )
            session.execute(
                text(
                    """
                    INSERT INTO weather_layers
                        (layer_name, source_name, variable, file_format,
                         valid_from, valid_until, uri,
                         acquisition_time, bbox, crs, metadata)
                    VALUES
                        (:layer_name, :source_name, :variable, :file_format,
                         :valid_from, :valid_until, :uri,
                         :acquisition_time, :bbox, :crs, :metadata)
                    """
                ),
                {
                    "layer_name": layer_name,
                    "source_name": self.source_name,
                    "variable": variable,
                    "file_format": file_format,
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "uri": uri,
                    "acquisition_time": acquisition_time,
                    "bbox": bbox,
                    "crs": crs,
                    "metadata": json.dumps({"file_format": file_format}),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _list_weather_files(self) -> list[str]:
        """Return absolute paths of all weather files for this source in data_dir.

        Returns
        -------
        list[str]
            Absolute paths to ``<source_name>_*.nc`` and
            ``<source_name>_*.parquet`` files.
        """
        prefix = f"{self.source_name}_"
        result: list[str] = []
        try:
            for fname in os.listdir(self.data_dir):
                if fname.startswith(prefix) and (
                    fname.endswith(".nc") or fname.endswith(".parquet")
                ):
                    result.append(os.path.join(self.data_dir, fname))
        except FileNotFoundError:
            logger.warning("Data directory not found: %s", self.data_dir)
        return result

    def _get_all_db_rows(self) -> list[dict[str, str]]:
        """Return all weather_layers rows for this source as plain dicts.

        Returns
        -------
        list[dict[str, str]]
            Each dict contains at least ``layer_name`` and ``uri``.
        """
        session = session_local()
        try:
            rows = session.execute(
                text(
                    "SELECT layer_name, uri FROM weather_layers "
                    "WHERE source_name = :source_name"
                ),
                {"source_name": self.source_name},
            ).fetchall()
            return [{"layer_name": row[0], "uri": row[1]} for row in rows]
        except Exception as exc:
            logger.error(
                "Failed to query weather_layers for source '%s': %s",
                self.source_name,
                exc,
            )
            return []
        finally:
            session.close()

    def _delete_db_rows_by_uri(self, uris: set[str]) -> None:
        """Delete weather_layers rows whose URI matches any value in *uris*.

        Parameters
        ----------
        uris : set[str]
            Set of absolute file paths to remove from the database.
        """
        session = session_local()
        try:
            for uri in uris:
                session.execute(
                    text(
                        "DELETE FROM weather_layers "
                        "WHERE source_name = :source_name AND uri = :uri"
                    ),
                    {"source_name": self.source_name, "uri": uri},
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Module-level helpers (not part of the public API)
# ---------------------------------------------------------------------------


def _infer_file_format(ext: str) -> str:
    """Return the file format string for a given file extension.

    Parameters
    ----------
    ext : str
        Lower-cased file extension including the leading dot
        (e.g. ``".nc"``, ``".parquet"``).

    Returns
    -------
    str
        ``"netcdf"`` for ``.nc``, ``"parquet"`` for ``.parquet``.

    Raises
    ------
    ValueError
        If the extension is not recognised.
    """
    mapping = {".nc": "netcdf", ".parquet": "parquet"}
    if ext not in mapping:
        raise ValueError(
            f"Unsupported weather file extension '{ext}'. "
            f"Expected one of: {list(mapping.keys())}"
        )
    return mapping[ext]


def _extract_variable_from_stem(stem: str, source_name: str) -> str:
    """Extract the variable name from a file stem by stripping the source prefix.

    Convention: ``<source_name>_<variable>[_<date_suffix>]``.
    The date suffix (if any) is a trailing eight-digit token (YYYYMMDD).

    Parameters
    ----------
    stem : str
        File stem without extension, e.g. ``"era5_temperature_2m_20240101"``.
    source_name : str
        Source prefix to strip, e.g. ``"era5"``.

    Returns
    -------
    str
        Variable name, e.g. ``"temperature_2m"``.
    """
    prefix = f"{source_name}_"
    remainder = stem[len(prefix) :] if stem.startswith(prefix) else stem

    # Strip trailing date suffix YYYYMMDD if present.
    parts = remainder.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        return parts[0]
    return remainder


def _read_temporal_metadata(
    path: str,
    file_format: str,
    variable: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract valid_from, valid_until, bbox (WKT), and CRS from a weather file.

    Parameters
    ----------
    path : str
        Absolute path to the file.
    file_format : str
        ``"netcdf"`` or ``"parquet"``.
    variable : str
        Variable name; used to infer metadata from Parquet column names.

    Returns
    -------
    tuple[str | None, str | None, str | None, str | None]
        ``(valid_from, valid_until, bbox_wkt, crs)``
    """
    if file_format == "netcdf":
        meta = extract_netcdf_layer_metadata(path)
        return (
            meta.get("valid_from"),
            meta.get("valid_until"),
            meta.get("bbox"),
            meta.get("crs"),
        )

    # Parquet: infer time range from the datetime column.
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["datetime"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        valid_from = df["datetime"].min().isoformat()
        valid_until = df["datetime"].max().isoformat()
    except Exception as exc:
        logger.warning("Could not infer time range from Parquet file %s: %s", path, exc)
        valid_from = None
        valid_until = None

    return valid_from, valid_until, None, "EPSG:4326"
