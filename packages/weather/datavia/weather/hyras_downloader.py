"""
HYRASDownloader — fetches daily HYRAS climatological grids from the
DWD OpenData server for a configurable set of variables and date range.

HYRAS (Hydrometeorological Raster Dataset for Germany) provides daily
gridded observational data on a regular ETRS89-LAEA grid (EPSG:3035).
Files are freely available under CC BY 4.0 from:

  https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/

One NetCDF file covers a full calendar year for a single variable.  The
downloader auto-discovers the current version string (e.g. ``v6-1``) from
the DWD HTML directory listing so that version bumps are handled without
code changes.

HYRAS data is already in target units (°C, mm/day, %), except radiation,
which is converted from W/m² to PAR at query time.  See
:data:`~datavia.weather.source_registry.SOURCE_REGISTRY`.

HYRAS has no humidity grid besides relative humidity, so the vapour pressure
deficit (kPa) is derived here: from each year's ``tasmax``, ``tasmin`` and
``hurs`` files a ``vpd`` file is written (FAO-56, see
:mod:`datavia.weather.vapour_pressure`) and ingested like a HYRAS file.
"""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from datavia.core.downloader_url import URLDownloader

from .vapour_pressure import (
    CF_ATTRIBUTES,
    NC_NAME,
    vapour_pressure_deficit_from_relative_humidity,
)
from .vapour_pressure import VARIABLE as _VAPOUR_PRESSURE_DEFICIT

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

#: Root URL for all HYRAS daily grids.
_HYRAS_BASE_URL: str = (
    "https://opendata.dwd.de/climate_environment/CDC/grids_germany/daily/hyras_de/"
)

#: Maps pipeline variable names to their HYRAS-specific subdirectory,
#: filename prefix, and NetCDF variable name.
#:
#: ``et0_fao_evapotranspiration`` is intentionally absent — it is not
#: available from HYRAS and must be obtained from DWD stations.
_VARIABLE_MAP: dict[str, dict[str, str]] = {
    "2m_temperature": {
        "subdir": "air_temperature_mean",
        "prefix": "tas_hyras_1",
        "nc_variable": "tas",
    },
    "temperature_2m_max": {
        "subdir": "air_temperature_max",
        "prefix": "tasmax_hyras_1",
        "nc_variable": "tasmax",
    },
    "temperature_2m_min": {
        "subdir": "air_temperature_min",
        "prefix": "tasmin_hyras_1",
        "nc_variable": "tasmin",
    },
    "total_precipitation": {
        "subdir": "precipitation",
        "prefix": "pr_hyras_1",
        "nc_variable": "pr",
    },
    "surface_solar_radiation_downwards": {
        "subdir": "radiation_global",
        "prefix": "rsds_hyras_5",
        "nc_variable": "rsds",
    },
    "relative_humidity_2m": {
        "subdir": "humidity",
        "prefix": "hurs_hyras_1",
        "nc_variable": "hurs",
    },
}

#: HYRAS variables the vapour pressure deficit is computed from (FAO-56 with
#: the daily mean relative humidity), in the order the formula takes them.
_VAPOUR_PRESSURE_DEFICIT_INPUTS: tuple[str, str, str] = (
    "temperature_2m_max",
    "temperature_2m_min",
    "relative_humidity_2m",
)

#: Variables computed from downloaded HYRAS files instead of downloaded.
_DERIVED_VARIABLES: frozenset[str] = frozenset({_VAPOUR_PRESSURE_DEFICIT})

#: Days per dask chunk when deriving a variable from whole-year files.
_TIME_CHUNK_DAYS: int = 31


