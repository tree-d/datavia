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

HYRAS data is already in target units (°C, mm/day, W/m²) and does not
require unit conversion — see :data:`~datavia.weather.source_registry.SOURCE_REGISTRY`.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from datavia.core.downloader_url import URLDownloader

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
        **kwargs: Any,
    ) -> None:
        """Initialise the HYRAS downloader.

        Parameters
        ----------
        variables : list[str], optional
            Pipeline variable names to download, e.g.
            ``["2m_temperature", "total_precipitation"]``.
            Defaults to ``["2m_temperature"]``.  Each name must be a key in
            :data:`_VARIABLE_MAP`; requesting a variable not available from
            HYRAS (such as ``et0_fao_evapotranspiration``) raises
            :exc:`ValueError` at download time.
        date_start : date or str, optional
            First day of the download range (inclusive). Defaults to today.
        date_end : date or str, optional
            Last day of the download range (inclusive). Defaults to today.
        **kwargs : Any
            Additional keyword arguments forwarded to
            :class:`~datavia.core.downloader_url.URLDownloader`.
        """
        super().__init__(url=_HYRAS_BASE_URL, **kwargs)
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
        ``.dat`` extension to generic binary content.

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

        1. Validates the variable name against :data:`_VARIABLE_MAP`.
        2. Auto-discovers the exact filename via
           :meth:`_discover_latest_filename`.
        3. Downloads the file using the inherited retry-and-resume logic from
           :class:`~datavia.core.downloader_url.URLDownloader`.

        Returns
        -------
        str
            Newline-joined absolute paths of all successfully downloaded
            ``.nc`` files.  An empty string is returned if every download
            failed.

        Raises
        ------
        ValueError
            If any requested variable is not available from HYRAS (e.g.
            ``et0_fao_evapotranspiration``).
        RuntimeError
            If version auto-discovery fails for any ``(variable, year)`` pair.
        """
        unknown = [v for v in self.variables if v not in _VARIABLE_MAP]
        if unknown:
            raise ValueError(
                f"Variable(s) not available from HYRAS: {unknown}. "
                f"Available: {sorted(_VARIABLE_MAP)}."
            )

        years = self._years_in_range(self.date_start, self.date_end)
        downloaded_paths: list[str] = []

        for variable in self.variables:
            mapping = _VARIABLE_MAP[variable]
            subdir = mapping["subdir"]
            prefix = mapping["prefix"]
            subdir_url = f"{_HYRAS_BASE_URL}{subdir}/"

            for year in years:
                filename = self._discover_latest_filename(subdir_url, year, prefix)
                self.url = f"{subdir_url}{filename}"
                logger.info(
                    "Downloading HYRAS file: variable=%s, year=%d, url=%s",
                    variable,
                    year,
                    self.url,
                )
                path = super().download()
                if path != "failed":
                    downloaded_paths.append(path)
                    logger.info("HYRAS download complete: %s", path)
                else:
                    logger.warning(
                        "HYRAS download failed: variable=%s, year=%d, url=%s",
                        variable,
                        year,
                        self.url,
                    )

        return "\n".join(downloaded_paths)
