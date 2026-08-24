"""Spatial and temporal interpolation methods for pipeline use."""

import logging
import os
from typing import Any, Literal

import numpy as np

try:
    import rasterio
except ImportError:  # pragma: no cover - optional dependency
    rasterio = None
from scipy.ndimage import distance_transform_edt, map_coordinates

try:
    import xarray as xr

    XARRAY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    xr = None  # type: ignore[assignment]
    XARRAY_AVAILABLE = False

try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    pd = None
    PANDAS_AVAILABLE = False

try:
    import pyproj

    PYPROJ_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    pyproj = None  # type: ignore[assignment]
    PYPROJ_AVAILABLE = False

from .coordinate_transforms import transform_coordinates

logger = logging.getLogger(__name__)

#: Mean Earth radius in kilometres, used for equirectangular distance approximation.
_EARTH_RADIUS_KM: float = 6371.0

#: Distance floor in km to prevent division by zero when a query point
#: coincides exactly with a station location during IDW weighting.
_COINCIDENT_STATION_EPSILON_KM: float = 1e-9


def _to_naive_ts(raw: Any) -> "pd.Timestamp":
    """Coerce *raw* to a timezone-naive UTC :class:`pandas.Timestamp`.

    Parameters
    ----------
    raw : Any
        A datetime-like value (string, Timestamp, datetime, etc.).

    Returns
    -------
    pd.Timestamp
        Timezone-naive UTC timestamp.
    """
    t = pd.Timestamp(raw)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def spatial_interpolate(
    tiff_path: str,
    coords: np.ndarray,
    coords_crs: str = "EPSG:4326",
    interpolation_order: int = 3,
    band: int = 1,
) -> np.ndarray:
    """
    Cubic interpolation of raster values at given coordinates.

    Parameters
    ----------
    tiff_path : str
        Path to GeoTIFF raster file
    coords : np.ndarray
        Array of coordinates, shape (N, 2)
                - For EPSG:4326: (lon, lat) pairs where
                    coords[:, 0] = longitude and coords[:, 1] = latitude
                - For EPSG:25832: (x, y) pairs where
                    coords[:, 0] = easting and coords[:, 1] = northing
    coords_crs : str, default 'EPSG:4326'
        CRS of input coordinates
    interpolation_order : int, default 3
        Interpolation order (1=linear, 3=cubic)
    band : int, default 1
        Band number to interpolate (1-indexed). Use for multi-band TIFFs to
        select a specific band; defaults to band 1 (first band).

    Returns
    -------
    np.ndarray
        Interpolated values at each coordinate location

    Raises
    ------
    ImportError
        If required geospatial libraries are not available
    """
    if rasterio is None:
        raise ImportError(
            "rasterio is required for spatial interpolation. "
            "Install with `pip install rasterio`."
        )

    with rasterio.open(tiff_path) as src:
        # Ensure coords is a numpy array so 2-D indexing works with plain lists
        coords = np.asarray(coords, dtype=float)

        # Transform coordinates to raster CRS if needed
        if coords_crs != str(src.crs):
            coords = transform_coordinates(coords, coords_crs, str(src.crs))

        # Read the requested band (1-indexed); clamp to valid range
        band_idx = max(1, min(band, src.count))
        band_data = src.read(band_idx).astype(np.float64)

        # Nodata handling: fill nodata cells with the value of the nearest valid
        # pixel (nearest-neighbor propagation via distance transform).  This
        # preserves the actual local signal rather than using a global median, so
        # if a query point lands exactly on a nodata pixel its interpolated value
        # is still derived from real nearby measurements.  Out-of-domain points
        # (fully outside the raster extent) return NaN via mode='constant'.
        nodata = src.nodata
        if nodata is not None:
            nodata_mask = band_data == nodata
            if nodata_mask.any() and not nodata_mask.all():
                # For each nodata pixel, find the nearest valid pixel index.
                # Cast to ndarray explicitly so mypy can verify downstream indexing.
                _, nearest_idx = distance_transform_edt(
                    nodata_mask, return_indices=True
                )
                nearest_idx = np.asarray(nearest_idx)
                filled = band_data.copy()
                filled[nodata_mask] = band_data[
                    nearest_idx[0][nodata_mask], nearest_idx[1][nodata_mask]
                ]
                band_data = filled

        transform = src.transform

        # Convert world coordinates to fractional row/col for all points at once
        cols, rows = ~transform * (coords[:, 0], coords[:, 1])

        # Stack coordinates for scipy.ndimage.map_coordinates
        # map_coordinates expects (row, col) order;
        # rowcol conversion above already provides that order.
        coord_array = np.vstack([rows, cols])

        # Perform interpolation for all points simultaneously
        coordinates = np.asarray(coord_array, dtype=np.float64)

        # Ensure order is one of the accepted literal values
        order_val = min(max(interpolation_order, 0), 5)
        if order_val == 0:
            order_literal: Literal[0, 1, 2, 3, 4, 5] = 0
        elif order_val == 1:
            order_literal = 1
        elif order_val == 2:
            order_literal = 2
        elif order_val == 3:
            order_literal = 3
        elif order_val == 4:
            order_literal = 4
        else:
            order_literal = 5

        interpolated_values = map_coordinates(
            band_data,
            coordinates,
            order=order_literal,
            cval=np.nan,
            prefilter=interpolation_order > 1,
            mode="constant",
        )

        result = np.asarray(interpolated_values)

        return result


