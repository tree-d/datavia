"""
SaverWeather — Saver implementation for weather data.

For sources that declare a ``zarr_grid`` in ``SOURCE_REGISTRY`` (e.g.
``"HYRAS"``, ``"ERA5_land"``), downloaded NetCDF files are ingested directly
into the per-(source, variable, year) Zarr store via
:class:`~datavia.weather.zarr_store_manager.ZarrStoreManager`.  No ``.nc``
copy is retained on disk; the Zarr store is the authoritative data backend.
Coverage tracking is handled by
:class:`~datavia.weather.coverage_manager.CoverageManager`, which rebuilds
``weather_layers`` DB rows from the store on the next ``update_data`` call
when no rows exist for a variable.

For sources without a ``zarr_grid`` entry (e.g. DWD station Parquet files),
the original copy-and-register behaviour is preserved: the file is copied to
the configured data directory and a row is inserted in ``weather_layers``.
"""

import datetime
import hashlib
import json
import logging
import os
import shutil

import pandas as pd
import pyarrow.parquet as pq
from sqlalchemy import text

from datavia.config import get_config
from datavia.core.interfaces import Saver
from datavia.library.database.connection import session_local
from datavia.library.database.query import check_weather_source_exists
from datavia.library.formats import extract_netcdf_layer_metadata

from .source_registry import SOURCE_REGISTRY
from .zarr_store_manager import ZarrStoreManager

logger = logging.getLogger(__name__)

