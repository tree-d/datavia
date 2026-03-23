"""End-to-end tests for the SoilPipeline.

These tests exercise the full pipeline against real remote services (SoilGrids
WCS API and HiHydroSoil OpenDAP catalogue) and a live PostGIS database.

The tests are skipped unless the environment variable ``DATAVIA_E2E`` is set
to ``"1"`` so that they are excluded from the normal unit-test run::

    DATAVIA_E2E=1 pytest tests/test_soil_e2e.py -v

A Docker PostGIS container must be reachable and the database must be
initialisable. The tests manage the container lifecycle themselves (start if
required, stop in teardown).

Properties and depth layers are intentionally limited to a minimal subset
(one SoilGrids and one HiHydroSoil coverage each) to keep the download
duration reasonable in CI.
"""

import logging
import os

import numpy as np
import pytest
from datavia.soil.pipeline import SoilPipeline

from datavia.library.database.start import initialize_database
from datavia.runner import get_container_status, start_container, stop_container

logging.basicConfig(level=logging.INFO)

_E2E_GUARD = pytest.mark.skipif(
    os.getenv("DATAVIA_E2E") != "1",
    reason="DATAVIA_E2E not set; skipping soil pipeline end-to-end test",
)

# Coordinates of German cities used across all retrieval assertions.
# Column order: (longitude, latitude) in EPSG:4326.
_GERMAN_COORDS = np.array(
    [
        [13.405, 52.520],  # Berlin
        [11.576, 48.137],  # Munich
        [6.960, 50.938],  # Cologne
    ]
)

# Single SoilGrids coverage: clay at 0-5 cm depth.
_SOILGRIDS_COVERAGE_ID = "clay_0-5cm_mean"

# Single HiHydroSoil coverage: field capacity at 0-5 cm depth.
_HIHYDROSOIL_COVERAGE_ID = "field_capacity_0-5cm_mean"


# ---------------------------------------------------------------------------
# Container setup / teardown
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_database():
    """Module-scoped fixture that starts the PostGIS container once.

    The container is stopped in the module teardown regardless of whether any
    test fails so that resources are not left running after the test session.
    """
    container_was_running = get_container_status()
    if not container_was_running:
        start_container()
    initialize_database()

    yield  # run all tests in the module

    if not container_was_running:
        stop_container()