def _build_spatial_interp_coords(
    ds: "xr.Dataset",
    variable: str,
    lats: np.ndarray,
    lons: np.ndarray,
    input_crs: str = "EPSG:4326",
) -> dict[str, "xr.DataArray"]:
    """Build vectorised xarray.DataArray.interp() coordinates for N input points.

    Detects whether the dataset uses geographic coordinates (ERA5-style
    ``latitude``/``longitude``) or projected coordinates (HYRAS-style ``x``/``y``
    with a CF ``grid_mapping`` attribute).  Input coordinates are reprojected to
    the dataset's native CRS as needed using a single ``pyproj`` batch call.

    Parameters
    ----------
    ds : xr.Dataset
        Open xarray dataset.
    variable : str
        Variable name; used to read the ``grid_mapping`` attribute.
    lats : np.ndarray
        Latitudes (or northing values) of input points, shape ``(N,)``.
        Interpreted in *input_crs*.
    lons : np.ndarray
        Longitudes (or easting values) of input points, shape ``(N,)``.
        Interpreted in *input_crs*.
    input_crs : str, optional
        CRS of the input *lats*/*lons* coordinates, given as an EPSG string
        (e.g. ``"EPSG:4326"``, ``"EPSG:3035"``).  Defaults to
        ``"EPSG:4326"``.  Any CRS understood by ``pyproj`` is accepted.

    Returns
    -------
    dict[str, xr.DataArray]
        Keyword arguments suitable for ``ds[variable].interp(**kwargs)`` where
        each value is an ``xr.DataArray`` of shape ``(N,)`` with dim
        ``"points"``.  For ERA5: ``{"latitude": ..., "longitude": ...}``;
        for HYRAS: ``{"y": ..., "x": ...}``.

    Raises
    ------
    ImportError
        If coordinate reprojection is required but ``pyproj`` is not
        installed.
    """
    grid_mapping_name = ds[variable].attrs.get("grid_mapping")

    # Projected grid (e.g. HYRAS EPSG:3035): dimensions are x/y in meters.
    if (
        grid_mapping_name
        and grid_mapping_name in ds
        and "x" in ds.dims
        and "y" in ds.dims
    ):
        if not PYPROJ_AVAILABLE:
            raise ImportError(
                "pyproj is required to interpolate projected NetCDF files "
                "(e.g. HYRAS EPSG:3035). Install with `pip install pyproj`."
            )
        crs_file = pyproj.CRS.from_cf(ds[grid_mapping_name].attrs)
        transformer = get_transformer(input_crs, crs_file)
        # Batch-reproject all N points in a single transformer call.
        x_arr, y_arr = transformer.transform(lons, lats)
        return {
            "y": xr.DataArray(y_arr, dims="points"),
            "x": xr.DataArray(x_arr, dims="points"),
        }

    # Geographic grid (ERA5, ICON): dimensions are latitude/longitude in degrees.
    # When input is not WGS84, reproject to EPSG:4326 degrees first.
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    if input_crs != "EPSG:4326":
        if not PYPROJ_AVAILABLE:
            raise ImportError(
                "pyproj is required to reproject non-EPSG:4326 input "
                "coordinates for geographic NetCDF files. "
                "Install with `pip install pyproj`."
            )
        t = transformer = get_transformer(input_crs, "EPSG:4326")
        lons, lats = t.transform(lons, lats)
    return {
        lat_name: xr.DataArray(lats, dims="points"),
        lon_name: xr.DataArray(lons, dims="points"),
    }