#: Parquet metadata columns that are not weather observation variables.
#: Used by :func:`_resolve_variables` to identify observation columns.
_PARQUET_METADATA_COLS: frozenset[str] = frozenset(
    {"station_id", "latitude", "longitude", "datetime"}
)


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
        variable: str | None = None,
        register_only: bool = False,
    ) -> bool:
        """Copy a weather file to the data directory and register it in the DB.

        The behaviour depends on the source:

        - **Zarr-enabled sources** (``.nc`` input, ``zarr_grid`` in
          ``SOURCE_REGISTRY``): the NetCDF file is ingested directly into the
          per-(variable, year) Zarr store via :meth:`_ingest_nc_to_zarr`.
          No ``.nc`` copy is kept; no DB row is inserted here.  Coverage
          tracking is handled automatically by
          :class:`~datavia.weather.coverage_manager.CoverageManager` on the
          next ``update_data`` call.
        - **Parquet sources** (e.g. DWD station files): file is copied to the
          data directory and a row is inserted in ``weather_layers``.

        The *reproject* and *resolution_m* parameters are accepted for
        interface compatibility but are not applied to weather files.

        When *register_only* is ``True`` and *data_path* points to a Zarr
        store directory (extension ``.zarr``), the DB registration is rebuilt
        from the store contents via :meth:`_rebuild_zarr_registration`.

        Parameters
        ----------
        data_path : str
            Absolute path to the downloaded file (or Zarr store directory).
        reproject : bool, optional
            Ignored for weather files; present for interface compatibility.
            Defaults to ``False``.
        resolution_m : int, optional
            Ignored for weather files. Defaults to ``None``.
        variable : str, optional
            Explicit variable name for Parquet DB registration.  Not used for
            Zarr-enabled sources (variables are read from the dataset).
        register_only : bool, optional
            When ``True`` skip the file-copy step.  For Zarr store paths this
            triggers :meth:`_rebuild_zarr_registration` instead.
            Defaults to ``False``.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on any error.
        """
        try:
            ext = os.path.splitext(data_path)[1].lower()

            # Zarr-enabled sources: ingest .nc directly into the Zarr store.
            # No .nc copy is kept; coverage DB rows are rebuilt automatically
            # by CoverageManager on the next update_data() call.
            if ext == ".nc" and _has_zarr_grid(self.source_name):
                return self._ingest_nc_to_zarr(data_path)

            # Orphan Zarr store re-discovered by sync_files_and_database:
            # rebuild DB registration from the store contents.
            if ext == ".zarr" and _has_zarr_grid(self.source_name):
                return self._rebuild_zarr_registration(data_path)

            file_format = _infer_file_format(ext)

            if register_only:
                # File already lives in the data directory.  Its stem is the
                # definitive layer name — no source_name prefix needed because
                # the file was placed there by a previous save() that already
                # applied the descriptive naming convention.
                dest_path = data_path
                layer_name = os.path.splitext(os.path.basename(data_path))[0]
            else:
                # Derive a descriptive stem from the file content BEFORE
                # copying so the permanent filename reflects what is actually
                # inside the file rather than the random temp-path stem
                # produced by tempfile.mkstemp.
                dest_stem = _build_dest_stem(data_path, file_format, self.source_name)
                dest_path = os.path.join(self.data_dir, f"{dest_stem}{ext}")
                shutil.copy2(data_path, dest_path)
                logger.info("Copied weather file to %s", dest_path)
                layer_name = dest_stem

            # Resolve the list of variables to register for this file.
            # When an explicit variable is given, register exactly that one.
            # For multi-variable NetCDF files (ERA5 downloads), read the
            # variable names from the file and insert one row per variable so
            # that per-variable path queries return the correct file.
            variables_to_register: list[str] = _resolve_variables(
                dest_path, file_format, variable, self.source_name
            )

            # Temporal / spatial metadata is the same for all variables in
            # a single file — read it once.
            valid_from, valid_until, bbox, crs = _read_temporal_metadata(
                dest_path, file_format, variables_to_register[0]
            )
            # Station IDs are only meaningful for parquet files (DWD station
            # data).  Persisting them in the metadata JSON enables the
            # station-aware cache check in check_data_exists().
            station_ids = _read_station_ids(dest_path, file_format)

            for var in variables_to_register:
                # Use a per-variable layer name so each row is uniquely
                # identifiable even when multiple variables share one file.
                var_layer_name = (
                    f"{layer_name}_{var}"
                    if len(variables_to_register) > 1
                    else layer_name
                )
                self._insert_weather_layer(
                    layer_name=var_layer_name,
                    variable=var,
                    file_format=file_format,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    uri=dest_path,
                    bbox=bbox,
                    crs=crs,
                    station_ids=station_ids,
                )
                logger.info(
                    "Registered weather layer '%s' (variable='%s') in database.",
                    var_layer_name,
                    var,
                )
            return True

        except Exception as exc:
            logger.error("Failed to save weather file %s: %s", data_path, exc)
            return False

    def list_managed_files(self) -> list[str]:
        """Return absolute paths of all weather files written by this saver.

        Scans the data directory for files matching
        ``<source_name>_*.nc`` and ``<source_name>_*.parquet``.  This is a
        pure filesystem operation and must not access the database.

        Returns
        -------
        list[str]
            Absolute paths to every matching file currently on disk.
            Returns an empty list when no files are found or the data
            directory does not exist.
        """
        return self._list_weather_files()

    def delete_registration(self, uri: str) -> None:
        """Remove all database registrations for the given file URI.

        Deletes every ``weather_layers`` row whose ``uri`` matches *uri*
        for this source.  Covers multi-row cases where a single file
        produces one row per variable (e.g. ERA5 multi-variable NetCDF).
        The file itself is not touched.

        Parameters
        ----------
        uri : str
            Absolute path to the file whose registrations should be removed.
        """
        self._delete_db_rows_by_uri({uri})
        logger.info("Removed database registration for URI: %s", uri)

    def check_data_exists(
        self,
        variable: str,
        from_dt: str | None = None,
        to_dt: str | None = None,
        station_ids: list[str] | None = None,
    ) -> bool:
        """Check whether data for a variable exists in the configured time window.

        When *station_ids* is supplied the check also verifies that at least
        one registered layer covers every requested station.  This prevents
        false positives for DWD parquet sources where a prior download for a
        different station set would otherwise satisfy the time-overlap check.

        Parameters
        ----------
        variable : str
            Variable name to check, e.g. ``"temperature_2m"``.
        from_dt : str, optional
            Start of the time window (ISO-8601 datetime string).
        to_dt : str, optional
            End of the time window (ISO-8601 datetime string).
        station_ids : list[str], optional
            Station identifiers that must all be present in a registered layer.
            When ``None`` only the time-overlap check is applied, preserving
            existing behaviour for ERA5/HYRAS callers.

        Returns
        -------
        bool
            ``True`` when at least one weather layer exists for the requested
            variable, time range, and (if given) station set.
        """
        return check_weather_source_exists(
            source_name=self.source_name,
            variable=variable,
            from_dt=from_dt,
            to_dt=to_dt,
            station_ids=station_ids,
        )

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
        station_ids: list[str] | None = None,
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
        station_ids : list[str] or None, optional
            Sorted list of station identifiers stored in the parquet file.
            ``None`` for non-parquet (e.g. NetCDF) sources.
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
                    "metadata": json.dumps(
                        {
                            "file_format": file_format,
                            "station_ids": station_ids,
                        }
                    ),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _list_weather_files(self) -> list[str]:
        """Return absolute paths of all weather data managed by this saver.

        For Zarr-enabled sources the authoritative data backend is the Zarr
        store, not NetCDF files.  Only ``.zarr`` store directories and
        ``.parquet`` files are returned; ``.nc`` files are intentionally
        excluded.  This allows
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database` to
        detect any legacy ``.nc``-based DB rows as orphans and remove them,
        forcing CoverageManager to re-compute coverage from the Zarr stores.

        For non-Zarr sources, scans the data directory for
        ``<source_name>_*.nc`` and ``<source_name>_*.parquet`` files as before.

        Returns
        -------
        list[str]
            Absolute paths to all managed weather files and Zarr stores.
        """
        is_zarr = _has_zarr_grid(self.source_name)
        prefix = f"{self.source_name}_"
        result: list[str] = []

        try:
            for fname in os.listdir(self.data_dir):
                if not fname.startswith(prefix):
                    continue
                # For Zarr sources, skip .nc files — Zarr stores are canonical.
                if is_zarr and fname.endswith(".nc"):
                    continue
                if fname.endswith(".nc") or fname.endswith(".parquet"):
                    result.append(os.path.join(self.data_dir, fname))
        except FileNotFoundError:
            logger.warning("Data directory not found: %s", self.data_dir)

        if is_zarr:
            source_root = os.path.join(self.data_dir, self.source_name)
            try:
                for var_name in os.listdir(source_root):
                    var_dir = os.path.join(source_root, var_name)
                    if not os.path.isdir(var_dir):
                        continue
                    for store_name in os.listdir(var_dir):
                        if store_name.endswith(".zarr"):
                            result.append(os.path.join(var_dir, store_name))
            except FileNotFoundError:
                pass

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

    def save_nc_to_zarr(self, nc_path: str) -> bool:
        """Ingest a downloaded NetCDF file into the Zarr store.

        Public entry point called by the pipeline's ``update_data`` for
        Zarr-enabled sources.  Delegates to :meth:`_ingest_nc_to_zarr`.

        Parameters
        ----------
        nc_path : str
            Absolute path to the downloaded ``.nc`` file to ingest.

        Returns
        -------
        bool
            ``True`` when every variable was written successfully,
            ``False`` on any error.
        """
        return self._ingest_nc_to_zarr(nc_path)

    def _ingest_nc_to_zarr(self, nc_path: str) -> bool:
        """Ingest a downloaded NetCDF file into the Zarr store for each variable.

        Opens *nc_path* with :func:`xarray.open_dataset`, resolves the
        pipeline variable names from ``SOURCE_REGISTRY``, and calls
        :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.write_dataset`
        for each variable.  The source NetCDF file is not retained; all data
        is preserved in the Zarr store.

        Parameters
        ----------
        nc_path : str
            Absolute path to the downloaded ``.nc`` file to ingest.

        Returns
        -------
        bool
            ``True`` when every variable was written successfully,
            ``False`` on any error.
        """
        import xarray as xr

        try:
            mgr = ZarrStoreManager(self.data_dir, self.source_name)
            variables = _resolve_variables(nc_path, "netcdf", None, self.source_name)
            with xr.open_dataset(nc_path) as ds:
                for var in variables:
                    mgr.write_dataset(ds, var)
                    logger.info(
                        "Ingested %s/%s into Zarr store.", self.source_name, var
                    )
            return True
        except Exception as exc:
            logger.error(
                "Failed to ingest NetCDF '%s' into Zarr store: %s", nc_path, exc
            )
            return False

    def _rebuild_zarr_registration(self, zarr_path: str) -> bool:
        """Repopulate ``weather_layers`` from an orphan Zarr store.

        Called when :meth:`save` receives a ``.zarr`` directory path, which
        happens when
        :meth:`~datavia.core.interfaces.Pipeline.sync_files_and_database`
        discovers a Zarr store on disk that has no DB rows.

        Parameters
        ----------
        zarr_path : str
            Absolute path to the Zarr store directory, e.g.
            ``<data_dir>/HYRAS/2m_temperature/2023.zarr``.

        Returns
        -------
        bool
            ``True`` when at least one DB row was inserted, ``False`` on error.
        """
        from .coverage_manager import CoverageManager

        try:
            variable = os.path.basename(os.path.dirname(zarr_path))
            mgr = CoverageManager(
                self.source_name,
                [variable],
                data_dir=self.data_dir,
            )
            rows = mgr.rebuild_from_store(variable)
            logger.info("Rebuilt %d DB row(s) from Zarr store: %s", rows, zarr_path)
            return True
        except Exception as exc:
            logger.error(
                "Failed to rebuild Zarr registration for '%s': %s", zarr_path, exc
            )
            return False


