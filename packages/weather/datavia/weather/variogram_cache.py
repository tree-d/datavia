"""Cache of fitted variograms for the kriged ``DWD_stations`` source.

Fitting a variogram means binning the squared differences of all station
pairs over all days and fitting a model to them; it is the part of the
kriging that depends on the whole station network at once.  The
``DWD_stations`` source fits **one variogram per variable and calendar
month**, from all station-days of that month, and keeps it in this cache, so
updating a store or re-kriging a month reuses the fit instead of repeating it.

Cache layout (``root`` is normally ``<data_dir>/DWD_stations/_variograms``)::

    <root>/<variable>/<YYYY-MM>_<settings hash>.json

The *settings hash* is a short hash of everything the fit depends on besides
the data (model, bin width, largest lag, elevation-gradient filter, minimum
pairs per bin, and the CRS the distances are measured in).  Changing any of
them therefore never reuses an old fit; the old files simply stay unused.

Each file is a JSON document::

    {
      "version": 1,
      "variogram": {"model": "exponential", "nugget": ..., "sill": ...,
                    "range_m": ...},
      "bins": {"lag_m": [...], "semivariance": [...], "pair_count": [...]},
      "converged": true,
      "meta": {"variable": "total_precipitation", "month": "2025-06",
               "n_days": 30, "n_stations": 1987, "complete": true,
               "settings": {...}, "created": "2026-11-20T12:00:00+00:00"}
    }

``variogram`` and ``bins`` are the formats of
:meth:`datavia.kriging.Variogram.to_dict` and
:meth:`datavia.kriging.EmpiricalBins.to_dict`; only their public
``to_dict``/``from_dict`` methods are used, so the cache does not depend on
the internals of the kriging package.

**When a cached fit is reused.** A month that was complete when it was
fitted (every day of it already published) is never fitted again.  A month
that was still running (the current month) is fitted again once more days
are available than the cached fit used, so its variogram follows the data
as the month fills up.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datavia.kriging import EmpiricalBins, KrigingResult, Variogram

logger = logging.getLogger(__name__)

#: Version of the cache file format.
CACHE_FILE_VERSION: int = 1


@dataclass(frozen=True)
class CachedVariogram:
    """A fitted variogram with the data it was fitted to.

    Attributes
    ----------
    variogram : datavia.kriging.Variogram
        The fitted theoretical variogram (range in metres).
    bins : datavia.kriging.EmpiricalBins or None
        The empirical variogram the model was fitted to.
    converged : bool or None
        Whether the fit converged.
    meta : dict
        ``variable``, ``month`` (``"YYYY-MM"``), ``n_days`` and
        ``n_stations`` used, ``complete`` (whether the month was complete),
        the fit ``settings``, and the ``created`` time stamp (UTC, ISO 8601).
    path : Path
        The cache file.
    from_cache : bool
        ``True`` when the variogram was read from the cache, ``False`` when it
        was fitted by this call.
    """

    variogram: Variogram
    bins: EmpiricalBins | None
    converged: bool | None
    meta: dict[str, Any]
    path: Path
    from_cache: bool


class VariogramCache:
    """Monthly variogram fits, stored as JSON files.

    Parameters
    ----------
    root : str or Path
        Cache directory, normally ``<data_dir>/DWD_stations/_variograms``.
        Created when the first variogram is written.
    """

    def __init__(self, root: str | Path) -> None:
        """Initialise the cache; see the class docstring for the parameters."""
        self.root = Path(root)

    @staticmethod
    def settings_hash(settings: dict[str, Any]) -> str:
        """Return a short, stable hash of the fit settings.

        Parameters
        ----------
        settings : dict
            JSON-serialisable fit settings (model, bin width, ...).

        Returns
        -------
        str
            The first 10 hexadecimal digits of the SHA-1 of the settings,
            serialised as JSON with sorted keys.
        """
        text = json.dumps(settings, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[
            :10
        ]

    def path(self, variable: str, month: date, settings: dict[str, Any]) -> Path:
        """Return the cache file of one variable, month and settings.

        Parameters
        ----------
        variable : str
            Pipeline variable name, e.g. ``"total_precipitation"``.
        month : date
            Any day of the month.
        settings : dict
            Fit settings; see :meth:`settings_hash`.

        Returns
        -------
        Path
            ``<root>/<variable>/<YYYY-MM>_<hash>.json`` (it may not exist).
        """
        return (
            self.root / variable / f"{month:%Y-%m}_{self.settings_hash(settings)}.json"
        )

    def load(self, path: str | Path) -> CachedVariogram:
        """Read one cache file.

        Parameters
        ----------
        path : str or Path
            A file written by :meth:`get_or_fit`.

        Returns
        -------
        CachedVariogram
            The variogram, bins and metadata, with ``from_cache=True``.

        Raises
        ------
        ValueError
            If the file has another format version, or an unknown model.
        KeyError
            If a required field is missing.
        """
        from datavia.kriging import EmpiricalBins, Variogram

        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_FILE_VERSION:
            raise ValueError(
                f"{path}: unsupported variogram cache version {data.get('version')!r}"
            )
        bins = data.get("bins")
        return CachedVariogram(
            variogram=Variogram.from_dict(data["variogram"]),
            bins=None if bins is None else EmpiricalBins.from_dict(bins),
            converged=data.get("converged"),
            meta=data["meta"],
            path=path,
            from_cache=True,
        )

    def get_or_fit(
        self,
        variable: str,
        month: date,
        settings: dict[str, Any],
        n_days: int,
        n_stations: int,
        complete: bool,
        fit: Callable[[], KrigingResult],
    ) -> CachedVariogram:
        """Return the cached variogram of a month, or fit and cache it.

        The cached fit is reused when it was made for a complete month, or
        when it already used at least *n_days* days; otherwise *fit* is called
        and its result replaces the cache file.  A cache file that cannot be
        read (corrupt, other version) is treated as missing.

        Parameters
        ----------
        variable : str
            Pipeline variable name.
        month : date
            Any day of the month.
        settings : dict
            Fit settings (JSON-serialisable); part of the cache key.
        n_days : int
            Number of days of the month with data now.
        n_stations : int
            Number of stations in the fit (stored as metadata only).
        complete : bool
            Whether every day of the month is published, i.e. the data of
            the month will not grow any more.
        fit : callable
            Called without arguments when a fit is needed.  Returns a
            :class:`datavia.kriging.KrigingResult` whose ``variogram``,
            ``bins`` and ``converged`` are cached.

        Returns
        -------
        CachedVariogram
            The variogram to use; ``from_cache`` tells whether it was reused.
        """
        path = self.path(variable, month, settings)
        if path.exists():
            try:
                cached = self.load(path)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring unreadable variogram cache %s: %s", path, exc)
            else:
                if (
                    cached.meta.get("complete")
                    or cached.meta.get("n_days", 0) >= n_days
                ):
                    logger.debug("Variogram %s from cache", path.name)
                    return cached
                logger.info(
                    "Variogram %s %s: refitting with %d days (cached fit used %d)",
                    variable,
                    f"{month:%Y-%m}",
                    n_days,
                    cached.meta.get("n_days", 0),
                )

        result = fit()
        meta = {
            "variable": variable,
            "month": f"{month:%Y-%m}",
            "n_days": int(n_days),
            "n_stations": int(n_stations),
            "complete": bool(complete),
            "settings": settings,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        document = {
            "version": CACHE_FILE_VERSION,
            "variogram": result.variogram.to_dict(),
            "bins": None if result.bins is None else result.bins.to_dict(),
            "converged": None if result.converged is None else bool(result.converged),
            "meta": meta,
        }
        _write_atomically(path, json.dumps(document, indent=1) + "\n")
        logger.info(
            "Variogram %s %s fitted (%d days, %d stations%s) -> %s",
            variable,
            meta["month"],
            n_days,
            n_stations,
            "" if result.converged in (True, None) else ", fit did NOT converge",
            path,
        )
        return CachedVariogram(
            result.variogram,
            result.bins,
            result.converged,
            meta,
            path,
            from_cache=False,
        )


def _write_atomically(path: Path, text: str) -> None:
    """Write *text* to *path* through a temporary file and an atomic rename.

    Concurrent readers never see a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
