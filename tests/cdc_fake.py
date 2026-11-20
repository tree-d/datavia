"""A fake DWD Climate Data Center for tests of the CDC station download.

:class:`FakeCDC` holds station archives (built in memory with the real file
layout: a ``produkt_*.txt`` table and a ``Metadaten_Geographie_*.txt`` table
per zip) and serves them, together with the directory listings, through a
fake ``requests.Session``.  Every requested URL is recorded in
:attr:`FakeCDC.requests`.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from unittest.mock import MagicMock

from datavia.weather.dwd_cdc import CDC_DAILY_URL

#: Columns of the fake ``kl`` product table (a subset of the real one).
KL_COLUMNS = ["QN_4", "TMK", "TXK", "TNK", "UPM"]


def product_table(
    station: str, first: date, values: dict[str, Iterable[float]]
) -> bytes:
    """A ``produkt_*.txt`` table: one row per day from *first*, ``;``-separated.

    Parameters
    ----------
    station : str
        Station id (written without leading zeros, as the DWD does).
    first : date
        Day of the first row.
    values : dict
        Column name -> daily values (``-999`` marks a missing value).
    """
    columns = list(values)
    rows = list(zip(*values.values(), strict=True))
    lines = ["STATIONS_ID;MESS_DATUM;" + ";".join(f"{c:>5}" for c in columns) + ";eor"]
    for i, row in enumerate(rows):
        day = first + timedelta(days=i)
        cells = ";".join(f"{v:>7}" for v in row)
        lines.append(f"{int(station):>11};{day:%Y%m%d};{cells};eor")
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def geography_table(station: str, positions: list[tuple[float, float, float]]) -> bytes:
    """A ``Metadaten_Geographie_*.txt`` table; *positions* are (h, lat, lon) rows."""
    lines = [
        "Stations_id;Stationshoehe;Geogr.Breite;Geogr.Laenge;von_datum;bis_datum;Stationsname"
    ]
    for i, (h, lat, lon) in enumerate(positions):
        lines.append(
            f"{int(station):>6};{h:8.2f};{lat:8.4f};{lon:8.4f};"
            f"1990010{i + 1};        ;Großstadt"
        )
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def station_zip(
    station: str,
    first: date,
    values: dict[str, Iterable[float]],
    positions: list[tuple[float, float, float]] | None = None,
    product: str = "klima",
) -> bytes:
    """A station archive with a product table and a geography table."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"produkt_{product}_tag_{first:%Y%m%d}_20991231_{station}.txt",
            product_table(station, first, values),
        )
        archive.writestr(
            f"Metadaten_Geographie_{station}.txt",
            geography_table(station, positions or [(100.0, 51.0, 12.0)]),
        )
        archive.writestr(f"Metadaten_Parameter_klima_tag_{station}.txt", b"irrelevant")
    return buffer.getvalue()


def archive_name(group: str, station: str, branch: str, first: date, last: date) -> str:
    """The DWD file name of a station archive."""
    tag = {"kl": "KL", "more_precip": "RR"}[group]
    if branch == "recent":
        return f"tageswerte_{tag}_{station}_akt.zip"
    return f"tageswerte_{tag}_{station}_{first:%Y%m%d}_{last:%Y%m%d}_hist.zip"


@dataclass
class FakeCDC:
    """Station archives served through a fake HTTP session.

    Attributes
    ----------
    files : dict
        ``{(group, branch): {file name: zip bytes}}``.
    requests : list[str]
        Every URL requested, in order.
    errors : dict
        ``{url: [status, ...]}``: the next requests of *url* answer with these
        HTTP statuses (consumed one per request) before the real content.
    """

    files: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    errors: dict[str, list[int]] = field(default_factory=dict)

    def add(
        self,
        group: str,
        branch: str,
        station: str,
        first: date,
        values: dict[str, Iterable[float]],
        positions: list[tuple[float, float, float]] | None = None,
    ) -> str:
        """Add one station archive; return its file name."""
        values = {k: list(v) for k, v in values.items()}
        n_days = len(next(iter(values.values())))
        last = first + timedelta(days=n_days - 1)
        name = archive_name(group, station, branch, first, last)
        product = "nieder" if group == "more_precip" else "klima"
        self.files.setdefault((group, branch), {})[name] = station_zip(
            station, first, values, positions, product
        )
        return name

    def listing(self, group: str, branch: str) -> bytes:
        """The HTML directory listing of one group/branch."""
        names = sorted(self.files.get((group, branch), {}))
        links = "\n".join(f'<a href="{n}">{n}</a>' for n in names)
        header = '<a href="../">../</a>\n<a href="X_Beschreibung.txt">d</a>'
        return f"<html>{header}\n{links}</html>".encode()

    def get(self, url: str, timeout: float | None = None) -> MagicMock:
        """``requests.Session.get``: serve a listing or an archive."""
        del timeout
        self.requests.append(url)
        pending = self.errors.get(url)
        if pending:
            return MagicMock(status_code=pending.pop(0), content=b"")
        path = url.removeprefix(CDC_DAILY_URL + "/")
        group, branch, name = path.split("/", 2)
        if name == "":
            return MagicMock(status_code=200, content=self.listing(group, branch))
        content = self.files.get((group, branch), {}).get(name)
        if content is None:
            return MagicMock(status_code=404, content=b"")
        return MagicMock(status_code=200, content=content)

    def session(self) -> MagicMock:
        """A fake ``requests.Session`` backed by this server."""
        session = MagicMock()
        session.get.side_effect = self.get
        return session

    def archive_requests(self) -> list[str]:
        """The requested archive (zip) URLs."""
        return [u for u in self.requests if u.endswith(".zip")]
