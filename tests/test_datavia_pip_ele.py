import os
import sys

# Add the parent directory (datavia-pipelines) to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import numpy as np
from datavia.elevation import ElevationPipeline

from datavia.core.datavia import Datavia
from datavia.library.database.start import initialize_database
from datavia.runner import get_container_status, start_container, stop_container

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


ElevationPipeline = ElevationPipeline(
    url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000"
)

MyDatavia = Datavia(pipelines=(ElevationPipeline,))


def test_elevation_pipeline():
    if not get_container_status():
        start_container()
        logger.info("Initialized container.")
    initialize_database()

    MyDatavia()
    MyDatavia.elevation.sync_files_and_database()
    MyDatavia.elevation.update_data()
    print("\nFetching elevation data at specified coordinates...")
    print(
        MyDatavia.elevation.get_data(
            coords=np.array([[10.0, 50.0], [11.0, 51.0]]), crs_coords="EPSG:4326"
        )
    )
    print("\nTest completed successfully.")

    stop_container()


if __name__ == "__main__":
    test_elevation_pipeline()
