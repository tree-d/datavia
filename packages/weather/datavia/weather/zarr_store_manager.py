"""
Zarr store manager for per-(source, variable, year) gridded weather data.

Encapsulates all Zarr I/O for the weather pipeline.  One store is maintained
per ``(source_name, variable, year)`` triple, laid out as::

    <data_dir>/<source_name>/<variable>/<year>.zarr/

Stores use a fixed Germany-extent coordinate grid defined in
``SOURCE_REGISTRY["zarr_grid"]``.  Only cells that have been written have
chunk files on disk; all other cells silently return ``fill_value`` (NaN) at
zero disk cost.

Write safety
------------
``write_dataset`` guards each write with a ``fasteners.InterProcessLock``
(file-based) and a ``.write_in_progress`` sentinel next to the store directory.
If the process is killed during a write the sentinel persists, and subsequent
calls to ``open_store`` / ``open_multi_year`` skip that store.
``CoverageManager.rebuild_from_store`` can recover such stores by scanning
written chunk files and re-registering only fully-covered months.

Spatial gap-filling
--------------------
``write_dataset`` fills nodata/fill-sentinel cells within each downloaded
slab (nearest-neighbour, per spatial dimension) exactly once, immediately
before writing, via
:func:`~datavia.library.interpolation.fill_spatial_gaps`. Query-time code
(``interpolate_dataset``) assumes this fill already happened and does not
re-fill on every read. Cells that were never downloaded — or that sit
between two independently-downloaded regions — are filled only within the
extent of the slab being written, so they remain ``NaN`` until a future
download covers them.

See also
--------
- :mod:`datavia.weather.source_registry` — ``zarr_grid`` definitions
- ``docs/development/zarr_store_implementation_plan.md``
- ``docs/development/zarr_design_review.md``
"""

from __future__ import annotations

import shutil
from pathlib import Path

import dask.array as da
import fasteners
import numpy as np
import pandas as pd
import xarray as xr
from zarr.codecs import BloscCodec

from datavia.library.interpolation import fill_spatial_gaps

from .source_registry import SOURCE_REGISTRY

# Name of the sentinel file written while a write is in progress.
_SENTINEL = ".write_in_progress"


def _sentinel_path(path: Path) -> Path:
    """Return the sibling sentinel path for a store directory.

    Placed next to the store directory (like the ``.lock`` file) rather than
    inside it, so it never appears in the store's own Zarr member listing
    (which would otherwise trip a ``ZarrUserWarning`` during ``to_zarr``/
    ``open_zarr`` group enumeration).

    Parameters
    ----------
    path : Path
        Zarr store directory, e.g. ``.../2024.zarr``.

    Returns
    -------
    Path
        e.g. ``.../2024.zarr.write_in_progress``.
    """
    return path.parent / (path.name + _SENTINEL)


