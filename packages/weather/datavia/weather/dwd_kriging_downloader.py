"""Kriged daily DWD station fields: the grid downloader of ``DWD_stations``.

:class:`DWDKrigingDownloader` turns the daily observations of the DWD
station network into daily grids, in two steps:

1. **Station data** — :class:`~datavia.weather.dwd_cdc.CDCClient` downloads
   (and caches) the station archives of the DWD Climate Data Center and reads
   one variable for a whole year.
2. **Kriging** — :func:`datavia.kriging.krige` interpolates the stations onto
   every land cell of the HYRAS 1 km grid (:mod:`datavia.weather.dwd_grid`)
   by *external drift kriging* (EDK): ordinary kriging extended by a linear
   dependence on terrain elevation, the "drift".  Each calendar month is
   kriged with one variogram, fitted to all station-days of that month and
   cached by :class:`~datavia.weather.variogram_cache.VariogramCache`.

The result is one NetCDF file per variable and calendar year, laid out like
a HYRAS file (dimensions ``time, y, x`` in EPSG:3035, HYRAS short variable
names, a CF ``crs`` grid-mapping variable), which
:class:`~datavia.weather.saver_weather.SaverWeather` ingests into
``<data_dir>/DWD_stations/<variable>/<year>.zarr`` exactly like HYRAS.

**Whole years.** Projected Zarr stores are written one whole year at a time,
so each file covers 1 January to the last published day of that year (at
most 31 December).  Months that the existing store already holds completely
are copied from it instead of being kriged again; only the running month and
months not yet in the store are kriged.

**Time stamps** follow HYRAS: day D is stamped at D 00:00 UTC, precipitation
(the 06-06 UTC day) at D 06:00 UTC.

**Cost.** Kriging is the expensive step: about 370 000 land cells per day, each
solving a kriging system over the reporting stations within ``max_dist``.  The
solve grows with the cube of their number, and its weights are reused only
while the same stations report on consecutive days.  Precipitation is
therefore much more expensive than the other variables: the rain gauge
network is about four times denser, and which gauges report changes almost
daily, while the climate stations report nearly every day.  The Mojo kernels
use all CPU cores; months already complete in the store are not kriged again.

Cache directories (``<data_dir>`` is ``get_config().data_directory``)::

    <data_dir>/DWD_stations/_cdc/         station archives (dwd_cdc)
    <data_dir>/DWD_stations/_grid/        cell elevation (dwd_grid)
    <data_dir>/DWD_stations/_variograms/  monthly variograms (variogram_cache)
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from datavia.core.interfaces import Downloader

from .dwd_cdc import CDC_PRODUCTS, CDCClient, StationData
from .dwd_grid import CELL_SIZE, GRID_CRS, GRID_X, GRID_Y, grid_elevation
from .variogram_cache import CachedVariogram, VariogramCache

logger = logging.getLogger(__name__)

#: Name of this source in the registry and on disk.
SOURCE_NAME: str = "DWD_stations"

#: Marks a missing station value for the kriging (never a real value).
_STATION_NODATA: float = -9999.0

#: A trailing day counts as published once this share of the stations that
#: reported on the busiest day of the year has reported on it too.
_MIN_REPORTING_SHARE: float = 0.5


@dataclass(frozen=True)
class KrigingSettings:
    """How one variable is kriged.

    Attributes
    ----------
    max_dist : float
        Only stations within this distance [m] of a cell take part.
    correct_negative : bool
        Set negative estimates to 0 (for precipitation).
    dist_zero : bool
        With a single station reporting within ``max_dist``, estimate 0 if it
        is farther than half a cell diagonal (for precipitation: a lone
        distant rain gauge says little about a dry cell).
    drift : bool
        ``True``: external drift kriging with the cell elevation as drift;
        ``False``: ordinary kriging.
    model : str
        Variogram model, ``"exponential"`` or ``"spherical"``.
    bin_width : float
        Width [m] of the distance bins of the empirical variogram.
    max_lag : float
        Largest station distance [m] used for the empirical variogram.
    max_elevation_gradient : float
        Station pairs with ``|dz| / distance`` above this are left out of the
        empirical variogram (pairs across steep terrain).
    min_pairs_per_bin : int
        Bins with fewer station pairs are merged into a neighbour.
    """

    max_dist: float
    correct_negative: bool = False
    dist_zero: bool = False
    drift: bool = True
    model: str = "exponential"
    bin_width: float = 1.0e3
    max_lag: float = 8.0e4
    max_elevation_gradient: float = 0.2
    min_pairs_per_bin: int = 30

    def variogram_settings(self) -> dict[str, Any]:
        """Return everything a variogram fit depends on besides the data.

        Used as the key of the :class:`VariogramCache`.

        Returns
        -------
        dict
            ``model``, ``bin_width``, ``max_lag``, ``max_elevation_gradient``,
            ``min_pairs_per_bin`` and the ``crs`` of the station coordinates.
        """
        return {
            "model": self.model,
            "bin_width": self.bin_width,
            "max_lag": self.max_lag,
            "max_elevation_gradient": self.max_elevation_gradient,
            "min_pairs_per_bin": self.min_pairs_per_bin,
            "crs": GRID_CRS,
        }


#: Kriging settings per variable: those of the operational German Drought
#: Monitor (precipitation and mean temperature), and the temperature settings
#: for the other temperature and humidity fields.
KRIGING_SETTINGS: dict[str, KrigingSettings] = {
    "total_precipitation": KrigingSettings(
        max_dist=1.0e5, correct_negative=True, dist_zero=True
    ),
    "2m_temperature": KrigingSettings(max_dist=1.5e5),
    "temperature_2m_max": KrigingSettings(max_dist=1.5e5),
    "temperature_2m_min": KrigingSettings(max_dist=1.5e5),
    "relative_humidity_2m": KrigingSettings(max_dist=1.5e5),
}

#: HYRAS short variable name, CF attributes and time-stamp hour per variable.
NC_VARIABLES: dict[str, dict[str, Any]] = {
    "2m_temperature": {
        "name": "tas",
        "hour": 0,
        "attrs": {
            "standard_name": "air_temperature",
            "long_name": "Daily Mean Air Temperature",
            "units": "degree_Celsius",
            "cell_methods": "time: mean",
        },
    },
    "temperature_2m_max": {
        "name": "tasmax",
        "hour": 0,
        "attrs": {
            "standard_name": "air_temperature",
            "long_name": "Daily Maximum Air Temperature",
            "units": "degree_Celsius",
            "cell_methods": "time: maximum",
        },
    },
    "temperature_2m_min": {
        "name": "tasmin",
        "hour": 0,
        "attrs": {
            "standard_name": "air_temperature",
            "long_name": "Daily Minimum Air Temperature",
            "units": "degree_Celsius",
            "cell_methods": "time: minimum",
        },
    },
    "total_precipitation": {
        "name": "pr",
        "hour": 6,
        "attrs": {
            "standard_name": "thickness_of_rainfall_amount",
            "long_name": "Daily Precipitation Sum (06-06 UTC)",
            "units": "mm",
            "cell_methods": "time: sum",
        },
    },
    "relative_humidity_2m": {
        "name": "hurs",
        "hour": 0,
        "attrs": {
            "standard_name": "relative_humidity",
            "long_name": "Daily Mean Relative Humidity",
            "units": "%",
            "cell_methods": "time: mean",
        },
    },
}


class DWDKrigingDownloader(Downloader):
    """Krige daily DWD station observations onto the HYRAS 1 km grid.

    One NetCDF file is produced per variable and calendar year touched by
    ``[date_start, date_end]``; see the module docstring for what it contains.

    Parameters
    ----------
    variables : list[str], optional
        Pipeline variable names, keys of :data:`KRIGING_SETTINGS`.  Defaults
        to ``["2m_temperature"]``.
    date_start, date_end : date or str, optional
        First and last day requested (inclusive).  Default: yesterday (UTC).
        Whole calendar years are produced.
    temporal_resolution : str, optional
        Must be ``"daily"`` (the default); the station data are daily.
    data_dir : str or Path, optional
        datavia data directory holding the caches and the existing stores.
        Defaults to ``get_config().data_directory``.
    client : CDCClient, optional
        Station data client; by default one caching in
        ``<data_dir>/DWD_stations/_cdc``.

    Raises
    ------
    ValueError
        If *temporal_resolution* is not ``"daily"``, or a variable is not
        supported.
    """

    def __init__(
        self,
        variables: list[str] | None = None,
        date_start: date | str | None = None,
        date_end: date | str | None = None,
        temporal_resolution: str = "daily",
        data_dir: str | Path | None = None,
        client: CDCClient | None = None,
    ) -> None:
        """Initialise the downloader; see the class docstring for the parameters."""
        if temporal_resolution != "daily":
            raise ValueError(
                "DWD_stations provides daily values only; "
                f"temporal_resolution={temporal_resolution!r} is not supported."
            )
        self.variables: list[str] = variables or ["2m_temperature"]
        unknown = [v for v in self.variables if v not in KRIGING_SETTINGS]
        if unknown:
            raise ValueError(
                f"Variables {unknown} are not available from DWD_stations. "
                f"Supported: {sorted(KRIGING_SETTINGS)}"
            )
        if data_dir is None:
            from datavia.config import get_config

            data_dir = get_config().data_directory
        self.data_dir = Path(data_dir)
        self.source_dir = self.data_dir / SOURCE_NAME
        self.client = client or CDCClient(self.source_dir / "_cdc")
        self.variogram_cache = VariogramCache(self.source_dir / "_variograms")
        yesterday = self.client.last_available_day(self.variables[0])
        self.date_start: date = _as_date(date_start) or yesterday
        self.date_end: date = _as_date(date_end) or yesterday
        if self.date_end < self.date_start:
            raise ValueError(
                f"date_end ({self.date_end}) must not be before "
                f"date_start ({self.date_start})"
            )

    def download(self) -> str:
        """Krige every requested variable and year; return the NetCDF paths.

        Returns
        -------
        str
            Newline-joined paths of the written ``.nc`` files (temporary
            files; the saver ingests and the pipeline discards them).  Years
            with no published day yet are skipped.

        Raises
        ------
        ImportError
            If ``datavia-kriging`` (with Mojo) or ``datavia-elevation`` is not
            installed.
        RuntimeError
            If the station data cannot be downloaded.
        """
        _kriging()  # fail early with a clear message when Mojo is missing
        elevation = grid_elevation(self.source_dir / "_grid")
        paths = []
        for variable in self.variables:
            for year in range(self.date_start.year, self.date_end.year + 1):
                path = self.krige_year(variable, year, elevation)
                if path is not None:
                    paths.append(str(path))
        return "\n".join(paths)

    def krige_year(
        self, variable: str, year: int, elevation: np.ndarray | None = None
    ) -> Path | None:
        """Krige one variable for one calendar year into a NetCDF file.

        Parameters
        ----------
        variable : str
            Pipeline variable name.
        year : int
            Calendar year.
        elevation : np.ndarray, optional
            Cell elevation from :func:`~datavia.weather.dwd_grid.grid_elevation`,
            shape ``(len(GRID_Y), len(GRID_X))``; read from its cache if not
            given.

        Returns
        -------
        Path or None
            The NetCDF file, or ``None`` if no day of *year* is published yet.
        """
        if elevation is None:
            elevation = grid_elevation(self.source_dir / "_grid")
        settings = KRIGING_SETTINGS[variable]
        first = date(year, 1, 1)
        last = min(date(year, 12, 31), self.client.last_available_day(variable))
        if last < first:
            logger.warning("DWD_stations %s %d: no published day yet", variable, year)
            return None

        reusable = self._stored_months(variable, year)
        months = pd.period_range(first, last, freq="M")
        to_krige = [m for m in months if m not in reusable]

        stations: StationData | None = None
        if to_krige:
            stations = self.client.station_data(variable, first, last)
            last = _last_published_day(stations, fallback=first)
            months = pd.period_range(first, last, freq="M")
            to_krige = [m for m in months if m not in reusable]

        dates = pd.date_range(first, last, freq="D")
        path = _create_netcdf(variable, year, dates, settings)
        land = np.isfinite(elevation)
        cells = _cells(land, elevation)
        logger.info(
            "DWD_stations %s %d: %s .. %s, %d months to krige, %d reused, %d cells",
            variable,
            year,
            first,
            last,
            len(to_krige),
            len(months) - len(to_krige),
            land.sum(),
        )

        variograms: dict[str, dict[str, Any]] = {}
        for month in months:
            month_days = (dates >= month.start_time) & (dates <= month.end_time)
            day_index = np.flatnonzero(month_days)
            if month in reusable:
                block = reusable[month]
            else:
                assert stations is not None  # stations are read when kriging
                month_data = stations.between(
                    dates[day_index[0]].date(), dates[day_index[-1]].date()
                )
                block, cached = self._krige_month(
                    variable, month, month_data, cells, land, settings
                )
                variograms[str(month)] = {
                    **cached.variogram.to_dict(),
                    "file": str(cached.path),
                }
            _write_days(path, NC_VARIABLES[variable]["name"], day_index, block)
        _set_global_attrs(path, variable, settings, variograms)
        return path

    # ------------------------------------------------------------------
    # Kriging one month
    # ------------------------------------------------------------------

    def _krige_month(
        self,
        variable: str,
        month: pd.Period,
        data: StationData,
        cells: tuple[np.ndarray, np.ndarray, np.ndarray],
        land: np.ndarray,
        settings: KrigingSettings,
    ) -> tuple[np.ndarray, CachedVariogram]:
        """Fit (or reuse) the month's variogram and krige its days.

        Returns
        -------
        block : np.ndarray
            Estimates, float32, shape ``(n_days, len(GRID_Y), len(GRID_X))``;
            NaN outside the land mask and where kriging gave no estimate.
        variogram : CachedVariogram
            The variogram used.
        """
        kriging = _kriging()
        sx, sy = _project_stations(data)
        sh = data.elevation
        values = np.where(np.isfinite(data.values), data.values, _STATION_NODATA)
        kwargs = _krige_kwargs(settings, kriging)
        n_days = int(np.isfinite(data.values).any(axis=0).sum())
        complete = data.dates[-1] >= month.end_time.normalize()

        def fit() -> Any:
            empty = np.empty(0)
            return kriging.krige(empty, empty, empty, sx, sy, sh, values, **kwargs)

        cached = self.variogram_cache.get_or_fit(
            variable,
            month.start_time.date(),
            settings.variogram_settings(),
            n_days=n_days,
            n_stations=data.n_stations,
            complete=bool(complete),
            fit=fit,
        )

        started = time.perf_counter()
        cx, cy, ch = cells
        result = kriging.krige(
            cx, cy, ch, sx, sy, sh, values, variogram=cached.variogram, **kwargs
        )
        estimates = np.where(
            result.estimates == kriging.OUT_NODATA, np.nan, result.estimates
        ).astype(np.float32)
        block = np.full((len(data.dates), *land.shape), np.nan, dtype=np.float32)
        block[:, land] = estimates.T
        logger.info(
            "DWD_stations %s %s kriged: %d days, %d stations, %.0f s "
            "(variogram %s, %d singular cell-days)",
            variable,
            month,
            len(data.dates),
            data.n_stations,
            time.perf_counter() - started,
            "cached" if cached.from_cache else "fitted",
            result.singular_count,
        )
        return block, cached

    # ------------------------------------------------------------------
    # Reusing an existing store
    # ------------------------------------------------------------------

    def _stored_months(self, variable: str, year: int) -> dict[pd.Period, np.ndarray]:
        """Return the months the existing year store holds completely.

        Returns
        -------
        dict
            ``{month: block}`` with ``block`` of shape
            ``(n_days, len(GRID_Y), len(GRID_X))``, for every month whose every
            day is in the store.  Empty if there is no (readable) store.
        """
        store = self.source_dir / variable / f"{year}.zarr"
        if not store.exists():
            return {}
        try:
            import xarray as xr

            with xr.open_zarr(store, consolidated=False) as ds:
                if variable not in ds or ds.sizes.get("x") != len(GRID_X):
                    return {}
                da = ds[variable].sortby("y")
                days = pd.DatetimeIndex(da["time"].values).normalize()
                months: dict[pd.Period, np.ndarray] = {}
                for month in pd.period_range(f"{year}-01", f"{year}-12", freq="M"):
                    wanted = pd.date_range(month.start_time, month.end_time, freq="D")
                    if wanted.isin(days).all():
                        index = days.get_indexer(wanted)
                        months[month] = np.asarray(
                            da.isel(time=index).values, dtype=np.float32
                        )
                return months
        except Exception as exc:  # an unreadable store is simply rebuilt
            logger.warning("Cannot reuse %s (%s); kriging the whole year", store, exc)
            return {}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _kriging() -> Any:
    """Import :mod:`datavia.kriging`, with a clear error when it is missing.

    Raises
    ------
    ImportError
        If datavia-kriging (or its Mojo runtime) is not installed.
    """
    try:
        import datavia.kriging as kriging
    except ImportError as exc:
        raise ImportError(
            "The DWD_stations source needs datavia-kriging: "
            "pip install 'datavia-weather[kriging]' (plus Mojo, see "
            "packages/kriging/README.md), or use the pixi 'kriging' feature."
        ) from exc
    return kriging


def _krige_kwargs(settings: KrigingSettings, kriging: Any) -> dict[str, Any]:
    """Keyword arguments of :func:`datavia.kriging.krige` for *settings*."""
    model_types = {"exponential": kriging.EXPONENTIAL, "spherical": kriging.SPHERICAL}
    return {
        "nodata": _STATION_NODATA,
        "max_dist": settings.max_dist,
        "drift": settings.drift,
        "model_type": model_types[settings.model],
        "correct_negative": settings.correct_negative,
        "dist_zero": settings.dist_zero,
        "threshold_dist": 0.5 * float(np.hypot(CELL_SIZE, CELL_SIZE)),
        "bin_width": settings.bin_width,
        "max_lag": settings.max_lag,
        "max_elevation_gradient": settings.max_elevation_gradient,
        "min_pairs_per_bin": settings.min_pairs_per_bin,
    }


def _cells(
    land: np.ndarray, elevation: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Easting, northing and elevation of the land cells, each 1-D."""
    xx, yy = np.meshgrid(GRID_X, GRID_Y)
    return xx[land], yy[land], elevation[land]