# ---------------------------------------------------------------------------
# Module-level helpers (not part of the public API)
# ---------------------------------------------------------------------------


def _has_zarr_grid(source_name: str) -> bool:
    """Return ``True`` when *source_name* has
    a ``zarr_grid`` entry in ``SOURCE_REGISTRY``.

    Parameters
    ----------
    source_name : str
        Source identifier to look up in ``SOURCE_REGISTRY``.

    Returns
    -------
    bool
        ``True`` when the source is configured for Zarr storage.
    """
    return "zarr_grid" in SOURCE_REGISTRY.get(source_name, {})


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


def _build_dest_stem(
    data_path: str,
    file_format: str,
    source_name: str,
) -> str:
    """Build a descriptive, content-derived filename stem for a weather file.

    For NetCDF files the source file is opened before the copy so that the
    permanent filename reflects the file's actual content rather than the
    random temp-path stem produced by :func:`tempfile.mkstemp`.

    Naming rules:

    - Single data variable:
      ``{source_name}_{nc_variable}_{YYYYmm_start}_{YYYYmm_end}_{bbox_hash}``
      e.g. ``ERA5_land_2m_temperature_202401_202412_a3f91c``.
    - Multiple data variables:
      ``{source_name}_{YYYYmm_start}_{YYYYmm_end}_{bbox_hash}``
      (encoding all variable names would produce an impractically long stem).
    - Parquet with ``station_id`` column:
      ``{source_name}_{year_start}_{year_end}_{station_hash}``
      e.g. ``DWD_stations_2024_2024_a3f91c``.  ``station_hash`` is the first
      6 hex digits of the MD5 of the sorted station-ID list, so two downloads
      with different station sets always receive different stems.
    - Parquet without ``station_id`` column: ``{source_name}_{year}``
      (backward-compatible fallback for files that pre-date this convention).
    - Unreadable NC / Parquet: ``{source_name}_{temp_stem}`` as a safe
      fallback so existing behaviour for Parquet station files is preserved.

    Parameters
    ----------
    data_path : str
        Absolute path to the source file (typically a temp file).
    file_format : str
        ``"netcdf"`` or ``"parquet"``.
    source_name : str
        Pipeline source identifier, e.g. ``"HYRAS"`` or ``"ERA5_land"``.

    Returns
    -------
    str
        Descriptive filename stem without extension or directory component.
    """
    if file_format == "netcdf":
        meta = extract_netcdf_layer_metadata(data_path)
        nc_vars: list[str] = meta.get("variables", [])
        valid_from: str | None = meta.get("valid_from")
        valid_until: str | None = meta.get("valid_until")
        bbox_wkt: str | None = meta.get("bbox")

        year_start = valid_from[:4] if valid_from else "unknown"
        month_start = valid_from[5:7] if valid_from else "XX"
        year_end = valid_until[:4] if valid_until else "unknown"
        month_end = valid_until[5:7] if valid_until else "XX"
        bbox_tag = (
            hashlib.md5(bbox_wkt.encode(), usedforsecurity=False).hexdigest()[:6]
            if bbox_wkt
            else "nobbox"
        )
        time_range = f"{year_start}{month_start}_{year_end}{month_end}"

        if len(nc_vars) == 1:
            return f"{source_name}_{nc_vars[0]}_{time_range}_{bbox_tag}"
        # Multiple variables or metadata unreadable — omit variable name.
        return f"{source_name}_{time_range}_{bbox_tag}"

    # Parquet (DWD station files): derive a unique stem from the station set
    # and date range so that two downloads with different station sets never
    # produce the same filename.  Falls back gracefully when the station_id
    # column is absent (backward compatibility) or the file is unreadable.
    try:
        df = pd.read_parquet(data_path, columns=["datetime", "station_id"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        year_start = str(df["datetime"].min().year)
        year_end = str(df["datetime"].max().year)
        station_ids = sorted(df["station_id"].unique().tolist())
        station_hash = hashlib.md5(
            str(station_ids).encode(), usedforsecurity=False
        ).hexdigest()[:6]
        return f"{source_name}_{year_start}_{year_end}_{station_hash}"
    except Exception as exc:
        logger.warning(
            "Could not derive station-hash stem from '%s': %s; "
            "falling back to datetime-only stem.",
            data_path,
            exc,
        )

    try:
        df_dt = pd.read_parquet(data_path, columns=["datetime"])
        df_dt["datetime"] = pd.to_datetime(df_dt["datetime"])
        year = str(df_dt["datetime"].min().year)
        return f"{source_name}_{year}"
    except Exception:
        stem = os.path.splitext(os.path.basename(data_path))[0]
        return f"{source_name}_{stem}"


def _resolve_variables(
    dest_path: str,
    file_format: str,
    explicit_variable: str | None,
    source_name: str,
) -> list[str]:
    """Determine the list of variable names to register for a weather file.

    When *explicit_variable* is provided it is used as-is.  When it is
    ``None`` the variables are inferred from the file content:

    - **NetCDF**: all data-variable names are read from the file.  This
      ensures that a multi-variable ERA5 download results in one DB row per
      variable so per-variable path queries return the correct file.
    - **Parquet**: the source name is used as a placeholder because Parquet
      station files contain multiple observation columns without a single
      canonical variable name.

    Parameters
    ----------
    dest_path : str
        Absolute path to the saved file.
    file_format : str
        ``"netcdf"`` or ``"parquet"``.
    explicit_variable : str or None
        Explicitly supplied variable name; ``None`` triggers auto-detection.
    source_name : str
        Source identifier used as fallback for Parquet files.

    Returns
    -------
    list[str]
        Non-empty list of variable names.  Contains exactly one entry when
        *explicit_variable* is provided.
    """
    if explicit_variable is not None:
        return [explicit_variable]

    if file_format == "netcdf":
        meta = extract_netcdf_layer_metadata(dest_path)
        nc_vars = meta.get("variables", [])
        if nc_vars:
            # Some sources (e.g. HYRAS) store data under short CF variable
            # names ("tas", "pr") that differ from the pipeline-level names
            # ("2m_temperature", "total_precipitation").  Remap them using the
            # nc_variable_map registered in SOURCE_REGISTRY so that the DB
            # rows use the same names that the getter and callers expect.
            nc_var_map: dict[str, str] = SOURCE_REGISTRY.get(source_name, {}).get(
                "nc_variable_map", {}
            )
            if nc_var_map:
                # Translate CF short names to pipeline names.  Also keeps
                # variables that are already pipeline names (i.e. values of
                # nc_variable_map) so that files stored under their pipeline
                # name pass through unchanged.  Auxiliary CF coordinates
                # (time_bnds, x_bnds, crs, etc.) that appear in neither the
                # keys nor the values of nc_variable_map are silently dropped.
                pipeline_names = set(nc_var_map.values())
                remapped: list[str] = []
                for v in nc_vars:
                    if v in nc_var_map:
                        remapped.append(nc_var_map[v])
                    elif v in pipeline_names:
                        remapped.append(v)
                nc_vars = remapped
            if nc_vars:
                return list(nc_vars)
        # Fallback: use source_name when metadata extraction fails.
        logger.warning(
            "Could not read variable names from NetCDF '%s'; "
            "using source_name '%s' as fallback.",
            dest_path,
            source_name,
        )
        return [source_name]

    # Parquet: read the schema to find observation columns.
    # DWD Parquet files always carry metadata columns (station_id, latitude,
    # longitude, datetime); every other column is an observation variable.
    try:
        schema = pq.read_schema(dest_path)
        obs_vars = [name for name in schema.names if name not in _PARQUET_METADATA_COLS]
        if obs_vars:
            return obs_vars
    except Exception as exc:
        logger.warning(
            "Could not infer variable names from Parquet '%s': %s; "
            "using source_name '%s' as fallback.",
            dest_path,
            exc,
            source_name,
        )
    return [source_name]


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
        df = pd.read_parquet(path, columns=["datetime"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        valid_from = df["datetime"].min().isoformat()
        valid_until = df["datetime"].max().isoformat()
    except Exception as exc:
        logger.warning("Could not infer time range from Parquet file %s: %s", path, exc)
        valid_from = None
        valid_until = None

    return valid_from, valid_until, None, "EPSG:4326"


def _read_station_ids(path: str, file_format: str) -> list[str] | None:
    """Return sorted unique station IDs from a parquet weather file.

    Called during :meth:`SaverWeather.save` so that station identifiers are
    persisted in the ``metadata`` JSON column.  This enables the
    station-aware cache check in
    :func:`~datavia.library.database.query.check_weather_source_exists`.

    Parameters
    ----------
    path : str
        Absolute path to the file.
    file_format : str
        ``"netcdf"`` or ``"parquet"``.  NetCDF files have no station IDs;
        ``None`` is returned immediately for them.

    Returns
    -------
    list[str] or None
        Sorted list of unique station ID strings for parquet files, or
        ``None`` when the file is NetCDF, the column is absent, or the
        file cannot be read.
    """
    if file_format != "parquet":
        return None
    try:
        df = pd.read_parquet(path, columns=["station_id"])
        return sorted(str(sid) for sid in df["station_id"].unique())
    except Exception as exc:
        logger.warning("Could not read station_id column from '%s': %s", path, exc)
        return None
