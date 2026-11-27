Kriging of DWD Station Data
===========================

The weather source ``"DWD_stations"`` turns the daily observations of the
German Weather Service (DWD) station network into daily 1 km grids by
*external drift kriging*.  This page explains the method, the
``datavia.kriging`` package that computes it, and how the DWD source uses it.

.. contents::
   :local:
   :depth: 2

Quick start
-----------

.. code-block:: python

    from datavia.weather import WeatherPipeline

    pipeline = WeatherPipeline({
        "source": "DWD_stations",
        "variables": ["total_precipitation", "2m_temperature"],
        "date_start": "2025-06-01",
        "date_end": "2025-06-30",
    })
    pipeline.update_data()          # download stations, krige, store (Zarr)
    leipzig = [[12.37, 51.34]]      # (lon, lat)
    pipeline.get_data(leipzig, variable="total_precipitation",
                      datetime_utc="2025-06-10")

``update_data`` works in whole calendar years (see `Storage`_): the first call
for a year kriges every month of it, which is expensive for precipitation (see
`Cost`_).  Later calls only krige months that are not yet stored.

Installation
~~~~~~~~~~~~

The kriging runs in Mojo, which comes from the Modular package channel:

.. code-block:: bash

    # pip: datavia-weather with the kriging extra pulls in datavia-kriging
    # and datavia-elevation; the [mojo] extra of datavia-kriging adds Mojo/MAX
    pip install "datavia-weather[kriging]" "datavia-kriging[mojo]"

    # pixi (this repository): the "kriging" feature is part of the default
    # and dev environments
    pixi install

Variables
~~~~~~~~~

=========================== ===================================== ============
Pipeline variable           DWD product (group / column)          Unit
=========================== ===================================== ============
``total_precipitation``     ``more_precip`` / ``RS`` (06-06 UTC)  mm
``2m_temperature``          ``kl`` / ``TMK`` (daily mean)         °C
``temperature_2m_max``      ``kl`` / ``TXK``                      °C
``temperature_2m_min``      ``kl`` / ``TNK``                      °C
``relative_humidity_2m``    ``kl`` / ``UPM`` (daily mean)         %
``vapour_pressure``         ``kl`` / ``VPM`` (daily mean)         kPa
``vapour_pressure_deficit`` ``kl`` / ``TXK``, ``TNK``, ``VPM``    kPa
=========================== ===================================== ============

The vapour pressure deficit is computed at each station before kriging,
after FAO-56: the mean of the saturation vapour pressures at ``TXK`` and
``TNK`` minus the measured daily mean vapour pressure ``VPM`` (see
:mod:`datavia.weather.vapour_pressure`). A day is missing at a station if
any of the three columns is. Negative kriging estimates are set to 0.

Radiation is not available: the DWD daily solar network has only about 40
stations.

The method
----------

Kriging
~~~~~~~

Kriging estimates the value at a target ``s0`` as a weighted sum of the
station values::

    z(s0) = sum_i  w_i * z_i

The weights depend on how similar values are as a function of the distance
between them, described by the **variogram** ``gamma(h)``: half the expected
squared difference of two values ``h`` metres apart.  Nearby stations
usually differ little (small ``gamma``), distant ones a lot, up to a plateau
(the *sill*) beyond which stations are unrelated (the *range*).

**Ordinary kriging** chooses the weights that make the estimate unbiased and
its error variance smallest.  They solve the linear system::

    | Gamma  1 | | w  |   | gamma_0 |     Gamma_ij  = gamma(|s_i - s_j|)
    | 1^T    0 | | mu | = |    1    |     gamma_0,i = gamma(|s_i - s0|)

The row of ones forces the weights to sum to 1; ``mu`` is its Lagrange
multiplier.

**External drift kriging** (EDK, the default) also lets the local mean follow
the terrain elevation ``h``, which is known at every cell: the weights must
reproduce the target's elevation, ``sum_i w_i h_i = h0``.  The system gets one
more row and column::

    | Gamma  1  h | | w   |   | gamma_0 |
    | 1^T    0  0 | | mu1 | = |    1    |
    | h^T    0  0 | | mu2 |   |   h0    |

This is what makes a mountain cell colder than the valley stations around it,
or wetter on a windward slope, without a separate lapse-rate correction.

The variogram
~~~~~~~~~~~~~

The variogram is estimated from the data in two steps.

**1. Empirical variogram.** Every pair of stations ``(i, j)`` closer than
``max_lag`` (80 km) contributes, on every day on which both report, its
squared difference to the distance bin (``bin_width`` = 1 km) of its
separation.  The semivariance of a bin is::

    gamma_k = sum over pairs in bin k of (z_i - z_j)^2 / (2 * pairs_k)

Pairs whose elevation difference per metre of distance exceeds
``max_elevation_gradient`` (0.2) are left out: their difference reflects the
terrain, not the distance.  Bins with fewer than ``min_pairs_per_bin`` (30)
pairs are merged into a neighbouring bin.

**2. Model fit.** A nugget + exponential model (or nugget + spherical) is
fitted to the bins::

    exponential:  gamma(h) = c0 + c * (1 - exp(-h / a))
    spherical:    gamma(h) = c0 + c * (1.5 h/a - 0.5 (h/a)^3)   for h < a,
                             c0 + c                             for h >= a

with nugget ``c0`` (noise and variation below the station spacing), partial
sill ``c`` and range ``a``.  The fit minimises the pair-weighted absolute
misfit ``sum_k pairs_k * |gamma_k - gamma(h_k)|`` by a bounded, deterministic
Nelder-Mead search on lags and semivariances normalised to (0, 1].  The
fitted range is in metres; nugget and sill stay in normalised units, which
does not change the kriging weights.

Special cases
~~~~~~~~~~~~~

