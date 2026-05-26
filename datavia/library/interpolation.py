"""Spatial and temporal interpolation methods for pipeline use."""

import logging
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
    pd = None  # type: ignore[assignment]
    PANDAS_AVAILABLE = False

from .coordinate_transforms import transform_coordinates

logger = logging.getLogger(__name__)


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
        - For EPSG:4326: (lon, lat) pairs where coords[:, 0] = longitude, coords[:, 1] = latitude
        - For EPSG:25832: (x, y) pairs where coords[:, 0] = easting, coords[:, 1] = northing
    coords_crs : str, default 'EPSG:4326'
        CRS of input coordinates
    interpolation_order : int, default 3
        Interpolation order (1=linear, 3=cubic)
    band : int, default 1
        Band number to interpolate (1-indexed). Use for multi-band TIFFs to
        select a specific band; defaults to band 1 for backward compatibility.

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
        # Note: map_coordinates expects (row, col) order - handled by rowcol conversion above
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


def interpolate_netcdf(
    nc_path: str,
    lat: float,
    lon: float,
    variable: str,
    datetime_utc: Any,
) -> float | np.ndarray:
    r"""Sample a NetCDF variable at a geographic point for one or more time steps.

    Opens the file with xarray, selects the nearest time step(s) to
    *datetime_utc*, and bilinearly interpolates the variable to (*lat*, *lon*).
    When *datetime_utc* is a single ``datetime``-like object a scalar ``float``
    is returned; when it is a sequence an ``np.ndarray`` of shape ``(T,)`` is
    returned instead.

    Parameters
    ----------
    nc_path : str
        Absolute path to the NetCDF file (``*.nc``).
    lat : float
        Geographic latitude in degrees North (EPSG:4326).
    lon : float
        Geographic longitude in degrees East (EPSG:4326).
    variable : str
        Name of the variable to sample, e.g. ``"temperature_2m"``.
    datetime_utc : datetime-like or sequence of datetime-like
        One or more UTC timestamps.  Passed directly to
        ``xarray.Dataset.sel`` with ``method="nearest"``.

    Returns
    -------
    float
        Interpolated scalar value when a single timestamp is provided.
    np.ndarray
        Array of shape ``(T,)`` when a sequence of timestamps is provided.

    Raises
    ------
    ImportError
        If xarray is not installed.
    KeyError
        If *variable* does not exist in the NetCDF file.
    """
    if not XARRAY_AVAILABLE:
        raise ImportError(
            "xarray is required for NetCDF interpolation. "
            "Install datavia-weather or run `pip install xarray netCDF4`."
        )

    with xr.open_dataset(nc_path) as ds:
        if variable not in ds.data_vars:
            raise KeyError(
                f"Variable '{variable}' not found in '{nc_path}'. "
                f"Available variables: {list(ds.data_vars)}"
            )

        # Detect coordinate names (ERA5 uses 'latitude'/'longitude',
        # ICON may use 'lat'/'lon').
        lat_name = "latitude" if "latitude" in ds.coords else "lat"
        lon_name = "longitude" if "longitude" in ds.coords else "lon"

        # Bilinear spatial interpolation to the requested point.
        point = ds[variable].interp(
            {lat_name: lat, lon_name: lon},
            method="linear",
        )

        # Temporal selection: nearest available time step.
        if "time" in point.coords:
            point = point.sel(time=datetime_utc, method="nearest")

        values = point.values

    if values.ndim == 0:
        return float(values)
    return np.asarray(values, dtype=float)


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
        Search radius in kilometres.  Defaults to 50 km.

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
    earth_radius_km = 6371.0
    dlat = np.radians(df["latitude"].values - lat)
    dlon = np.radians(df["longitude"].values - lon)
    dist_km: np.ndarray = earth_radius_km * np.sqrt(
        dlat**2 + (np.cos(lat_rad) * dlon) ** 2
    )
    df = df[dist_km <= radius_km].copy()
    dist_km = dist_km[dist_km <= radius_km]

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
    distances = np.where(distances < 1e-9, 1e-9, distances)
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