def _project_stations(data: StationData) -> tuple[np.ndarray, np.ndarray]:
    """Station positions in the grid CRS (metres)."""
    from datavia.library.coordinate_transforms import get_transformer

    x, y = get_transformer("EPSG:4326", GRID_CRS).transform(
        data.longitude, data.latitude
    )
    return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)


def _last_published_day(data: StationData, fallback: date) -> date:
    """The last day on which most stations have reported.

    The DWD publishes a day's values station by station during the next
    morning; a day that only a few stations have reported yet would be kriged
    from a thin, uneven network.  Trailing days with fewer than
    :data:`_MIN_REPORTING_SHARE` of the busiest day's stations are dropped.
    """
    reporting = np.isfinite(data.values).sum(axis=0)
    if reporting.size == 0 or reporting.max() == 0:
        return fallback
    published = np.flatnonzero(reporting >= _MIN_REPORTING_SHARE * reporting.max())
    last: date = data.dates[published[-1]].date()
    return last


def _create_netcdf(
    variable: str, year: int, dates: pd.DatetimeIndex, settings: KrigingSettings
) -> Path:
    """Create the year's NetCDF file with coordinates and an empty variable."""
    import netCDF4
    from pyproj import CRS

    spec = NC_VARIABLES[variable]
    with tempfile.NamedTemporaryFile(
        suffix=".nc", prefix=f"dwd_kriged_{spec['name']}_{year}_", delete=False
    ) as tmp:
        path = Path(tmp.name)
    stamps = dates + pd.Timedelta(hours=spec["hour"])
    with netCDF4.Dataset(path, "w", format="NETCDF4") as nc:
        nc.createDimension("time", len(dates))
        nc.createDimension("y", len(GRID_Y))
        nc.createDimension("x", len(GRID_X))
        t = nc.createVariable("time", "f8", ("time",))
        t.units = "hours since 1970-01-01 00:00:00"
        t.calendar = "standard"
        t.standard_name = "time"
        t[:] = (stamps - pd.Timestamp("1970-01-01")) / pd.Timedelta(hours=1)
        for name, values, standard_name in (
            ("y", GRID_Y, "projection_y_coordinate"),
            ("x", GRID_X, "projection_x_coordinate"),
        ):
            coord = nc.createVariable(name, "f8", (name,))
            coord.standard_name = standard_name
            coord.units = "m"
            coord[:] = values
        crs = nc.createVariable("crs", "i4")
        crs.setncatts(CRS(GRID_CRS).to_cf())
        crs.epsg_code = GRID_CRS
        data = nc.createVariable(
            spec["name"],
            "f4",
            ("time", "y", "x"),
            zlib=True,
            complevel=3,
            fill_value=np.float32(np.nan),
            chunksizes=(1, len(GRID_Y), len(GRID_X)),
        )
        data.setncatts(spec["attrs"])
        data.grid_mapping = "crs"
        data.kriging_settings = str(asdict(settings))
    return path