class HYRASDownloader(URLDownloader):
    """Download daily HYRAS grids from the DWD OpenData server.

    One NetCDF (``.nc``) file per variable per year is downloaded.  The date
    range is expanded to whole calendar years — e.g. ``2024-06-01`` to
    ``2025-02-28`` downloads the ``2024`` and ``2025`` annual files for each
    requested variable.

    The exact filename (including version string such as ``v6-1``) is
    auto-discovered by fetching the DWD HTML directory listing for each
    subdirectory.  This makes the downloader robust to DWD releasing updated
    versions without prior notice.

    Inherits the retry-and-resume logic from
    :class:`~datavia.core.downloader_url.URLDownloader`.
    """

    def __init__(
        self,
        variables: list[str] | None = None,
        date_start: date | str | None = None,
        date_end: date | str | None = None,
        temporal_resolution: str = "daily",
    ) -> None:
        """Initialise the HYRAS downloader.

        Parameters
        ----------
        variables : list[str], optional
            Pipeline variable names to download, e.g.
            ``["2m_temperature", "total_precipitation"]``.
            Defaults to ``["2m_temperature"]``.  Each name must be a key in
            :data:`_VARIABLE_MAP` or ``"vapour_pressure_deficit"`` (derived,
            see :meth:`download`); requesting a variable not available from
            HYRAS (such as ``et0_fao_evapotranspiration``) raises
            :exc:`ValueError` at download time.
        date_start : date or str, optional
            First day of the download range (inclusive). Defaults to today.
        date_end : date or str, optional
            Last day of the download range (inclusive). Defaults to today.
        temporal_resolution : str, optional
            Must be ``"daily"`` (default).  ``"hourly"`` raises
            :exc:`ValueError` immediately because HYRAS provides daily data
            only.

        Raises
        ------
        ValueError
            If *temporal_resolution* is ``"hourly"``.
        """
        if temporal_resolution == "hourly":
            raise ValueError(
                "HYRASDownloader does not support temporal_resolution='hourly'. "
                "HYRAS provides daily observations only."
            )
        super().__init__(url=_HYRAS_BASE_URL)
        self.variables: list[str] = variables or ["2m_temperature"]
        self.date_start: str = (
            str(date_start) if date_start is not None else str(date.today())
        )
        self.date_end: str = (
            str(date_end) if date_end is not None else str(date.today())
        )

    @staticmethod
    def _years_in_range(date_start: str, date_end: str) -> list[int]:
        """Return the sorted list of calendar years covered by the date range.

        The range is expanded to whole years so that every annual HYRAS NetCDF
        file that overlaps with ``[date_start, date_end]`` is included.

        Parameters
        ----------
        date_start : str
            ISO-8601 date string of the first day, e.g. ``"2024-06-01"``.
        date_end : str
            ISO-8601 date string of the last day, e.g. ``"2025-02-28"``.

        Returns
        -------
        list[int]
            Sorted list of integer years, e.g. ``[2024, 2025]``.

        Raises
        ------
        ValueError
            If ``date_end`` is earlier than ``date_start``.
        """
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
        if end < start:
            raise ValueError(
                f"date_end ({date_end}) must not be earlier than"
                f" date_start ({date_start})."
            )
        return list(range(start.year, end.year + 1))

    def _discover_latest_filename(self, subdir_url: str, year: int, prefix: str) -> str:
        """Fetch the DWD HTML directory listing and return the latest filename.

        DWD releases version updates (e.g. ``v6-1`` → ``v6-2``) without
        notice.  This method scrapes the directory listing HTML to find all
        filenames matching ``{prefix}_{year}_v{major}-{minor}_de.nc`` and
        returns the one with the highest ``(major, minor)`` version tuple.

        Parameters
        ----------
        subdir_url : str
            Full URL of the HYRAS subdirectory, e.g.
            ``"https://opendata.dwd.de/.../air_temperature_mean/"``.
        year : int
            Calendar year of the desired file.
        prefix : str
            HYRAS filename prefix, e.g. ``"tas_hyras_1"``.

        Returns
        -------
        str
            Exact filename of the latest version, e.g.
            ``"tas_hyras_1_2024_v6-1_de.nc"``.

        Raises
        ------
        RuntimeError
            If no matching file is found in the directory listing.
        """
        logger.info("Discovering HYRAS filename: subdir=%s, year=%d", subdir_url, year)
        response = self.session.get(subdir_url, timeout=30)
        response.raise_for_status()

        pattern = re.compile(rf"{re.escape(prefix)}_{year}_v(\d+)-(\d+)_de\.nc")
        matches = pattern.findall(response.text)
        if not matches:
            raise RuntimeError(
                f"No HYRAS file found for prefix='{prefix}', year={year}"
                f" at {subdir_url}. Directory listing may have changed."
            )

        best_major, best_minor = max(
            ((int(maj), int(mino)) for maj, mino in matches),
        )
        filename = f"{prefix}_{year}_v{best_major}-{best_minor}_de.nc"
        logger.info("Discovered HYRAS filename: %s", filename)
        return filename

    def _validate_content_type(self, content_type: str) -> bool:
        """Accept NetCDF and generic binary content types served by DWD.

        Parameters
        ----------
        content_type : str
            HTTP Content-Type header value from the DWD server response.

        Returns
        -------
        bool
            ``True`` when the content type is compatible with NetCDF files.
        """
        if not content_type:
            logger.warning("No Content-Type header — proceeding with download.")
            return True
        valid_types = [
            "application/octet-stream",
            "application/x-netcdf",
            "application/netcdf",
            "text/plain",
        ]
        return any(vt in content_type.lower() for vt in valid_types)

    def _get_final_filename(self, temp_path: str, content_type: str) -> str:
        """Return the temporary path with a ``.nc`` extension.

        Overrides the parent implementation which would otherwise assign a
        ``.dat`` extension to generic binary content.  The permanent,
        descriptive filename is assigned by
        :class:`~datavia.weather.saver_weather.SaverWeather` when it copies
        the file into the data directory.

        Parameters
        ----------
        temp_path : str
            Path to the partially downloaded temporary file (ends in
            ``.download``).
        content_type : str
            HTTP Content-Type header value (unused here).

        Returns
        -------
        str
            Path with the ``.download`` suffix replaced by ``.nc``.
        """
        return temp_path.rsplit(".", 1)[0] + ".nc"

    def download(self) -> str:
        """Download all HYRAS files for the configured variables and date range.

        For each ``(variable, year)`` pair the method:

        1. Validates the variable name against :data:`_VARIABLE_MAP` and
           :data:`_DERIVED_VARIABLES`.
        2. Auto-discovers the exact filename via
           :meth:`_discover_latest_filename`.
        3. Downloads the file using the inherited retry-and-resume logic from
           :class:`~datavia.core.downloader_url.URLDownloader`.

        ``vapour_pressure_deficit`` is not a HYRAS file: the ``tasmax``,
        ``tasmin`` and ``hurs`` files of each year are downloaded and combined
        into one ``vpd`` file by :func:`_write_vapour_pressure_deficit`.
        Input files of variables that were not requested themselves are
        deleted afterwards.

        Returns
        -------
        str
            Newline-joined absolute paths of all successfully downloaded or
            derived ``.nc`` files.  An empty string is returned if every
            download failed.

        Raises
        ------
        ValueError
            If any requested variable is not available from HYRAS (e.g.
            ``et0_fao_evapotranspiration``), or the inputs of the vapour
            pressure deficit do not share one grid and one set of days.
        RuntimeError
            If version auto-discovery fails for any ``(variable, year)`` pair.
        """
        available = set(_VARIABLE_MAP) | _DERIVED_VARIABLES
        unknown = [v for v in self.variables if v not in available]
        if unknown:
            raise ValueError(
                f"Variable(s) not available from HYRAS: {unknown}. "
                f"Available: {sorted(available)}."
            )

        years = self._years_in_range(self.date_start, self.date_end)
        files = self._files_to_download()
        downloaded: dict[tuple[str, int], str] = {}

        try:
            from tqdm import tqdm

            progress = tqdm(total=len(files) * len(years), desc="HYRAS", unit="file")
        except ImportError:
            progress = None

        try:
            for variable in files:
                for year in years:
                    path = self._download_file(variable, year)
                    if path is not None:
                        downloaded[(variable, year)] = path
                    if progress is not None:
                        progress.update(1)
            result = [
                path
                for (variable, _), path in downloaded.items()
                if variable in self.variables
            ]
            if _VAPOUR_PRESSURE_DEFICIT in self.variables:
                result.extend(self._derive_vapour_pressure_deficit(years, downloaded))
        finally:
            if progress is not None:
                progress.close()
            # Helper inputs are removed even when a download or the
            # derivation fails, so no temporary file is left behind.
            for (variable, _), path in downloaded.items():
                if variable not in self.variables:
                    Path(path).unlink(missing_ok=True)
        return "\n".join(result)

    def _files_to_download(self) -> list[str]:
        """Return the HYRAS files to fetch: requested ones plus derived inputs.

        Returns
        -------
        list[str]
            Pipeline names of :data:`_VARIABLE_MAP`, requested order first,
            each once.
        """
        files = [v for v in self.variables if v in _VARIABLE_MAP]
        if _VAPOUR_PRESSURE_DEFICIT in self.variables:
            files += [v for v in _VAPOUR_PRESSURE_DEFICIT_INPUTS if v not in files]
        return files

    def _download_file(self, variable: str, year: int) -> str | None:
        """Download the latest version of one ``(variable, year)`` HYRAS file.

        Parameters
        ----------
        variable : str
            Pipeline variable name, a key of :data:`_VARIABLE_MAP`.
        year : int
            Calendar year.

        Returns
        -------
        str or None
            Path of the downloaded ``.nc`` file, or ``None`` if the download
            failed.

        Raises
        ------
        RuntimeError
            If version auto-discovery fails.
        """
        mapping = _VARIABLE_MAP[variable]
        subdir_url = f"{_HYRAS_BASE_URL}{mapping['subdir']}/"
        filename = self._discover_latest_filename(subdir_url, year, mapping["prefix"])
        self.url = f"{subdir_url}{filename}"
        logger.info(
            "Downloading HYRAS file: variable=%s, year=%d, url=%s",
            variable,
            year,
            self.url,
        )
        path = super().download()
        if path == "failed":
            logger.warning(
                "HYRAS download failed: variable=%s, year=%d, url=%s",
                variable,
                year,
                self.url,
            )
            return None
        logger.info("HYRAS download complete: %s", path)
        return path

    @staticmethod
    def _derive_vapour_pressure_deficit(
        years: list[int], downloaded: dict[tuple[str, int], str]
    ) -> list[str]:
        """Write one vapour pressure deficit file per year whose inputs arrived.

        Parameters
        ----------
        years : list[int]
            The requested calendar years.
        downloaded : dict[tuple[str, int], str]
            Downloaded file per ``(variable, year)``.

        Returns
        -------
        list[str]
            Paths of the ``vpd`` files; years with a failed input download
            are skipped with a warning.

        Raises
        ------
        ValueError
            If the inputs of a year do not share one grid and one set of days.
        """
        paths = []
        for year in years:
            inputs = [
                downloaded.get((v, year)) for v in _VAPOUR_PRESSURE_DEFICIT_INPUTS
            ]
            if any(path is None for path in inputs):
                logger.warning(
                    "HYRAS %d: an input of the vapour pressure deficit failed to "
                    "download; skipping that year.",
                    year,
                )
                continue
            temperature_max, temperature_min, humidity = (str(p) for p in inputs)
            paths.append(
                _write_vapour_pressure_deficit(
                    temperature_max, temperature_min, humidity, year
                )
            )
        return paths


