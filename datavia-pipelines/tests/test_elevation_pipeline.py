import sys
import os

# Add the parent directory (datavia-pipelines) to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.elevation import ElevationPipeline
from datavia.library.database.start import initialize_database
from datavia.runner import start_container, stop_container, get_container_status
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)

meinehoehe = ElevationPipeline(
    url="https://sgx.geodatenzentrum.de/wcs_dgm200_inspire?VERSION=2.0.1&SERVICE=WCS&REQUEST=GetCoverage&COVERAGEID=dgm200_inspire__EL.GridCoverage&format=image/tiff&crs=EPSG:25832&bbox=280000,5235000,921000,6101000"
)
meinehoehe()
if not get_container_status():
    start_container()
initialize_database()
meinehoehe.sync_files_and_database()

if meinehoehe.find_files():
    print("Elevation data files already exist.")
else:
    print("No elevation data files found. Downloading...")
    meinehoehe.update_data()


print("Fetching elevation data at specified coordinates...")
print(
    meinehoehe.get_data(
        coords=np.array([[10.0, 50.0], [11.0, 51.0]]), crs_coords="EPSG:4326"
    )
)
stop_container()