def interpolate_netcdf(
    nc_path: str | list[str],
    lats: float | np.ndarray,
    lons: float | np.ndarray,
    variable: str,
    datetime_utc: Any,
    input_crs: str = "EPSG:4326",
    temporal_resolution: str = "daily",
) -> float | np.ndarray:
    r"""Sample a NetCDF variable at one or more geographic points.

    Opens the file **once** with xarray and interpolates all coordinates in a
    single vectorised call, eliminating per-point file-open overhead.
    Spatial gaps are assumed to already be filled at write time — by
    :func:`prepare_netcdf` for legacy NetCDF sources, or by
    :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.write_dataset`
    at ingestion for Zarr-backed datasets — so no fill is performed here.

    Supports both geographic coordinate files (ERA5: ``latitude``/``longitude``
    dimensions in degrees) and projected coordinate files (HYRAS: ``x``/``y``
    dimensions in meters with a CF ``grid_mapping`` attribute).  The correct
    interpolation path is selected automatically; ``pyproj`` is used for
    reprojection when the file carries a ``grid_mapping``.

    ERA5 files produced by ``cdsapi >= 0.7`` name their time dimension
    ``valid_time`` instead of ``time``; both names are handled transparently.

    Parameters
    ----------
    nc_path : str or list[str]
        Absolute path to a NetCDF file (``*.nc``), or a list of paths that
        will each be opened with :func:`xarray.open_dataset` and concatenated
        along the ``"time"`` dimension with :func:`xarray.concat`.  Use a
        list when the query time range spans multiple monthly chunks.
        A plain string is opened with :func:`xarray.open_dataset` (unchanged
        single-file behaviour).
    lats : float or np.ndarray
        Latitude(s) of the query point(s) in *input_crs*.  A scalar float
        produces a scalar (or 1-D time-series) return value; an array of
        shape ``(N,)`` produces an ``(N,)`` array.
    lons : float or np.ndarray
        Longitude(s) (or easting values) of the query point(s) in
        *input_crs*.  Must be the same shape as *lats*.
    variable : str
        Name of the variable to sample, e.g. ``"tas"`` or ``"2m_temperature"``.
    datetime_utc : datetime-like or sequence of datetime-like
        One or more UTC timestamps, supplied as a string
        (e.g. ``"2024-06-15T12:00:00"`` or ``"2024-06-15T12:00:00Z"``), a
        :class:`numpy.datetime64`, a :class:`pandas.Timestamp`, or a Python
        :class:`datetime.datetime`.  Timezone-aware values (including the
        trailing ``Z`` notation) are accepted: they are converted to UTC and
        then stripped of timezone information before comparison with the
        timezone-naive time axis stored in ERA5/HYRAS NetCDF files.
        Used with ``method="nearest"`` for ``temporal_resolution="daily"``.
        For ``temporal_resolution="hourly"``, used to identify the calendar
        day from which all sub-daily time steps are returned.
    input_crs : str, optional
        CRS of the input *lats*/*lons* coordinates, as an EPSG string
        (e.g. ``"EPSG:4326"`` or ``"EPSG:3035"``).  Defaults to
        ``"EPSG:4326"``.  Coordinates are reprojected to the file's native
        CRS automatically.
    temporal_resolution : str, optional
        ``"daily"`` (default) — return the single nearest time step.
        ``"hourly"`` — return all sub-daily time steps for the requested day
        as an extra trailing dimension.

    Returns
    -------
    float
        Interpolated scalar value when *lats*/*lons* are scalars,
        ``temporal_resolution="daily"``, and a single timestamp is provided.
    np.ndarray
        - Shape ``(T,)`` — scalar coordinate, sequence of daily timestamps.
        - Shape ``(N,)`` — array of coordinates, single daily timestamp.
        - Shape ``(N, T)`` — array of coordinates, sequence of daily timestamps.
        - Shape ``(T,)`` — scalar coordinate, ``temporal_resolution="hourly"``,
          all *T* sub-daily steps for the requested day.
        - Shape ``(N, T)`` — array of coordinates, ``temporal_resolution="hourly"``.

    Raises
    ------
    ImportError
        If xarray is not installed, or if the file uses projected coordinates
        and pyproj is not installed.
    KeyError
        If *variable* does not exist in the NetCDF file.
    ValueError
        If *temporal_resolution* is not ``"daily"`` or ``"hourly"``.

    Notes
    -----
    Spatial dimension coordinates are sorted to ascending order before
    gap-filling with :meth:`~xarray.DataArray.interpolate_na`.  This ensures
    compatibility with ERA5-Land files, which store the latitude dimension in
    descending order (North → South) as delivered by the CDS API.

    When *nc_path* is a list, each file is opened individually with
    :func:`xarray.open_dataset` and the results are concatenated in-memory
    using :func:`xarray.concat` along the detected time dimension (``"time"``
    or ``"valid_time"``).  This avoids any dependency on ``dask`` while still
    supporting multi-file queries.

    ERA5-Land NetCDF files store their time axis as timezone-naive
    ``datetime64`` values.  A timezone-aware *datetime_utc* value (e.g. one
    ending in ``"Z"`` or carrying a :attr:`~datetime.datetime.tzinfo`) is
    converted to UTC and then made timezone-naive before the nearest-neighbour
    lookup so that the types are always comparable.
    """
    if not XARRAY_AVAILABLE:
        raise ImportError(
            "xarray is required for NetCDF interpolation. "
            "Install datavia-weather or run `pip install xarray netCDF4`."
        )

    # Open one file or eagerly concatenate several files along the time
    # dimension.  A plain string uses open_dataset (existing single-file path,
    # unchanged).  A list with one entry is also opened directly to avoid a
    # spurious outer dimension.  A list with 2+ entries opens each file
    # individually and concatenates in memory with xr.concat — no dask
    # required.  The time dimension name is detected from the first file so
    # that ERA5 files (which use 'valid_time' from cdsapi >= 0.7) are handled
    # correctly; using dim="time" blindly would create a new outer dimension
    # instead of concatenating along the existing one.
    if isinstance(nc_path, list) and len(nc_path) > 1:
        opened_datasets = [xr.open_dataset(f) for f in nc_path]
        time_dim_name = next(
            (d for d in opened_datasets[0].dims if d in ("time", "valid_time")),
            "time",
        )
        dataset = xr.concat(opened_datasets, dim=time_dim_name)
        for _d in opened_datasets:
            _d.close()
    elif isinstance(nc_path, list):
        dataset = xr.open_dataset(nc_path[0])
    else:
        dataset = xr.open_dataset(nc_path)
    with dataset as ds:
        return interpolate_dataset(
            ds,
            lats=lats,
            lons=lons,
            variable=variable,
            datetime_utc=datetime_utc,
            input_crs=input_crs,
            temporal_resolution=temporal_resolution,
        )