def _write_vapour_pressure_deficit(
    temperature_max_path: str,
    temperature_min_path: str,
    humidity_path: str,
    year: int,
) -> str:
    """Compute the daily vapour pressure deficit of one HYRAS year.

    FAO-56 with the daily mean relative humidity (see
    :func:`~datavia.weather.vapour_pressure.vapour_pressure_deficit_from_relative_humidity`),
    per grid cell and day.  The arrays are processed in dask chunks of
    :data:`_TIME_CHUNK_DAYS` days, so the three inputs are never all in
    memory at once.

    Parameters
    ----------
    temperature_max_path, temperature_min_path, humidity_path : str
        The year's HYRAS ``tasmax``, ``tasmin`` and ``hurs`` files.
    year : int
        Calendar year, used in the temporary file name.

    Returns
    -------
    str
        Path of a temporary NetCDF file with the variable ``vpd`` (kPa) on
        the HYRAS grid (``time, y, x``, EPSG:3035), the grid mapping and the
        time stamps of the ``tasmax`` file.

    Raises
    ------
    ValueError
        If the three files differ in grid or days.
    """
    import xarray as xr

    chunks = {"time": _TIME_CHUNK_DAYS}
    with (
        xr.open_dataset(temperature_max_path, chunks=chunks) as tasmax_ds,
        xr.open_dataset(temperature_min_path, chunks=chunks) as tasmin_ds,
        xr.open_dataset(humidity_path, chunks=chunks) as hurs_ds,
    ):
        temperature_max = tasmax_ds[_VARIABLE_MAP["temperature_2m_max"]["nc_variable"]]
        stamps = temperature_max["time"]
        arrays = [
            _by_day(ds[_VARIABLE_MAP[variable]["nc_variable"]])
            for ds, variable in zip(
                (tasmax_ds, tasmin_ds, hurs_ds),
                _VAPOUR_PRESSURE_DEFICIT_INPUTS,
                strict=True,
            )
        ]
        try:
            aligned = xr.align(*arrays, join="exact")
        except ValueError as exc:
            raise ValueError(
                f"HYRAS {year}: tasmax, tasmin and hurs differ in grid or days, "
                f"so no vapour pressure deficit can be computed: {exc}"
            ) from exc
        deficit = vapour_pressure_deficit_from_relative_humidity(*aligned)
        # Store the day under the tasmax stamp, as HYRAS does for its files.
        deficit = deficit.assign_coords(time=stamps.values)
        deficit.attrs = {**CF_ATTRIBUTES, "cell_methods": "time: mean"}
        dataset = deficit.to_dataset(name=NC_NAME)
        grid_mapping = temperature_max.attrs.get("grid_mapping")
        if grid_mapping is not None and grid_mapping in tasmax_ds:
            dataset[NC_NAME].attrs["grid_mapping"] = grid_mapping
            dataset[grid_mapping] = tasmax_ds[grid_mapping]
        with tempfile.NamedTemporaryFile(
            suffix=".nc", prefix=f"hyras_{NC_NAME}_{year}_", delete=False
        ) as tmp:
            path = tmp.name
        dataset.to_netcdf(
            path,
            encoding={
                NC_NAME: {
                    "dtype": "float32",
                    "zlib": True,
                    "complevel": 3,
                    "_FillValue": np.float32(np.nan),
                }
            },
        )
    logger.info("HYRAS %d: vapour pressure deficit written to %s", year, path)
    return path


def _by_day(values: xr.DataArray) -> xr.DataArray:
    """Return *values* with each time stamp floored to its day.

    The HYRAS variables stamp their days at different hours; flooring lets
    them be aligned by day.
    """
    return values.assign_coords(time=values["time"].dt.floor("D"))
