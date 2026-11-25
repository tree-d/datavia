"""Constants and result types of :mod:`datavia.kriging`, and variogram files.

Variogram models
----------------
A variogram ``gamma(h)`` gives half the expected squared difference of two
values ``h`` metres apart.  Two models are available, each with three
parameters, *nugget* ``c0``, partial *sill* ``c`` and *range* ``a`` [m]:

- **exponential** (:data:`EXPONENTIAL`, the default)::

      gamma(h) = c0 + c * (1 - exp(-h / a))

  approaches ``c0 + c`` asymptotically (95 % of it at ``h = 3 a``);
- **spherical** (:data:`SPHERICAL`)::

      gamma(h) = c0 + c * (1.5 h/a - 0.5 (h/a)**3)   for h < a
      gamma(h) = c0 + c                              for h >= a

  reaches ``c0 + c`` exactly at the range.

For both, ``gamma(0)`` is the nugget, not 0: the nugget stands for measurement
noise and variation on scales smaller than the station spacing, so kriging
does not reproduce a station's value exactly at the station.  A range of 0
means no spatial correlation (``gamma = c0 + c`` everywhere but at 0).

A *fitted* variogram has its range in metres but nugget and sill in
normalised units: the fit works on semivariances divided by the largest
binned semivariance.  Kriging weights do not change when the whole variogram
is scaled, so the estimates are the same; only the absolute values of nugget
and sill are not in the unit of the data squared (the bins,
:class:`EmpiricalBins`, are).

Variogram files
---------------
A variogram is saved as a small, self-describing JSON file::

    {
     "version": 1,
     "model": "exponential",
     "nugget": 0.2,
     "sill": 0.9,
     "range_m": 39900.0,
     "converged": true,
     "bins": {"lag_m": [...], "semivariance": [...], "pair_count": [...]}
    }

=============== ===========================================================
``version``     File format version (1).
``model``       ``"exponential"`` or ``"spherical"``.
``nugget``      Nugget ``c0``.
``sill``        Partial sill ``c``.
``range_m``     Range ``a`` in metres.
``converged``   Only for a fitted variogram: whether the fit converged.
``bins``        Only for a fitted variogram: the empirical variogram it was
                fitted to, in physical units (lag in metres, semivariance in
                the unit of the data squared, number of station pairs).
=============== ===========================================================

Floats are written at full precision, so a variogram read back is identical
to the one written, and kriging with it gives bit-identical estimates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

OUT_NODATA = -9999.0
"""Estimate of targets without one: cells whose elevation is the DEM's nodata
value, and target-days whose kriging system was singular."""

SPHERICAL = 1
"""Model type of the nugget + spherical variogram."""

EXPONENTIAL = 2
"""Model type of the nugget + exponential variogram (the default)."""

_FILE_VERSION = 1
_MODEL_NAMES = {SPHERICAL: "spherical", EXPONENTIAL: "exponential"}
_MODEL_TYPES = {name: model_type for model_type, name in _MODEL_NAMES.items()}


@dataclass(frozen=True)
class Variogram:
    """Theoretical variogram: a model of semivariance against distance.

    See the module docstring for the model formulas and units.

    Attributes
    ----------
    nugget : float
        Semivariance at distance 0 (``c0``): measurement noise and variation
        below the station spacing.
    sill : float
        Partial sill (``c``): how far the semivariance rises above the nugget.
    range : float
        Range ``a`` in metres: where the spherical model reaches its plateau,
        or the distance scale of the exponential model.
    model_type : int
        :data:`EXPONENTIAL` (default) or :data:`SPHERICAL`.

    Examples
    --------
    >>> v = Variogram(nugget=0.1, sill=0.9, range=40000.0)
    >>> v.to_dict()["model"]
    'exponential'
    >>> Variogram.from_dict(v.to_dict()) == v
    True
    """

    nugget: float
    sill: float
    range: float
    model_type: int = EXPONENTIAL

    def to_dict(self) -> dict[str, Any]:
        """Return the variogram as a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            ``model`` (by name), ``nugget``, ``sill`` and ``range_m``.

        Raises
        ------
        ValueError
            If :attr:`model_type` is neither :data:`SPHERICAL` nor
            :data:`EXPONENTIAL`.
        """
        if self.model_type not in _MODEL_NAMES:
            raise ValueError(f"unknown variogram model type {self.model_type}")
        return {
            "model": _MODEL_NAMES[self.model_type],
            "nugget": float(self.nugget),
            "sill": float(self.sill),
            "range_m": float(self.range),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variogram:
        """Build a variogram from a dict made by :meth:`to_dict`.

        Parameters
        ----------
        data : dict[str, Any]
            Must hold ``model``, ``nugget``, ``sill`` and ``range_m``; other
            keys (``bins``, ``converged``, ``version``, ...) are ignored.

        Returns
        -------
        Variogram
            The variogram.

        Raises
        ------
        ValueError
            If ``model`` is not ``"exponential"`` or ``"spherical"``.
        KeyError
            If a required key is missing.
        """
        model = data["model"]
        if model not in _MODEL_TYPES:
            raise ValueError(
                f"unknown variogram model {model!r}, "
                f"expected one of {sorted(_MODEL_TYPES)}"
            )
        return cls(
            float(data["nugget"]),
            float(data["sill"]),
            float(data["range_m"]),
            _MODEL_TYPES[model],
        )

    def save(self, path: str | os.PathLike) -> None:
        """Write the variogram to a JSON file (format: see the module docstring).

        Parameters
        ----------
        path : str or os.PathLike
            File to write; overwritten if it exists.

        Raises
        ------
        ValueError
            If :attr:`model_type` is unknown.
        OSError
            If the file cannot be written.
        """
        _write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: str | os.PathLike) -> Variogram:
        """Read a variogram file.

        Parameters
        ----------
        path : str or os.PathLike
            A file written by :meth:`save` or
            :meth:`KrigingResult.save_variogram`.

        Returns
        -------
        Variogram
            The variogram, identical to the one written.

        Raises
        ------
        ValueError
            If the file is not valid JSON, has another format version or an
            unknown model.
        KeyError
            If a required field is missing.
        OSError
            If the file cannot be read.
        """
        return cls.from_dict(_read_json(path))


@dataclass(frozen=True)
class EmpiricalBins:
    """The populated bins of an empirical variogram, in physical units.

    Each bin summarises all station pairs (on all days) whose separation falls
    into one distance class.

    Attributes
    ----------
    lag : np.ndarray
        Representative distance of each bin in metres: the middle of the bin,
        or of the whole range of empty bins merged into it.
    semivariance : np.ndarray
        ``sum((z_i - z_j)**2) / (2 * pairs)`` of each bin, in the unit of the
        data squared.
    pair_count : np.ndarray
        Number of station pairs (summed over days) in each bin; bins with more
        pairs weigh more in the fit.
    """

    lag: np.ndarray
    semivariance: np.ndarray
    pair_count: np.ndarray

    def to_dict(self) -> dict[str, list]:
        """Return the bins as a JSON-compatible dict of lists.

        Returns
        -------
        dict[str, list]
            ``lag_m``, ``semivariance`` and ``pair_count``.
        """
        return {
            "lag_m": np.asarray(self.lag, dtype=np.float64).tolist(),
            "semivariance": np.asarray(self.semivariance, dtype=np.float64).tolist(),
            "pair_count": np.asarray(self.pair_count, dtype=np.int64).tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, list]) -> EmpiricalBins:
        """Build the bins from a dict made by :meth:`to_dict`.

        Parameters
        ----------
        data : dict[str, list]
            ``lag_m``, ``semivariance`` and ``pair_count``, of equal length.

        Returns
        -------
        EmpiricalBins
            The bins (float64 lag and semivariance, int64 pair counts).

        Raises
        ------
        KeyError
            If a key is missing.
        """
        return cls(
            np.asarray(data["lag_m"], dtype=np.float64),
            np.asarray(data["semivariance"], dtype=np.float64),
            np.asarray(data["pair_count"], dtype=np.int64),
        )

    @classmethod
    def load(cls, path: str | os.PathLike) -> EmpiricalBins:
        """Read the bins from a variogram file.

        Parameters
        ----------
        path : str or os.PathLike
            A file written by :meth:`KrigingResult.save_variogram` for a
            fitted variogram.

        Returns
        -------
        EmpiricalBins
            The bins the variogram was fitted to.

        Raises
        ------
        ValueError
            If the file holds no bins (the variogram was given, not fitted),
            is not valid JSON, or has another format version.
        KeyError
            If a bins field is missing.
        OSError
            If the file cannot be read.
        """
        data = _read_json(path)
        if "bins" not in data:
            raise ValueError(
                f"{os.fspath(path)} holds no empirical bins "
                "(the variogram was not fitted)"
            )
        return cls.from_dict(data["bins"])


@dataclass(frozen=True)
class KrigingResult:
    """Result of :func:`~datavia.kriging.krige` or :func:`~datavia.kriging.krige_grid`.

    Attributes
    ----------
    estimates : np.ndarray
        The estimates, float32; :data:`OUT_NODATA` where there is none.  Shape
        ``(n_points, n_days)`` from ``krige`` and ``(n_days, ny, nx)`` from
        ``krige_grid``, without the day axis for single-day input.
    variogram : Variogram
        The variogram used: the one given, or the one fitted.
    bins : EmpiricalBins or None
        The empirical variogram the variogram was fitted to; ``None`` if the
        variogram was given.
    converged : bool or None
        Whether the variogram fit converged (a fit that did not still gives a
        usable, if less certain, variogram); ``None`` if it was given.
    singular_count : int
        Target-days whose kriging system was singular (estimate
        :data:`OUT_NODATA`), e.g. external drift kriging where all reporting
        stations share one elevation.
    sum_warning_count : int
        Weight sets whose sum differs from 1 by more than 1e-4, a symptom of
        a nearly singular system.  They are used anyway.
    """

    estimates: np.ndarray
    variogram: Variogram
    bins: EmpiricalBins | None
    converged: bool | None
    singular_count: int
    sum_warning_count: int

    def save_variogram(self, path: str | os.PathLike) -> None:
        """Write the variogram used to a JSON file.

        A fitted variogram is written with ``converged`` and the empirical
        ``bins``; a given one without.  Read it back with
        :meth:`Variogram.load` (and the bins with :meth:`EmpiricalBins.load`).

        Parameters
        ----------
        path : str or os.PathLike
            File to write; overwritten if it exists.

        Raises
        ------
        ValueError
            If the variogram's model type is unknown.
        OSError
            If the file cannot be written.
        """
        data = self.variogram.to_dict()
        if self.converged is not None:
            data["converged"] = bool(self.converged)
        if self.bins is not None:
            data["bins"] = self.bins.to_dict()
        _write_json(path, data)


def _write_json(path: str | os.PathLike, data: dict[str, Any]) -> None:
    """Write a variogram file: *data* with the format ``version`` first.

    Parameters
    ----------
    path : str or os.PathLike
        File to write; overwritten if it exists.
    data : dict[str, Any]
        JSON-compatible content, without ``version``.

    Raises
    ------
    OSError
        If the file cannot be written.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": _FILE_VERSION, **data}, f, indent=1)
        f.write("\n")


def _read_json(path: str | os.PathLike) -> dict[str, Any]:
    """Read a variogram file and check its format version.

    Parameters
    ----------
    path : str or os.PathLike
        File written by :func:`_write_json`.

    Returns
    -------
    dict[str, Any]
        The file's content, ``version`` included.

    Raises
    ------
    ValueError
        If the file is not valid JSON (:class:`json.JSONDecodeError`), or its
        ``version`` is not :data:`_FILE_VERSION`.
    OSError
        If the file cannot be read.
    """
    with open(path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    version = data.get("version")
    if version != _FILE_VERSION:
        raise ValueError(
            f"{os.fspath(path)}: unsupported variogram file version {version!r}"
        )
    return data