def _detect_spatial_dims(da: "xr.DataArray") -> tuple[str, str] | None:
    """Return the ``(x_dim, y_dim)`` spatial dimension names of *da*, if any.

    Parameters
    ----------
    da : xr.DataArray
        Data variable to inspect.

    Returns
    -------
    tuple[str, str] or None
        ``(x_dim, y_dim)`` for a recognised projected (``x``/``y``) or
        geographic (``longitude``/``latitude`` or ``lon``/``lat``) pair, or
        ``None`` when *da* has no recognisable pair of spatial dimensions.
    """
    if "x" in da.dims and "y" in da.dims:
        return "x", "y"
    if "longitude" in da.dims and "latitude" in da.dims:
        return "longitude", "latitude"
    if "lon" in da.dims and "lat" in da.dims:
        return "lon", "lat"
    return None


def fill_spatial_gaps(
    da: "xr.DataArray",
    x_dim: str,
    y_dim: str,
    restore_order: bool = False,
) -> "xr.DataArray":
    """Mask the fill sentinel and replace nodata cells with the nearest neighbour.

    Shared by :func:`prepare_netcdf` (one-time, at save time) and the Zarr
    ingestion path in
    :meth:`~datavia.weather.zarr_store_manager.ZarrStoreManager.write_dataset`
    (one-time, at ingest time). :func:`interpolate_netcdf`/
    :func:`interpolate_dataset` assume the data was already filled by one of
    these and do not fill at query time.

    Parameters
    ----------
    da : xr.DataArray
        Data variable to fill.
    x_dim : str
        Name of the "horizontal" spatial dimension (``"x"``, ``"longitude"``,
        or ``"lon"``).
    y_dim : str
        Name of the "vertical" spatial dimension (``"y"``, ``"latitude"``, or
        ``"lat"``).
    restore_order : bool, optional
        When ``True``, restore each spatial dimension's original ascending/
        descending order after filling. Required by callers that write the
        result into a region-aligned Zarr store (``to_zarr(region="auto")``),
        which requires the written data's coordinate order to exactly match
        the store's existing order. Defaults to ``False``, which is
        appropriate for query-time callers where only the *values*, not the
        on-disk coordinate order, matter.

    Returns
    -------
    xr.DataArray
        *da* with the fill sentinel masked and nodata cells filled along
        both spatial dimensions.
    """
    fill_val = da.attrs.get("_FillValue", None)
    if fill_val is not None:
        da = da.where(da != fill_val)

    # ERA5-Land stores latitude in descending order (North → South).
    # xarray's interpolate_na with method="nearest" requires the dimension
    # coordinate to be monotonically increasing, so each spatial dimension
    # is sorted to ascending order first.  sortby() is order-agnostic: it
    # is a no-op when the coordinate is already ascending (HYRAS) and
    # reverses it when descending (ERA5).
    x_ascending = (
        bool(da[x_dim].values[0] <= da[x_dim].values[-1])
        if da.sizes[x_dim] > 1
        else True
    )
    y_ascending = (
        bool(da[y_dim].values[0] <= da[y_dim].values[-1])
        if da.sizes[y_dim] > 1
        else True
    )
    da = da.sortby(x_dim)
    da = da.sortby(y_dim)
    # Two sequential 1-D fills (x then y) approximate a 2-D nearest-
    # neighbour fill.  A true 2-D solution exists via
    # scipy.ndimage.distance_transform_edt, but it requires extracting raw
    # numpy arrays and iterating over time slices, making it considerably
    # more complex.  For the convex HYRAS/ERA5 grids used here the
    # directional approximation is adequate.
    #
    # Zarr-backed DataArrays are dask-chunked along the spatial dimensions.
    # interpolate_na with fill_value="extrapolate" uses apply_ufunc internally
    # and requires each interpolated dimension to be a single contiguous chunk.
    # Rechunk to -1 (one chunk per spatial dim) before filling so that dask
    # does not raise "consists of multiple chunks" errors.  For in-memory
    # arrays da.chunks is None/falsy and this branch is a no-op.
    if da.chunks:
        da = da.chunk({x_dim: -1, y_dim: -1})
    da = da.interpolate_na(dim=x_dim, method="nearest", fill_value="extrapolate")
    da = da.interpolate_na(dim=y_dim, method="nearest", fill_value="extrapolate")

    if restore_order:
        # region="auto" Zarr writes require the written data's coordinate
        # order to exactly match the store's existing order, so undo the
        # ascending sort above when the original order was descending.
        if not x_ascending:
            da = da.sortby(x_dim, ascending=False)
        if not y_ascending:
            da = da.sortby(y_dim, ascending=False)

    return da