def _write_days(
    path: Path, name: str, day_index: np.ndarray, block: np.ndarray
) -> None:
    """Write the grids of consecutive days into the NetCDF variable *name*."""
    import netCDF4

    with netCDF4.Dataset(path, "a") as nc:
        nc[name][day_index[0] : day_index[-1] + 1] = block


def _set_global_attrs(
    path: Path,
    variable: str,
    settings: KrigingSettings,
    variograms: dict[str, dict[str, Any]],
) -> None:
    """Record provenance: source, method, settings and the variograms used."""
    import json

    import netCDF4

    with netCDF4.Dataset(path, "a") as nc:
        nc.setncatts(
            {
                "Conventions": "CF-1.8",
                "title": f"DWD station {variable}, external drift kriging, 1 km",
                "source": "DWD Climate Data Center daily station observations "
                f"({CDC_PRODUCTS[variable].group}/{CDC_PRODUCTS[variable].column})",
                "institution": "Deutscher Wetterdienst (data); datavia (kriging)",
                "method": "external drift kriging with DGM200 cell-mean elevation "
                "as drift"
                if settings.drift
                else "ordinary kriging",
                "kriging_settings": json.dumps(asdict(settings)),
                "variograms": json.dumps(variograms),
                "license": "DWD data: CC BY 4.0, Deutscher Wetterdienst",
            }
        )


def _as_date(value: date | str | None) -> date | None:
    """Parse an ISO date string (or pass a date through)."""
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


__all__ = [
    "KRIGING_SETTINGS",
    "NC_VARIABLES",
    "DWDKrigingDownloader",
    "KrigingSettings",
]