class ZarrStoreManager:
    """Create, write to, and read from per-(source, variable, year) Zarr stores.

    Parameters
    ----------
    data_dir : str
        Root data directory (from ``get_config().data_directory``).
    source_name : str
        Source identifier registered in ``SOURCE_REGISTRY``, e.g.
        ``"ERA5_land"``.
    store_root : Path, optional
        Override the root directory under which stores are created.  Defaults
        to ``Path(data_dir) / source_name``.  Pass a ``tmp_path`` fixture in
        tests to isolate store state without touching the real data directory.

    Raises
    ------
    KeyError
        If ``source_name`` is not in ``SOURCE_REGISTRY`` or has no
        ``"zarr_grid"`` entry.
    """

    def __init__(
        self,
        data_dir: str,
        source_name: str,
        store_root: Path | None = None,
    ) -> None:
        """Initialise the manager for a given source."""
        if source_name not in SOURCE_REGISTRY:
            raise KeyError(
                f"Source '{source_name}' is not registered in SOURCE_REGISTRY."
            )
        entry = SOURCE_REGISTRY[source_name]
        if "zarr_grid" not in entry:
            raise KeyError(
                f"Source '{source_name}' has no 'zarr_grid' entry in "
                "SOURCE_REGISTRY.  Add one before using ZarrStoreManager."
            )

        self._data_dir = Path(data_dir)
        self._source_name = source_name
        self._store_root = store_root
        self._grid: dict = entry["zarr_grid"]
        self._nc_variable_map: dict[str, str] = entry.get("nc_variable_map", {})
        # Reverse map: Datavia variable name → ERA5/HYRAS short name.
        self._variable_to_nc: dict[str, str] = {
            v: k for k, v in self._nc_variable_map.items()
        }

    # -------------------------------------------------------------------------
    # Path helpers
    # -------------------------------------------------------------------------

    def store_path(self, variable: str, year: int) -> Path:
        """Return the filesystem path to the Zarr store directory.

        Parameters
        ----------
        variable : str
            Datavia variable name, e.g. ``"2m_temperature"``.
        year : int
            Calendar year.

        Returns
        -------
        Path
            Absolute path: ``<data_dir>/<source_name>/<variable>/<year>.zarr``,
            or ``<store_root>/<variable>/<year>.zarr`` when ``store_root`` was
            provided.
        """
        root = self._store_root or (self._data_dir / self._source_name)
        return root / variable / f"{year}.zarr"

    # -------------------------------------------------------------------------
    # Store lifecycle
    # -------------------------------------------------------------------------

    def _is_projected(self) -> bool:
        """Return ``True`` when the store uses a projected (non-WGS84) CRS.

        Checks the ``"crs"`` entry in ``zarr_grid``.  Geographic stores
        (``EPSG:4326``) use a pre-defined latitude/longitude skeleton and
        partial-region writes.  Projected stores (e.g. HYRAS ``EPSG:3035``)
        write full-year slabs from the downloaded dataset using its native
        x/y coordinates — no skeleton or alignment step is required.

        Returns
        -------
        bool
            ``True`` when the source's native CRS is not ``EPSG:4326``.
        """
        return self._grid.get("crs", "EPSG:4326") != "EPSG:4326"

    def ensure_store(self, variable: str, year: int) -> None:
        """Create the Zarr store skeleton for *variable* / *year* if it does not exist.

        For **geographic** sources (``crs="EPSG:4326"``): writes store
        metadata and all three coordinate arrays (``time``, ``latitude``,
        ``longitude``) to disk, but defers writing the data variable chunks
        (``compute=False``) so that no NaN chunk files are created.  Unwritten
        chunks return ``fill_value`` at zero disk cost.  Safe to call multiple
        times — a no-op when the store already exists.

        For **projected** sources (e.g. ``crs="EPSG:3035"``): the store is
        created wholesale from the downloaded dataset in :meth:`write_dataset`
        and no skeleton is needed, so this method is a no-op.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year for which to create the store.
        """
        if self._is_projected():
            return

        path = self.store_path(variable, year)
        if path.exists():
            return

        path.mkdir(parents=True, exist_ok=True)

        time_index = self._build_time_index(year)
        lat = self._grid["latitude"]
        lon = self._grid["longitude"]
        fill = self._grid["fill_value"]
        dtype = self._grid["dtype"]
        chunks = self._grid["chunks"]
        codec_cfg = self._grid["codec"]

        compressor = BloscCodec(
            cname=codec_cfg["cname"],
            clevel=codec_cfg["clevel"],
            shuffle=codec_cfg["shuffle"],
        )
        chunk_tuple = (chunks["time"], chunks["latitude"], chunks["longitude"])
        shape = (len(time_index), len(lat), len(lon))

        # Use a dask array so that to_zarr(compute=False) defers the data
        # variable write — only metadata and coordinate chunks are written.
        data = da.full(shape, fill, dtype=dtype, chunks=chunk_tuple)

        ds_skeleton = xr.Dataset(
            {variable: xr.DataArray(data, dims=["time", "latitude", "longitude"])},
            coords={
                "time": time_index,
                "latitude": ("latitude", lat),
                "longitude": ("longitude", lon),
            },
        )
        ds_skeleton[variable].attrs["grid_mapping"] = "crs"
        ds_skeleton["latitude"].attrs["units"] = "degrees_north"
        ds_skeleton["longitude"].attrs["units"] = "degrees_east"

        encoding = {
            variable: {
                "fill_value": fill,
                "_FillValue": fill,
                "compressors": [compressor],
                "chunks": chunk_tuple,
            }
        }

        ds_skeleton.to_zarr(
            str(path),
            # compute=False defers writing the data variable so that no NaN
            # chunk files are created on disk; only metadata and coordinate
            # chunks are written.
            compute=False,
            consolidated=False,
            encoding=encoding,
        )

    def write_dataset(self, ds: xr.Dataset, variable: str) -> None:
        """Write an xarray Dataset into the appropriate Zarr region(s).

        Normalises time coordinates (``valid_time`` → ``time``), resolves
        ERA5 short variable names to Datavia names, determines the target
        year(s) from the time axis, calls :meth:`ensure_store` for each year,
        verifies coordinate alignment, fills spatial nodata gaps via
        :func:`~datavia.library.interpolation.fill_spatial_gaps`, and writes
        via ``to_zarr(region="auto")``.

        Datasets spanning two calendar years (e.g. a December-January
        download) are split automatically and written to two separate stores.

        Each write is guarded by an ``InterProcessLock`` and a
        ``.write_in_progress`` sentinel next to the store directory.  The
        sentinel is removed only when the write completes without exception.

        Parameters
        ----------
        ds : xr.Dataset
            Dataset returned by the downloader.  ERA5 short names (e.g.
            ``"t2m"``) are resolved to Datavia names via ``nc_variable_map``.
        variable : str
            Datavia variable name to write (e.g. ``"2m_temperature"``).

        Raises
        ------
        ValueError
            If any coordinate value in ``ds`` deviates from the nearest store
            grid point by more than half a grid cell.
        KeyError
            If *variable* cannot be resolved from the dataset's data variables.
        """
        ds = self._normalise_time(ds)
        ds = self._select_variable(ds, variable)

        years = sorted({int(ts.year) for ts in pd.DatetimeIndex(ds.time.values)})
        is_projected = self._is_projected()

        for year in years:
            ds_year = ds.sel(time=str(year))

            if not is_projected:
                # Create (or verify) the pre-defined grid skeleton before
                # locking so concurrent writers converge on the same skeleton.
                self.ensure_store(variable, year)

            path = self.store_path(variable, year)
            sentinel = _sentinel_path(path)
            lock = fasteners.InterProcessLock(str(path) + ".lock")

            with lock:
                if is_projected:
                    # Projected source (e.g. HYRAS EPSG:3035): write the
                    # full-year slab wholesale from the downloaded dataset
                    # using its native x/y coordinates.  The store directory
                    # is created here because ensure_store is a no-op for
                    # projected sources.
                    path.mkdir(parents=True, exist_ok=True)
                    sentinel.write_text("write in progress\n", encoding="utf-8")
                    try:
                        # Mask the fill sentinel and replace nodata cells
                        # with the nearest valid neighbour before writing. A
                        # full-year overwrite has no downstream ordering
                        # requirement, so the fill's coordinate order does
                        # not need to be restored.
                        filled_var = fill_spatial_gaps(ds_year[variable], "x", "y")
                        ds_year = ds_year.assign({variable: filled_var})
                        ds_year.to_zarr(str(path), mode="w", consolidated=False)
                    finally:
                        if sentinel.exists():
                            sentinel.unlink()
                else:
                    # Geographic source (e.g. ERA5_land EPSG:4326): snap
                    # coordinates to the pre-defined grid and write the
                    # region into the skeleton via region="auto".
                    sentinel.write_text("write in progress\n", encoding="utf-8")
                    try:
                        ds_aligned = self._align_to_store(ds_year, variable, year)
                        # Mask the fill sentinel and replace nodata cells
                        # with the nearest valid neighbour before writing.
                        # region="auto" requires the written data's
                        # coordinate order to exactly match the store's
                        # existing (descending-latitude) order, so
                        # restore_order=True undoes the ascending sort that
                        # the nearest-neighbour fill requires.
                        filled_var = fill_spatial_gaps(
                            ds_aligned[variable],
                            "longitude",
                            "latitude",
                            restore_order=True,
                        )
                        ds_aligned = ds_aligned.assign({variable: filled_var})
                        ds_aligned.to_zarr(
                            str(path),
                            region="auto",
                            consolidated=False,
                        )
                    finally:
                        if sentinel.exists():
                            sentinel.unlink()

    def open_store(self, variable: str, year: int) -> xr.Dataset:
        """Open an existing Zarr store as a lazy xarray Dataset.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year.

        Returns
        -------
        xr.Dataset
            Lazy Dataset backed by the Zarr store.  Unwritten cells return
            ``fill_value`` (NaN).  Use as a context manager to ensure the
            underlying store is closed when done.

        Raises
        ------
        FileNotFoundError
            If the store directory does not exist on disk.
        RuntimeError
            If the store has a ``.write_in_progress`` sentinel, indicating
            a previous write was interrupted.  Run
            ``CoverageManager.rebuild_from_store()`` to recover.
        """
        path = self.store_path(variable, year)
        if not path.exists():
            raise FileNotFoundError(
                f"Zarr store not found: {path}.  "
                f"Download {self._source_name}/{variable}/{year} first."
            )
        _check_sentinel(path)
        return xr.open_zarr(str(path), consolidated=False)

    def open_multi_year(self, variable: str, years: list[int]) -> xr.Dataset:
        """Open and concatenate multiple year stores along the time axis.

        Uses ``xr.open_mfdataset`` with ``engine="zarr"`` and
        ``combine="by_coords"`` so that common-year (8760 h) and leap-year
        (8784 h) stores concatenate correctly without a ``preprocess`` step.

        Years whose store does not exist on disk, or whose store has a
        ``.write_in_progress`` sentinel, are silently skipped.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        years : list[int]
            Ordered list of calendar years to open.

        Returns
        -------
        xr.Dataset
            Concatenated lazy Dataset.

        Raises
        ------
        FileNotFoundError
            If none of the requested years have a complete store on disk.
        """
        paths = []
        for year in years:
            path = self.store_path(variable, year)
            if path.exists() and not _sentinel_path(path).exists():
                paths.append(str(path))

        if not paths:
            raise FileNotFoundError(
                f"No complete Zarr stores found for "
                f"{self._source_name}/{variable} in years {years}."
            )

        return xr.open_mfdataset(
            paths,
            engine="zarr",
            combine="by_coords",
            chunks={},
            consolidated=False,
        )

    def delete_store(self, variable: str, year: int, confirmed: bool) -> None:
        """Delete the Zarr store directory from disk.

        This method operates on the filesystem only.  The caller must remove
        the corresponding ``weather_layers`` DB rows *before* calling this
        method so that a failed filesystem delete leaves DB rows intact and
        recoverable via ``CoverageManager.rebuild_from_store()``.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        year : int
            Calendar year.
        confirmed : bool
            Must be ``True`` to proceed.  When ``False`` the method is a
            no-op.  The caller (CLI command or test) sets this flag; no
            interactive prompt is issued here.

        Raises
        ------
        FileNotFoundError
            If the store does not exist on disk and ``confirmed=True``.
        """
        if not confirmed:
            return

        path = self.store_path(variable, year)
        if not path.exists():
            raise FileNotFoundError(f"Zarr store not found: {path}")

        shutil.rmtree(path)

    # -------------------------------------------------------------------------
    # Query helpers
    # -------------------------------------------------------------------------

    def covered_bbox_for_period(
        self,
        variable: str,
        date_start: str,
        date_end: str,
    ) -> tuple[float, float, float, float] | None:
        """Return the spatial bbox of non-fill data for the given time period.

        Reads the store(s) covering ``[date_start, date_end]`` lazily and
        computes the axis-aligned bounding box of all lat/lon cells that have
        at least one non-NaN value in that window.  Used by
        ``CoverageManager.rebuild_from_store`` to reconstruct DB rows from
        store contents.

        Parameters
        ----------
        variable : str
            Datavia variable name.
        date_start : str
            ISO date string (``YYYY-MM-DD``).
        date_end : str
            ISO date string (``YYYY-MM-DD``).

        Returns
        -------
        tuple[float, float, float, float] | None
            ``(west, south, east, north)`` in EPSG:4326, or ``None`` if the
            store does not exist or contains only fill values for that period.
        """
        start = pd.Timestamp(date_start)
        end = pd.Timestamp(date_end)
        years = list(range(start.year, end.year + 1))

        try:
            ds = self.open_multi_year(variable, years)
        except FileNotFoundError:
            return None

        with ds:
            da_period = ds[variable].sel(time=slice(date_start, date_end))
            has_data = da_period.notnull().any(dim="time").compute()

        if not bool(has_data.any()):
            return None

        if self._is_projected():
            return _projected_bbox_to_wgs84(
                has_data,
                crs=self._grid.get("crs", "EPSG:3035"),
                as_tuple=True,
            )  # type: ignore[return-value]

        lat_mask = has_data.any(dim="longitude")
        lon_mask = has_data.any(dim="latitude")

        lats = has_data.latitude.values[lat_mask.values]
        lons = has_data.longitude.values[lon_mask.values]

        return (
            float(lons.min()),
            float(lats.min()),
            float(lons.max()),
            float(lats.max()),
        )

    def list_available_variables(self) -> set[str]:
        """Return the variable names for which at least one Zarr store exists.

        Scans ``<data_dir>/<source_name>/`` (or ``store_root/``) for
        sub-directories containing at least one ``*.zarr`` directory.

        Returns
        -------
        set[str]
            Variable names derived from matching sub-directory names.
        """
        root = self._store_root or (self._data_dir / self._source_name)
        if not root.exists():
            return set()

        return {
            subdir.name
            for subdir in root.iterdir()
            if subdir.is_dir()
            and any(
                child.is_dir() and child.suffix == ".zarr" for child in subdir.iterdir()
            )
        }

    def migrate_nc_file(self, nc_path: str, variable: str) -> None:
        """Import an existing NetCDF file into the Zarr store.

        Opens *nc_path* with :func:`xarray.open_dataset` and calls
        :meth:`write_dataset`.  Does not remove the original file — the caller
        is responsible for cleanup after confirming the write succeeded.

        Parameters
        ----------
        nc_path : str
            Absolute path to a previously saved ``.nc`` file.
        variable : str
            Datavia variable name of the data in the file.
        """
        with xr.open_dataset(nc_path) as ds:
            self.write_dataset(ds, variable)

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _build_time_index(self, year: int) -> pd.DatetimeIndex:
        """Return the full-year UTC time axis for *year*.

        Builds a ``pd.DatetimeIndex`` covering every step of the year at the
        frequency specified in ``zarr_grid["time_freq"]`` (``"1h"`` for ERA5,
        ``"1D"`` for HYRAS).  The resulting index is UTC-naive, consistent
        with ERA5 delivery and the store's stored ``datetime64[ns]`` values.

        Parameters
        ----------
        year : int
            Calendar year.

        Returns
        -------
        pd.DatetimeIndex
            Hourly or daily timestamps from ``YYYY-01-01`` to the last step of
            ``YYYY-12-31``.
        """
        freq = self._grid["time_freq"]
        end = f"{year}-12-31 23:00" if freq == "1h" else f"{year}-12-31"
        return pd.date_range(start=f"{year}-01-01", end=end, freq=freq)

    def _normalise_time(self, ds: xr.Dataset) -> xr.Dataset:
        """Rename ``valid_time`` → ``time`` and cast to ``datetime64[ns]``.

        ERA5 downloads ship with ``valid_time`` as the time dimension name.
        This normalisation step makes all downstream code assume ``ds.time``
        exists and is a UTC-naive ``datetime64[ns]`` array.

        Parameters
        ----------
        ds : xr.Dataset
            Input dataset, possibly with ``valid_time`` coordinate.

        Returns
        -------
        xr.Dataset
            Dataset with a ``time`` dimension of dtype ``datetime64[ns]``.
        """
        if "valid_time" in ds.coords and "time" not in ds.coords:
            ds = ds.rename({"valid_time": "time"})
        # Strip timezone info and cast to nanosecond precision to match the
        # store's time axis which is stored as UTC-naive datetime64[ns].
        time_values = pd.DatetimeIndex(ds.time.values)
        if time_values.tz is not None:
            time_values = time_values.tz_localize(None)
        ds = ds.assign_coords(time=time_values.values.astype("datetime64[ns]"))
        return ds

    def _select_variable(self, ds: xr.Dataset, variable: str) -> xr.Dataset:
        """Reduce *ds* to the single data variable *variable*.

        First checks whether the Datavia variable name is already present.
        If not, resolves it via ``nc_variable_map`` (ERA5/HYRAS short names)
        and renames the variable.

        Parameters
        ----------
        ds : xr.Dataset
            Input dataset, possibly containing multiple variables.
        variable : str
            Datavia variable name, e.g. ``"2m_temperature"``.

        Returns
        -------
        xr.Dataset
            Dataset with a single data variable named *variable*.

        Raises
        ------
        KeyError
            If *variable* cannot be found in ``ds`` directly or via
            ``nc_variable_map``.
        """
        if variable in ds:
            selected = ds[[variable]]
        else:
            nc_name = self._variable_to_nc.get(variable)
            if nc_name and nc_name in ds:
                selected = ds[[nc_name]].rename({nc_name: variable})
            else:
                raise KeyError(
                    f"Variable '{variable}' not found in dataset.  "
                    f"Available data variables: {list(ds.data_vars)}.  "
                    f"nc_variable_map: {self._nc_variable_map}."
                )

        if self._is_projected():
            # Carry the CF grid_mapping variable along so that
            # interpolate_dataset / _build_spatial_interp_coords can detect
            # the native CRS and reproject query coordinates accordingly.
            gm_name = selected[variable].attrs.get("grid_mapping")
            if gm_name and gm_name in ds and gm_name not in selected:
                selected = selected.assign({gm_name: ds[gm_name]})

        # For geographic sources (ERA5, EPSG:4326) only: drop any coordinate or
        # variable that is not part of the pre-defined skeleton store.
        # ERA5 downloads from the CDS API contain GRIB metadata artefacts that
        # are absent from the skeleton and cause zarr to raise on region writes:
        #
        #   - `number` (ensemble member) is a scalar coordinate (ndim == 0).
        #   - `expver` (experiment version) is a 1-D coordinate whose
        #     dimension is `valid_time`; after _normalise_time() that becomes
        #     `time`, so a dimension-intersection filter incorrectly keeps it.
        #
        # A whitelist is the correct approach for the skeleton (region="auto")
        # path.  Projected sources (HYRAS EPSG:3035) write wholesale via
        # mode="w" and must NOT be filtered here — their native x/y/lat/lon/crs
        # arrays are required by the interpolator.
        if not self._is_projected():
            skeleton_coords = {"time", "latitude", "longitude"}
            extra = [
                name
                for name in list(selected.coords) + list(selected.data_vars)
                if name != variable and name not in skeleton_coords
            ]
            if extra:
                selected = selected.drop_vars(extra)

        return selected

    def _align_to_store(self, ds: xr.Dataset, variable: str, year: int) -> xr.Dataset:
        """Snap *ds* coordinates to the store grid and verify alignment.

        Finds the nearest store grid point for each downloaded coordinate
        value and replaces the coordinate with that exact grid value.  This
        ensures that ``to_zarr(region="auto")`` can compute integer index
        offsets without rounding errors.

        Parameters
        ----------
        ds : xr.Dataset
            Single-year dataset with ``time``, ``latitude``, ``longitude``
            dimension coordinates.
        variable : str
            Datavia variable name — used in error messages only.
        year : int
            Target calendar year — used in error messages only.

        Returns
        -------
        xr.Dataset
            Dataset with latitude/longitude coordinates snapped to the exact
            store grid values.

        Raises
        ------
        ValueError
            If any coordinate value deviates from its nearest store grid point
            by more than half a grid cell (tolerance = ``step / 2``).
        """
        store_lat = self._grid["latitude"].astype(float)
        store_lon = self._grid["longitude"].astype(float)

        lat_step = abs(float(store_lat[1] - store_lat[0]))
        lon_step = abs(float(store_lon[1] - store_lon[0]))

        snapped_lats = _snap_coords(
            ds.latitude.values.astype(float),
            store_lat,
            lat_step,
            "latitude",
            self._source_name,
            variable,
            year,
        )
        snapped_lons = _snap_coords(
            ds.longitude.values.astype(float),
            store_lon,
            lon_step,
            "longitude",
            self._source_name,
            variable,
            year,
        )

        return ds.assign_coords(latitude=snapped_lats, longitude=snapped_lons)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _projected_bbox_to_wgs84(
    has_data: xr.DataArray,
    crs: str,
    as_tuple: bool,
) -> tuple[float, float, float, float] | str:
    """Convert the non-null spatial extent of a projected DataArray to WGS84.

    Extracts the x/y extent of cells with at least one non-NaN value and
    reprojects the four bbox corners to EPSG:4326 using ``pyproj``.

    Parameters
    ----------
    has_data : xr.DataArray
        Boolean 2-D DataArray with ``x`` and ``y`` dimensions indicating
        which cells contain valid data.
    crs : str
        Native CRS of the store, e.g. ``"EPSG:3035"``.
    as_tuple : bool
        When ``True`` return ``(west, south, east, north)`` floats.
        When ``False`` return a WKT ``POLYGON ((...))`` string.

    Returns
    -------
    tuple[float, float, float, float] or str
        Bounding box in EPSG:4326, in the format selected by *as_tuple*.
    """
    try:
        import pyproj
    except ImportError as exc:
        raise ImportError(
            "pyproj is required to compute bounding boxes from projected Zarr stores. "
            "Install with `pip install pyproj`."
        ) from exc

    x_mask = has_data.any(dim="y")
    y_mask = has_data.any(dim="x")
    xs = has_data.x.values[x_mask.values]
    ys = has_data.y.values[y_mask.values]
    if len(xs) == 0 or len(ys) == 0:
        xs = has_data.x.values
        ys = has_data.y.values

    x_min, x_max = float(xs.min()), float(xs.max())
    y_min, y_max = float(ys.min()), float(ys.max())

    transformer = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    # Reproject all four corners and take the envelope.
    corners_x = [x_min, x_max, x_max, x_min]
    corners_y = [y_min, y_min, y_max, y_max]
    lons, lats = transformer.transform(corners_x, corners_y)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)

    if as_tuple:
        return (west, south, east, north)
    return (
        f"POLYGON (({west} {south}, {east} {south}, "
        f"{east} {north}, {west} {north}, {west} {south}))"
    )