# ---------------------------------------------------------------------------
# SoilGrids-only E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_soilgrids_coverage_downloaded_and_retrievable(live_database) -> None:
    """Clay at 0-5 cm is downloaded from SoilGrids and returns plausible values.

    Expected raw storage unit: g/kg (integer-scaled).
    Conversion to percentage: divide by 10.
    Plausible clay range for Central Europe: 5 - 60 %.
    """
    pipeline = SoilPipeline(
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()

    update_ok = pipeline.update_data()
    assert update_ok, (
        "update_data() returned False for 'clay_0-5cm_mean'. "
        "Check log output for the download error."
    )

    # Verify the coverage is registered in the database.
    stored = pipeline.getter.get_stored_coverage_ids()
    assert any("clay" in cid for cid in stored), (
        f"No clay coverage found in stored IDs: {stored}"
    )

    # Retrieve values at German city coordinates.
    values = pipeline.get_data(
        coords=_GERMAN_COORDS,
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
        crs_coords="EPSG:4326",
    )

    # Single match — get_data returns a 1-D array directly.
    if isinstance(values, dict):
        values = values.get(_SOILGRIDS_COVERAGE_ID, np.array([]))

    assert len(values) == len(_GERMAN_COORDS), (
        "Expected one value per input coordinate."
    )
    assert not np.all(np.isnan(values)), "All returned values are NaN."

    # Plausibility: raw values are g/kg integer-scaled; divide by 10 for %.
    clay_percent = values / 10.0
    assert np.all(clay_percent >= 0), "Negative clay percentage detected."
    assert np.all(clay_percent <= 100), "Clay percentage exceeds 100 %."


# ---------------------------------------------------------------------------
# HiHydroSoil-only E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_hihydrosoil_coverage_downloaded_and_retrievable(live_database) -> None:
    """Field capacity at 0-5 cm is downloaded from HiHydroSoil and returns
    plausible values.

    Expected raw storage unit: integers x 10 000.
    Conversion to cm³/cm³: multiply by 0.0001.
    Plausible field-capacity range: 0.05 - 0.55 cm\u00b3/cm\u00b3.
    """
    pipeline = SoilPipeline(
        properties=["field_capacity"],
        depths=["0-5cm"],
        hihydrosoil_depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()

    update_ok = pipeline.update_data()
    assert update_ok, (
        "update_data() returned False for 'field_capacity_0-5cm_mean'. "
        "Check log output for the download error."
    )

    stored = pipeline.getter.get_stored_coverage_ids()
    assert any("field_capacity" in cid for cid in stored), (
        f"No field_capacity coverage found in stored IDs: {stored}"
    )

    values = pipeline.get_data(
        coords=_GERMAN_COORDS,
        properties=["field_capacity"],
        depths=["0-5cm"],
        value="mean",
        crs_coords="EPSG:4326",
    )

    if isinstance(values, dict):
        values = values.get(_HIHYDROSOIL_COVERAGE_ID, np.array([]))

    assert len(values) == len(_GERMAN_COORDS)
    assert not np.all(np.isnan(values)), "All field_capacity values are NaN."

    # Convert raw integer representation to cm³/cm³.
    fc_volumetric = values * 0.0001
    assert np.all(fc_volumetric >= 0), "Negative field capacity detected."
    assert np.all(fc_volumetric <= 1.0), "Field capacity exceeds 1.0 cm³/cm³."


# ---------------------------------------------------------------------------
# Multi-source E2E (SoilGrids + HiHydroSoil together)
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_multi_source_pipeline_returns_dict_keyed_by_coverage_id(
    live_database,
) -> None:
    """A two-property request spanning both sources returns a dict with all keys.

    Requesting clay (SoilGrids) and field_capacity (HiHydroSoil) at the same
    depth and statistic must yield a result dict containing both coverage IDs.
    """
    pipeline = SoilPipeline(
        properties=["clay", "field_capacity"],
        depths=["0-5cm"],
        hihydrosoil_depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()
    pipeline.update_data()

    result = pipeline.get_data(
        coords=_GERMAN_COORDS,
        properties=["clay", "field_capacity"],
        depths=["0-5cm"],
        value="mean",
        crs_coords="EPSG:4326",
    )

    assert isinstance(result, dict), (
        "Expected a dict when multiple coverage IDs match the request."
    )
    assert _SOILGRIDS_COVERAGE_ID in result, (
        f"'{_SOILGRIDS_COVERAGE_ID}' missing from result keys: {list(result)}"
    )
    assert _HIHYDROSOIL_COVERAGE_ID in result, (
        f"'{_HIHYDROSOIL_COVERAGE_ID}' missing from result keys: {list(result)}"
    )

    for cov_id, values in result.items():
        assert len(values) == len(_GERMAN_COORDS), (
            f"Coverage '{cov_id}' returned {len(values)} values, "
            f"expected {len(_GERMAN_COORDS)}."
        )


# ---------------------------------------------------------------------------
# Incremental update E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_second_update_data_call_skips_already_stored_coverages(
    live_database,
) -> None:
    """Calling update_data a second time must return True without any new downloads.

    The pipeline must detect that all requested coverages are already in the
    local data directory and the PostGIS database, and skip the download step.
    """
    pipeline = SoilPipeline(
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()
    pipeline.update_data()  # First call: downloads if necessary.

    # Re-initialise a fresh pipeline instance pointing at the same data.
    pipeline2 = SoilPipeline(
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
    )
    pipeline2()
    pipeline2.sync_files_and_database()

    second_result = pipeline2.update_data()
    assert second_result is True, (
        "Second update_data() call failed even though coverage is already stored."
    )


# ---------------------------------------------------------------------------
# get_available_properties E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_get_available_properties_reflects_stored_data(live_database) -> None:
    """get_available_properties returns the canonical names of stored coverages."""
    pipeline = SoilPipeline(
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()
    pipeline.update_data()

    available = pipeline.get_available_properties()
    assert "clay" in available, f"'clay' not reported as available. Got: {available}"


# ---------------------------------------------------------------------------
# configure + update_data workflow E2E
# ---------------------------------------------------------------------------


@_E2E_GUARD
def test_configure_then_update_downloads_new_coverage(live_database) -> None:
    """After configure(), update_data() downloads newly requested coverages.

    Starts with clay only, then reconfigures to add sand. The second
    update_data() call must download the sand coverage without re-downloading
    clay.
    """
    pipeline = SoilPipeline(
        properties=["clay"],
        depths=["0-5cm"],
        value="mean",
    )
    pipeline()
    pipeline.sync_files_and_database()
    pipeline.update_data()

    # Reconfigure to also include sand.
    pipeline.configure(properties=["clay", "sand"], depths=["0-5cm"])
    second_result = pipeline.update_data()

    assert second_result is True, (
        "update_data() after configure() returned False. sand coverage download failed."
    )

    available = pipeline.get_available_properties()
    assert "clay" in available
    assert "sand" in available
