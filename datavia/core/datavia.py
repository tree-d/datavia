"""Main Datavia controller for managing data integration pipelines.

The Datavia class serves as the central controller for orchestrating
multiple data integration pipelines. It initializes the database and
manages the lifecycle of registered pipelines.

Example
-------
>>> from datavia import Datavia
>>> from datavia.elevation import ElevationPipeline
>>>
>>> # Create controller with pipelines
>>> dv = Datavia(pipelines=[ElevationPipeline()])
>>> _ = dv()  # Initialize # doctest: +SKIP
>>> _ = dv.elevation.update_data()  # Update elevation data # doctest: +SKIP
>>>
>>> # Access pipeline
>>> coords = np.array([[13.4050, 52.5200]])  # Berlin
>>> data = dv.elevation.get_data(coords, crs_coords="EPSG:4326") # doctest: +SKIP
"""

import logging

from ..library.database.start import initialize_database
from .interfaces import Pipeline


class Datavia:
    """Main Datavia controller class.

    This replaces the old UpdateManager and becomes the central controller
    for managing data integration pipelines.

    Parameters
    ----------
    pipelines : list[Pipeline]
        List of pipeline instances to manage

    Attributes
    ----------
    pipelines : list[Pipeline]
        Registered pipeline instances
    """

    def __init__(self, pipelines: list[Pipeline]):
        """Initialize Datavia controller with pipelines.

        Parameters
        ----------
        pipelines : list[Pipeline]
            List of pipeline instances to register
        """
        self.pipelines = pipelines

    def __call__(self) -> "Datavia":
        """Initialize the Datavia controller and add pipelines."""
        initialize_database()
        logging.info("Datavia controller initialized with database.")
        for pipeline in self.pipelines:
            self.add_pipeline(pipeline())
        return self

    def add_pipeline(self, pipeline: Pipeline) -> None:
        """Add a pipeline to the Datavia controller."""
        self.__setattr__(pipeline.name, pipeline)
