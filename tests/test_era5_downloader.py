"""Unit tests for :class:`~datavia.weather.era5_downloader.ERA5Downloader`.

Covers _build_request_date_fields(), _snap_bbox(), monthly/quarterly/yearly
chunking, chunk_by dispatch, and download() with a mocked CDS client.
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------


class TestERA5DownloaderDateFields:
    """Tests for ERA5Downloader._build_request_date_fields().

    Verifies that the helper produces correct, sorted, zero-padded year/month/day
    lists for various date ranges without touching the network or cdsapi.
    """

    def test_single_day_produces_single_entries(self) -> None:
        """A one-day range yields exactly one year, one month, one day."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-01-15", "2024-01-15"
        )
        assert years == ["2024"]
        assert months == ["01"]
        assert days == ["15"]

    def test_multi_day_same_month(self) -> None:
        """A range within one month yields one year, one month, all spanned days."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-03-10", "2024-03-12"
        )
        assert years == ["2024"]
        assert months == ["03"]
        assert days == ["10", "11", "12"]

    def test_multi_month_same_year(self) -> None:
        """A range spanning two months collects the correct months and days."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2024-01-30", "2024-02-02"
        )
        assert years == ["2024"]
        assert months == ["01", "02"]
        # Days 30, 31 from January and 01, 02 from February.
        assert days == ["01", "02", "30", "31"]

    def test_multi_year_range(self) -> None:
        """A range crossing a year boundary yields both years."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2023-12-30", "2024-01-02"
        )
        assert years == ["2023", "2024"]
        assert months == ["01", "12"]
        # Days: 30, 31 from Dec-2023 and 01, 02 from Jan-2024.
        assert days == ["01", "02", "30", "31"]

    def test_end_before_start_raises(self) -> None:
        """ValueError is raised when date_end precedes date_start."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._build_request_date_fields("2024-03-01", "2024-02-01")

    def test_lists_are_sorted(self) -> None:
        """All returned lists are lexicographically sorted."""
        from datavia.weather.era5_downloader import ERA5Downloader

        years, months, days = ERA5Downloader._build_request_date_fields(
            "2023-11-28", "2024-02-03"
        )
        assert years == sorted(years)
        assert months == sorted(months)
        assert days == sorted(days)


# ---------------------------------------------------------------------------
# ERA5Downloader._snap_bbox (issue #8)

# ---------------------------------------------------------------------------


class TestERA5SnapBbox:
    """Tests for the ERA5-Land 0.1° grid-snapping helper."""

    def test_already_aligned_bbox_unchanged(self) -> None:
        """A bbox already on 0.1° boundaries should be returned as-is."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.1, 5.9, 47.3, 15.0]
        result = ERA5Downloader._snap_bbox(bbox)
        assert round(result[0], 6) == 55.1  # north — ceil → same
        assert round(result[1], 6) == 5.9  # west  — floor → same
        assert round(result[2], 6) == 47.3  # south — floor → same
        assert round(result[3], 6) == 15.0  # east  — ceil → same

    def test_north_east_rounded_up(self) -> None:
        """North and east edges are ceiled to the next 0.1° boundary."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.04, 5.91, 47.31, 15.04]
        result = ERA5Downloader._snap_bbox(bbox)
        # north 55.04 → ceil to 55.1
        assert round(result[0], 6) == round(55.1, 6)
        # east 15.04 → ceil to 15.1
        assert round(result[3], 6) == round(15.1, 6)

    def test_south_west_rounded_down(self) -> None:
        """South and west edges are floored to the previous 0.1° boundary."""
        from datavia.weather.era5_downloader import ERA5Downloader

        bbox = [55.0, 5.96, 47.36, 15.0]
        result = ERA5Downloader._snap_bbox(bbox)
        # west 5.96 → floor to 5.9
        assert round(result[1], 6) == round(5.9, 6)
        # south 47.36 → floor to 47.3
        assert round(result[2], 6) == round(47.3, 6)

    def test_snap_applied_in_init(self) -> None:
        """ERA5Downloader stores the snapped bbox, not the raw input."""
        from datavia.weather.era5_downloader import ERA5Downloader

        raw_bbox = [55.04, 5.91, 47.31, 15.04]
        dl = ERA5Downloader(bbox=raw_bbox)
        # snapped values must differ from the raw non-aligned inputs
        assert dl.bbox != raw_bbox