For each target and day, only the stations within ``max_dist`` that report a
value take part:

=============================== ===============================================
Reporting stations              Estimate
=============================== ===============================================
none, or all exactly 0          0
one                             its value; 0 with ``dist_zero`` if it is
                                farther than ``threshold_dist`` (precipitation)
two                             inverse squared distance weighting
three or more                   kriging
singular kriging system         ``OUT_NODATA`` (-9999), counted in
                                ``singular_count``
=============================== ===============================================

With ``correct_negative`` (precipitation), negative estimates, a usual kriging
undershoot next to dry stations, become 0.  The kriging weights depend only on
which stations report, not on their values, so they are reused for
consecutive days with the same reporting stations.

The ``datavia.kriging`` package
-------------------------------

The method is implemented in the separate package ``datavia-kriging``
(``packages/kriging``; import name ``datavia.kriging``).  It depends on numpy
and Mojo only, not on the rest of datavia, so it can be used on its own:

.. code-block:: python

    from datavia.kriging import krige, krige_grid, Variogram

    # station values: (n_stations, n_days); missing values equal `nodata`
    r = krige(x, y, h, sta_x, sta_y, sta_h, sta_z, nodata=-9999.0, max_dist=1.5e5)
    r.estimates          # (n_points, n_days) float32
    r.variogram          # fitted from all days
    r.save_variogram("temperature.json")

    # reuse the variogram, e.g. for other days or other targets
    r2 = krige(x2, y2, h2, sta_x, sta_y, sta_h, new_z, nodata=-9999.0,
               max_dist=1.5e5, variogram=Variogram.load("temperature.json"))

See the :mod:`datavia.kriging` API reference for every setting.  The Mojo
sources are compiled on first use into ``__mojocache__/`` next to them; the
computation uses all CPU cores, with results independent of the core count.

How the ``DWD_stations`` source uses it
---------------------------------------

Step 1: station data
~~~~~~~~~~~~~~~~~~~~

:class:`~datavia.weather.dwd_cdc.CDCClient` downloads the station archives of
the DWD Climate Data Center (``historical``: quality controlled, up to the end
of last year; ``recent``: about the last 550 days) for the requested period and
reads one variable.  Where both archives have a day, the historical value
wins.  Values outside a plausible range are dropped; of several stations at
one position only the one with the most data is kept.  Station positions and
elevations are those of the DWD metadata.

Step 2: kriging
~~~~~~~~~~~~~~~

:class:`~datavia.weather.dwd_kriging_downloader.DWDKrigingDownloader` kriges
onto every land cell of the HYRAS grid (EPSG:3035, 1 km, 665 x 890 cells), so
DWD-kriged and HYRAS values can be compared cell by cell.  The drift is the
mean DGM200 elevation of each 1 km cell; cells outside the DGM200 (outside
Germany) are not kriged and are filled from the nearest land cell when stored,
as for HYRAS.

Settings per variable (those of the operational German Drought Monitor for
precipitation and mean temperature):

======================= ============ ================ ============
Variable                ``max_dist`` correct_negative ``dist_zero``
======================= ============ ================ ============
``total_precipitation`` 100 km       yes              yes
temperatures, humidity  150 km       no               no
======================= ============ ================ ============

All variables use EDK, the exponential model, 1 km bins up to 80 km, and
``threshold_dist`` = half a cell diagonal (707 m).

Variogram cache
~~~~~~~~~~~~~~~

Each calendar month is kriged with **one variogram, fitted to all station-days
of that month**, and the fit is cached::

    <data_dir>/DWD_stations/_variograms/<variable>/<YYYY-MM>_<settings hash>.json

A month that was complete when it was fitted is never fitted again; the
running month is refitted when more days have been published.  The settings
hash changes with any fit setting, so a changed setting never reuses an old
fit.  See :mod:`datavia.weather.variogram_cache` for the file format.  The
kriged NetCDF files also record the variogram of every month in their
``variograms`` attribute.

Storage
~~~~~~~

Kriged fields are stored like HYRAS, one Zarr store per variable and year::

    <data_dir>/DWD_stations/<variable>/<year>.zarr

Each store covers 1 January to the last published day of the year (the
DWD publishes a day during the next morning; a day only a few stations have
reported yet is left for the next update).  Months already complete in the
store are copied instead of kriged again.  Day ``D`` is stamped at ``D 00:00``
UTC, precipitation (the 06-06 UTC day) at ``D 06:00``, as in HYRAS.

The other caches live next to it: ``_cdc/`` (station archives) and ``_grid/``
(cell elevations).

Cost
~~~~

Kriging is the expensive step: about 370 000 land cells per day, each
solving a kriging system over the reporting stations within ``max_dist``.  The
solve grows with the cube of their number, and its weights are reused only
while the same stations report on consecutive days.  Precipitation is
therefore much more expensive than the other variables: the rain gauge
network is about four times denser, and which gauges report changes almost
daily, while the climate stations report nearly every day.  The Mojo kernels
use all CPU cores; months already complete in the store are not kriged again.

Accuracy
~~~~~~~~

Compared with HYRAS (which the DWD interpolates from the same networks) over
all common cells of June 2025: precipitation MAE 0.21 mm/day, bias +0.01 mm,
correlation 0.990; daily mean temperature MAE 0.18 °C, bias +0.05 °C,
correlation 0.997.

Station supplement
------------------

Independently of the kriged grids, any gridded source can blend the station
observations themselves into its values at query time: configure
``"dwd_stations": True`` (or a list of DWD station ids).  The daily station
values are then saved as Parquet and an inverse-distance-weighted station
estimate is blended into the gridded value for daily, single-timestamp
queries; see :class:`~datavia.weather.dwd_downloader.DWDStationDownloader`.
