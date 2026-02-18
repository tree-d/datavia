import logging
import os

import numpy as np
import pytest
from datavia.elevation import ElevationPipeline

from datavia.core.datavia import Datavia
from datavia.library.database.start import initialize_database
from datavia.runner import get_container_status, start_container, stop_container

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.skipif(
    os.getenv("DATAVIA_E2E") != "1",
    reason="DATAVIA_E2E not set; skipping integration test",
)
def test_elevation_pipeline_inside_datavia_e2e():
    """Test the elevation pipeline via the Datavia controller."""
    # Setup: Ensure container is running and database is initialized
    if not get_container_status():
        start_container()
        logger.info("Initialized container.")
    initialize_database()

    try:
        elevation_pipeline = ElevationPipeline(
            url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000"
        )
        datavia_controller = Datavia(pipelines=(elevation_pipeline,))

        # Run the controller to initialize, then sync and update data
        datavia_controller()
        datavia_controller.elevation.sync_files_and_database()
        datavia_controller.elevation.update_data()

        # Verify data retrieval
        coords = np.array([[10.0, 50.0], [11.0, 51.0]])
        elevation_data = datavia_controller.elevation.get_data(
            coords=coords, crs_coords="EPSG:4326"
        )

        assert elevation_data is not None, "get_data should return data, not None."
        assert len(elevation_data) == len(
            coords
        ), "Should receive one elevation value per coordinate."
        assert np.all(
            elevation_data > 0
        ), "Elevation values should be positive for the given coordinates."

    finally:
        # Teardown: Stop the container after the test
        stop_container()
