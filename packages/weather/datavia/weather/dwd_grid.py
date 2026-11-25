"""The 1 km grid of the kriged ``DWD_stations`` source and its elevation drift.

Kriged DWD station fields are stored on exactly the grid of the DWD HYRAS
datasets (``HYRAS-DE``, 1 km, ETRS89-LAEA / EPSG:3035), so the two sources
can be compared cell by cell and share one coverage envelope.  The grid
constants below are those of the HYRAS v6-1 files (665 columns x 890 rows).

External drift kriging needs the elevation of every cell.  It is the mean of
the BKG DGM200 terrain model (200 m, EPSG:25832, Germany only; fetched with
:class:`datavia.elevation.ElevationPipeline`) over each 1 km cell: a kriged
value stands for its whole cell, so the drift must too.  Cells without any
DGM200 data (outside Germany) get NaN and are left out of the kriging.

The averaged elevation is cached in
``<data_dir>/DWD_stations/_grid/elevation_1km.npz``; computing it takes a
few seconds, and it never changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: CRS of the grid (ETRS89 / LAEA Europe, the HYRAS grid).
GRID_CRS: str = "EPSG:3035"

#: Cell size of the grid in metres.
CELL_SIZE: float = 1000.0

#: Easting of the cell centres in metres (west to east), as in HYRAS.
GRID_X: np.ndarray = np.arange(4021500.0, 4685500.0 + 1.0, CELL_SIZE)

#: Northing of the cell centres in metres (south to north), as in HYRAS.
GRID_Y: np.ndarray = np.arange(2674500.0, 3563500.0 + 1.0, CELL_SIZE)

#: DGM200 pixels below this are fill values (-9999 outside Germany, and the
#: declared nodata of about -3.4e38 along the edge), not terrain.
_DEM_MIN_VALID: float = -500.0

#: Version of the cached elevation file; bump when its computation changes.
_CACHE_VERSION: int = 1


def grid_elevation(
    cache_dir: str | Path, dem_path: str | Path | None = None
) -> np.ndarray:
    """Return the mean DGM200 elevation of every grid cell, cached.

    Parameters
    ----------
    cache_dir : str or Path
        Directory of the cache file ``elevation_1km.npz`` (created when
        needed), normally ``<data_dir>/DWD_stations/_grid``.
    dem_path : str or Path, optional
        GeoTIFF of the terrain model to average.  By default the DGM200 is
        downloaded (once) through :class:`datavia.elevation.ElevationPipeline`.
        Only used when the cache does not exist yet.

    Returns
    -------
    np.ndarray
        Elevation in metres, float64, shape ``(len(GRID_Y), len(GRID_X))``
        with row 0 the southernmost row; NaN where the terrain model has no
        data.

    Raises
    ------
    ImportError
        If ``datavia-elevation`` is needed but not installed.
    """
    cache = Path(cache_dir) / "elevation_1km.npz"
    if cache.exists():
        with np.load(cache) as f:
            if int(f["version"]) == _CACHE_VERSION and f["elevation"].shape == (
                len(GRID_Y),
                len(GRID_X),
            ):
                return np.asarray(f["elevation"])
        logger.info("Elevation cache %s is outdated; recomputing", cache)

    path = Path(dem_path) if dem_path is not None else _dgm200_path()
    logger.info("Averaging %s onto the 1 km %s grid", path.name, GRID_CRS)
    elevation = average_onto_grid(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, elevation=elevation, version=_CACHE_VERSION)
    tmp.replace(cache)
    return elevation


def average_onto_grid(dem_path: str | Path) -> np.ndarray:
    """Average a terrain raster onto the 1 km grid.

    Each grid cell gets the mean of the raster pixels that fall into it
    (``rasterio`` average resampling, area weighted), ignoring fill values.
    This works directly on the pixel footprints: each cell gets an area
    mean, not the point sample that
    :func:`datavia.library.interpolation.spatial_interpolate` returns.

    Parameters
    ----------
    dem_path : str or Path
        Single-band terrain GeoTIFF in any projected CRS.

    Returns
    -------
    np.ndarray
        Elevation in metres, shape ``(len(GRID_Y), len(GRID_X))``, row 0 the
        southernmost; NaN where no valid pixel falls into a cell.
    """
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float64)
        dem[~(dem >= _DEM_MIN_VALID)] = np.nan  # fill values and NaN
        # Rasters are north-up: the transform's origin is the top-left corner.
        dst_transform = from_origin(
            GRID_X[0] - CELL_SIZE / 2, GRID_Y[-1] + CELL_SIZE / 2, CELL_SIZE, CELL_SIZE
        )
        out = np.full((len(GRID_Y), len(GRID_X)), np.nan)
        reproject(
            source=dem,
            destination=out,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=GRID_CRS,
            dst_nodata=np.nan,
            resampling=Resampling.average,
        )
    return out[::-1]  # north-up rows -> row 0 southernmost, like GRID_Y


def _dgm200_path() -> Path:
    """Download the DGM200 through datavia if needed; return its GeoTIFF path.

    Raises
    ------
    ImportError
        If ``datavia-elevation`` is not installed.
    RuntimeError
        If no elevation layer is registered after the update.
    """
    try:
        from datavia.elevation import ElevationPipeline
    except ImportError as exc:
        raise ImportError(
            "The kriged DWD_stations source needs datavia-elevation for the "
            "terrain drift: pip install 'datavia-weather[kriging]'"
        ) from exc
    from datavia.library.database.query import get_raster_metadata

    pipeline = ElevationPipeline()
    pipeline.update_data()  # a no-op when the DGM200 is already downloaded
    layers = get_raster_metadata(pipeline.name)
    if not layers:
        raise RuntimeError(
            "No elevation layer registered after ElevationPipeline update"
        )
    return Path(layers[0]["uri"])