def prepare_netcdf(path: str) -> None:
    """Fill spatial gaps in a NetCDF file's data variables, in place.

    Sorts each spatial dimension to ascending order and replaces missing
    cells with the nearest valid neighbour, for every data variable that has
    recognisable spatial dimensions. This is the one-time preparation that
    lets :func:`interpolate_netcdf`/:func:`interpolate_dataset` assume the
    data is already gap-filled and skip filling on every query.

    Supports both geographic coordinate files (ERA5-style ``latitude``/
    ``longitude`` or ``lat``/``lon`` dimensions) and projected coordinate
    files (HYRAS-style ``x``/``y`` dimensions).  Variables without a
    recognised pair of spatial dimensions are left untouched.

    The file is rewritten atomically: the prepared dataset is written to a
    temporary file in the same directory as *path*, and only once that
    write succeeds is it moved over *path*.  If preparation fails, *path*
    is left unmodified.

    Parameters
    ----------
    path : str
        Absolute path to the NetCDF file to prepare, in place.

    Raises
    ------
    ImportError
        If xarray is not installed.
    """
    if not XARRAY_AVAILABLE:
        raise ImportError(
            "xarray is required to prepare NetCDF files. "
            "Install datavia-weather or run `pip install xarray netCDF4`."
        )

    with xr.open_dataset(path) as ds:
        # Load fully into memory so the source file can be closed before the
        # temporary output file is written to a different path.
        prepared = ds.load()

    for var_name in list(prepared.data_vars):
        da = prepared[var_name]
        spatial_dims = _detect_spatial_dims(da)
        if spatial_dims is None:
            continue
        x_dim, y_dim = spatial_dims
        prepared[var_name] = fill_spatial_gaps(da, x_dim, y_dim)

    tmp_path = f"{path}.tmp"
    try:
        prepared.to_netcdf(tmp_path)
    finally:
        prepared.close()
    os.replace(tmp_path, path)