# ---------------------------------------------------------------------------
# unit_conversions (issue #5)

# ---------------------------------------------------------------------------


class TestERA5DownloaderMonthlyChunking:
    """Tests for :meth:`ERA5Downloader._iter_monthly_chunks`.

    Verifies the chunking logic independently of CDS access.  All tests are
    purely computational — no network or ``cdsapi`` interaction.
    """

    def test_single_month_produces_one_chunk(self) -> None:
        """A range within one calendar month yields exactly one chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-05", "2024-01-20")
        assert len(chunks) == 1
        assert chunks[0] == ("2024-01-05", "2024-01-20")

    def test_full_month_boundaries(self) -> None:
        """A range covering exactly one full month yields the full-month chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-02-01", "2024-02-29")
        assert chunks == [("2024-02-01", "2024-02-29")]

    def test_three_month_range_produces_three_chunks(self) -> None:
        """A range spanning January to March 2024 yields exactly three chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-03-31")
        assert len(chunks) == 3
        assert chunks[0] == ("2024-01-01", "2024-01-31")
        assert chunks[1] == ("2024-02-01", "2024-02-29")
        assert chunks[2] == ("2024-03-01", "2024-03-31")

    def test_partial_first_and_last_months(self) -> None:
        """Partial start and end months are clipped to the requested range."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-15", "2024-03-10")
        assert len(chunks) == 3
        assert chunks[0] == ("2024-01-15", "2024-01-31")
        assert chunks[1] == ("2024-02-01", "2024-02-29")
        assert chunks[2] == ("2024-03-01", "2024-03-10")

    def test_year_boundary_chunk_count(self) -> None:
        """A range crossing a year boundary produces the correct number of chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2023-11-01", "2024-02-28")
        # Nov-2023, Dec-2023, Jan-2024, Feb-2024 → 4 chunks.
        assert len(chunks) == 4
        assert chunks[0][0] == "2023-11-01"
        assert chunks[-1][1] == "2024-02-28"

    def test_single_day_range(self) -> None:
        """A single-day range produces one chunk with equal start and end."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-06-15", "2024-06-15")
        assert chunks == [("2024-06-15", "2024-06-15")]

    def test_end_before_start_raises(self) -> None:
        """ValueError is raised when date_end precedes date_start."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_monthly_chunks("2024-03-01", "2024-02-01")

    def test_chunks_are_sorted(self) -> None:
        """The returned chunk list is sorted by start date."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-12-31")
        start_dates = [c[0] for c in chunks]
        assert start_dates == sorted(start_dates)

    def test_full_year_produces_twelve_chunks(self) -> None:
        """A full calendar year produces exactly 12 monthly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks("2024-01-01", "2024-12-31")
        assert len(chunks) == 12


# ---------------------------------------------------------------------------
# ERA5Downloader — download() with mocked CDS client (Step 9)

# ---------------------------------------------------------------------------


class TestERA5DownloaderDownloadMocked:
    """Tests for :meth:`ERA5Downloader.download` with a mocked CDS client.

    All tests replace ``cdsapi.Client`` with a mock so no network access is
    required.  The mock records how many ``retrieve()`` calls were made, which
    verifies the monthly-chunking logic end-to-end.
    """

    @staticmethod
    def _make_mock_client(tmp_path) -> MagicMock:
        """Return a ``cdsapi.Client`` mock whose ``retrieve`` writes a stub file.

        The mock's ``retrieve`` side-effect creates a real temporary file so
        that the caller can check that paths are produced.

        Parameters
        ----------
        tmp_path : pathlib.Path
            Directory used for stub output files.

        Returns
        -------
        MagicMock
            Configured mock with a ``retrieve`` method that returns a job-like
            object with a ``download`` method.
        """
        import tempfile

        client_mock = MagicMock()

        def fake_retrieve(dataset, request):
            fd, path = tempfile.mkstemp(suffix=".nc", dir=str(tmp_path), prefix="era5_")
            import os

            os.close(fd)
            job = MagicMock()
            job.reply = {"status": "completed", "request_id": "test-job"}
            job.download = MagicMock(side_effect=lambda p: None)
            # The saver reads the path we pass to download(), so record it.
            job._out_path = path
            # Capture what path was used in the outer download() call.
            client_mock._last_out_path = path
            return job

        client_mock.retrieve = MagicMock(side_effect=fake_retrieve)
        return client_mock

    def test_three_month_range_submits_three_jobs(self, tmp_path) -> None:
        """A three-month range triggers exactly three CDS retrieve() calls.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-03-31",
            buffer_days=0,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        assert client_mock.retrieve.call_count == 3, (
            f"Expected 3 CDS retrieve() calls for a 3-month range, "
            f"got {client_mock.retrieve.call_count}"
        )
        # Result must be a newline-joined string of 3 paths.
        paths = result.splitlines()
        assert len(paths) == 3

    def test_single_month_submits_one_job(self, tmp_path) -> None:
        """A range within one calendar month triggers exactly one retrieve() call.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-06-01",
            date_end="2024-06-30",
            buffer_days=0,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        assert client_mock.retrieve.call_count == 1
        assert len(result.splitlines()) == 1

    def test_buffer_days_extends_first_chunk_into_prior_month(self, tmp_path) -> None:
        """buffer_days=1 starting on 2024-02-01 stretches the first chunk into January.

        The effective start becomes 2024-01-31, so the chunking produces two
        jobs: one for January (single day) and one for February.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-02-01",
            date_end="2024-02-29",
            buffer_days=1,
        )
        client_mock = self._make_mock_client(tmp_path)

        with patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi:
            mock_cdsapi.Client.return_value = client_mock
            result = downloader.download()

        # Effective start = 2024-01-31 → Jan chunk + Feb chunk = 2 jobs.
        assert client_mock.retrieve.call_count == 2, (
            f"Expected 2 jobs when buffer_days spans into previous month, "
            f"got {client_mock.retrieve.call_count}"
        )
        assert len(result.splitlines()) == 2

    def test_cds_queue_timeout_raises_timeout_error(self, tmp_path) -> None:
        """TimeoutError is raised when a queued job exceeds cds_queue_timeout.

        The mock job stays in ``"queued"`` state indefinitely.  With
        ``cds_queue_timeout=0`` the timeout is triggered on the first poll.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        downloader = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-01-31",
            buffer_days=0,
            cds_queue_timeout=0,
        )

        client_mock = MagicMock()

        def fake_retrieve_queued(dataset, request):
            job = MagicMock()
            job.reply = {"status": "queued", "request_id": "timeout-job"}
            job.update = MagicMock()
            job.delete = MagicMock()
            return job

        client_mock.retrieve = MagicMock(side_effect=fake_retrieve_queued)

        with (
            patch("datavia.weather.era5_downloader.cdsapi") as mock_cdsapi,
            patch("datavia.weather.era5_downloader.time.sleep"),
        ):
            mock_cdsapi.Client.return_value = client_mock
            with pytest.raises(TimeoutError, match="timeout-job"):
                downloader.download()

    def test_composite_downloader_collects_multi_path_era5_result(
        self, tmp_path
    ) -> None:
        """CompositeWeatherDownloader.download() flattens multi-path ERA5 results.

        When ERA5Downloader returns a newline-joined 3-path string,
        CompositeWeatherDownloader must pass all three paths through so that
        WeatherPipeline can call SaverWeather.save() on each.

        Parameters
        ----------
        tmp_path : pathlib.Path
            pytest-provided temporary directory.
        """
        from datavia.weather.composite_downloader import CompositeWeatherDownloader

        multi_path_result = "/tmp/era5_jan.nc\n/tmp/era5_feb.nc\n/tmp/era5_mar.nc"

        with patch(
            "datavia.weather.composite_downloader.get_grid_downloader_class"
        ) as mock_registry:
            mock_grid_class = MagicMock()
            mock_grid_instance = MagicMock()
            mock_grid_instance.download.return_value = multi_path_result
            mock_grid_class.return_value = mock_grid_instance
            mock_registry.return_value = mock_grid_class

            composite = CompositeWeatherDownloader(
                config={
                    "source": "ERA5_land",
                    "variables": ["2m_temperature"],
                    "date_start": "2024-01-01",
                    "date_end": "2024-03-31",
                }
            )
            result = composite.download()

        paths = [p for p in result.splitlines() if p]
        assert len(paths) == 3
        assert "/tmp/era5_jan.nc" in paths
        assert "/tmp/era5_feb.nc" in paths
        assert "/tmp/era5_mar.nc" in paths


# ---------------------------------------------------------------------------
# WeatherPipeline — cds_queue_timeout in known config keys (Step 9)

# ---------------------------------------------------------------------------


class TestERA5DownloaderQuarterlyChunking:
    """Unit tests for :meth:`ERA5Downloader._iter_quarterly_chunks`."""

    def test_full_year_produces_four_quarters(self) -> None:
        """A full calendar year produces exactly four quarterly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-01-01", "2023-12-31")
        assert len(chunks) == 4
        assert chunks[0] == ("2023-01-01", "2023-03-31")
        assert chunks[1] == ("2023-04-01", "2023-06-30")
        assert chunks[2] == ("2023-07-01", "2023-09-30")
        assert chunks[3] == ("2023-10-01", "2023-12-31")

    def test_two_full_years_produce_eight_quarters(self) -> None:
        """Two full calendar years produce eight quarterly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2022-01-01", "2023-12-31")
        assert len(chunks) == 8

    def test_partial_first_quarter_clipped(self) -> None:
        """A range starting mid-quarter clips the first chunk correctly."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-02-15", "2023-06-30")
        # Q1 clipped: starts 2023-02-15
        assert chunks[0] == ("2023-02-15", "2023-03-31")
        # Q2 is full
        assert chunks[1] == ("2023-04-01", "2023-06-30")

    def test_partial_last_quarter_clipped(self) -> None:
        """A range ending mid-quarter clips the last chunk correctly."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-07-01", "2023-11-15")
        assert chunks[-1] == ("2023-10-01", "2023-11-15")

    def test_single_day_produces_one_chunk(self) -> None:
        """A one-day range produces a single quarterly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2023-05-20", "2023-05-20")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-05-20", "2023-05-20")

    def test_invalid_range_raises(self) -> None:
        """date_end < date_start raises ValueError."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_quarterly_chunks("2023-06-01", "2023-01-01")

    def test_cross_year_boundary_quarterly(self) -> None:
        """A range spanning a year boundary (Q4 → Q1) produces correct chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2022-11-01", "2023-03-31")
        # Q4 2022 clipped
        assert chunks[0] == ("2022-11-01", "2022-12-31")
        # Q1 2023 full
        assert chunks[1] == ("2023-01-01", "2023-03-31")
        assert len(chunks) == 2

    def test_leap_year_q1_end(self) -> None:
        """Q1 of a leap year ends on Feb 29 when the range includes it."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks("2024-01-01", "2024-06-30")
        # Q1 must end on 2024-03-31 (not Feb 29 — quarters end on month boundaries)
        assert chunks[0] == ("2024-01-01", "2024-03-31")


# ---------------------------------------------------------------------------
# ERA5Downloader — yearly chunking

# ---------------------------------------------------------------------------


class TestERA5DownloaderYearlyChunking:
    """Unit tests for :meth:`ERA5Downloader._iter_yearly_chunks`."""

    def test_full_year_produces_one_chunk(self) -> None:
        """A full calendar year produces exactly one yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-01-01", "2023-12-31")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-01-01", "2023-12-31")

    def test_three_years_produces_three_chunks(self) -> None:
        """Three calendar years produce exactly three yearly chunks."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2022-01-01", "2024-12-31")
        assert len(chunks) == 3
        assert chunks[0] == ("2022-01-01", "2022-12-31")
        assert chunks[1] == ("2023-01-01", "2023-12-31")
        assert chunks[2] == ("2024-01-01", "2024-12-31")

    def test_partial_first_year_clipped(self) -> None:
        """A range starting mid-year clips the first yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-06-01", "2024-12-31")
        assert chunks[0] == ("2023-06-01", "2023-12-31")
        assert chunks[1] == ("2024-01-01", "2024-12-31")

    def test_partial_last_year_clipped(self) -> None:
        """A range ending mid-year clips the last yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2022-01-01", "2023-09-30")
        assert chunks[-1] == ("2023-01-01", "2023-09-30")

    def test_single_day_produces_one_chunk(self) -> None:
        """A one-day range produces a single yearly chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks("2023-07-04", "2023-07-04")
        assert len(chunks) == 1
        assert chunks[0] == ("2023-07-04", "2023-07-04")

    def test_invalid_range_raises(self) -> None:
        """date_end < date_start raises ValueError."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="date_end"):
            ERA5Downloader._iter_yearly_chunks("2023-12-01", "2023-01-01")


# ---------------------------------------------------------------------------
# ERA5Downloader — chunk_by parameter validation and dispatch

# ---------------------------------------------------------------------------


class TestERA5DownloaderChunkBy:
    """Unit tests for the ``chunk_by`` parameter on ERA5Downloader."""

    def test_invalid_chunk_by_raises(self) -> None:
        """Passing an unknown chunk_by value raises ValueError at construction."""
        from datavia.weather.era5_downloader import ERA5Downloader

        with pytest.raises(ValueError, match="chunk_by"):
            ERA5Downloader(
                variables=["2m_temperature"],
                date_start="2024-01-01",
                date_end="2024-12-31",
                chunk_by="weekly",  # Not a valid value.
            )

    def test_default_chunk_by_is_monthly(self) -> None:
        """The default chunk_by value is 'monthly'."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
        )
        assert dl.chunk_by == "monthly"

    def test_get_chunks_monthly_dispatch(self) -> None:
        """_get_chunks with chunk_by='monthly' returns one chunk per month."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-03-31",
            chunk_by="monthly",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 3  # Jan, Feb, Mar

    def test_get_chunks_quarterly_dispatch(self) -> None:
        """_get_chunks with chunk_by='quarterly' returns one chunk per quarter."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="quarterly",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 4

    def test_get_chunks_yearly_dispatch(self) -> None:
        """_get_chunks with chunk_by='yearly' returns one chunk per year."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2023-01-01",
            date_end="2024-12-31",
            chunk_by="yearly",
        )
        chunks = dl._get_chunks("2023-01-01")
        assert len(chunks) == 2

    def test_get_chunks_none_dispatch(self) -> None:
        """_get_chunks with chunk_by='none' returns a single chunk."""
        from datavia.weather.era5_downloader import ERA5Downloader

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="none",
        )
        chunks = dl._get_chunks("2024-01-01")
        assert len(chunks) == 1
        assert chunks[0] == ("2024-01-01", "2024-12-31")

    @patch("datavia.weather.era5_downloader.cdsapi")
    def test_chunk_by_none_produces_one_cds_job(self, mock_cdsapi: MagicMock) -> None:
        """download() with chunk_by='none' submits exactly one CDS job."""

        from datavia.weather.era5_downloader import ERA5Downloader

        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        # Simulate a job that downloads an empty temp file.
        def fake_download(path: str) -> None:
            with open(path, "wb"):
                pass

        mock_job = MagicMock()
        mock_job.reply = {"status": "completed", "request_id": "test-none"}
        mock_job.download.side_effect = fake_download
        mock_client.retrieve.return_value = mock_job

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2024-01-01",
            date_end="2024-12-31",
            chunk_by="none",
            buffer_days=0,
        )
        dl.download()

        assert mock_client.retrieve.call_count == 1, (
            "Expected 1 CDS job for chunk_by='none', "
            f"got {mock_client.retrieve.call_count}"
        )

    @patch("datavia.weather.era5_downloader.cdsapi")
    def test_chunk_by_quarterly_two_years_produces_eight_jobs(
        self, mock_cdsapi: MagicMock
    ) -> None:
        """download() with chunk_by='quarterly' over 2 years submits 8 CDS jobs."""
        from datavia.weather.era5_downloader import ERA5Downloader

        mock_client = MagicMock()
        mock_cdsapi.Client.return_value = mock_client

        def fake_download(path: str) -> None:
            with open(path, "wb"):
                pass

        mock_job = MagicMock()
        mock_job.reply = {"status": "completed", "request_id": "test-quarterly"}
        mock_job.download.side_effect = fake_download
        mock_client.retrieve.return_value = mock_job

        dl = ERA5Downloader(
            variables=["2m_temperature"],
            date_start="2022-01-01",
            date_end="2023-12-31",
            chunk_by="quarterly",
            buffer_days=0,
        )
        dl.download()

        assert mock_client.retrieve.call_count == 8, (
            "Expected 8 CDS jobs for 2 years quarterly, "
            f"got {mock_client.retrieve.call_count}"
        )


# ---------------------------------------------------------------------------
# CompositeWeatherDownloader — chunk_by forwarding

# ---------------------------------------------------------------------------
# Chunk-boundary invariants
# ---------------------------------------------------------------------------


class TestChunkInvariants:
    """Invariant tests for monthly, quarterly, and yearly chunk iterators.

    These tests verify properties that must hold for any valid chunking
    output, regardless of the specific date range used:

    - Contiguity: consecutive chunks are adjacent (no gaps, no overlap).
    - Containment: every chunk's start/end lies within the requested range.

    These invariants complement the boundary-exact tests in
    TestERA5DownloaderMonthlyChunking etc. by testing structural
    correctness across a wider range of inputs.
    """

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2024-01-01", "2024-06-30"),
            ("2023-11-15", "2024-03-20"),
            ("2024-01-01", "2024-12-31"),
        ],
    )
    def test_monthly_chunks_are_contiguous_no_gaps(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that consecutive monthly chunks share a boundary with no gap.

        The end of chunk[i] and the start of chunk[i+1] must be exactly one
        calendar day apart.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        import datetime as dt
        from datetime import timedelta

        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks(date_start, date_end)
        for i in range(len(chunks) - 1):
            end_of_current = dt.date.fromisoformat(chunks[i][1])
            start_of_next = dt.date.fromisoformat(chunks[i + 1][0])
            assert start_of_next - end_of_current == timedelta(days=1), (
                f"Gap between chunk {i} and {i + 1}: "
                f"{chunks[i][1]} → {chunks[i + 1][0]}"
            )

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2024-01-01", "2024-06-30"),
            ("2023-11-15", "2024-03-20"),
            ("2024-01-01", "2024-12-31"),
        ],
    )
    def test_monthly_chunks_within_requested_bounds(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that all monthly chunk dates fall within the requested range.

        The first chunk's start must equal date_start and the last chunk's
        end must equal date_end.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_monthly_chunks(date_start, date_end)
        assert chunks[0][0] == date_start
        assert chunks[-1][1] == date_end

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2024-01-01", "2024-12-31"),
            ("2022-01-01", "2023-12-31"),
        ],
    )
    def test_quarterly_chunks_are_contiguous_no_gaps(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that consecutive quarterly chunks share a boundary with no gap.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        import datetime as dt
        from datetime import timedelta

        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks(date_start, date_end)
        for i in range(len(chunks) - 1):
            end_of_current = dt.date.fromisoformat(chunks[i][1])
            start_of_next = dt.date.fromisoformat(chunks[i + 1][0])
            assert start_of_next - end_of_current == timedelta(days=1), (
                f"Gap between quarterly chunk {i} and {i + 1}: "
                f"{chunks[i][1]} → {chunks[i + 1][0]}"
            )

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2024-01-01", "2024-12-31"),
            ("2022-01-01", "2023-12-31"),
        ],
    )
    def test_quarterly_chunks_within_requested_bounds(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that quarterly chunk boundaries equal the requested start and end.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_quarterly_chunks(date_start, date_end)
        assert chunks[0][0] == date_start
        assert chunks[-1][1] == date_end

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2022-01-01", "2024-12-31"),
            ("2023-06-01", "2025-05-31"),
        ],
    )
    def test_yearly_chunks_are_contiguous_no_gaps(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that consecutive yearly chunks share a boundary with no gap.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        import datetime as dt
        from datetime import timedelta

        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks(date_start, date_end)
        for i in range(len(chunks) - 1):
            end_of_current = dt.date.fromisoformat(chunks[i][1])
            start_of_next = dt.date.fromisoformat(chunks[i + 1][0])
            assert start_of_next - end_of_current == timedelta(days=1), (
                f"Gap between yearly chunk {i} and {i + 1}: "
                f"{chunks[i][1]} → {chunks[i + 1][0]}"
            )

    @pytest.mark.parametrize(
        "date_start,date_end",
        [
            ("2022-01-01", "2024-12-31"),
            ("2023-06-01", "2025-05-31"),
        ],
    )
    def test_yearly_chunks_within_requested_bounds(
        self, date_start: str, date_end: str
    ) -> None:
        """Test that yearly chunk boundaries equal the requested start and end.

        Args:
            date_start: Start of the requested date range (ISO-8601).
            date_end: End of the requested date range (ISO-8601).

        Returns:
            None
        """
        from datavia.weather.era5_downloader import ERA5Downloader

        chunks = ERA5Downloader._iter_yearly_chunks(date_start, date_end)
        assert chunks[0][0] == date_start
        assert chunks[-1][1] == date_end
