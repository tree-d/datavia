"""Throwaway probe for a data provider's API (template).

Copy this file to a temporary directory outside the repository, edit the
constants in the "Request" section, and run it from the datavia root:

    pixi run -e dev python -I /tmp/.../scripts/probe.py /tmp/.../downloads

It makes one optional discovery request and one small data request, saves
the payload into the given (empty) download directory, and prints what the
research brief needs: status, content type, format, CRS, grid, pixel
registration, fill values, units, time coordinate, and a sanity value at a
known point.  The defaults query a 10 km window of the BKG DGM200 elevation
WCS around Berlin, so the template runs as is.

Credentials: read them from their standard location or an environment
variable inside ``_auth``; never hard-code or print them.
"""

import sys
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import requests
import xarray as xr
from rasterio.warp import transform as warp_transform

# ---------------------------------------------------------------------------
# Request: edit these for the provider under test
# ---------------------------------------------------------------------------

#: Discovery request (GetCapabilities / DescribeCoverage / OpenAPI / STAC);
#: ``None`` to skip.
DISCOVERY_URL: str | None = (
    "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire"
    "?SERVICE=WCS&VERSION=2.0.1&REQUEST=DescribeCoverage"
    "&COVERAGEID=dgm200_inspire__EL.GridCoverage"
)

#: Data request: base URL and query parameters.  Keep the extent tiny.
DATA_URL: str = "https://sgx.geodatenzentrum.de/wcs_dgm200_inspire"
#: A list of pairs, not a dict, because keys may repeat (WCS needs one
#: ``SUBSET`` per axis).
DATA_PARAMS: list[tuple[str, str]] = [
    ("SERVICE", "WCS"),
    ("VERSION", "2.0.1"),
    ("REQUEST", "GetCoverage"),
    ("COVERAGEID", "dgm200_inspire__EL.GridCoverage"),
    ("FORMAT", "image/tiff"),
    # 10 km x 10 km around Berlin in the native EPSG:25832.  The axis labels
    # ("E", "N") come from DescribeCoverage; "x"/"y" are rejected.
    ("SUBSET", "E(795000,805000)"),
    ("SUBSET", "N(5825000,5835000)"),
]

#: File name for the payload; the suffix decides how it is inspected
#: (``.tif`` → rasterio, ``.nc`` / ``.grib`` / ``.zarr`` → xarray, else raw).
OUTPUT_NAME: str = "probe.tif"

#: Known place for a plausibility check: (lon, lat) in EPSG:4326 and label.
SANITY_POINT: tuple[float, float, str] = (13.405, 52.520, "Berlin")

#: Generic client name; no personal data.
USER_AGENT: str = "datavia-research-probe"

#: Seconds before a request is abandoned.
TIMEOUT_S: int = 60

# ---------------------------------------------------------------------------
# Probe logic: usually no edits needed below
# ---------------------------------------------------------------------------


def _auth() -> dict[str, Any]:
    """Return extra ``requests`` keyword arguments for authentication.

    Returns
    -------
    dict[str, Any]
        E.g. ``{"headers": {"Authorization": ...}}`` built from an environment
        variable or a credentials file.  Empty for open services.
    """
    return {}


def _get(url: str, params: list[tuple[str, str]] | None = None) -> requests.Response:
    """Send one GET request and print the response summary.

    Parameters
    ----------
    url : str
        Request URL.
    params : list[tuple[str, str]], optional
        Query parameters; a list so that keys may repeat.

    Returns
    -------
    requests.Response
        The response (not raised on HTTP errors, so the body can be printed).
    """
    auth_kwargs = _auth()
    headers = {"User-Agent": USER_AGENT, **auth_kwargs.pop("headers", {})}
    response = requests.get(
        url, params=params, headers=headers, timeout=TIMEOUT_S, **auth_kwargs
    )
    print(f"GET {response.url}")
    print(f"  status:       {response.status_code}")
    print(f"  content-type: {response.headers.get('Content-Type')}")
    print(f"  bytes:        {len(response.content)}")
    return response


def probe_discovery() -> None:
    """Run the discovery request and print the start of the response body."""
    if DISCOVERY_URL is None:
        return
    print("== Discovery ==")
    response = _get(DISCOVERY_URL)
    print(response.text[:3000])