def interpolate_dataset(
    ds: "xr.Dataset",
    lats: float | np.ndarray,
    lons: float | np.ndarray,
    variable: str,
    datetime_utc: Any,
    input_crs: str = "EPSG:4326",
    temporal_resolution: str = "daily",
) -> float | np.ndarray:
    r"""Sample a variable from an already-open xarray Dataset at one or more points.

    Identical to :func:`interpolate_netcdf` but accepts a pre-opened
    :class:`xarray.Dataset` instead of a file path.  Use this overload when
    the dataset is already in memory or backed by a Zarr store — it avoids the
    redundant ``open_dataset`` call and works correctly with lazy Zarr-backed
    datasets (only the chunks touching the query region are loaded).

    Parameters
    ----------
    ds : xr.Dataset
        Open xarray Dataset containing *variable*.  May be backed by a NetCDF
        file, a Zarr store, or any other xarray-compatible source.  ERA5
        datasets with ``valid_time`` as the time dimension name are handled
        transparently.
    lats : float or np.ndarray
        Latitude(s) of the query point(s) in *input_crs*.  A scalar float
        produces a scalar (or 1-D time-series) return value; an array of
        shape ``(N,)`` produces an ``(N,)`` array.
    lons : float or np.ndarray
        Longitude(s) (or easting values) of the query point(s) in
        *input_crs*.  Must be the same shape as *lats*.
    variable : str
        Name of the variable to sample, e.g. ``"2m_temperature"``.
    datetime_utc : datetime-like or sequence of datetime-like
        One or more UTC timestamps.  See :func:`interpolate_netcdf` for
        full semantics.
    input_crs : str, optional
        CRS of the input *lats*/*lons* coordinates.  Defaults to
        ``"EPSG:4326"``.
    temporal_resolution : str, optional
        ``"daily"`` (default) or ``"hourly"``.  See :func:`interpolate_netcdf`
        for full semantics.

    Returns
    -------
    float or np.ndarray
        Interpolated value(s) with the same shape semantics as
        :func:`interpolate_netcdf`.

    Raises
    ------
    ImportError
        If xarray is not installed, or if projected coordinates are used and
        pyproj is not installed.
    KeyError
        If *variable* does not exist in *ds*.
    ValueError
        If *temporal_resolution* is not ``"daily"`` or ``"hourly"``.
    """
    if not XARRAY_AVAILABLE:
        raise ImportError(
            "xarray is required for dataset interpolation. "
            "Install datavia-weather or run `pip install xarray netCDF4`."
        )

    scalar_input = np.ndim(lats) == 0
    lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))
    lons_arr = np.atleast_1d(np.asarray(lons, dtype=float))

    if variable not in ds.data_vars:
        raise KeyError(
            f"Variable '{variable}' not found in the dataset. "
            f"Available variables: {list(ds.data_vars)}"
        )

    da = ds[variable]

    # Spatial gaps are assumed to already be filled at write time — by
    # prepare_netcdf() for legacy NetCDF sources, or by
    # ZarrStoreManager.write_dataset() at ingestion for Zarr-backed
    # datasets. Gaps that were never downloaded (or that fall between
    # independently-downloaded Zarr cells) remain NaN.

    # Build vectorised spatial interpolation coordinates for all N points
    # in one batch — single pyproj call, single xarray interp call.
    interp_coords = _build_spatial_interp_coords(
        ds, variable, lats_arr, lons_arr, input_crs
    )

    # Bilinear spatial interpolation across all N points simultaneously.
    point = da.interp(interp_coords, method="linear")

    # Temporal selection.
    # ERA5 files from cdsapi >= 0.7 use 'valid_time' instead of 'time'.
    if temporal_resolution not in ("daily", "hourly"):
        raise ValueError(
            f"temporal_resolution must be 'daily' or 'hourly', "
            f"got '{temporal_resolution}'."
        )
    time_dim = "time" if "time" in point.coords else "valid_time"
    if time_dim in point.coords:
        if temporal_resolution == "hourly":
            if not PANDAS_AVAILABLE:
                raise ImportError(
                    "pandas is required for temporal_resolution='hourly'. "
                    "Install with `pip install pandas`."
                )
            _raw = (
                datetime_utc[0]
                if isinstance(datetime_utc, (list, tuple))
                else datetime_utc
            )
            day_start = pd.Timestamp(_raw)
            if day_start.tzinfo is not None:
                day_start = day_start.tz_convert("UTC").tz_localize(None)
            day_start = day_start.normalize()
            day_end = day_start + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
            point = point.sel({time_dim: slice(day_start, day_end)})
        else:
            if isinstance(datetime_utc, (list, tuple)):
                ts_list = [_to_naive_ts(t) for t in datetime_utc]
                point = point.sel({time_dim: ts_list}, method="nearest")
            else:
                point = point.sel(
                    {time_dim: _to_naive_ts(datetime_utc)}, method="nearest"
                )

    values = np.asarray(point.values, dtype=float)

    # Restore scalar semantics when a single point was requested.
    if scalar_input:
        if values.ndim == 0:
            return float(values)
        if values.ndim == 1 and values.shape[0] == 1:
            return float(values[0])
        return values.squeeze()

    return values


