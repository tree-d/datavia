"""Main Datavia controller for managing data integration pipelines.

The Datavia class serves as the central controller for orchestrating
multiple data integration pipelines. It initializes the database and
manages the lifecycle of registered pipelines.

Example
-------
>>> import numpy as np
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

    Groups several pipelines under one object.  After calling the instance
    (``dv()``), each pipeline is available as an attribute named after its
    :attr:`~datavia.core.interfaces.Pipeline.name`, which is the pipeline's
    ``config["source"]``:

    - ``ElevationPipeline()`` → ``dv.elevation``
    - ``SoilPipeline()`` → ``dv.soil``;
      ``SoilPipeline({"source": "mysoil"})`` → ``dv.mysoil``
    - ``WeatherPipeline({"source": "ERA5_land", ...})`` → ``dv.ERA5_land``

    The attributes do not exist until ``dv()`` has been called.  Two
    pipelines with the same name overwrite each other.

    Using ``Datavia`` is optional.  Each pipeline also works on its own, e.g.
    ``ElevationPipeline().update_data()``.

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
        """Initialise the database and register pipelines without starting them.

        Creates the metadata database and data directory if they do not
        exist yet (see :mod:`datavia.config`).  No network access happens.

        Each pipeline's downloader, saver, and getter are constructed lazily
        on the first call to ``update_data`` or ``get_data``, so importing or
        creating a :class:`Datavia` instance does not trigger any network I/O
        or heavy initialisation for pipelines that are not used in a given run.

        Returns
        -------
        Datavia
            Self for method chaining.
        """
        initialize_database()
        logging.info("Datavia controller initialised with database.")
        for pipeline in self.pipelines:
            # Register without calling pipeline() — lazy init on first use.
            self.add_pipeline(pipeline)
        return self

    def add_pipeline(self, pipeline: Pipeline) -> None:
        """Register *pipeline* as the attribute ``self.<pipeline.name>``.

        Parameters
        ----------
        pipeline : Pipeline
            Pipeline instance to expose.  It is not initialised here; its
            components are built on its first ``update_data`` / ``get_data``
            call.
        """
        self.__setattr__(pipeline.name, pipeline)
