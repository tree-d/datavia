import logging

from ..library.database.start import initialize_database
from .interfaces import Pipeline


class Datavia:
    """Main Datavia controller class.

    This replaces the old UpdateManager and becomes the central controller
    for managing data integration pipelines.
    """

    def __init__(self, pipelines: list[Pipeline]):
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