def interpolate_station_parquet(
    parquet_path: str,
    lat: float,
    lon: float,
    variable: str,
    datetime_utc: Any,
    radius_km: float = 50.0,
) -> float:
    """Estimate a weather variable at a point using nearby station observations.

    Loads station records within *radius_km* of the target coordinate from a
    Parquet file, filters to the nearest time step, and computes an
    inverse-distance-weighted (IDW) average across all matched stations.

    Parameters
    ----------
    parquet_path : str
        Absolute path to the Parquet file containing station observations.
        Expected columns: ``latitude``, ``longitude``, ``datetime``,
        ``<variable>``.
    lat : float
        Target geographic latitude in degrees North.
    lon : float
        Target geographic longitude in degrees East.
    variable : str
        Name of the observation column to aggregate.
    datetime_utc : datetime-like
        Target UTC timestamp; the nearest available timestamp is used.
    radius_km : float, optional
        Search radius in kilometers.  Defaults to 50 km.

    Returns
    -------
    float
        IDW-weighted average of station observations at the target point.
        Returns ``float("nan")`` when no stations are found within the radius.

    Raises
    ------
    ImportError
        If pandas is not installed.
    KeyError
        If *variable* column is missing in the Parquet file.
    """
    if not PANDAS_AVAILABLE:
        raise ImportError(
            "pandas is required for station Parquet interpolation. "
            "Install datavia-weather or run `pip install pandas pyarrow`."
        )

    df: Any = pd.read_parquet(parquet_path)

    if variable not in df.columns:
        raise KeyError(
            f"Variable column '{variable}' not found in '{parquet_path}'. "
            f"Available columns: {list(df.columns)}"
        )

    # --- Radius filter via equirectangular approximation (fast, sufficient
    #     for the ~50 km radii used here; error <0.5 % at German latitudes). ---
    lat_rad = np.radians(lat)
    dlat = np.radians(df["latitude"].values - lat)
    dlon = np.radians(df["longitude"].values - lon)
    dist_km: np.ndarray = _EARTH_RADIUS_KM * np.sqrt(
        dlat**2 + (np.cos(lat_rad) * dlon) ** 2
    )
    # Reset the DataFrame index and rebuild dist_km together so that
    # df.index and dist_km positions remain aligned for the later IDW step.
    radius_mask = dist_km <= radius_km
    df = df[radius_mask].copy()
    dist_km = dist_km[radius_mask]
    df.reset_index(drop=True, inplace=True)

    if df.empty:
        logger.warning(
            "No stations found within %.1f km of (%.4f, %.4f) in '%s'",
            radius_km,
            lat,
            lon,
            parquet_path,
        )
        return float("nan")

    # Nearest time step selection.
    df["datetime"] = pd.to_datetime(df["datetime"])
    target = pd.Timestamp(datetime_utc)
    nearest_ts = df["datetime"].iloc[(df["datetime"] - target).abs().argmin()]
    df = df[df["datetime"] == nearest_ts]

    values = df[variable].values.astype(float)
    distances: np.ndarray = dist_km[df.index]

    # Avoid division by zero for coincident stations.
    distances = np.where(
        distances < _COINCIDENT_STATION_EPSILON_KM,
        _COINCIDENT_STATION_EPSILON_KM,
        distances,
    )
    weights = 1.0 / distances
    return float(np.average(values, weights=weights))


def blend_gridded_and_station(
    gridded_value: float,
    station_value: float,
    station_weight: float = 0.6,
) -> float:
    """Blend a gridded model value with a station-derived estimate.

    Performs a simple weighted average.  Station data are typically of higher
    quality near measured locations, so *station_weight* defaults to 0.6.
    The gridded weight is computed as ``1 - station_weight``.

    Parameters
    ----------
    gridded_value : float
        Value obtained from a gridded model file (e.g. ERA5 NetCDF).
    station_value : float
        Value estimated from nearby station observations.
    station_weight : float, optional
        Weight assigned to the station estimate. Must be in ``[0, 1]``.
        Defaults to ``0.6``.

    Returns
    -------
    float
        Weighted blend of *gridded_value* and *station_value*.

    Raises
    ------
    ValueError
        If *station_weight* is outside ``[0, 1]``.
    """
    if not 0.0 <= station_weight <= 1.0:
        raise ValueError(
            f"station_weight must be between 0 and 1, got {station_weight}."
        )
    gridded_weight = 1.0 - station_weight
    return float(gridded_weight * gridded_value + station_weight * station_value)