def _snap_coords(
    values: np.ndarray,
    grid: np.ndarray,
    step: float,
    axis_name: str,
    source_name: str,
    variable: str,
    year: int,
) -> np.ndarray:
    """Snap each value in *values* to the nearest point in *grid*.

    Parameters
    ----------
    values : np.ndarray
        1-D array of coordinate values to snap.
    grid : np.ndarray
        1-D array of reference grid values.
    step : float
        Grid step size.  Values farther than ``step / 2`` from the nearest
        grid point trigger a ``ValueError``.
    axis_name : str
        ``"latitude"`` or ``"longitude"`` — used in error messages.
    source_name : str
        Source identifier — used in error messages.
    variable : str
        Variable name — used in error messages.
    year : int
        Calendar year — used in error messages.

    Returns
    -------
    np.ndarray
        1-D array of snapped values taken directly from *grid*.

    Raises
    ------
    ValueError
        If any value deviates from its nearest grid point by more than
        ``step / 2``.
    """
    snapped = np.empty(len(values), dtype=float)
    tol = step / 2.0

    for i, v in enumerate(values):
        diffs = np.abs(grid - v)
        idx = int(np.argmin(diffs))
        if diffs[idx] > tol:
            raise ValueError(
                f"{axis_name.capitalize()} {v:.6f} is {diffs[idx]:.6f}° from the "
                f"nearest store grid point ({grid[idx]:.6f}°) for "
                f"{source_name}/{variable}/{year}.  "
                f"Tolerance is {tol:.6f}° (half a grid cell of {step:.6f}°)."
            )
        snapped[i] = grid[idx]

    return snapped


def _check_sentinel(path: Path) -> None:
    """Raise ``RuntimeError`` if *path* contains a ``.write_in_progress`` sentinel.

    Parameters
    ----------
    path : Path
        Zarr store directory to check.

    Raises
    ------
    RuntimeError
        If the sentinel file is present.
    """
    sentinel = _sentinel_path(path)
    if sentinel.exists():
        raise RuntimeError(
            f"Store {path} has a '.write_in_progress' sentinel, indicating a "
            "previous write was interrupted.  "
            "Run CoverageManager.rebuild_from_store() to recover the store."
        )