def probe_data(download_dir: Path) -> Path | None:
    """Run the data request and save the payload.

    Parameters
    ----------
    download_dir : Path
        Empty directory for the downloaded file.

    Returns
    -------
    Path or None
        Path of the saved payload, or ``None`` if the request failed.
    """
    print("== Data ==")
    response = _get(DATA_URL, DATA_PARAMS)
    if not response.ok:
        print(response.text[:3000])
        return None
    path = download_dir / OUTPUT_NAME
    path.write_bytes(response.content)
    print(f"  saved to:     {path}")
    return path


def describe_raster(path: Path) -> None:
    """Print grid, registration, fill values and a sanity value of a raster.

    Parameters
    ----------
    path : Path
        A file rasterio can open (GeoTIFF, ...).
    """
    with rasterio.open(path) as src:
        print("== Raster ==")
        print(f"  driver / dtype: {src.driver} / {src.dtypes}")
        print(f"  crs:            {src.crs}")
        print(f"  shape (b,r,c):  {src.count}, {src.height}, {src.width}")
        print(f"  resolution:     {src.res}")
        print(f"  bounds:         {src.bounds}")
        print(f"  transform:      {tuple(src.transform)[:6]}")
        print(f"  pixel-is:       {src.tags().get('AREA_OR_POINT', 'not tagged')}")
        print(f"  nodata:         {src.nodata}")
        print(f"  units / scales: {src.units} / {src.scales} / offsets {src.offsets}")
        print(f"  band descr.:    {src.descriptions}")
        print(f"  tags:           {src.tags()}")
        data = src.read(1)
        print(f"  min / max:      {np.nanmin(data)} / {np.nanmax(data)}")
        common = [-9999, -32768, -3.4028235e38, 65535, 255]
        for fill in common:
            count = int(np.sum(data == fill))
            if count:
                print(f"  suspicious value {fill}: {count} cells")
        lon, lat, label = SANITY_POINT
        xs, ys = warp_transform("EPSG:4326", src.crs, [lon], [lat])
        if src.bounds.left <= xs[0] <= src.bounds.right and (
            src.bounds.bottom <= ys[0] <= src.bounds.top
        ):
            row, col = src.index(xs[0], ys[0])
            print(f"  value at {label}: {data[row, col]}")
        else:
            print(f"  {label} is outside the returned extent")


def describe_dataset(path: Path) -> None:
    """Print dimensions, variables, attributes and time stamps of a dataset.

    Parameters
    ----------
    path : Path
        A file xarray can open (NetCDF, GRIB with cfgrib, Zarr).
    """
    with xr.open_dataset(path, decode_times=True, mask_and_scale=False) as ds:
        print("== Dataset ==")
        print(ds)
        for name, var in ds.data_vars.items():
            print(f"  {name}: dims={var.dims} dtype={var.dtype}")
            for key in (
                "units",
                "long_name",
                "_FillValue",
                "missing_value",
                "scale_factor",
                "add_offset",
                "grid_mapping",
                "cell_methods",
            ):
                if key in var.attrs or key in var.encoding:
                    value = var.attrs.get(key, var.encoding.get(key))
                    print(f"    {key}: {value}")
        for coord in ("time", "valid_time", "t"):
            if coord in ds.coords:
                times = ds[coord].values
                print(f"  {coord}: {times[:3]} ... {times[-3:]} (n={times.size})")
                print(f"    attrs: {dict(ds[coord].attrs)}")
        print(f"  global attrs: {dict(ds.attrs)}")


def main() -> int:
    """Run the probe; the first argument is the empty download directory.

    Returns
    -------
    int
        Process exit code: 0 on success, 1 if the data request failed.
    """
    if len(sys.argv) != 2:
        print("usage: probe.py <empty download directory>")
        return 1
    download_dir = Path(sys.argv[1])
    download_dir.mkdir(parents=True, exist_ok=True)
    probe_discovery()
    path = probe_data(download_dir)
    if path is None:
        return 1
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        describe_raster(path)
    elif suffix in {".nc", ".nc4", ".grib", ".grb", ".zarr"}:
        describe_dataset(path)
    else:
        print(path.read_bytes()[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
