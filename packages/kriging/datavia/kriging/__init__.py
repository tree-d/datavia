"""External drift kriging (EDK) and ordinary kriging (OK) of station data, in Mojo.

``datavia.kriging`` interpolates values measured at irregularly placed stations
(rain gauges, climate stations, ...) onto arbitrary points or onto every cell of
a grid.  It is self-contained: it depends only on numpy at the Python level
and on Mojo/MAX at run time, not on the rest of datavia.

Kriging methods
---------------
**Ordinary kriging** estimates the value at a point ``s0`` as a weighted sum
of the station values, ``z(s0) = sum_i w_i z_i``.  The weights are chosen so
that the estimate is unbiased and its error variance is as small as possible,
given how strongly values at two places are related as a function of their
distance.  That relation is the *variogram*: ``gamma(h)`` is half the expected
squared difference of two values ``h`` metres apart.  The weights solve::

    | Gamma  1 | | w  |   | gamma_0 |     Gamma_ij  = gamma(|s_i - s_j|)
    | 1^T    0 | | mu | = |    1    |     gamma_0,i = gamma(|s_i - s0|)

where the row of ones makes the weights sum to 1.

**External drift kriging** additionally lets the local mean follow a second
variable known everywhere, here the terrain elevation ``h`` (the "drift"):
the weights must also reproduce the target's elevation,
``sum_i w_i h_i = h0``.  This captures, for example, the decrease of
temperature with height or the increase of precipitation on windward slopes,
which plain distance weighting cannot.  It is the default (``drift=True``).

Pipeline
--------
1. **Empirical variogram** (unless a variogram is given): every station pair
   within ``max_lag`` adds ``(z_i - z_j)**2`` to the distance bin of its
   separation, on every day; pairs across steep terrain
   (``|dh| / distance > max_elevation_gradient``) are left out, and bins with
   fewer than ``min_pairs_per_bin`` pairs are merged into a neighbour.  The
   semivariance of a bin is ``sum / (2 * pairs)``.
2. **Variogram fit**: a nugget + exponential (default) or nugget + spherical
   model is fitted to the bins by a bounded Nelder-Mead search minimising the
   pair-weighted absolute misfit; see :class:`Variogram`.
3. **Kriging**: for every target and day, the stations within ``max_dist``
   that report a value are selected and an estimator is chosen:

   - no station, or all reporting stations exactly 0: the estimate is 0;
   - one station: its value (or 0 with ``dist_zero``, if it is farther than
     ``threshold_dist``);
   - two stations: inverse squared distance weighting (the kriging system is
     ill-conditioned with so few stations);
   - three or more: the kriging system above.  Its weights depend only on
     which stations report, so they are reused for consecutive days with the
     same reporting stations.

   A singular system gives :data:`OUT_NODATA` for that target and day.

Missing data
------------
Station values are a ``(n_stations, n_days)`` array; a missing value equals
the ``nodata`` argument (choose a number no real value can take, e.g.
``-9999``; NaN is not supported).  Targets without an estimate get
:data:`OUT_NODATA` (``-9999.0``): grid cells whose elevation equals
``dem_nodata`` (:func:`krige_grid`) and singular kriging systems.

Note that a target with *no* station within ``max_dist`` gets 0, the right
answer for zero-inflated variables like precipitation (no nearby rain gauge
reports rain) but not for, e.g., temperature: choose ``max_dist`` large enough
that every target has stations in range.

Mojo
----
The computation runs in Mojo on all CPU cores the process may use, with the
GIL released.  The Mojo sources (``_core.mojo`` and ``kernels/``) are compiled
by ``mojo.importer`` on the first call and cached in ``__mojocache__/`` next to
them, so the first call takes a few seconds and the package directory must be
writable.  Install Mojo and MAX with ``pip install "datavia-kriging[mojo]"`` or
from the conda channel ``https://conda.modular.com/max`` (packages ``mojo`` and
``max``).

Examples
--------
Krige synthetic temperatures (5 degC at sea level, -6.5 K per km) from 60
stations onto two points, fitting the variogram from the 10 days given:

>>> import numpy as np
>>> from datavia.kriging import krige
>>> rng = np.random.default_rng(0)
>>> sx, sy = rng.uniform(0, 5e4, 60), rng.uniform(0, 5e4, 60)
>>> sh = rng.uniform(0, 800, 60)
>>> z = 5 - 0.0065 * sh[:, None] + rng.normal(0, 0.3, (60, 10))
>>> result = krige(
...     np.array([1e4, 4e4]), np.array([2e4, 3e4]), np.array([100.0, 700.0]),
...     sx, sy, sh, z, nodata=-9999.0, max_dist=1e5,
... )  # doctest: +SKIP
>>> result.estimates.shape  # doctest: +SKIP
(2, 10)

Keep the fitted variogram and reuse it for later days:

>>> result.save_variogram("temperature.json")  # doctest: +SKIP
>>> from datavia.kriging import Variogram
>>> later = krige(..., variogram=Variogram.load("temperature.json"))  # doctest: +SKIP

See Also
--------
krige : Kriging at points.
krige_grid : Kriging onto every cell of a 2-D grid.
Variogram : The variogram model, and saving and loading it.
"""

from ._krige import krige, krige_grid
from ._types import (
    EXPONENTIAL,
    OUT_NODATA,
    SPHERICAL,
    EmpiricalBins,
    KrigingResult,
    Variogram,
)

load_variogram = Variogram.load
"""Read a variogram JSON file; the same as :meth:`Variogram.load`."""

__all__ = [
    "EXPONENTIAL",
    "OUT_NODATA",
    "SPHERICAL",
    "EmpiricalBins",
    "KrigingResult",
    "Variogram",
    "krige",
    "krige_grid",
    "load_variogram",
]
